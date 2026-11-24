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

from miner import (
    CHAIN_COMMITMENT_MAX_BYTES,
    COMPUTE_HORDE_MANIFEST_RESERVE,
    SubmissionError,
    encode_envelope,
    main,
    merge_envelope,
    submit,
)

FIXTURES = Path(__file__).resolve().parents[2] / "spec/fixtures"
ENVELOPES = json.loads((FIXTURES / "commitment-v2/envelopes.json").read_text())
IMAGE = "ghcr.io/example/factory@sha256:" + "ab" * 32
ENVELOPE = encode_envelope(IMAGE)
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
    published = submit(api, IMAGE, 2)
    replayed = submit(api, IMAGE, 2)
    assert published.outcome == "published" and replayed.outcome == "unchanged"
    assert published.hotkey == NEURON.hotkey and published.commitment_block == 99
    assert api.writes == [ENVELOPE]


def test_ambiguous_write_reads_without_resubmitting() -> None:
    api = FakeApi()
    api.lose_response = True
    assert submit(api, IMAGE, 2).outcome == "recovered"
    assert len(api.writes) == 1


def test_unconfirmed_is_not_success_and_never_rewrites() -> None:
    api = FakeApi()
    api.lose_response, api.store_write = True, False
    with pytest.raises(SubmissionError, match="unconfirmed"):
        submit(api, IMAGE, 2, confirm_seconds=0.001)
    assert len(api.writes) == 1


def test_subnet_mismatch_fails_before_write() -> None:
    api = FakeApi()
    with pytest.raises(SubmissionError, match="different subnet"):
        submit(api, IMAGE, 12)
    assert api.writes == []


def test_confirmed_value_requires_registered_hotkey() -> None:
    api = FakeApi()
    api.registered = False
    with pytest.raises(SubmissionError, match="registered"):
        submit(api, IMAGE, 2)


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
        submit(api, value, 2)
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
        submit(api, IMAGE, 2)
    assert len(api.writes) == 1


def test_envelope_holds_the_full_reference() -> None:
    assert ENVELOPE == b"<F:ghcr.io/example/factory@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s>"


@pytest.mark.parametrize("case", ENVELOPES["valid"])
def test_fixture_commitments_hold_the_encoded_envelope(case: dict[str, str]) -> None:
    assert encode_envelope(case["image"]) in case["commitment"].encode()


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


@pytest.mark.parametrize("limit", [CHAIN_COMMITMENT_MAX_BYTES, 200])
def test_merge_keeps_compute_horde_reserve(limit: int) -> None:
    room = limit - COMPUTE_HORDE_MANIFEST_RESERVE - len(ENVELOPE)
    assert merge_envelope(b"<D:" + b"x" * (room - 4) + b">", ENVELOPE, limit)
    with pytest.raises(SubmissionError, match="ComputeHorde"):
        merge_envelope(b"<D:" + b"x" * (room - 3) + b">", ENVELOPE, limit)


@pytest.mark.parametrize("limit", [CHAIN_COMMITMENT_MAX_BYTES, 200])
def test_merge_never_exceeds_the_limit(limit: int) -> None:
    # An existing manifest larger than the reserve: only the total length check can catch it.
    fits = b"<M:" + b"A" * (limit - len(ENVELOPE) - 4) + b">"
    assert len(merge_envelope(fits, ENVELOPE, limit)) == limit
    with pytest.raises(SubmissionError, match=str(limit)):
        merge_envelope(b"<M:" + b"A" * (limit - len(ENVELOPE) - 3) + b">", ENVELOPE, limit)


def test_default_limit_is_the_chain_bound() -> None:
    assert CHAIN_COMMITMENT_MAX_BYTES == 512
    fits = b"<M:" + b"A" * (512 - len(ENVELOPE) - 4) + b">"
    assert len(merge_envelope(fits, ENVELOPE)) == 512


@pytest.mark.parametrize("limit", [CHAIN_COMMITMENT_MAX_BYTES, 200])
def test_overlong_reference_fails_before_any_request(limit: int) -> None:
    api = FakeApi()
    # Envelope = 3 + len("ghcr.io/example/") + package + 1 + 43 + 1.
    longest = "p" * (limit - COMPUTE_HORDE_MANIFEST_RESERVE - 64)
    assert len(encode_envelope(f"ghcr.io/example/{longest}@sha256:{'ab' * 32}", limit)) + 33 == limit
    with pytest.raises(ValueError, match=str(limit)):
        submit(api, f"ghcr.io/example/{longest}p@sha256:{'ab' * 32}", 2, max_bytes=limit)
    assert api.reads == 0 and api.writes == []


CAP_IMAGE = f"ghcr.io/example/{'p' * 100}@sha256:{'ab' * 32}"  # 164-byte envelope
CLI_BASE = ["--pylon-address", "http://127.0.0.1:1", "--identity", "miner", "--netuid", "2"]


@pytest.mark.parametrize(
    ("env", "expected"),
    [({}, "512"), ({"COMMITMENT_MAX_BYTES": ""}, "512"), ({"COMMITMENT_MAX_BYTES": "150"}, "150")],
)
def test_cli_cap_comes_from_the_environment(env: dict[str, str], expected: str) -> None:
    result = CliRunner().invoke(main, [CAP_IMAGE, *CLI_BASE], env={"PYLON_IDENTITY_TOKEN": "", **env})
    assert result.exit_code != 0
    if expected == "512":
        assert "PYLON_IDENTITY_TOKEN" in result.output  # passed the size check, stopped at the token
    else:
        assert "150" in result.output  # refused before any network request


def test_cli_cap_flag() -> None:
    result = CliRunner().invoke(
        main, [CAP_IMAGE, *CLI_BASE, "--commitment-max-bytes", "150"], env={"PYLON_IDENTITY_TOKEN": ""}
    )
    assert result.exit_code != 0
    assert "150" in result.output


def test_preserves_foreign_content_on_chain() -> None:
    api = FakeApi(initial=b"<D:x><M:A3;S2>")
    result = submit(api, IMAGE, 2)
    assert result.outcome == "published"
    assert bytes(api.writes[-1]) == b"<D:x>" + ENVELOPE + b"<M:A3;S2>"


def test_unchanged_when_own_envelope_matches() -> None:
    api = FakeApi(initial=b"<D:x>" + ENVELOPE + b"<M:A3>")
    assert submit(api, IMAGE, 2).outcome == "unchanged"
    assert api.writes == []


def test_undecodable_commitment_is_never_overwritten() -> None:
    api = FakeApi(initial_hex="0xzz")
    with pytest.raises(SubmissionError, match="preserve"):
        submit(api, IMAGE, 2)
    assert api.writes == []


def test_readback_ignores_concurrent_manifest_change() -> None:
    api = FakeApi(initial=b"<M:A3>")
    api.after_write = b"<M:A4>"
    assert submit(api, IMAGE, 2).outcome == "published"
