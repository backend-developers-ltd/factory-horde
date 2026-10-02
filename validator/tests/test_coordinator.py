"""Crash recovery and round gates independent of executor timing and Nexus callback delivery."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from nexus.v1 import BlockBeat, ContextId, ReceiveEvent, SubnetBuilder
from pydantic import ValidationError

from validator.coordinator import (
    FactoryEligibility,
    RoundCoordinator,
    RoundTick,
    RoundTiming,
    Schedule,
    SkippedEvaluation,
)
from validator.discovery import DiscoverySnapshot
from validator.record_files import RecordFiles
from validator.records import (
    CohortMember,
    Deadlines,
    JobRequest,
    JobStatus,
    JudgeReport,
    RoundPlan,
    StopRequest,
    request_for,
)
from validator.result_repository import ResultRepository
from validator.round_actor import RoundCoordinatorNode
from validator.round_repository import RoundRepository

from .test_results import BEAT

START = datetime(2026, 10, 1, 10, tzinfo=UTC)
IMAGE = "ghcr.io/example/factory@sha256:" + "a" * 64
JUDGE = "ghcr.io/example/judge@sha256:" + "b" * 64
TIMING = RoundTiming(
    generation=timedelta(seconds=100),
    confirmation=timedelta(seconds=10),
    evaluation=timedelta(seconds=60),
    judge_reserve=timedelta(seconds=10),
    stop_grace_seconds=5,
)


@dataclass
class Fixture:
    repository: ResultRepository
    count: int = 2
    discoveries: int = 0

    def discovery(self, identity: UUID) -> DiscoverySnapshot:
        files = self.repository.files
        path = f"control/discovery/{identity}.json"
        try:
            return files.read(path, DiscoverySnapshot)
        except FileNotFoundError:
            self.discoveries += 1
            snapshot = DiscoverySnapshot(
                netuid=2,
                block=BEAT.block_number,
                block_hash=BEAT.block_hash,
                observed_at=START,
                validator_hotkey="5" + "z" * 47,
                allowed_hotkeys=None,
                cohort=tuple(
                    CohortMember(miner_hotkey="5" + letter * 47, uid=i + 2, image=IMAGE)
                    for i, letter in enumerate("abcde"[: self.count])
                ),
                rejected=(),
            )
            files.publish(path, snapshot)
            return snapshot

    def coordinator(self) -> RoundCoordinator:
        return RoundCoordinator(self.repository, TIMING, JUDGE, self.discovery)

    def start(self) -> RoundPlan:
        result = self.coordinator().tick(START, BEAT)
        assert len(result.jobs) == self.count and not result.errors
        return self.repository.rounds.rounds()[0].plan

    def status(
        self,
        request: JobRequest,
        *,
        finished: bool = True,
        code: int = 0,
        forced: bool = False,
        finished_at: datetime | None = None,
        observed_at: datetime | None = None,
    ) -> None:
        start = request.created_at + timedelta(seconds=1)
        finish = finished_at or start + timedelta(seconds=2)
        status = JobStatus(
            round_id=request.round_id,
            job_id=request.job_id,
            miner_hotkey=request.miner_hotkey,
            kind=request.kind,
            factory_job_id=request.factory_job_id,
            state="finished" if finished and code == 0 and not forced else "failed" if finished else "running",
            observed_at=observed_at or finish,
            execution="exited" if finished else "running",
            startup_forbidden=finished,
            container_id=request.job_id.hex * 2,
            container_name=request.container_name,
            started_at=start,
            finished_at=finish if finished else None,
            exit_code=code if finished else None,
            forced=forced,
            reason="fixture failure" if code or forced else None,
        )
        self.repository.files.replace(f"control/statuses/{request.job_id}.json", status)
        if request.kind == "factory":
            for name in ("main.py", "README.md"):
                self.repository.files.write_bytes(f"{request.output_dir}/{name}", b"fixture\n", immutable=True)
        else:
            report = JudgeReport(
                round_id=request.round_id,
                job_id=request.job_id,
                miner_hotkey=request.miner_hotkey,
                kind="judge",
                factory_job_id=request.factory_job_id,
                checked_files=("main.py", "README.md"),
                score=0.0,
                failure=None,
            )
            self.repository.files.publish(f"{request.report_dir}/report.json", report)


@pytest.fixture
def fixture(tmp_path: Path) -> Fixture:
    return Fixture(ResultRepository(RoundRepository(tmp_path)))


def test_defaults_and_invalid_windows() -> None:
    assert RoundTiming().deadlines(START) == Deadlines.defaults(START)
    for changes in (
        {"generation": timedelta()},
        {"judge_reserve": timedelta(hours=1)},
        {"confirmation": timedelta(seconds=1)},
        {"stop_grace_seconds": 61},
    ):
        with pytest.raises(ValidationError):
            RoundTiming.model_validate(RoundTiming().model_dump() | changes)


def test_no_initial_block_means_no_discovery_or_requests(fixture: Fixture) -> None:
    for _ in range(3):
        assert fixture.coordinator().tick(START, None) == RoundTick((), (), ())
    assert fixture.discoveries == 0 and fixture.repository.rounds.rounds() == ()
    assert fixture.repository.rounds.requests() == ()


def test_early_completion_waits_for_evaluation_then_scores_without_callbacks(fixture: Fixture) -> None:
    plan = fixture.start()
    for member in plan.cohort:
        fixture.status(request_for(plan, member, "factory"))
    coordinator = fixture.coordinator()
    for now in (
        START + timedelta(seconds=5),
        plan.deadlines.generation_end,
        plan.deadlines.evaluation_start - timedelta(microseconds=1),
    ):
        result = coordinator.tick(now, BEAT)
        assert not result.errors and not result.unresolved
        assert all(r.kind == "factory" for r in fixture.repository.rounds.requests())
    coordinator.tick(plan.deadlines.evaluation_start, BEAT)
    assert len(fixture.repository.rounds.requests()) == 4
    for member in plan.cohort:
        fixture.status(request_for(plan, member, "judge"))
    coordinator.tick(plan.deadlines.round_end - timedelta(seconds=1), BEAT)
    assert fixture.repository.latest_usable_round() is not None
    assert len(fixture.repository.rounds.rounds()) == 1
    coordinator.tick(plan.deadlines.round_end, BEAT)
    usable = fixture.repository.latest_usable_round()
    assert usable is not None and usable[0] == plan and [s.score for s in usable[1]] == [0.0, 0.0]
    assert not (fixture.repository.files.root / "control/projections").exists()


def test_unresolved_factory_allows_other_scores_but_skips_next_slot(fixture: Fixture) -> None:
    plan = fixture.start()
    first, second = plan.cohort
    factory = request_for(plan, first, "factory")
    hanging = request_for(plan, second, "factory")
    fixture.status(factory)
    coordinator = fixture.coordinator()
    result = coordinator.tick(plan.deadlines.generation_end, BEAT)
    assert hanging.job_id in result.unresolved
    stop = fixture.repository.files.read(f"control/stops/{hanging.job_id}.json", StopRequest)
    assert stop.requested_at == plan.deadlines.generation_end
    coordinator.tick(plan.deadlines.evaluation_start, BEAT)
    assert {r.miner_hotkey for r in fixture.repository.rounds.requests() if r.kind == "judge"} == {first.miner_hotkey}
    fixture.status(request_for(plan, first, "judge"))
    result = coordinator.tick(plan.deadlines.round_end, BEAT)
    assert result.unresolved == (hanging.job_id,)
    assert fixture.repository.latest_usable_round() is not None
    assert len(fixture.repository.rounds.rounds()) == 1
    # Fresh coordinator, same permanent stop; reconciliation permits only the next unskipped slot.
    fixture.status(hanging, code=137, forced=True)
    fixture.coordinator().tick(plan.deadlines.round_end + timedelta(seconds=1), BEAT)
    assert len(fixture.repository.rounds.rounds()) == 1
    fixture.coordinator().tick(plan.deadlines.round_end + TIMING.interval, BEAT)
    assert len(fixture.repository.rounds.rounds()) == 2
    assert fixture.repository.files.read(f"control/stops/{hanging.job_id}.json", StopRequest) == stop


def test_live_judge_is_stopped_and_holds_admission_without_a_chain_beat(fixture: Fixture) -> None:
    plan = fixture.start()
    for member in plan.cohort:
        fixture.status(request_for(plan, member, "factory"))
    fixture.coordinator().tick(plan.deadlines.evaluation_start, BEAT)
    judges = [request_for(plan, member, "judge") for member in plan.cohort]
    for judge in judges:
        fixture.status(judge, finished=False)
    restarted = fixture.coordinator()
    result = restarted.tick(plan.deadlines.judge_end, None)
    assert set(result.unresolved) == {j.job_id for j in judges}
    for judge in judges:
        assert fixture.repository.files.read(f"control/stops/{judge.job_id}.json", StopRequest).reason == "deadline"
    restarted.tick(plan.deadlines.round_end, BEAT)
    assert len(fixture.repository.rounds.rounds()) == 1
    assert fixture.repository.latest_usable_round() is None


@pytest.mark.parametrize("boundary", ["seed", "plan", "input", "request"])
def test_crash_at_publication_recovers_original_ids_and_deadlines(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    original = RecordFiles.write_bytes
    tripped = False

    def crash(files: RecordFiles, relative: str, content: bytes, *, immutable: bool) -> None:
        nonlocal tripped
        original(files, relative, content, immutable=immutable)
        matches = {
            "seed": relative == "control/schedule.json" and b'"round_id"' in content,
            "plan": relative.endswith("/round.json"),
            "input": relative.endswith("/input/task.json"),
            "request": relative.startswith("control/requests/"),
        }
        if matches[boundary] and not tripped:
            tripped = True
            raise OSError("injected crash after durable publication")

    with monkeypatch.context() as patch:
        patch.setattr(RecordFiles, "write_bytes", crash)
        try:
            fixture.coordinator().tick(START, BEAT)
        except OSError:
            pass
    assert tripped
    schedule = fixture.repository.files.read("control/schedule.json", Schedule)
    previous = fixture.repository.rounds.rounds()
    identity = schedule.pending.round_id if schedule.pending else previous[0].plan.round_id
    result = fixture.coordinator().tick(START + timedelta(seconds=2), BEAT)
    assert not result.errors
    records = fixture.repository.rounds.rounds()
    assert len(records) == 1 and records[0].plan.round_id == identity
    assert records[0].plan.deadlines == TIMING.deadlines(START)
    assert len(fixture.repository.rounds.requests()) == fixture.count
    assert fixture.discoveries == 1


@pytest.mark.parametrize("boundary", ["generation_end", "evaluation_start", "judge_end", "round_end"])
def test_restart_at_stage_boundaries_keeps_plan_and_never_reopens_generation(fixture: Fixture, boundary: str) -> None:
    plan = fixture.start()
    now = {
        "generation_end": plan.deadlines.generation_end,
        "evaluation_start": plan.deadlines.evaluation_start,
        "judge_end": plan.deadlines.judge_end,
        "round_end": plan.deadlines.round_end,
    }[boundary]
    fixture.coordinator().tick(now, BEAT)
    fixture.coordinator().tick(now, BEAT)
    assert fixture.repository.rounds.rounds()[0].plan == plan
    assert {job.job_id for job in fixture.repository.rounds.requests()} == {m.factory_job_id for m in plan.cohort}
    assert len(fixture.repository.rounds.rounds()) == 1


def test_corrupt_factory_status_does_not_prevent_other_stops(fixture: Fixture) -> None:
    plan = fixture.start()
    first, second = (request_for(plan, m, "factory") for m in plan.cohort)
    fixture.repository.files.write_bytes(f"control/statuses/{first.job_id}.json", b"{broken", immutable=False)
    result = fixture.coordinator().tick(plan.deadlines.generation_end, BEAT)
    assert result.errors and set(result.unresolved) == {first.job_id, second.job_id}
    for job in (first, second):
        assert fixture.repository.files.read(f"control/stops/{job.job_id}.json", StopRequest).job_id == job.job_id


@pytest.mark.parametrize("failure", ["nonzero", "forced", "missing_output", "late"])
def test_ineligible_factory_never_authorizes_judge(fixture: Fixture, failure: str) -> None:
    fixture.count = 1
    plan = fixture.start()
    factory, judge = (request_for(plan, plan.cohort[0], kind) for kind in ("factory", "judge"))
    fixture.status(factory, code=7 if failure == "nonzero" else 0, forced=failure == "forced")
    if failure == "missing_output":
        (fixture.repository.files.root / factory.output_dir / "main.py").unlink()
    now = plan.deadlines.judge_end if failure == "late" else plan.deadlines.evaluation_start
    result = fixture.coordinator().tick(now, BEAT)
    assert not result.errors
    assert all(request.kind == "factory" for request in fixture.repository.rounds.requests())
    skipped = fixture.repository.files.read(f"control/skipped-evaluations/{judge.job_id}.json", SkippedEvaluation)
    assert (
        skipped.reason
        == {
            "nonzero": "factory_failed",
            "forced": "factory_failed",
            "missing_output": "missing_output",
            "late": "evaluation_expired",
        }[failure]
    )


@pytest.mark.parametrize("offset", [-1, 0, 1, 300])
@pytest.mark.parametrize("observe_cutoff", [False, True])
def test_finish_cutoff_survives_late_observation_and_restart(
    fixture: Fixture,
    offset: int,
    observe_cutoff: bool,
) -> None:
    fixture.count = 1
    plan = fixture.start()
    factory, judge = (request_for(plan, plan.cohort[0], kind) for kind in ("factory", "judge"))
    cutoff = plan.deadlines.evaluation_start
    # Use a point inside the evaluation window, not its judge deadline.
    finish = cutoff + timedelta(microseconds=offset)
    observed = cutoff + timedelta(seconds=20)
    relative = f"control/factory-eligibility/{factory.job_id}.json"
    if observe_cutoff:
        result = fixture.coordinator().tick(cutoff, None)
        assert not result.errors and factory.job_id in result.unresolved
        pending = fixture.repository.files.read(relative, FactoryEligibility)
        assert pending.decision == "pending" and pending.cutoff == cutoff
    fixture.status(factory, finished_at=finish, observed_at=observed)
    result = fixture.coordinator().tick(observed, BEAT)
    assert not result.errors
    eligibility = fixture.repository.files.read(relative, FactoryEligibility)
    assert eligibility.decision == ("timely" if offset <= 0 else "late")
    assert eligibility.cutoff == cutoff
    assert (judge in fixture.repository.rounds.requests()) == (offset <= 0)
    if offset > 0:
        skipped = fixture.repository.files.read(f"control/skipped-evaluations/{judge.job_id}.json", SkippedEvaluation)
        assert skipped.reason == "factory_late"
    # A fresh repository/coordinator retains the decision, IDs and original cutoff.
    fixture.repository = ResultRepository(RoundRepository(fixture.repository.files.root))
    fixture.coordinator().tick(observed + timedelta(seconds=1), BEAT)
    assert fixture.repository.files.read(relative, FactoryEligibility) == eligibility


def test_factory_finishing_at_minute_70_cannot_enter_minute_65_cohort(fixture: Fixture) -> None:
    coordinator = RoundCoordinator(fixture.repository, RoundTiming(), JUDGE, fixture.discovery)
    coordinator.tick(START, BEAT)
    plan = fixture.repository.rounds.rounds()[0].plan
    for member in plan.cohort:
        fixture.status(request_for(plan, member, "factory"), finished_at=START + timedelta(minutes=70))
    result = coordinator.tick(START + timedelta(minutes=70), BEAT)
    assert not result.errors and not result.unresolved
    assert all(request.kind == "factory" for request in fixture.repository.rounds.requests())
    for member in plan.cohort:
        skipped = fixture.repository.files.read(
            f"control/skipped-evaluations/{member.judge_job_id}.json", SkippedEvaluation
        )
        assert skipped.reason == "factory_late"


@pytest.mark.parametrize("offset", [-1, 1])
def test_concurrent_result_publication_waits_for_persisted_eligibility(
    fixture: Fixture,
    monkeypatch: pytest.MonkeyPatch,
    offset: int,
) -> None:
    fixture.count = 1
    plan = fixture.start()
    factory, judge = (request_for(plan, plan.cohort[0], kind) for kind in ("factory", "judge"))
    cutoff = plan.deadlines.evaluation_start
    observed = cutoff + timedelta(seconds=20)
    relative = f"control/factory-eligibility/{factory.job_id}.json"
    original = RoundRepository.status

    def arriving_status(rounds: RoundRepository, request: JobRequest) -> JobStatus | None:
        status = original(rounds, request)
        if request == factory and status is None and (rounds.files.root / relative).exists():
            fixture.status(factory, finished_at=cutoff + timedelta(seconds=offset), observed_at=observed)
            fixture.repository.finalize(factory, BEAT)
        return status

    monkeypatch.setattr(RoundRepository, "status", arriving_status)
    result = fixture.coordinator().tick(observed, BEAT)
    assert not result.errors
    assert fixture.repository.read(factory) is not None
    assert fixture.repository.files.read(relative, FactoryEligibility).decision == "pending"
    assert judge not in fixture.repository.rounds.requests()
    result = fixture.coordinator().tick(observed, BEAT)
    assert not result.errors
    assert (judge in fixture.repository.rounds.requests()) == (offset < 0)


def test_fresh_actor_emits_separate_job_contexts_and_resubscribes_same_ids(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = fixture.start()
    jobs = tuple(request_for(plan, member, "factory") for member in plan.cohort)
    coordinator = fixture.coordinator()

    def pending(_now: datetime, _beat: BlockBeat | None) -> RoundTick:
        return RoundTick(jobs, tuple(j.job_id for j in jobs), ())

    monkeypatch.setattr(coordinator, "tick", pending)
    node = RoundCoordinatorNode(coordinator)
    builder = SubnetBuilder(nodes=[node])
    observed: list[set[ContextId]] = []
    for _ in range(2):
        actor = node.build_actor(pipe_to_bus=builder.pipe_to_bus, context_store=builder.context_store)
        with builder.context_store.create_context() as ctx:
            actor.handle_block(ctx, ReceiveEvent(ctx_id=ctx.id, target=node.block_beat, payload=BEAT))
            events = actor.handle(ctx, ReceiveEvent(ctx_id=ctx.id, target=node.sink, payload=START))
            assert isinstance(events, tuple)
            inputs = [event for event in events if event.source == node.factory]
            assert len(inputs) == 2 and len({event.ctx_id for event in inputs}) == 2
            assert all(event.ctx_id != ctx.id for event in inputs)
            observed.append({event.ctx_id for event in inputs})
    assert not observed[0] & observed[1]


def test_completed_legacy_fixture_does_not_override_new_schedule(fixture: Fixture) -> None:
    plan = fixture.start()
    for member in plan.cohort:
        fixture.status(request_for(plan, member, "factory"), code=7)
    record = fixture.repository.rounds.rounds()[0]
    fixture.repository.rounds.save_round(
        record.model_copy(
            update={"stage": "complete", "completed_at": START + timedelta(seconds=4), "unresolved_jobs": ()}
        )
    )
    # Earlier manually driven fixtures have complete outcomes but no coordinator schedule.
    (fixture.repository.files.root / "control/schedule.json").unlink()
    result = fixture.coordinator().tick(START + timedelta(seconds=5), BEAT)
    assert not result.errors
    assert len(fixture.repository.rounds.rounds()) == 2
    assert fixture.repository.rounds.rounds()[0].stage == "complete"


@pytest.mark.parametrize("artifact", ["symlink", "directory", "unreadable"])
def test_unsafe_output_is_ineligible_but_host_io_failure_stays_unresolved(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, artifact: str
) -> None:
    fixture.count = 1
    plan = fixture.start()
    factory, judge = (request_for(plan, plan.cohort[0], kind) for kind in ("factory", "judge"))
    fixture.status(factory)
    original = RecordFiles.read_artifact

    def unavailable(files: RecordFiles, relative: str) -> bytes:
        if relative.endswith("/main.py"):
            raise OSError("injected disk failure")
        return original(files, relative)

    with monkeypatch.context() as patch:
        patch.setattr(RecordFiles, "read_artifact", unavailable)
        failed = fixture.coordinator().tick(plan.deadlines.evaluation_start, BEAT)
        assert failed.errors and judge.job_id in failed.unresolved
    path = fixture.repository.files.root / factory.output_dir / "main.py"
    if artifact == "unreadable":
        path.chmod(0)
    else:
        path.unlink()
        if artifact == "directory":
            path.mkdir()
        else:
            path.symlink_to("/etc/passwd")
    observed = fixture.coordinator().tick(plan.deadlines.evaluation_start, BEAT)
    assert not observed.errors and not observed.unresolved
    skipped = fixture.repository.files.read(f"control/skipped-evaluations/{judge.job_id}.json", SkippedEvaluation)
    assert skipped.reason == "invalid_output"
