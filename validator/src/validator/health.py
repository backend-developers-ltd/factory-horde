"""File-only executor observations and disposable application readiness snapshots."""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from validator.records import NonnegativeInt, Record, UtcTime

type DockerOperation = Literal["container", "pull", "create", "start", "stop", "kill", "info"]
type Check = Literal[
    "chain", "records", "writable_paths", "executor", "docker", "reconciled", "job_observations", "coordinator"
]


class ExecutorHealth(Record):
    """A heartbeat is distinct from a daemon response and from per-job termination."""

    started_at: UtcTime
    observed_at: UtcTime
    pid: NonnegativeInt
    docker_observed_at: UtcTime
    docker_ok: bool
    docker_error: str | None
    reconciled: bool
    rejected_requests: NonnegativeInt
    active_workers: NonnegativeInt


class OperationCount(Record):
    """Executor counters reset when its process is replaced."""

    operation: DockerOperation
    outcome: Literal["ok", "error"]
    value: NonnegativeInt


class OperationHistogram(Record):
    """Cumulative standard-library histogram, forwarded without redrawing samples."""

    operation: DockerOperation
    sum: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    count: NonnegativeInt
    bounds: tuple[float | Literal["+Inf"], ...]
    buckets: tuple[NonnegativeInt, ...]

    @model_validator(mode="after")
    def _cumulative_buckets(self) -> Self:
        if (
            self.bounds != (0.1, 0.5, 1.0, 5.0, 30.0, 120.0, 600.0, "+Inf")
            or len(self.buckets) != len(self.bounds)
            or tuple(sorted(self.buckets)) != self.buckets
            or self.buckets[-1] != self.count
        ):
            raise ValueError("Invalid executor histogram buckets")
        return self


class ExecutorMetrics(Record):
    """Compatible metrics snapshot published atomically by the host executor."""

    observed_at: UtcTime
    pid: NonnegativeInt
    active_workers: NonnegativeInt
    factory_horde_executor_operations_total: tuple[OperationCount, ...]
    factory_horde_executor_operation_seconds: tuple[OperationHistogram, ...]


class Readiness(Record):
    """Rebuilt on actor ticks; no execution authorization depends on this projection."""

    observed_at: UtcTime
    checks: dict[Check, bool]
    errors: tuple[str, ...] = ()
    phase: Literal["idle", "generation", "stopping", "evaluation", "complete"] = "idle"
    active_jobs: NonnegativeInt = 0
    unresolved_jobs: NonnegativeInt = 0
    blocked_next_round: bool = False
    usable_scores: NonnegativeInt = 0
    usable_round_completed_at: UtcTime | None = None
    executor_observed_at: UtcTime | None = None
    docker_observed_at: UtcTime | None = None
    chain_observed_at: UtcTime | None = None
    coordinator_observed_at: UtcTime | None = None

    def current_checks(self, now: datetime, max_age: float, dispatch_enabled: bool) -> dict[str, bool]:
        """Fail closed when actor ticks stop, even if HTTP still answers."""
        checks: dict[str, bool] = {name: ok for name, ok in self.checks.items()}
        checks["monitor"] = fresh(self.observed_at, now, max_age)
        for name, observed in (
            ("executor", self.executor_observed_at),
            ("docker", self.docker_observed_at),
            ("chain", self.chain_observed_at),
        ):
            checks[name] = checks[name] and fresh(observed, now, max_age)
        if dispatch_enabled:
            checks["coordinator"] = checks["coordinator"] and fresh(self.coordinator_observed_at, now, max_age)
        return checks


def fresh(observed: datetime | None, now: datetime, max_age: float) -> bool:
    """Future-dated or absent evidence cannot establish current readiness."""
    return observed is not None and 0 <= (now - observed).total_seconds() <= max_age
