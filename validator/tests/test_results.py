"""Report acceptance, persistent replay and the selected public Nexus store contract."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from nexus.v1 import (
    BlockBeat,
    BlockHash,
    BlockNumber,
    Epoch,
    ExecutorFailureException,
    ExecutorFailureTaskResultToPersist,
    Hotkey,
    Neuron,
    NexusException,
    ProcessedInput,
    Routed,
    SubnetBuilder,
    SuccessfulTaskResultToPersist,
    TaskResultId,
    TaskResultNotFoundException,
    Timestamp,
    Timestamped,
)

from validator.record_files import RecordConflictError, decode_record
from validator.records import JobRequest, JobStatus, RoundRecord, request_for
from validator.result_repository import ResultNotReady, ResultRejected, ResultRepository
from validator.result_store import EVALUATION_TASK, FACTORY_TASK, FileResultStoreProvider, FileTaskResultStore
from validator.round_repository import RoundRepository

FIXTURES = Path(__file__).resolve().parents[2] / "spec/fixtures/protocol-v1"
BEAT = BlockBeat(BlockNumber(500), Timestamp(1_790_855_700_000), BlockHash("0x" + "e" * 64))
LATER = BlockBeat(BlockNumber(1000), Timestamp(1_790_861_700_000), BlockHash("0x" + "f" * 64))


@dataclass
class Pair:
    repo: ResultRepository
    factory: JobRequest
    judge: JobRequest
    factory_status: JobStatus
    judge_status: JobStatus
    target: Neuron

    def successful(
        self, request: JobRequest, *, later: bool = False
    ) -> SuccessfulTaskResultToPersist[JobRequest, JobStatus, JobStatus]:
        observed = self.factory_status if request.kind == "factory" else self.judge_status
        return SuccessfulTaskResultToPersist(
            result=Timestamped(
                executor_output=ProcessedInput(input=Routed(request, self.target), output=observed),
                processing_started=observed.observed_at + timedelta(days=int(later)),
                processing_finished=observed.observed_at + timedelta(days=int(later)),
                block_at_finish=LATER if later else BEAT,
            ),
            executor_public_output=observed,
        )

    def failure(self, request: JobRequest) -> ExecutorFailureTaskResultToPersist[JobRequest]:
        return ExecutorFailureTaskResultToPersist(
            result=Timestamped(
                executor_output=ProcessedInput(
                    input=Routed(request, self.target),
                    output=ExecutorFailureException(NexusException("framework observation")),
                ),
                processing_started=self.judge_status.observed_at,
                processing_finished=self.judge_status.observed_at,
                block_at_finish=BEAT,
            )
        )


@pytest.fixture
def pair(tmp_path: Path) -> Pair:
    record = decode_record((FIXTURES / "round.json").read_bytes(), RoundRecord)
    rounds = RoundRepository(tmp_path)
    rounds.prepare_round(record.plan, (FIXTURES / "specification.md").read_text())
    factory, judge = (request_for(record.plan, record.plan.cohort[0], kind) for kind in ("factory", "judge"))
    for request in (factory, judge):
        rounds.publish_request(record.plan, request)
    judge_status = decode_record((FIXTURES / "status.json").read_bytes(), JobStatus)
    factory_status = JobStatus.model_validate(
        judge_status.model_dump()
        | {
            "job_id": factory.job_id,
            "kind": "factory",
            "container_name": factory.container_name,
            "container_id": "c" * 64,
            "started_at": record.plan.deadlines.start,
            "finished_at": record.plan.deadlines.generation_end,
            "observed_at": record.plan.deadlines.generation_end,
        }
    )
    rounds.files.replace(f"control/statuses/{factory.job_id}.json", factory_status)
    rounds.files.replace(f"control/statuses/{judge.job_id}.json", judge_status)
    rounds.files.write_bytes(f"{judge.report_dir}/report.json", (FIXTURES / "report.json").read_bytes(), immutable=True)
    target = Neuron.model_validate_json((FIXTURES.parent / "discovery-v1/neuron.json").read_bytes())
    return Pair(ResultRepository(rounds), factory, judge, factory_status, judge_status, target)


def test_zero_score_and_replay_keep_original_metadata(pair: Pair) -> None:
    result = pair.repo.finalize(pair.judge, BEAT)
    assert result.accepted is not None and result.accepted.score == 0
    original = pair.repo.files.read_bytes(pair.repo.result_path(pair.judge))
    (pair.repo.files.root / f"{pair.judge.report_dir}/report.json").unlink()
    resumed = ResultRepository(RoundRepository(pair.repo.files.root))
    assert resumed.finalize(pair.judge, LATER) == result
    assert resumed.files.read_bytes(resumed.result_path(pair.judge)) == original
    assert result.processing_started == pair.judge_status.started_at
    assert result.processing_finished == pair.judge_status.finished_at
    assert result.completion.number == 500 and result.result_id == pair.judge.projection_id


@pytest.mark.parametrize("field", ["job_id", "round_id", "miner_hotkey", "factory_job_id"])
def test_report_attribution_rejected(pair: Pair, field: str) -> None:
    report = json.loads((FIXTURES / "report.json").read_text())
    report[field] = "5" + "a" * 47 if field == "miner_hotkey" else str(uuid4())
    pair.repo.files.write_bytes(f"{pair.judge.report_dir}/report.json", json.dumps(report).encode(), immutable=False)
    result = pair.repo.finalize(pair.judge, BEAT)
    assert result.failure and result.accepted is None


@pytest.mark.parametrize("score", ["NaN", "Infinity", "-Infinity", "-0.1", "1.1", '"0.5"', "true"])
def test_invalid_report_score_never_accepted(pair: Pair, score: str) -> None:
    raw = (FIXTURES / "report.json").read_bytes().replace(b'"score": 0.0', f'"score": {score}'.encode())
    pair.repo.files.write_bytes(f"{pair.judge.report_dir}/report.json", raw, immutable=False)
    result = pair.repo.finalize(pair.judge, BEAT)
    assert result.failure and result.accepted is None


@pytest.mark.parametrize("raw", [b'{"score":', b"{}", b"[]", b'{"score":0,"score":1}'])
def test_malformed_report_is_permanent_failure(pair: Pair, raw: bytes) -> None:
    pair.repo.files.write_bytes(f"{pair.judge.report_dir}/report.json", raw, immutable=False)
    result = pair.repo.finalize(pair.judge, BEAT)
    assert result.failure and result.accepted is None
    pair.repo.files.write_bytes(
        f"{pair.judge.report_dir}/report.json", (FIXTURES / "report.json").read_bytes(), immutable=False
    )
    assert ResultRepository(RoundRepository(pair.repo.files.root)).finalize(pair.judge, LATER) == result


@pytest.mark.parametrize("kind", ["factory", "judge"])
def test_live_or_missing_execution_cannot_be_finalized(pair: Pair, kind: str) -> None:
    request = pair.factory if kind == "factory" else pair.judge
    status = pair.factory_status if kind == "factory" else pair.judge_status
    running = JobStatus.model_validate(
        status.model_dump()
        | {
            "state": "running",
            "execution": "running",
            "exit_code": None,
            "finished_at": None,
            "startup_forbidden": False,
        }
    )
    pair.repo.files.replace(f"control/statuses/{request.job_id}.json", running)
    with pytest.raises(ResultNotReady):
        pair.repo.finalize(pair.judge, BEAT)
    (pair.repo.files.root / f"control/statuses/{request.job_id}.json").unlink()
    with pytest.raises(ResultNotReady):
        pair.repo.finalize(pair.judge, BEAT)
    assert pair.repo.read(pair.judge) is None


@pytest.mark.parametrize("kind", ["factory", "judge"])
@pytest.mark.parametrize(
    "changes", [dict(exit_code=7), dict(exit_code=137, forced=True), dict(exit_code=137, oom_killed=True)]
)
def test_nonzero_forced_and_oom_are_not_scores(pair: Pair, kind: str, changes: dict[str, object]) -> None:
    request = pair.factory if kind == "factory" else pair.judge
    observed = pair.factory_status if kind == "factory" else pair.judge_status
    status = JobStatus.model_validate(
        observed.model_dump() | changes | {"state": "failed", "reason": "fixture failure"}
    )
    pair.repo.files.replace(f"control/statuses/{request.job_id}.json", status)
    result = pair.repo.finalize(pair.judge, BEAT)
    assert result.failure and result.accepted is None


def test_missing_final_report_does_not_authorize_rerun(pair: Pair) -> None:
    before = pair.repo.rounds.requests()
    (pair.repo.files.root / f"{pair.judge.report_dir}/report.json").unlink()
    result = pair.repo.finalize(pair.judge, BEAT)
    assert result.failure and result.accepted is None
    assert pair.repo.rounds.requests() == before


def test_nexus_success_projection_rebuilds_and_ignores_new_context_times(pair: Pair) -> None:
    store = FileTaskResultStore(pair.repo)
    assert FileResultStoreProvider(store).get_task_result_store() is store
    builder = SubnetBuilder(nodes=[])
    with builder.context_store.create_context() as ctx:
        original = store.add_successful_task_result(ctx, EVALUATION_TASK, pair.successful(pair.judge))
    with builder.context_store.create_context() as ctx:
        assert (
            store.add_successful_task_result(ctx, EVALUATION_TASK, pair.successful(pair.judge, later=True)) == original
        )
    recovered = FileTaskResultStore(ResultRepository(RoundRepository(pair.repo.files.root)))
    assert recovered.get_task_result(EVALUATION_TASK, original.id) == original
    epoch = Epoch(BlockNumber(500), BlockNumber(500))
    assert recovered.get_successful_tasks_for_epoch(EVALUATION_TASK, epoch) == (original,)
    assert recovered.get_successful_tasks_for_epoch(EVALUATION_TASK, Epoch(BlockNumber(501), BlockNumber(1000))) == ()
    assert recovered.get_executor_failures_for_epoch(EVALUATION_TASK, epoch) == ()
    with pytest.raises(TaskResultNotFoundException):
        recovered.get_task_result(FACTORY_TASK, original.id)
    with pytest.raises(TaskResultNotFoundException):
        recovered.get_task_result(EVALUATION_TASK, TaskResultId(uuid4()))
    assert original.executor_payload.miner_hotkey != original.target.hotkey
    assert original.processing_started == pair.judge_status.started_at and original.block_at_finish == BEAT


def test_failure_projection_is_canonical_and_persistent(pair: Pair) -> None:
    (pair.repo.files.root / f"{pair.judge.report_dir}/report.json").unlink()
    store = FileTaskResultStore(pair.repo)
    builder = SubnetBuilder(nodes=[])
    with builder.context_store.create_context() as ctx:
        failure = store.add_executor_failure(ctx, EVALUATION_TASK, pair.failure(pair.judge))
    assert "report rejected" in str(failure.executor_failure.executor_error)
    recovered = FileTaskResultStore(ResultRepository(RoundRepository(pair.repo.files.root)))
    values = recovered.get_executor_failures_for_epoch(EVALUATION_TASK, Epoch(BlockNumber(0), BlockNumber(600)))
    assert len(values) == 1 and values[0].id == failure.id
    with builder.context_store.create_context() as ctx, pytest.raises(ResultRejected):
        recovered.add_successful_task_result(ctx, EVALUATION_TASK, pair.successful(pair.judge))


def test_immutable_content_and_task_category_conflicts(pair: Pair) -> None:
    store = FileTaskResultStore(pair.repo)
    builder = SubnetBuilder(nodes=[])
    with builder.context_store.create_context() as ctx, pytest.raises(RecordConflictError):
        store.add_successful_task_result(ctx, FACTORY_TASK, pair.successful(pair.judge))
    original = pair.repo.finalize(pair.judge, BEAT)
    assert original.accepted is not None
    changed = original.model_copy(update={"accepted": original.accepted.model_copy(update={"score": 0.75})})
    with pytest.raises(RecordConflictError):
        pair.repo.files.publish(pair.repo.result_path(pair.judge), changed)
    conflicting = pair.judge.model_copy(update={"image": pair.factory.image})
    with pytest.raises(RecordConflictError):
        pair.repo.finalize(conflicting, BEAT)
    assert pair.repo.read(pair.judge) == original


@pytest.mark.parametrize("boundary", ["decision", "projection", "after_decision"])
def test_write_failure_recovery_uses_existing_execution(
    pair: Pair, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    store = FileTaskResultStore(pair.repo)
    original_write = pair.repo.files.write_bytes
    report = pair.repo.files.read_bytes(f"{pair.judge.report_dir}/report.json")
    requests = pair.repo.rounds.requests()
    decision_path = pair.repo.result_path(pair.judge)

    def fail(path: str, content: bytes, *, immutable: bool) -> None:
        selected = path == decision_path if boundary != "projection" else path.startswith("control/projections/")
        if selected:
            if boundary == "after_decision":
                original_write(path, content, immutable=immutable)
            raise OSError("injected result write failure")
        original_write(path, content, immutable=immutable)

    monkeypatch.setattr(pair.repo.files, "write_bytes", fail)
    builder = SubnetBuilder(nodes=[])
    with builder.context_store.create_context() as ctx, pytest.raises(OSError, match="injected"):
        store.add_successful_task_result(ctx, EVALUATION_TASK, pair.successful(pair.judge))
    retained = pair.repo.read(pair.judge)
    monkeypatch.setattr(pair.repo.files, "write_bytes", original_write)
    recovered_repo = ResultRepository(RoundRepository(pair.repo.files.root))
    recovered = FileTaskResultStore(recovered_repo)
    with builder.context_store.create_context() as ctx:
        result = recovered.add_successful_task_result(ctx, EVALUATION_TASK, pair.successful(pair.judge, later=True))
    assert result.id == pair.judge.projection_id
    assert recovered_repo.rounds.requests() == requests
    assert recovered_repo.files.read_bytes(f"{pair.judge.report_dir}/report.json") == report
    if retained is not None:
        assert recovered_repo.read(pair.judge) == retained and result.block_at_finish == BEAT


def test_thread_safe_duplicate_save_and_epoch_index(pair: Pair) -> None:
    store = FileTaskResultStore(pair.repo)
    builder = SubnetBuilder(nodes=[])

    def save(_: int) -> TaskResultId:
        with builder.context_store.create_context() as ctx:
            return store.add_successful_task_result(ctx, EVALUATION_TASK, pair.successful(pair.judge)).id

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert set(pool.map(save, range(16))) == {pair.judge.projection_id}
    assert len(store.get_successful_tasks_for_epoch(EVALUATION_TASK, Epoch(BlockNumber(0), BlockNumber(500)))) == 1
    store.rebuild()
    assert len(store.get_successful_tasks_for_epoch(EVALUATION_TASK, Epoch(BlockNumber(0), BlockNumber(500)))) == 1


def test_completed_round_fallback_is_independent_of_epoch_and_unresolved_jobs(pair: Pair) -> None:
    result = pair.repo.finalize(pair.judge, BEAT)
    assert pair.repo.latest_usable_round() is None
    (record,) = pair.repo.rounds.rounds()
    pair.repo.rounds.save_round(
        record.model_copy(
            update={
                "stage": "complete",
                "completed_at": record.plan.deadlines.round_end,
                "unresolved_jobs": (pair.factory.job_id,),
            }
        )
    )
    old = record.plan.deadlines
    deadlines = old.model_copy(
        update={
            "start": old.start + timedelta(hours=3),
            "generation_end": old.generation_end + timedelta(hours=3),
            "evaluation_start": old.evaluation_start + timedelta(hours=3),
            "judge_end": old.judge_end + timedelta(hours=3),
            "round_end": old.round_end + timedelta(hours=3),
        }
    )
    plan = record.plan.model_copy(update={"round_id": uuid4(), "sequence": 2, "cohort": (), "deadlines": deadlines})
    empty = pair.repo.rounds.prepare_round(plan, (FIXTURES / "specification.md").read_text())
    pair.repo.rounds.save_round(
        empty.model_copy(update={"stage": "complete", "completed_at": plan.deadlines.round_end})
    )
    assert pair.repo.latest_usable_round() == (record.plan, (result.accepted,))


def test_returned_routing_target_cannot_mutate_cached_results(pair: Pair) -> None:
    store = FileTaskResultStore(pair.repo)
    builder = SubnetBuilder(nodes=[])
    with builder.context_store.create_context() as ctx:
        result = store.add_successful_task_result(ctx, EVALUATION_TASK, pair.successful(pair.judge))
    original = result.target.hotkey
    result.target.hotkey = Hotkey("5" + "z" * 47)
    assert store.get_task_result(EVALUATION_TASK, result.id).target.hotkey == original
    queried = store.get_successful_tasks_for_epoch(EVALUATION_TASK, Epoch(BlockNumber(0), BlockNumber(500)))
    queried[0].target.hotkey = Hotkey("5" + "y" * 47)
    assert store.get_task_result(EVALUATION_TASK, result.id).target.hotkey == original


def test_factory_projection_epoch_ranges_are_inclusive_and_chronological(pair: Pair) -> None:
    store = FileTaskResultStore(pair.repo)
    builder = SubnetBuilder(nodes=[])
    (old_record,) = pair.repo.rounds.rounds()
    for sequence, block in enumerate((900, 500, 700), start=2):
        member = old_record.plan.cohort[0].model_copy(update={"factory_job_id": uuid4(), "judge_job_id": uuid4()})
        plan = old_record.plan.model_copy(update={"round_id": uuid4(), "sequence": sequence, "cohort": (member,)})
        pair.repo.rounds.prepare_round(plan, (FIXTURES / "specification.md").read_text())
        request = request_for(plan, member, "factory")
        pair.repo.rounds.publish_request(plan, request)
        observed = pair.factory_status.model_copy(
            update={
                "job_id": request.job_id,
                "factory_job_id": request.job_id,
                "round_id": request.round_id,
                "container_name": request.container_name,
            }
        )
        pair.repo.files.replace(f"control/statuses/{request.job_id}.json", observed)
        frame = SuccessfulTaskResultToPersist(
            result=Timestamped(
                executor_output=ProcessedInput(input=Routed(request, pair.target), output=observed),
                processing_started=observed.observed_at,
                processing_finished=observed.observed_at,
                block_at_finish=BlockBeat(BlockNumber(block), BEAT.block_timestamp, BEAT.block_hash),
            ),
            executor_public_output=observed,
        )
        with builder.context_store.create_context() as ctx:
            store.add_successful_task_result(ctx, FACTORY_TASK, frame)
    selected = store.get_successful_tasks_for_epoch(FACTORY_TASK, Epoch(BlockNumber(500), BlockNumber(700)))
    assert [r.block_at_finish.block_number for r in selected] == [500, 700]
    recovered = FileTaskResultStore(ResultRepository(RoundRepository(pair.repo.files.root)))
    assert recovered.get_successful_tasks_for_epoch(FACTORY_TASK, Epoch(BlockNumber(500), BlockNumber(700))) == selected


def test_changed_framework_observation_cannot_replace_business_evidence(pair: Pair) -> None:
    store = FileTaskResultStore(pair.repo)
    builder = SubnetBuilder(nodes=[])
    original = pair.repo.finalize(pair.judge, BEAT)
    changed = pair.judge_status.model_copy(update={"observed_at": pair.judge_status.observed_at + timedelta(seconds=1)})
    incoming = SuccessfulTaskResultToPersist(
        result=Timestamped(
            executor_output=ProcessedInput(input=Routed(pair.judge, pair.target), output=changed),
            processing_started=changed.observed_at,
            processing_finished=changed.observed_at,
            block_at_finish=LATER,
        ),
        executor_public_output=changed,
    )
    with builder.context_store.create_context() as ctx, pytest.raises(RecordConflictError):
        store.add_successful_task_result(ctx, EVALUATION_TASK, incoming)
    assert pair.repo.read(pair.judge) == original
    assert store.get_successful_tasks_for_epoch(EVALUATION_TASK, Epoch(BlockNumber(0), BlockNumber(1000))) == ()
