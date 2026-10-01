"""Accepted-score lifetime, stable weighting, current registration and mandatory skip gate."""

import math
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from nexus.v1 import (
    BlockNumber,
    Epoch,
    Hotkey,
    ReceiveEvent,
    SetWeightsBeat,
    SetWeightsBeatNode,
    SubnetBuilder,
    WeightsCalculationBundle,
    WeightSetterNode,
)
from pylon_client.artanis.v1 import GetNeuronsResponse

from validator.main import Settings, Validator
from validator.records import RoundPlan, RoundRecord
from validator.result_store import FileTaskResultStore
from validator.weighing import Membership, NoUsableScores, RegistrationChanged, Weigher, WeightCalculation, softmax
from validator.weight_gate import WeightGate

from .test_results import BEAT, Pair
from .test_results import pair as pair

A, B, C = (Hotkey("5" + value * 47) for value in "abc")
EPOCH = Epoch(BlockNumber(999_000), BlockNumber(999_359))


@pytest.mark.parametrize(
    "scores,expected",
    [
        ({A: 0.5, B: 0.5}, {A: 0.5, B: 0.5}),
        ({A: 0.0}, {A: 1.0}),
        ({A: 0.0, B: 0.0, C: 0.0}, {A: 1 / 3, B: 1 / 3, C: 1 / 3}),
    ],
)
def test_equal_single_and_successful_zero_scores(scores: dict[Hotkey, float], expected: dict[Hotkey, float]) -> None:
    result = softmax(scores)
    assert result.keys() == expected.keys()
    assert all(math.isclose(result[key], value, rel_tol=1e-12) for key, value in expected.items())


def test_stable_softmax_exact_formula_and_extreme_temperature() -> None:
    result = softmax({A: 0.1, B: 0.8, C: 1.0})
    terms = {A: math.exp(-9), B: math.exp(-2), C: 1.0}
    assert all(math.isclose(result[key], value / sum(terms.values()), rel_tol=1e-12) for key, value in terms.items())
    assert math.isclose(math.fsum(result.values()), 1, rel_tol=1e-12)
    assert softmax({A: 0, B: 1}, 5e-324) == {A: 0, B: 1}


@pytest.mark.parametrize("temperature", [0, -1, math.inf, -math.inf, math.nan])
def test_invalid_temperature(temperature: float) -> None:
    with pytest.raises(ValueError, match="Temperature"):
        softmax({A: 0}, temperature)


@pytest.mark.parametrize("scores", [{}, {A: math.nan}, {A: math.inf}, {A: -0.1}, {A: 1.1}])
def test_empty_or_invalid_scores(scores: dict[Hotkey, float]) -> None:
    with pytest.raises(ValueError):
        softmax(scores)


@dataclass
class MembershipReader:
    values: list[Membership] = field(default_factory=list)
    calls: int = 0

    def read(self) -> Membership:
        selected = self.values[min(self.calls, len(self.values) - 1)]
        self.calls += 1
        return selected


def accepted(pair: Pair) -> tuple[Weigher, MembershipReader]:
    pair.repo.finalize(pair.judge, BEAT)
    original = pair.repo.rounds.rounds()[0]
    pair.repo.rounds.save_round(
        RoundRecord(plan=original.plan, stage="complete", completed_at=original.plan.deadlines.round_end)
    )
    membership = MembershipReader([Membership(1000, {Hotkey(pair.judge.miner_hotkey): 2})])
    return Weigher(FileTaskResultStore(pair.repo), membership.read), membership


def bundle(weigher: Weigher) -> WeightsCalculationBundle:
    return WeightsCalculationBundle(EPOCH, weigher.store)


def test_scores_outlive_epochs_and_repeated_opportunities_do_not_redraw(pair: Pair) -> None:
    weigher, membership = accepted(pair)
    before = pair.repo.files.read_bytes(pair.repo.result_path(pair.judge))
    assert weigher.calculate(bundle(weigher)) == {pair.judge.miner_hotkey: 1.0}
    assert weigher.calculate(bundle(weigher)) == {pair.judge.miner_hotkey: 1.0}
    assert membership.calls == 4
    assert pair.repo.files.read_bytes(pair.repo.result_path(pair.judge)) == before
    calculation = pair.repo.files.read("control/weight-calculation.json", WeightCalculation)
    assert calculation.epoch_start == EPOCH.first_block and calculation.round_id == pair.judge.round_id


def test_new_empty_round_preserves_previous_valid_history(pair: Pair) -> None:
    weigher, _ = accepted(pair)
    old = pair.repo.rounds.rounds()[0].plan
    plan = RoundPlan.model_validate(
        old.model_dump()
        | {
            "round_id": uuid4(),
            "sequence": old.sequence + 1,
            "cohort": (),
            "deadlines": old.deadlines.model_copy(
                update={
                    name: value + timedelta(days=1)
                    for name, value in old.deadlines.model_dump().items()
                    if name != "protocol_version"
                }
            ),
        }
    )
    specification = pair.repo.files.read_bytes(f"{old.directory}/specification.md").decode()
    pair.repo.rounds.prepare_round(plan, specification)
    pair.repo.rounds.save_round(RoundRecord(plan=plan, stage="complete", completed_at=plan.deadlines.round_end))
    assert weigher.calculate(bundle(weigher)) == {pair.judge.miner_hotkey: 1.0}


