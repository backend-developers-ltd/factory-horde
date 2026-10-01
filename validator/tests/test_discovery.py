"""Frozen discovery preserves block/role attribution and survives commitment updates."""

from pathlib import Path
from uuid import uuid4

import pytest
from pylon_client.artanis import BlockHash, BlockNumber, CommitmentDataHex, Hotkey, NetUid
from pylon_client.artanis.v1 import Block, GetCommitmentsResponse, GetNeuronsResponse, Neuron

from validator.discovery import decode_reference, discover, freeze_discovery
from validator.record_files import RecordConflictError, RecordFiles

FIXTURES = Path(__file__).resolve().parents[2] / "spec/fixtures/discovery-v1"
NEURON = Neuron.model_validate_json((FIXTURES / "neuron.json").read_bytes())
VALIDATOR = Hotkey("5" + "c" * 47)
IMAGE = "ghcr.io/example/factory@sha256:" + "a" * 64
BLOCK = Block(number=BlockNumber(100), hash=BlockHash("0x" + "a" * 64))


class FakeApi:
    netuid: NetUid = NetUid(2)

    def __init__(self) -> None:
        self.calls: list[str | int] = []
        self.commitments = {NEURON.hotkey: CommitmentDataHex(IMAGE.encode().hex())}
        self.neurons = {NEURON.hotkey: NEURON}
        self.membership_block = BLOCK

    def get_commitments(self) -> GetCommitmentsResponse:
        self.calls.append("commitments")
        return GetCommitmentsResponse(block=BLOCK, commitments=self.commitments)

    def get_neurons(self, block_number: BlockNumber) -> GetNeuronsResponse:
        self.calls.append(block_number)
        return GetNeuronsResponse(block=self.membership_block, neurons=self.neurons)


def test_registered_miner_with_validator_permit_is_eligible() -> None:
    api = FakeApi()
    snapshot = discover(api, 2, VALIDATOR)
    assert NEURON.validator_permit
    assert api.calls == ["commitments", BLOCK.number]
    assert len(snapshot.cohort) == 1 and snapshot.cohort[0].miner_hotkey == NEURON.hotkey
    assert snapshot.cohort[0].image == IMAGE
    assert snapshot.block == BLOCK.number and snapshot.block_hash == BLOCK.hash


def test_exclude_self_unregistered_bad_and_unselected_submissions() -> None:
    api = FakeApi()
    unknown, invalid = Hotkey("5" + "d" * 47), Hotkey("5" + "e" * 47)
    api.commitments.update(
        {
            VALIDATOR: CommitmentDataHex(IMAGE.encode().hex()),
            unknown: CommitmentDataHex("ff"),
            invalid: CommitmentDataHex("zz"),
        }
    )
    api.neurons[invalid] = NEURON.model_copy(update={"hotkey": invalid, "uid": 3})
    snapshot = discover(api, 2, VALIDATOR)
    assert len(snapshot.cohort) == 1
    assert {item.miner_hotkey for item in snapshot.rejected} == {VALIDATOR, unknown, invalid}
    assert discover(api, 2, VALIDATOR, ()).cohort == ()


def test_existing_snapshot_survives_update_and_fresh_repository(tmp_path: Path) -> None:
    api = FakeApi()
    identity = uuid4()
    before = freeze_discovery(RecordFiles(tmp_path), identity, api, 2, VALIDATOR)
    api.commitments[NEURON.hotkey] = CommitmentDataHex(IMAGE.replace("a" * 64, "b" * 64).encode().hex())
    api.calls.clear()
    recovered = freeze_discovery(RecordFiles(tmp_path), identity, api, 2, VALIDATOR)
    assert recovered == before and api.calls == []
    after = freeze_discovery(RecordFiles(tmp_path), uuid4(), api, 2, VALIDATOR)
    assert after.cohort[0].image != before.cohort[0].image
    assert after.cohort[0].factory_job_id != before.cohort[0].factory_job_id
    with pytest.raises(RecordConflictError):
        freeze_discovery(RecordFiles(tmp_path), identity, api, 12, VALIDATOR)


def test_mismatched_block_fails_closed() -> None:
    api = FakeApi()
    api.membership_block = BLOCK.model_copy(update={"hash": BlockHash("0x" + "b" * 64)})
    with pytest.raises(ValueError, match="same chain block"):
        discover(api, 2, VALIDATOR)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "0x",
        "0x0",
        "zz",
        "0x61 62",
        "0xff",
        IMAGE.encode().hex() + "0a",
        (IMAGE + "x" * 100).encode().hex(),
        IMAGE.replace("ghcr.io", "docker.io").encode().hex(),
    ],
)
def test_invalid_wire_data(value: str) -> None:
    with pytest.raises(ValueError):
        decode_reference(value)


def test_duplicate_uid_or_wrong_hotkey_rejected() -> None:
    api = FakeApi()
    another = Hotkey("5" + "d" * 47)
    api.neurons[another] = NEURON
    with pytest.raises(ValueError, match="attribution"):
        discover(api, 2, VALIDATOR)
    api.neurons[another] = NEURON.model_copy(update={"hotkey": another})
    with pytest.raises(ValueError, match="duplicate UIDs"):
        discover(api, 2, VALIDATOR)
