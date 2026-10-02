"""Durable application rounds, driven only by the owning Nexus actor's wall-clock ticks."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

import structlog
from nexus.v1 import BlockBeat
from prometheus_client import Counter, Histogram
from pydantic import UUID4, Field, model_validator

from validator.discovery import DiscoverySnapshot
from validator.record_files import RecordFormatError
from validator.records import (
    Deadlines,
    ImageReference,
    JobIdentity,
    JobRequest,
    Record,
    RoundPlan,
    RoundRecord,
    StopRequest,
    UtcTime,
    request_for,
)
from validator.result_records import ResultProjection
from validator.result_repository import ResultRepository

_logger = structlog.get_logger(__name__)
_events = Counter("factory_horde_coordinator_total", "Round coordinator events", ("event",))
_latency = Histogram("factory_horde_coordinator_seconds", "Round reconciliation latency")
SPECIFICATION = "Produce the fixed FactoryHorde greeting project. No model inference.\n"


class RoundTiming(Record):
    """Defaults preserve 60/5/55-minute stages and a final five-minute judge stop reserve."""

    generation: Annotated[timedelta, Field(gt=timedelta())] = timedelta(hours=1)
    confirmation: Annotated[timedelta, Field(gt=timedelta())] = timedelta(minutes=5)
    evaluation: Annotated[timedelta, Field(gt=timedelta())] = timedelta(minutes=55)
    judge_reserve: Annotated[timedelta, Field(gt=timedelta())] = timedelta(minutes=5)
    stop_grace_seconds: Annotated[int, Field(ge=0, le=60)] = 60

    @model_validator(mode="after")
    def _windows(self) -> Self:
        if self.judge_reserve >= self.evaluation:
            raise ValueError("Judge stop reserve must be shorter than evaluation")
        grace = timedelta(seconds=self.stop_grace_seconds)
        if grace > min(self.confirmation, self.judge_reserve):
            raise ValueError("Stop windows must accommodate the configured grace")
        return self

    @property
    def interval(self) -> timedelta:
        """Application cadence; never derived from chain tempo."""
        return self.generation + self.confirmation + self.evaluation

    def deadlines(self, start: datetime) -> Deadlines:
        """Freeze all absolute boundaries before discovery or external execution."""
        evaluation_start = start + self.generation + self.confirmation
        return Deadlines(
            start=start,
            generation_end=start + self.generation,
            evaluation_start=evaluation_start,
            judge_end=evaluation_start + self.evaluation - self.judge_reserve,
            round_end=start + self.interval,
        )


class RoundSeed(Record):
    """Retained admission intent lets partial discovery/input publication resume with the same IDs."""

    round_id: UUID4 = Field(default_factory=uuid4)
    sequence: Annotated[int, Field(ge=1)]
    deadlines: Deadlines
    judge_image: ImageReference
    stop_grace_seconds: Annotated[int, Field(ge=0, le=60)]
    specification: Annotated[str, Field(min_length=1)] = SPECIFICATION


class Schedule(Record):
    """One coordinator writer; a pending admission is persisted before discovery."""

    next_start: UtcTime
    next_sequence: Annotated[int, Field(ge=1)]
    pending: RoundSeed | None = None


class SkippedEvaluation(JobIdentity):
    """An intended judge was never authorized; this is not an executor termination claim."""

    reason: Literal["factory_failed", "factory_late", "missing_output", "invalid_output", "evaluation_expired"]
    observed_at: UtcTime


class FactoryEligibility(JobIdentity):
    """Persist the fixed finish cutoff; pending evidence cannot extend it after restart."""

    cutoff: UtcTime
    decision: Literal["pending", "timely", "late", "failed"]
    observed_at: UtcTime
    finished_at: UtcTime | None = None


@dataclass(frozen=True)
class RoundTick:
    """Disposable observations; authoritative round/job state remains in the shared tree."""

    jobs: tuple[JobRequest, ...]
    unresolved: tuple[UUID, ...]
    errors: tuple[str, ...]


class RoundCoordinator:
    """Reconcile files without Docker calls, waits or dependency on Nexus task delivery."""

    def __init__(
        self,
        repository: ResultRepository,
        timing: RoundTiming,
        judge_image: str,
        discover: Callable[[UUID], DiscoverySnapshot],
    ):
        self.repository = repository
        self.rounds = repository.rounds
        self.timing = timing
        self.judge_image = judge_image
        self.discover = discover

    def _save_schedule(self, schedule: Schedule) -> None:
        self.repository.files.replace("control/schedule.json", schedule)

    def _schedule(self, now: datetime, records: tuple[RoundRecord, ...]) -> Schedule:
        try:
            return self.repository.files.read("control/schedule.json", Schedule)
        except FileNotFoundError:
            schedule = Schedule(next_start=now, next_sequence=max((r.plan.sequence for r in records), default=0) + 1)
            self._save_schedule(schedule)
            return schedule

    def _admit(self, schedule: Schedule) -> RoundRecord:
        seed = schedule.pending
        if seed is None:
            seed = RoundSeed(
                sequence=schedule.next_sequence,
                deadlines=self.timing.deadlines(schedule.next_start),
                judge_image=self.judge_image,
                stop_grace_seconds=self.timing.stop_grace_seconds,
            )
            schedule = schedule.model_copy(update={"pending": seed})
            self._save_schedule(schedule)
        snapshot = self.discover(seed.round_id)
        plan = RoundPlan(
            round_id=seed.round_id,
            sequence=seed.sequence,
            deadlines=seed.deadlines,
            discovery_block=snapshot.block,
            discovery_block_hash=snapshot.block_hash,
            membership_snapshot="commitment_block",
            cohort=snapshot.cohort,
            judge_image=seed.judge_image,
            stop_grace_seconds=seed.stop_grace_seconds,
            specification_sha256=sha256(seed.specification.encode()).hexdigest(),
        )
        record = self.rounds.prepare_round(plan, seed.specification)
        self._save_schedule(Schedule(next_start=plan.deadlines.round_end, next_sequence=seed.sequence + 1))
        _events.labels("admitted").inc()
        _logger.info("round_admitted", round_id=str(plan.round_id), cohort=len(plan.cohort))
        return record

    def _stop(self, request: JobRequest) -> None:
        try:
            stop = self.repository.files.read(f"control/stops/{request.job_id}.json", StopRequest)
        except FileNotFoundError:
            stop = StopRequest(
                round_id=request.round_id,
                job_id=request.job_id,
                miner_hotkey=request.miner_hotkey,
                kind=request.kind,
                factory_job_id=request.factory_job_id,
                requested_at=request.deadline,
                reason="deadline",
            )
        self.rounds.publish_stop(request, stop)

    def _observe(self, request: JobRequest, now: datetime, beat: BlockBeat | None) -> bool:
        try:
            retained = self.repository.read(request)
            status = retained.status if retained is not None else self.rounds.status(request)
        except OSError, ValueError:
            if now >= request.deadline:
                self._stop(request)
            raise
        if now >= request.deadline and (status is None or not status.confirmed_stopped):
            self._stop(request)
        if status is None or not status.confirmed_stopped:
            return False
        if beat is not None and retained is None:
            self.repository.finalize(request, beat)
        return True

    def _factory_eligibility(self, factory: JobRequest, cutoff: datetime, now: datetime) -> FactoryEligibility:
        relative = f"control/factory-eligibility/{factory.job_id}.json"
        try:
            eligibility = self.repository.files.read(relative, FactoryEligibility)
        except FileNotFoundError:
            eligibility = FactoryEligibility(
                **factory.model_dump(include=set(JobIdentity.model_fields)),
                cutoff=cutoff,
                decision="pending",
                observed_at=now,
            )
            self.repository.files.replace(relative, eligibility)
        if not factory.same_job(eligibility) or eligibility.cutoff != cutoff:
            raise ValueError("Factory eligibility differs from the frozen job/cutoff")
        if eligibility.decision != "pending":
            return eligibility
        result = self.repository.read(factory)
        status = result.status if result is not None else self.rounds.status(factory)
        if status is None or not status.confirmed_stopped:
            return eligibility
        decision = (
            "failed"
            if not status.application_succeeded
            else "timely"
            if status.finished_at is not None and status.finished_at <= cutoff
            else "late"
        )
        eligibility = eligibility.model_copy(
            update={"decision": decision, "observed_at": now, "finished_at": status.finished_at}
        )
        self.repository.files.replace(relative, eligibility)
        _events.labels("factory_" + decision).inc()
        return eligibility

    def _judge_ready(self, factory: JobRequest, judge: JobRequest, now: datetime) -> bool:
        if now >= judge.created_at:
            eligibility = self._factory_eligibility(factory, judge.created_at, now)
        else:
            eligibility = None
        relative = f"control/skipped-evaluations/{judge.job_id}.json"
        try:
            skipped = self.repository.files.read(relative, SkippedEvaluation)
        except FileNotFoundError:
            pass
        else:
            if not judge.same_job(skipped):
                raise ValueError("Skipped evaluation attribution differs")
            return False
        result = self.repository.read(factory)
        reason: (
            Literal["factory_failed", "factory_late", "missing_output", "invalid_output", "evaluation_expired"] | None
        ) = None
        if now >= judge.deadline:
            reason = "evaluation_expired"
        elif eligibility is not None and eligibility.decision == "late":
            reason = "factory_late"
        elif eligibility is not None and eligibility.decision == "failed":
            reason = "factory_failed"
        elif result is None:
            return False
        elif result.failure is not None:
            reason = "factory_failed"
        elif eligibility is None or eligibility.decision != "timely":
            return False
        else:
            try:
                for name in ("main.py", "README.md"):
                    self.repository.files.read_artifact(f"{factory.output_dir}/{name}")
            except FileNotFoundError:
                reason = "missing_output"
            except RecordFormatError:
                reason = "invalid_output"
        if reason is None:
            return True
        self.repository.files.publish(
            relative,
            SkippedEvaluation(
                round_id=judge.round_id,
                job_id=judge.job_id,
                miner_hotkey=judge.miner_hotkey,
                kind="judge",
                factory_job_id=judge.factory_job_id,
                reason=reason,
                observed_at=now,
            ),
        )
        _events.labels("evaluation_skipped").inc()
        return False

    def _projected(self, request: JobRequest) -> bool:
        try:
            reference = self.repository.files.read(
                f"control/projections/{request.projection_id}.json", ResultProjection
            )
        except FileNotFoundError:
            return False
        if reference.result_id != request.projection_id or reference.result_path != self.repository.result_path(
            request
        ):
            raise ValueError("Task projection differs from the expected job")
        return True

    def _reconcile(
        self, record: RoundRecord, now: datetime, beat: BlockBeat | None, requests: dict[UUID, JobRequest]
    ) -> RoundTick:
        plan = record.plan
        unresolved: set[UUID] = set()
        subscriptions: list[JobRequest] = []
        errors: list[str] = []
        settled = True
        for member in plan.cohort:
            factory, judge = (request_for(plan, member, kind) for kind in ("factory", "judge"))
            for job in (factory, judge):
                try:
                    if job.job_id not in requests:
                        if job.kind == "factory":
                            if now < job.created_at or beat is None and now < job.deadline:
                                unresolved.add(job.job_id)
                                continue
                        else:
                            if not self._judge_ready(factory, judge, now) or beat is None:
                                continue
                        self.rounds.publish_request(plan, job)
                        requests[job.job_id] = job
                    if not self._observe(job, now, beat):
                        unresolved.add(job.job_id)
                    if self.repository.read(job) is None:
                        settled = False
                    if not self._projected(job):
                        subscriptions.append(job)
                except (OSError, ValueError) as error:
                    # One unreadable factory must not prevent stopping its judge or another miner.
                    unresolved.add(job.job_id)
                    errors.append(f"{job.job_id}: {error}")
            if judge.job_id not in requests:
                try:
                    self.repository.files.read(f"control/skipped-evaluations/{judge.job_id}.json", SkippedEvaluation)
                except FileNotFoundError:
                    settled = False
        complete = now >= plan.deadlines.round_end or (
            now >= plan.deadlines.evaluation_start and settled and not unresolved and not errors
        )
        stage = (
            "complete"
            if complete
            else "evaluation"
            if now >= plan.deadlines.evaluation_start
            else "stopping"
            if now >= plan.deadlines.generation_end
            else "generation"
        )
        stages = ("generation", "stopping", "evaluation", "complete")
        if stages.index(stage) < stages.index(record.stage):
            stage = record.stage
        updated = RoundRecord(
            plan=plan,
            stage=stage,
            unresolved_jobs=tuple(sorted(unresolved)),
            completed_at=record.completed_at or now if stage == "complete" else None,
        )
        if updated != record:
            self.rounds.save_round(updated)
            _events.labels("reconciled").inc()
            _logger.info("round_reconciled", round_id=str(plan.round_id), stage=stage, unresolved=len(unresolved))
        return RoundTick(tuple(subscriptions), tuple(sorted(unresolved)), tuple(errors))

    def tick(self, now: datetime, beat: BlockBeat | None) -> RoundTick:
        """Enforce old-work safety first, then resume/admit at most one application round.

        Raises:
            OSError: Durable state cannot be read or published; no admission follows that failure.
            ValueError: State or chain attribution is inconsistent.
        """
        with _latency.time():
            records = self.rounds.rounds()
            self.rounds.files.mkdir("control/requests")
            requests = {request.job_id: request for request in self.rounds.requests()}
            known = {job for r in records for m in r.plan.cohort for job in (m.factory_job_id, m.judge_job_id)}
            if set(requests) - known:
                raise ValueError("Published jobs lack a recoverable round; refusing new admission")
            schedule = self._schedule(now, records) if beat is not None else None
            jobs: list[JobRequest] = []
            unresolved: set[UUID] = set()
            errors: list[str] = []
            for record in records:
                if (
                    schedule is not None
                    and schedule.pending is not None
                    and record.plan.round_id == schedule.pending.round_id
                ):
                    continue
                result = self._reconcile(record, now, beat, requests)
                jobs.extend(result.jobs)
                unresolved.update(result.unresolved)
                errors.extend(result.errors)
            if beat is None or schedule is None:
                _events.labels("awaiting_chain").inc()
                return RoundTick(tuple(jobs), tuple(sorted(unresolved)), tuple(errors))
            active = any(r.stage != "complete" and now < r.plan.deadlines.round_end for r in records)
            if schedule.pending is not None:
                # A partially prepared round is the same admission, never a second active round.
                other_active = any(
                    r.plan.round_id != schedule.pending.round_id
                    and r.stage != "complete"
                    and now < r.plan.deadlines.round_end
                    for r in records
                )
                other_unresolved = unresolved - {
                    job
                    for r in records
                    if r.plan.round_id == schedule.pending.round_id
                    for m in r.plan.cohort
                    for job in (m.factory_job_id, m.judge_job_id)
                }
                if not other_active and not other_unresolved and not errors:
                    record = self._admit(schedule)
                    result = self._reconcile(record, now, beat, requests)
                    jobs.extend(result.jobs)
                    unresolved.update(result.unresolved)
                    errors.extend(result.errors)
            elif now >= schedule.next_start:
                if active or unresolved or errors:
                    skipped = (now - schedule.next_start) // self.timing.interval + 1
                    self._save_schedule(
                        schedule.model_copy(update={"next_start": schedule.next_start + skipped * self.timing.interval})
                    )
                    _events.labels("slot_held").inc()
                else:
                    # Missed whole slots are skipped. A partial slot keeps its original deadline.
                    skipped = (now - schedule.next_start) // self.timing.interval
                    schedule = schedule.model_copy(
                        update={"next_start": schedule.next_start + skipped * self.timing.interval}
                    )
                    record = self._admit(schedule)
                    result = self._reconcile(record, now, beat, requests)
                    jobs.extend(result.jobs)
                    unresolved.update(result.unresolved)
                    errors.extend(result.errors)
            for error in errors:
                _events.labels("error").inc()
                _logger.warning("round_observation_failed", error=error)
            return RoundTick(tuple(dict.fromkeys(jobs)), tuple(sorted(unresolved)), tuple(errors))