def test_failures_and_absent_history_have_no_weight_mapping(pair: Pair) -> None:
    failed = pair.judge_status.model_copy(update={"state": "failed", "exit_code": 7, "reason": "fixture"})
    pair.repo.files.replace(f"control/statuses/{pair.judge.job_id}.json", failed)
    weigher, _ = accepted(pair)
    with pytest.raises(NoUsableScores):
        weigher.calculate(bundle(weigher))
    assert not (pair.repo.files.root / "control/weight-calculation.json").exists()


def test_vanished_hotkey_and_reused_uid_do_not_transfer_score(pair: Pair) -> None:
    weigher, membership = accepted(pair)
    membership.values = [Membership(1001, {A: 2})]
    with pytest.raises(NoUsableScores):
        weigher.calculate(bundle(weigher))


def test_registration_changes_during_calculation_cancel_submission(pair: Pair) -> None:
    weigher, membership = accepted(pair)
    membership.values.append(Membership(1001, {A: 2}))
    with pytest.raises(RegistrationChanged):
        weigher.calculate(bundle(weigher))
    assert not (pair.repo.files.root / "control/weight-calculation.json").exists()


def test_wrong_store_cannot_bypass_shared_repository(pair: Pair) -> None:
    weigher, _ = accepted(pair)
    with pytest.raises(ValueError, match="share"):
        weigher.calculate(WeightsCalculationBundle(EPOCH, FileTaskResultStore(pair.repo)))


def test_gate_suppresses_empty_then_passes_registered_scores_on_primary_context(pair: Pair) -> None:
    reader = MembershipReader([Membership(1000, {Hotkey(pair.judge.miner_hotkey): 2})])
    node = WeightGate(Weigher(FileTaskResultStore(pair.repo), reader.read))
    builder = SubnetBuilder(nodes=[node])
    actor = node.build_actor(pipe_to_bus=builder.pipe_to_bus, context_store=builder.context_store)
    with builder.context_store.create_context() as ctx:
        beat = SetWeightsBeat(EPOCH, EPOCH.first_block)
        event = ReceiveEvent(ctx_id=ctx.id, target=node.sink, payload=beat)
        assert actor.handle(ctx, event) == ()
        assert reader.calls == 0
        accepted(pair)
        passed = actor.handle(ctx, event)
        assert (
            not isinstance(passed, tuple)
            and passed.source is node.ok
            and passed.ctx_id == ctx.id
            and passed.payload is beat
        )


def test_gate_reports_membership_errors_instead_of_emitting_empty_weights(pair: Pair) -> None:
    weigher, _ = accepted(pair)

    def unavailable() -> Membership:
        raise OSError("membership unavailable")

    weigher.membership = unavailable
    node = WeightGate(weigher)
    builder = SubnetBuilder(nodes=[node])
    actor = node.build_actor(pipe_to_bus=builder.pipe_to_bus, context_store=builder.context_store)
    with builder.context_store.create_context() as ctx:
        result = actor.handle(
            ctx, ReceiveEvent(ctx_id=ctx.id, target=node.sink, payload=SetWeightsBeat(EPOCH, EPOCH.first_block))
        )
        assert not isinstance(result, tuple) and result.source is node.error


def test_public_membership_attribution_is_checked(pair: Pair) -> None:
    neuron = pair.target.model_copy(update={"hotkey": A})
    response = GetNeuronsResponse.model_validate(
        {"block": {"number": 10, "hash": "0x" + "c" * 64}, "neurons": {A: neuron}}
    )
    assert Membership.from_response(response).uids == {A: neuron.uid}
    with pytest.raises(ValueError, match="attribution"):
        Membership.from_response(response.model_copy(update={"neurons": {B: neuron}}))
    with pytest.raises(ValueError, match="duplicate"):
        Membership.from_response(
            response.model_copy(update={"neurons": {A: neuron, B: neuron.model_copy(update={"hotkey": B})}})
        )


def test_graph_wires_same_mechanism_and_store(tmp_path: Path) -> None:
    settings = Settings.model_validate(
        {
            "NETUID": 2,
            "MECHANISM_ID": 1,
            "data_root": tmp_path,
            "pylon_service_address": "http://pylon.test",
            "pylon_open_access_token": "test",
            "pylon_identity_name": "validator",
            "pylon_identity_token": "test",
            "weights_enabled": True,
        }
    )
    validator = Validator(settings)
    nodes = {sink.owner_node for sink in validator.subnet_flow.sinks}
    opportunity = next(node for node in nodes if isinstance(node, SetWeightsBeatNode))
    setter = next(node for node in nodes if isinstance(node, WeightSetterNode))
    gate = next(node for node in nodes if isinstance(node, WeightGate))
    assert opportunity.mechanism_id == setter.mechanism_id == 1
    assert setter.task_result_store_provider is validator.tasks.store_provider
    assert gate.weigher.store is validator.tasks.store_provider.get_task_result_store()
