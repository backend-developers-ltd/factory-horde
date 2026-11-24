"""Freeze version-two envelope discovery at one chain block for actor-owned rounds."""

import base64
import re
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from prometheus_client import Counter, Histogram
from pydantic import TypeAdapter
from pylon_client.artanis import BlockNumber, CommitmentDataBytes, Config, NetUid, PylonClient
from pylon_client.artanis.v1 import GetCommitmentsResponse, GetNeuronsResponse

from validator.record_files import RecordConflictError, RecordFiles
from validator.records import BlockHash, CohortMember, ImageReference, MinerHotkey, NonnegativeInt, Record, UtcTime

# The chain's BigRaw bound for one commitment.
COMMITMENT_MAX_BYTES = 512
_SEGMENT = rb"[a-z0-9]+(?:[._-][a-z0-9]+)*"
_OWN_ENVELOPE = re.compile(rb"<F:([^<>]*)>")
_PAYLOAD = re.compile(rb"(" + _SEGMENT + rb"(?:/" + _SEGMENT + rb")*)@([A-Za-z0-9_-]{43})")
_image = TypeAdapter[str](ImageReference)
_events = Counter("factory_horde_discovery_total", "Commitment discovery outcomes", ("outcome",))
_latency = Histogram("factory_horde_discovery_seconds", "Block-aligned commitment discovery duration")


class DiscoveryApi(Protocol):
    """Public Pylon v1 identity reads needed by discovery."""

    @property
    def netuid(self) -> NetUid: ...

    def get_commitments(self) -> GetCommitmentsResponse: ...
    def get_neurons(self, block_number: BlockNumber) -> GetNeuronsResponse: ...


class RejectedSubmission(Record):
    """A commitment excluded from the frozen cohort, with an explicit reason."""

    miner_hotkey: MinerHotkey
    reason: str


class DiscoverySnapshot(Record):
    """Immutable membership, references and reserved job identities from one block."""

    netuid: NonnegativeInt
    block: NonnegativeInt
    block_hash: BlockHash
    observed_at: UtcTime
    validator_hotkey: MinerHotkey
    allowed_hotkeys: tuple[MinerHotkey, ...] | None
    cohort: tuple[CohortMember, ...]
    rejected: tuple[RejectedSubmission, ...]


def decode_reference(value: str, legacy_namespace: str | None = None) -> str:
    """Decode the single FactoryHorde envelope of a hex commitment into a GHCR digest reference.

    Envelopes carry ghcr.io/OWNER/PACKAGE. Older envelopes carry only PACKAGE and are read under
    `legacy_namespace` when it is configured. Other mechanisms' envelopes are ignored.

    Raises:
        ValueError: Hex, length, envelope, digest or rebuilt reference is invalid.
    """
    if re.fullmatch(r"(?:0x)?(?:[0-9a-fA-F]{2})+", value) is None:
        raise ValueError("Malformed hexadecimal commitment")
    raw = bytes(CommitmentDataBytes.fromhex(value.removeprefix("0x")))
    if len(raw) > COMMITMENT_MAX_BYTES:
        raise ValueError(f"Commitment exceeds the chain's {COMMITMENT_MAX_BYTES}-byte limit")
    payloads = _OWN_ENVELOPE.findall(raw)
    if not payloads:
        raise ValueError("No FactoryHorde submission")
    if len(payloads) > 1:
        raise ValueError("Multiple FactoryHorde submissions")
    match = _PAYLOAD.fullmatch(payloads[0])
    if match is None:
        raise ValueError("Malformed FactoryHorde submission")
    digest = base64.urlsafe_b64decode(match[2] + b"=")
    if base64.urlsafe_b64encode(digest).rstrip(b"=") != match[2]:
        raise ValueError("Malformed FactoryHorde submission")
    path = match[1].decode()
    if not path.startswith("ghcr.io/"):
        if legacy_namespace is None:
            raise ValueError("FactoryHorde submission lacks its registry owner")
        path = f"{legacy_namespace}/{path}"
    try:
        return _image.validate_python(f"{path}@sha256:{digest.hex()}")
    except ValueError as error:
        raise ValueError("Malformed FactoryHorde submission") from error


