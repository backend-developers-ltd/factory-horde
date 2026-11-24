"""Publish one FactoryHorde commitment envelope through the miner's own Pylon identity, keeping every other byte."""

import base64
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from typing import Literal, Protocol

import click
from prometheus_client import Counter, Histogram
from pylon_client.artanis import (
    BasePylonException,
    BlockNumber,
    CommitmentDataBytes,
    Config,
    IdentityName,
    NetUid,
    PylonAuthToken,
    PylonBadGateway,
    PylonClient,
    PylonNotFound,
    PylonRequestException,
    PylonTimeout,
)
from pylon_client.artanis.v1 import GetCommitmentResponse, GetNeuronsResponse, SetCommitmentResponse
from tenacity import Retrying, stop_after_attempt

# The chain's BigRaw bound for one commitment; Pylon writes up to 128 bytes as RawN and longer values as BigRaw.
CHAIN_COMMITMENT_MAX_BYTES = 512
# ComputeHorde's largest manifest envelope: "<M:" + six "X999" codes joined by ";" + ">".
COMPUTE_HORDE_MANIFEST_RESERVE = 33
SEGMENT = r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
IMAGE_PATTERN = rf"ghcr\.io/{SEGMENT}(?:/{SEGMENT})+@sha256:[0-9a-f]{{64}}"
OWN_ENVELOPE = re.compile(rb"<F:([^<>]*)>")
MANIFEST_ENVELOPE = re.compile(rb"<M:[^<>]*>")
_events = Counter("factory_horde_submissions_total", "One-shot submission outcomes", ("outcome",))
_latency = Histogram("factory_horde_submission_seconds", "Submission and read-back duration")


class SubmissionError(RuntimeError):
    """Publication is invalid, misconfigured or still unconfirmed; never claim success."""


class SubmissionApi(Protocol):
    """The version-one identity API used by the one-shot submitter."""

    @property
    def netuid(self) -> NetUid: ...

    def get_latest_neurons(self) -> GetNeuronsResponse: ...
    def get_neurons(self, block_number: BlockNumber) -> GetNeuronsResponse: ...
    def get_own_commitment(self) -> GetCommitmentResponse: ...
    def set_commitment(self, commitment: CommitmentDataBytes) -> SetCommitmentResponse: ...


@dataclass(frozen=True)
class Publication:
    """Read-back evidence of the own envelope; commitment_block is the last write by either mechanism."""

    outcome: Literal["published", "unchanged", "recovered"]
    netuid: int
    hotkey: str
    image: str
    commitment_block: int
    observed_block: int
    observed_block_hash: str


def validate_reference(image: str) -> str:
    """Accept a complete GHCR digest reference.

    Raises:
        ValueError: The registry or digest syntax is unsupported.
    """
    if re.fullmatch(IMAGE_PATTERN, image) is None:
        raise ValueError("Expected ghcr.io/owner/image@sha256:<64 lowercase hexadecimal characters>")
    return image


def encode_envelope(image: str, max_bytes: int = CHAIN_COMMITMENT_MAX_BYTES) -> bytes:
    """Encode IMAGE as FactoryHorde's commitment envelope: registry, owner, package and compact digest.

    Raises:
        ValueError: The image is invalid or its envelope can never fit MAX_BYTES beside ComputeHorde's manifest.
    """
    validate_reference(image)
    path, digest = image.split("@sha256:")
    compact = base64.urlsafe_b64encode(bytes.fromhex(digest)).rstrip(b"=")
    envelope = b"<F:" + path.encode() + b"@" + compact + b">"
    if len(envelope) + COMPUTE_HORDE_MANIFEST_RESERVE > max_bytes:
        raise ValueError(
            f"Image reference is too long: its envelope and ComputeHorde's manifest must fit {max_bytes} bytes"
        )
    return envelope


def merge_envelope(current: bytes, envelope: bytes, max_bytes: int = CHAIN_COMMITMENT_MAX_BYTES) -> bytes:
    """Replace only FactoryHorde's envelope, keeping every other byte and ComputeHorde's manifest room.

    Raises:
        SubmissionError: The result would exceed MAX_BYTES or ComputeHorde's manifest reserve.
    """
    own = list(OWN_ENVELOPE.finditer(current))
    if own:
        tail = OWN_ENVELOPE.sub(b"", current[own[0].end() :])
        merged = current[: own[0].start()] + envelope + tail
    else:
        manifest = MANIFEST_ENVELOPE.search(current)
        at = manifest.start() if manifest is not None else len(current)
        merged = current[:at] + envelope + current[at:]
    if len(merged) > max_bytes:
        raise SubmissionError(f"Commitment would exceed the {max_bytes}-byte limit")
    if len(MANIFEST_ENVELOPE.sub(b"", merged)) + COMPUTE_HORDE_MANIFEST_RESERVE > max_bytes:
        raise SubmissionError(
            f"Commitment would leave ComputeHorde less than its 33-byte manifest reserve within {max_bytes} bytes"
        )
    return merged


def _raw(value: str) -> bytes:
    if re.fullmatch(r"(?:0x)?(?:[0-9a-fA-F]{2})*", value) is None:
        raise ValueError("Malformed hexadecimal commitment")
    return bytes(CommitmentDataBytes.fromhex(value.removeprefix("0x")))


