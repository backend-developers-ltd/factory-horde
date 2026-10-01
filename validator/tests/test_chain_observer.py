"""The task-4 application shape observes the chain without publishing executable work."""

from pathlib import Path

import pytest
from nexus.v1 import BlockBeat, BlockHash, BlockNumber, ReceiveEvent, SubnetBuilder, Timestamp
from pydantic import ValidationError

from validator.chain_observer import ChainObservation, ChainObserverNode
from validator.main import Settings


def test_chain_observation_is_persisted_without_dispatch(tmp_path: Path) -> None:
    node = ChainObserverNode(tmp_path, mechanism_id=1)
    builder = SubnetBuilder(nodes=[node])
    actor = node.build_actor(pipe_to_bus=builder.pipe_to_bus, context_store=builder.context_store)
    with builder.context_store.create_context() as ctx:
        actor.handlers()[node.sink](
            ctx,
            ReceiveEvent(
                ctx_id=ctx.id,
                target=node.sink,
                payload=BlockBeat(BlockNumber(100), Timestamp(1000), BlockHash("0x" + "a" * 64)),
            ),
        )
    observation = node.files.read("control/chain-observation.json", ChainObservation)
    assert observation.block == 100
    assert observation.mechanism_id == 1
    assert not observation.dispatch_enabled
    assert not (tmp_path / "control/requests").exists()


def test_dispatch_requires_explicit_identity_and_judge() -> None:
    with pytest.raises(ValidationError, match="Dispatch requires"):
        Settings.model_validate(
            {
                "NETUID": 2,
                "pylon_service_address": "http://pylon.test",
                "pylon_open_access_token": "test",
                "dispatch_enabled": True,
            }
        )


def test_compose_seconds_configuration_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {
        "VALIDATOR_GENERATION_WINDOW": "100",
        "VALIDATOR_CONFIRMATION_WINDOW": "15",
        "VALIDATOR_EVALUATION_WINDOW": "65",
        "VALIDATOR_JUDGE_STOP_RESERVE": "10",
        "VALIDATOR_STOP_GRACE_SECONDS": "5",
    }.items():
        monkeypatch.setenv(key, value)
    settings = Settings.model_validate(
        {"NETUID": 2, "pylon_service_address": "http://pylon.test", "pylon_open_access_token": "test"}
    )
    assert settings.round_timing().interval.total_seconds() == 180
