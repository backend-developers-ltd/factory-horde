"""Publication idempotency and ambiguous-write recovery at the public Pylon boundary."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from pylon_client.artanis import (
    BlockHash,
    BlockNumber,
    CommitmentDataBytes,
    CommitmentDataHex,
    Hotkey,
    NetUid,
    PylonNotFound,
    PylonTimeoutException,
    TimeoutReason,
)
from pylon_client.artanis.v1 import Block, GetCommitmentResponse, GetNeuronsResponse, Neuron, SetCommitmentResponse

from miner import COMPUTE_HORDE_MANIFEST_RESERVE, SubmissionError, encode_envelope, main, merge_envelope, submit

FIXTURES = Path(__file__).resolve().parents[2] / "spec/fixtures"
ENVELOPES = json.loads((FIXTURES / "commitment-v2/envelopes.json").read_text())
NAMESPACE = "ghcr.io/example"
IMAGE = "ghcr.io/example/factory@sha256:" + "ab" * 32
ENVELOPE = encode_envelope(IMAGE, NAMESPACE)
BLOCK = Block(number=BlockNumber(100), hash=BlockHash("0x" + "a" * 64))
NEURON = Neuron.model_validate_json((FIXTURES / "discovery-v1/neuron.json").read_bytes())


class FakeApi:
    """Model an awaited write that can finish despite a lost HTTP response."""

    netuid: NetUid = NetUid(2)

    def __init__(self, initial: bytes = b"", initial_hex: str | None = None) -> None:
        self.current: GetCommitmentResponse | None = None
        self.writes: list[CommitmentDataBytes] = []
        self.reads = 0
        self.lose_response = False
        self.store_write = True
        self.registered = True
        # Bytes another writer leaves after our envelope while our write is in flight.
        self.after_write: bytes | None = None
        if initial_hex is not None or initial:
            self._store(initial_hex if initial_hex is not None else initial.hex())

    def _store(self, value: str) -> None:
        self.current = GetCommitmentResponse(
            commitment_block_number=BlockNumber(99),
            hotkey=NEURON.hotkey,
            commitment=CommitmentDataHex(value),
            block=BLOCK,
        )

    def get_latest_neurons(self) -> GetNeuronsResponse:
        return self.get_neurons(BLOCK.number)

    def get_neurons(self, block_number: BlockNumber) -> GetNeuronsResponse:
        assert block_number == BLOCK.number
        return GetNeuronsResponse(block=BLOCK, neurons={NEURON.hotkey: NEURON} if self.registered else {})

    def get_own_commitment(self) -> GetCommitmentResponse:
        self.reads += 1
        if self.current is None:
            raise PylonNotFound()
        return self.current

    def set_commitment(self, commitment: CommitmentDataBytes) -> SetCommitmentResponse:
        self.writes.append(commitment)
        if self.store_write:
            stored = bytes(commitment) if self.after_write is None else ENVELOPE + self.after_write
            self._store(stored.hex())
        if self.lose_response:
            raise PylonTimeoutException(TimeoutReason.READ)
        return SetCommitmentResponse()


def test_publish_readback_and_skip_unchanged() -> None:
    api = FakeApi()
    published = submit(api, IMAGE, 2, NAMESPACE)
    replayed = submit(api, IMAGE, 2, NAMESPACE)
    assert published.outcome == "published" and replayed.outcome == "unchanged"
    assert published.hotkey == NEURON.hotkey and published.commitment_block == 99
    assert api.writes == [ENVELOPE]


def test_ambiguous_write_reads_without_resubmitting() -> None:
    api = FakeApi()
    api.lose_response = True
    assert submit(api, IMAGE, 2, NAMESPACE).outcome == "recovered"
    assert len(api.writes) == 1


def test_unconfirmed_is_not_success_and_never_rewrites() -> None:
    api = FakeApi()
    api.lose_response, api.store_write = True, False
    with pytest.raises(SubmissionError, match="unconfirmed"):
        submit(api, IMAGE, 2, NAMESPACE, confirm_seconds=0.001)
    assert len(api.writes) == 1


def test_subnet_mismatch_fails_before_write() -> None:
    api = FakeApi()
    with pytest.raises(SubmissionError, match="different subnet"):
        submit(api, IMAGE, 12, NAMESPACE)
    assert api.writes == []


def test_confirmed_value_requires_registered_hotkey() -> None:
    api = FakeApi()
    api.registered = False
    with pytest.raises(SubmissionError, match="registered"):
        submit(api, IMAGE, 2, NAMESPACE)


@pytest.mark.parametrize(
    "value",
    [
        IMAGE.replace("ghcr.io", "docker.io"),
        IMAGE.replace("ghcr.io", "example.com"),
        "ghcr.io/example/factory:latest",
        IMAGE + "\n",
        IMAGE + "x",
        IMAGE.upper(),
        IMAGE.replace("example", "examplé"),
    ],
)
def test_bad_references_fail_before_writes(value: str) -> None:
    api = FakeApi()
    with pytest.raises(ValueError):
        submit(api, value, 2, NAMESPACE)
    assert not api.writes


def test_cli_rejects_invalid_image_without_network() -> None:
    result = CliRunner().invoke(
        main,
        [
            "ghcr.io/a/b:latest",
            "--pylon-address",
            "http://invalid",
            "--identity",
            "miner1",
            "--netuid",
            "2",
            "--namespace",
            NAMESPACE,
        ],
    )
    assert result.exit_code == 1
    assert "Expected ghcr.io" in result.output


def test_wrong_readback_identity_is_not_confirmation() -> None:
    api = FakeApi()
    api.set_commitment(CommitmentDataBytes(ENVELOPE))
    original = api.get_own_commitment()
    api.current = original.model_copy(update={"hotkey": Hotkey("5" + "c" * 47)})
    with pytest.raises(SubmissionError, match="registered"):
        submit(api, IMAGE, 2, NAMESPACE)
    assert len(api.writes) == 1


def test_envelope_is_compact() -> None:
    assert ENVELOPE == b"<F:factory@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s>"


@pytest.mark.parametrize("case", ENVELOPES["valid"])
def test_fixture_commitments_hold_the_encoded_envelope(case: dict[str, str]) -> None:
    assert encode_envelope(case["image"], ENVELOPES["namespace"]) in case["commitment"].encode()


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        (b"", ENVELOPE),
        (b"<M:A3;S2>", ENVELOPE + b"<M:A3;S2>"),
        (b"<D:x><M:A3>", b"<D:x>" + ENVELOPE + b"<M:A3>"),
        (b"<D:x><F:old@" + b"A" * 43 + b"><M:A3>", b"<D:x>" + ENVELOPE + b"<M:A3>"),
        (b"<M:A3><F:old@" + b"A" * 43 + b">", b"<M:A3>" + ENVELOPE),
        (b"junk\xff", b"junk\xff" + ENVELOPE),
    ],
)
def test_merge_changes_only_own_envelope(current: bytes, expected: bytes) -> None:
    assert merge_envelope(current, ENVELOPE) == expected


def test_merge_keeps_compute_horde_reserve() -> None:
    room = 128 - COMPUTE_HORDE_MANIFEST_RESERVE - len(ENVELOPE)
    assert merge_envelope(b"<D:" + b"x" * (room - 4) + b">", ENVELOPE)
    with pytest.raises(SubmissionError, match="ComputeHorde"):
        merge_envelope(b"<D:" + b"x" * (room - 3) + b">", ENVELOPE)


def test_merge_never_exceeds_the_writer_limit() -> None:
    # An existing manifest larger than the reserve: only the total length check can catch it.
    fits = b"<M:" + b"A" * (128 - len(ENVELOPE) - 4) + b">"
    assert len(merge_envelope(fits, ENVELOPE)) == 128
    with pytest.raises(SubmissionError, match="128"):
        merge_envelope(b"<M:" + b"A" * (128 - len(ENVELOPE) - 3) + b">", ENVELOPE)


def test_overlong_package_fails_before_any_request() -> None:
    api = FakeApi()
    longest = "p" * (128 - COMPUTE_HORDE_MANIFEST_RESERVE - 48)
    assert encode_envelope(f"{NAMESPACE}/{longest}@sha256:{'ab' * 32}", NAMESPACE)
    with pytest.raises(ValueError, match="128"):
        submit(api, f"{NAMESPACE}/{longest}p@sha256:{'ab' * 32}", 2, NAMESPACE)
    assert api.reads == 0 and api.writes == []


def test_preserves_foreign_content_on_chain() -> None:
    api = FakeApi(initial=b"<D:x><M:A3;S2>")
    result = submit(api, IMAGE, 2, NAMESPACE)
    assert result.outcome == "published"
    assert bytes(api.writes[-1]) == b"<D:x>" + ENVELOPE + b"<M:A3;S2>"


def test_unchanged_when_own_envelope_matches() -> None:
    api = FakeApi(initial=b"<D:x>" + ENVELOPE + b"<M:A3>")
    assert submit(api, IMAGE, 2, NAMESPACE).outcome == "unchanged"
    assert api.writes == []


def test_outside_namespace_fails_before_reading() -> None:
    api = FakeApi()
    with pytest.raises(ValueError, match="namespace"):
        submit(api, "ghcr.io/other/factory@sha256:" + "ab" * 32, 2, NAMESPACE)
    assert api.reads == 0 and api.writes == []


def test_undecodable_commitment_is_never_overwritten() -> None:
    api = FakeApi(initial_hex="0xzz")
    with pytest.raises(SubmissionError, match="preserve"):
        submit(api, IMAGE, 2, NAMESPACE)
    assert api.writes == []


def test_readback_ignores_concurrent_manifest_change() -> None:
    api = FakeApi(initial=b"<M:A3>")
    api.after_write = b"<M:A4>"
    assert submit(api, IMAGE, 2, NAMESPACE).outcome == "published"
