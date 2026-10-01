"""Publication idempotency and ambiguous-write recovery at the public Pylon boundary."""

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

from miner import SubmissionError, decode_reference, main, submit, validate_reference

FIXTURES = Path(__file__).resolve().parents[2] / "spec/fixtures"
IMAGE = "ghcr.io/example/factory@sha256:" + "a" * 64
BLOCK = Block(number=BlockNumber(100), hash=BlockHash("0x" + "a" * 64))
NEURON = Neuron.model_validate_json((FIXTURES / "discovery-v1/neuron.json").read_bytes())


class FakeApi:
    """Model an awaited write that can finish despite a lost HTTP response."""

    netuid: NetUid = NetUid(2)

    def __init__(self) -> None:
        self.current: GetCommitmentResponse | None = None
        self.writes: list[CommitmentDataBytes] = []
        self.lose_response = False
        self.store_write = True
        self.registered = True

    def get_latest_neurons(self) -> GetNeuronsResponse:
        return self.get_neurons(BLOCK.number)

    def get_neurons(self, block_number: BlockNumber) -> GetNeuronsResponse:
        assert block_number == BLOCK.number
        return GetNeuronsResponse(block=BLOCK, neurons={NEURON.hotkey: NEURON} if self.registered else {})

    def get_own_commitment(self) -> GetCommitmentResponse:
        if self.current is None:
            raise PylonNotFound()
        return self.current

    def set_commitment(self, commitment: CommitmentDataBytes) -> SetCommitmentResponse:
        self.writes.append(commitment)
        if self.store_write:
            self.current = GetCommitmentResponse(
                commitment_block_number=BlockNumber(99),
                hotkey=NEURON.hotkey,
                commitment=CommitmentDataHex(bytes(commitment).hex()),
                block=BLOCK,
            )
        if self.lose_response:
            raise PylonTimeoutException(TimeoutReason.READ)
        return SetCommitmentResponse()


def test_publish_readback_and_skip_unchanged() -> None:
    api = FakeApi()
    published = submit(api, IMAGE, 2)
    replayed = submit(api, IMAGE, 2)
    assert published.outcome == "published" and replayed.outcome == "unchanged"
    assert published.hotkey == NEURON.hotkey and published.commitment_block == 99
    assert api.writes == [IMAGE.encode()]
    assert validate_reference(IMAGE) == IMAGE


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


@pytest.mark.parametrize("value", ["", "0x", "0x0", "zz", "0x61 62", "0xff", IMAGE.encode().hex() + "0a"])
def test_bad_wire_values(value: str) -> None:
    with pytest.raises(ValueError):
        decode_reference(value)


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


def test_exact_byte_boundary() -> None:
    image = IMAGE.replace("factory", "f" * (128 - len(IMAGE) + len("factory")))
    assert len(image.encode()) == 128
    assert decode_reference("0x" + image.encode().hex()) == image
    with pytest.raises(ValueError, match="128"):
        validate_reference(image.replace("@", "f@"))


def test_cli_rejects_invalid_image_without_network() -> None:
    result = CliRunner().invoke(
        main, ["ghcr.io/a/b:latest", "--pylon-address", "http://invalid", "--identity", "miner1", "--netuid", "2"]
    )
    assert result.exit_code == 1
    assert "Expected ghcr.io" in result.output


def test_wrong_readback_identity_is_not_confirmation() -> None:
    api = FakeApi()
    api.set_commitment(CommitmentDataBytes(IMAGE.encode()))
    original = api.get_own_commitment()
    api.current = original.model_copy(update={"hotkey": Hotkey("5" + "c" * 47)})
    with pytest.raises(SubmissionError, match="registered"):
        submit(api, IMAGE, 2)
    assert len(api.writes) == 1