def _read(api: SubmissionApi) -> GetCommitmentResponse | None:
    try:
        return api.get_own_commitment()
    except PylonNotFound:
        return None


def _matches(response: GetCommitmentResponse | None, envelope: bytes) -> bool:
    if response is None:
        return False
    try:
        return OWN_ENVELOPE.findall(_raw(response.commitment)) == [envelope[3:-1]]
    except ValueError:
        return False


def _evidence(
    api: SubmissionApi,
    response: GetCommitmentResponse,
    image: str,
    netuid: int,
    outcome: Literal["published", "unchanged", "recovered"],
) -> Publication:
    membership = api.get_neurons(response.block.number)
    if api.netuid != netuid or membership.block != response.block or response.hotkey not in membership.neurons:
        raise SubmissionError("Read-back is not attributable to a registered identity at the observed block")
    return Publication(
        outcome,
        netuid,
        response.hotkey,
        image,
        response.commitment_block_number,
        response.block.number,
        response.block.hash,
    )


def submit(
    api: SubmissionApi,
    image: str,
    netuid: int,
    *,
    max_bytes: int = CHAIN_COMMITMENT_MAX_BYTES,
    confirm_seconds: float = 150,
) -> Publication:
    """Read first, replace only the own envelope, write at most once, then require registered read-back.

    HTTP timeouts can leave the service's shielded write running. Recovery polls
    reads only; automatic HTTP write retries must be disabled in the client config.

    Raises:
        SubmissionError: Configuration changes, the commitment cannot be preserved or confirmation never arrives.
        ValueError: The reference, size limit or confirmation period is invalid.
    """
    envelope = encode_envelope(image, max_bytes)
    if confirm_seconds <= 0:
        raise ValueError("Confirmation period must be positive")
    with _latency.time():
        try:
            api.get_latest_neurons()  # Resolves identity-scoped netuid before any mutation.
            if api.netuid != netuid:
                raise SubmissionError("Configured Pylon identity belongs to a different subnet")
            previous = _read(api)
            if previous is not None and _matches(previous, envelope):
                result = _evidence(api, previous, image, netuid, "unchanged")
            else:
                try:
                    current = b"" if previous is None else _raw(previous.commitment)
                except ValueError as error:
                    raise SubmissionError("Cannot preserve an undecodable commitment; refusing to overwrite") from error
                merged = merge_envelope(current, envelope, max_bytes)
                outcome: Literal["published", "recovered"] = "published"
                try:
                    api.set_commitment(CommitmentDataBytes(merged))
                except PylonRequestException, PylonBadGateway:
                    outcome = "recovered"
                deadline = time.monotonic() + confirm_seconds
                while True:
                    try:
                        response = _read(api)
                    except PylonRequestException:
                        response = None
                    if response is not None and _matches(response, envelope):
                        result = _evidence(api, response, image, netuid, outcome)
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise SubmissionError("Publication remains unconfirmed; read before resubmitting")
                    time.sleep(min(2, remaining))
        except Exception:
            _events.labels("error").inc()
            raise
        _events.labels(result.outcome).inc()
        return result


@click.command(
    help="Publish IMAGE as this miner's FactoryHorde envelope and confirm it on chain. "
    "Safe to repeat: an unchanged envelope is not rewritten. Supply PYLON_IDENTITY_TOKEN in the environment."
)
@click.argument("image")
@click.option("--pylon-address", envvar="PYLON_ADDRESS", required=True)
@click.option("--identity", envvar="PYLON_IDENTITY", required=True)
@click.option("--netuid", type=click.IntRange(min=0), envvar="NETUID", required=True)
@click.option(
    "--commitment-max-bytes",
    type=click.IntRange(min=1, max=CHAIN_COMMITMENT_MAX_BYTES),
    envvar="COMMITMENT_MAX_BYTES",
    default=None,
    help="Cap on the whole commitment (default: the chain's 512 bytes). Set it on hotkeys shared with ComputeHorde.",
)
@click.option("--confirm-seconds", type=click.FloatRange(min=1, max=600), default=150, show_default=True)
def main(
    image: str,
    pylon_address: str,
    identity: str,
    netuid: int,
    commitment_max_bytes: int | None,
    confirm_seconds: float,
) -> None:
    """Publish IMAGE once and exit after exact chain-facing confirmation.

    Supply PYLON_IDENTITY_TOKEN through the environment, never as a command argument.

    Raises:
        click.ClickException: Configuration, publication or read-back fails.
    """
    started = time.monotonic()
    try:
        max_bytes = commitment_max_bytes or CHAIN_COMMITMENT_MAX_BYTES
        encode_envelope(image, max_bytes)
        token = os.environ.get("PYLON_IDENTITY_TOKEN")
        if not token:
            raise click.ClickException("Set PYLON_IDENTITY_TOKEN in the environment")
        config = Config(
            address=pylon_address,
            identity_name=IdentityName(identity),
            identity_token=PylonAuthToken(token),
            retry=Retrying(stop=stop_after_attempt(1), reraise=True),
            timeout=PylonTimeout(read=confirm_seconds),
        )
        with PylonClient(config) as client:
            result = submit(client.v1.identity, image, netuid, max_bytes=max_bytes, confirm_seconds=confirm_seconds)
    except (BasePylonException, SubmissionError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps({**asdict(result), "elapsed_seconds": time.monotonic() - started}))


if __name__ == "__main__":
    main()