def discover(
    api: DiscoveryApi,
    netuid: int,
    validator_hotkey: str,
    legacy_namespace: str | None,
    allowed_hotkeys: tuple[str, ...] | None = None,
) -> DiscoverySnapshot:
    """Read commitments first, then membership at exactly their returned block.

    A valid registered submission defines miner eligibility. Validator permits and
    axon availability do not define miner roles. An optional explicit allowlist can
    constrain local fixtures; the validator's own hotkey is always excluded.
    `legacy_namespace` is needed only to read envelopes without ghcr.io/OWNER.

    Raises:
        ValueError: Subnet, block or membership attribution is inconsistent.
    """
    with _latency.time():
        try:
            commitments = api.get_commitments()
            if api.netuid != netuid:
                raise ValueError("Pylon identity belongs to a different subnet")
            membership = api.get_neurons(commitments.block.number)
            if membership.block != commitments.block:
                raise ValueError("Membership and commitments are not from the same chain block")
            if any(key != neuron.hotkey for key, neuron in membership.neurons.items()):
                raise ValueError("Membership hotkey attribution mismatch")
            if len({neuron.uid for neuron in membership.neurons.values()}) != len(membership.neurons):
                raise ValueError("Membership contains duplicate UIDs")
            cohort: list[CohortMember] = []
            rejected: list[RejectedSubmission] = []
            for hotkey, value in sorted(commitments.commitments.items()):
                neuron = membership.neurons.get(hotkey)
                if hotkey == validator_hotkey or (allowed_hotkeys is not None and hotkey not in allowed_hotkeys):
                    reason = "excluded role"
                elif neuron is None:
                    reason = "not registered at the commitment snapshot block"
                else:
                    try:
                        image = decode_reference(value, legacy_namespace)
                    except ValueError as error:
                        reason = str(error)
                    else:
                        cohort.append(CohortMember(miner_hotkey=hotkey, uid=neuron.uid, image=image))
                        continue
                rejected.append(RejectedSubmission(miner_hotkey=hotkey, reason=reason))
            result = DiscoverySnapshot(
                netuid=netuid,
                block=commitments.block.number,
                block_hash=commitments.block.hash,
                observed_at=datetime.now(UTC),
                validator_hotkey=validator_hotkey,
                allowed_hotkeys=None if allowed_hotkeys is None else tuple(sorted(set(allowed_hotkeys))),
                cohort=tuple(sorted(cohort, key=lambda member: member.uid)),
                rejected=tuple(rejected),
            )
        except Exception:
            _events.labels("error").inc()
            raise
        _events.labels("accepted_submission").inc(len(result.cohort))
        _events.labels("rejected_submission").inc(len(result.rejected))
        _events.labels("snapshot").inc()
        return result


def freeze_discovery(
    files: RecordFiles,
    snapshot_id: UUID,
    api: DiscoveryApi,
    netuid: int,
    validator_hotkey: str,
    legacy_namespace: str | None,
    allowed_hotkeys: tuple[str, ...] | None = None,
) -> DiscoverySnapshot:
    """Recover a committed snapshot before chain reads; later updates cannot rewrite it.

    `legacy_namespace` is passed to discovery for envelopes without ghcr.io/OWNER.

    Raises:
        RecordConflictError: A reused identity selects a different subnet or role policy.
    """
    relative = f"control/discovery/{snapshot_id}.json"
    allowed = None if allowed_hotkeys is None else tuple(sorted(set(allowed_hotkeys)))
    try:
        result = files.read(relative, DiscoverySnapshot)
    except FileNotFoundError:
        result = discover(api, netuid, validator_hotkey, legacy_namespace, allowed)
        files.publish(relative, result)
    if (result.netuid, result.validator_hotkey, result.allowed_hotkeys) != (netuid, validator_hotkey, allowed):
        raise RecordConflictError("Discovery identity already has a different subnet or role policy")
    return result


def freeze_via_pylon(
    files: RecordFiles,
    config: Config,
    netuid: int,
    validator_hotkey: str,
    legacy_namespace: str | None,
    snapshot_id: UUID,
) -> DiscoverySnapshot:
    """Actor callback using only public Pylon v1; the validator never loads a wallet."""
    with PylonClient(config) as client:
        return freeze_discovery(files, snapshot_id, client.v1.identity, netuid, validator_hotkey, legacy_namespace)
