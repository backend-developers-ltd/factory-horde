"""Accept terminal evidence once; rebuild queries from the shared tree without rerunning work."""

import json
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

import structlog
from nexus.v1 import BlockBeat
from prometheus_client import Counter, Histogram
from pydantic import ValidationError

from validator.record_files import RecordConflictError, RecordFormatError, encode_record
from validator.records import AcceptedResult, JobRequest, JobStatus, RoundLocation, RoundPlan, request_for
from validator.result_records import CompletionBlock, JobResult
from validator.round_repository import RoundRepository

_logger = structlog.get_logger(__name__)
_events = Counter("factory_horde_result_operations_total", "Business result operations", ("operation", "outcome"))
_latency = Histogram("factory_horde_result_operation_seconds", "Business result operation latency", ("operation",))


class ResultNotReady(ValueError):
    """Missing or unresolved execution cannot become an immutable application outcome."""


class ResultRejected(ValueError):
    """A confirmed execution does not qualify as a successful task or accepted score."""


@contextmanager
def result_operation(operation: Literal["finalize", "query", "project", "rebuild"]) -> Generator[None]:
    """Measure result operations using bounded labels and explicit failure logging.

    Raises:
        ResultNotReady: The enclosed operation still awaits terminal evidence.
    """
    with _latency.labels(operation).time():
        try:
            yield
        except ResultNotReady:
            _events.labels(operation, "pending").inc()
            raise
        except Exception:
            _events.labels(operation, "error").inc()
            _logger.exception("result_operation_failed", operation=operation)
            raise
        else:
            _events.labels(operation, "ok").inc()


class ResultRepository:
    """One actor-shared repository; immutable decisions are the only source of accepted scores."""

    def __init__(self, rounds: RoundRepository):
        self.rounds = rounds
        self.files = rounds.files

    def authorize(self, request: JobRequest) -> RoundPlan:
        """Require the exact published request and frozen round attribution.

        Raises:
            RecordConflictError: The business request differs from durable authorization.
        """
        location = self.files.read(f"control/rounds/{request.round_id}.json", RoundLocation)
        plan = self.rounds.read_round(location.directory).plan
        member = next((m for m in plan.cohort if m.miner_hotkey == request.miner_hotkey), None)
        if (
            plan.round_id != request.round_id
            or member is None
            or request != request_for(plan, member, request.kind)
            or self.files.read(f"control/requests/{request.job_id}.json", JobRequest) != request
        ):
            raise RecordConflictError("Result request differs from the frozen published execution")
        return plan

    @staticmethod
    def result_path(request: JobRequest) -> str:
        """Place factory outcomes beside the judge's canonical result.json."""
        base = request.input_dir.rsplit("/", 1)[0]
        return f"{base}/" + ("factory-result.json" if request.kind == "factory" else "result.json")

    def read(self, request: JobRequest) -> JobResult | None:
        """Read one retained decision without re-evaluating mutable raw evidence.

        Raises:
            RecordConflictError: A retained result belongs to a different request.
        """
        try:
            result = self.files.read(self.result_path(request), JobResult)
        except FileNotFoundError:
            return None
        if result.request != request:
            raise RecordConflictError("Retained result conflicts with the immutable request")
        return result

    def _terminal(self, request: JobRequest) -> JobStatus:
        status = self.rounds.status(request)
        if status is None or not status.confirmed_stopped:
            raise ResultNotReady(f"Execution is not confirmed stopped: {request.job_id}")
        return status

    def _accept(
        self, request: JobRequest, plan: RoundPlan, status: JobStatus, block: CompletionBlock
    ) -> AcceptedResult:
        member = next(m for m in plan.cohort if m.miner_hotkey == request.miner_hotkey)
        factory = request_for(plan, member, "factory")
        self.authorize(factory)
        factory_status = self._terminal(factory)
        if not factory_status.application_succeeded:
            raise ResultRejected("Factory did not exit successfully")
        if (
            status.started_at is None
            or status.finished_at is None
            or factory_status.finished_at is None
            or factory_status.finished_at > status.started_at
            or status.started_at < request.created_at
        ):
            raise ResultRejected("Judge did not follow confirmed factory completion and evaluation start")
        try:
            report = self.rounds.report(request)
        except (FileNotFoundError, RecordFormatError, ValidationError, json.JSONDecodeError) as error:
            raise ResultRejected(f"Judge report rejected: {type(error).__name__}") from error
        if report.score is None:
            raise ResultRejected("Judge reported failure: " + (report.failure or "missing score"))
        return AcceptedResult(
            round_id=request.round_id,
            job_id=request.job_id,
            miner_hotkey=request.miner_hotkey,
            kind="judge",
            factory_job_id=request.factory_job_id,
            score=report.score,
            accepted_at=datetime.now(UTC),
            report_sha256=sha256(encode_record(report)).hexdigest(),
            result_id=request.projection_id,
            processing_started=status.started_at,
            processing_finished=status.finished_at,
            completion_block=block.number,
            completion_block_hash=block.hash,
        )

    def finalize(self, request: JobRequest, beat: BlockBeat) -> JobResult:
        """Persist one decision atomically; replay returns original timing, block and score.

        Raises:
            ResultNotReady: Terminal execution evidence is absent or unresolved.
        """
        with self.files.lock, result_operation("finalize"):
            plan = self.authorize(request)
            existing = self.read(request)
            if existing is not None:
                # Re-fsync identical publication after an earlier ambiguous durability failure.
                self.files.publish(self.result_path(request), existing)
                return existing
            status = self._terminal(request)
            completion = CompletionBlock.from_beat(beat)
            accepted = None
            failure = None if status.application_succeeded else status.reason or "Execution failed"
            if failure is None and request.kind == "judge":
                try:
                    accepted = self._accept(request, plan, status, completion)
                except ResultRejected as error:
                    failure = str(error)
            result = JobResult(
                request=request,
                status=status,
                result_id=request.projection_id,
                processing_started=status.started_at or status.observed_at,
                processing_finished=status.finished_at or status.observed_at,
                completion=completion,
                accepted=accepted,
                failure=failure,
            )
            self.files.publish(self.result_path(request), result)
            _logger.info(
                "job_result_finalized",
                job_id=str(request.job_id),
                kind=request.kind,
                outcome="failed" if failure else "successful",
            )
            return result

    def latest_usable_round(self) -> tuple[RoundPlan, tuple[AcceptedResult, ...]] | None:
        """Return the latest completed round with scores, skipping empty rounds independently of epoch."""
        with self.files.lock, result_operation("query"):
            for record in reversed(self.rounds.rounds()):
                if record.stage != "complete":
                    continue
                scores: list[AcceptedResult] = []
                for member in record.plan.cohort:
                    request = request_for(record.plan, member, "judge")
                    result = self.read(request)
                    if result is not None and result.accepted is not None:
                        scores.append(result.accepted)
                if scores:
                    return record.plan, tuple(scores)
        return None
