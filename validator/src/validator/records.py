"""Version-one shared-file records; the host executor consumes their JSON with the stdlib."""

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Self
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import (
    UUID4,
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


def _relative_path(value: str) -> str:
    if "\\" in value or any(ord(c) < 32 for c in value) or any(p in ("", ".", "..") for p in value.split("/")):
        raise ValueError("expected a nonempty relative POSIX path without traversal")
    return value


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


type RelativePath = Annotated[str, AfterValidator(_relative_path)]
type UtcTime = Annotated[AwareDatetime, AfterValidator(_utc)]
type MinerHotkey = Annotated[str, StringConstraints(pattern=r"^[1-9A-HJ-NP-Za-km-z]{48}$")]
type Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
type BlockHash = Annotated[str, StringConstraints(pattern=r"^0x[0-9a-f]{64}$")]
type ImageReference = Annotated[
    str,
    StringConstraints(
        pattern=r"^(docker\.io|ghcr\.io)/[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+@sha256:[0-9a-f]{64}$",
        max_length=1024,
    ),
]
type JobKind = Literal["factory", "judge"]
type Score = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
type NonnegativeInt = Annotated[int, Field(ge=0)]


class Record(BaseModel):
    """Strict, immutable in-memory representation with explicit wire version."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, revalidate_instances="always")
    protocol_version: Literal[1] = 1

    @field_validator("protocol_version", mode="before")
    @classmethod
    def _integer_version(cls, value: object) -> int:
        if type(value) is not int:
            raise ValueError("protocol version must be an integer")
        return value


class Deadlines(Record):
    """Absolute wall-clock boundaries, independent of chain epochs."""

    start: UtcTime
    generation_end: UtcTime
    evaluation_start: UtcTime
    judge_end: UtcTime
    round_end: UtcTime

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if not self.start < self.generation_end < self.evaluation_start < self.judge_end < self.round_end:
            raise ValueError("stage deadlines must be strictly increasing")
        return self

    @classmethod
    def defaults(cls, start: datetime) -> Self:
        """Build the 60/5/55-minute schedule with a final five-minute stop reserve."""
        return cls(
            start=start,
            generation_end=start + timedelta(minutes=60),
            evaluation_start=start + timedelta(minutes=65),
            judge_end=start + timedelta(minutes=115),
            round_end=start + timedelta(minutes=120),
        )


class CohortMember(Record):
    """Frozen attribution and both execution IDs, reserved before publication."""

    miner_hotkey: MinerHotkey
    uid: NonnegativeInt
    image: ImageReference
    factory_job_id: UUID4 = Field(default_factory=uuid4)
    judge_job_id: UUID4 = Field(default_factory=uuid4)


class RoundPlan(Record):
    """Immutable portion of a round; timestamps alone never determine identity."""

    round_id: UUID4 = Field(default_factory=uuid4)
    sequence: NonnegativeInt
    deadlines: Deadlines
    discovery_block: NonnegativeInt
    discovery_block_hash: BlockHash
    membership_snapshot: Literal["commitment_block", "separate_reads"]
    cohort: tuple[CohortMember, ...]
    judge_image: ImageReference
    platform: Literal["linux/amd64"] = "linux/amd64"
    stop_grace_seconds: Annotated[int, Field(ge=0, le=60)] = 60
    specification_sha256: Sha256

    @model_validator(mode="after")
    def _unique_membership(self) -> Self:
        hotkeys = [m.miner_hotkey for m in self.cohort]
        uids = [m.uid for m in self.cohort]
        jobs = [job for m in self.cohort for job in (m.factory_job_id, m.judge_job_id)]
        if len(set(hotkeys)) != len(hotkeys) or len(set(uids)) != len(uids) or len(set(jobs)) != len(jobs):
            raise ValueError("cohort identities, UIDs and job IDs must be unique")
        return self

    @property
    def directory(self) -> str:
        """Canonical UTC location shared by host and validator mount roots."""
        start = self.deadlines.start
        return f"rounds/{start:%Y-%m-%d}/round-{self.sequence}-{start:%H-%M-%S}-{self.round_id}"


class RoundRecord(Record):
    """Persisted stage and unresolved execution, separate from usable scores."""

    plan: RoundPlan
    stage: Literal["generation", "stopping", "evaluation", "complete"] = "generation"
    unresolved_jobs: tuple[UUID4, ...] = ()
    completed_at: UtcTime | None = None

    @model_validator(mode="after")
    def _consistent_stage(self) -> Self:
        if (self.stage == "complete") != (self.completed_at is not None):
            raise ValueError("only a completed round has a completion time")
        jobs = {job for m in self.plan.cohort for job in (m.factory_job_id, m.judge_job_id)}
        if len(set(self.unresolved_jobs)) != len(self.unresolved_jobs) or not set(self.unresolved_jobs) <= jobs:
            raise ValueError("unresolved jobs must be distinct members of the frozen cohort")
        return self


class RoundLocation(Record):
    """Immutable discovery pointer, preventing reuse of a round ID at another date/path."""

    round_id: UUID4
    directory: RelativePath


class InputManifest(Record):
    """Read-only task.json available to factory and judge at /input/task.json."""

    round_id: UUID4
    miner_hotkey: MinerHotkey
    factory_job_id: UUID4
    judge_job_id: UUID4
    specification_sha256: Sha256


class JobIdentity(Record):
    """Common correlation fields; linkage is checked against a persisted request."""

    round_id: UUID4
    job_id: UUID4
    miner_hotkey: MinerHotkey
    kind: JobKind
    factory_job_id: UUID4

    @model_validator(mode="after")
    def _distinct_executions(self) -> Self:
        if (self.kind == "factory") != (self.job_id == self.factory_job_id):
            raise ValueError("factory identity must match itself; judge identity must differ")
        return self

    def same_job(self, other: JobIdentity) -> bool:
        """Compare all business attribution fields, ignoring observation metadata."""
        return all(
            a == b
            for a, b in zip(
                (self.round_id, self.job_id, self.miner_hotkey, self.kind, self.factory_job_id),
                (other.round_id, other.job_id, other.miner_hotkey, other.kind, other.factory_job_id),
                strict=True,
            )
        )

    @property
    def projection_id(self) -> UUID:
        """Stable Nexus result identity across new runtime contexts and timestamps."""
        return uuid5(NAMESPACE_URL, f"factory-horde:v1:{self.kind}:{self.job_id}")


class JobRequest(JobIdentity):
    """Immutable authorization with fixed mounts/commands, never arbitrary Docker args."""

    image: ImageReference
    platform: Literal["linux/amd64"]
    contract: Literal["factory-v1", "judge-v1"]
    input_dir: RelativePath
    output_dir: RelativePath
    report_dir: RelativePath | None
    created_at: UtcTime
    deadline: UtcTime
    stop_grace_seconds: Annotated[int, Field(ge=0, le=60)]

    @model_validator(mode="after")
    def _valid_contract(self) -> Self:
        if self.contract != f"{self.kind}-v1" or (self.report_dir is not None) != (self.kind == "judge"):
            raise ValueError("command/mount contract does not match job kind")
        if self.created_at >= self.deadline:
            raise ValueError("request must be created before its execution deadline")
        paths = [self.input_dir, self.output_dir, *([self.report_dir] if self.report_dir else [])]
        if any(a == b or a.startswith(b + "/") for i, a in enumerate(paths) for j, b in enumerate(paths) if i != j):
            raise ValueError("job mounts must be disjoint")
        return self

    @property
    def container_name(self) -> str:
        """Deterministic Docker identity; no timestamps or hotkeys enter the name."""
        return f"factory-horde-{self.job_id}"


class StopRequest(JobIdentity):
    """Permanent cancellation of future startup, retained after terminal confirmation."""

    requested_at: UtcTime
    reason: Literal["deadline", "operator", "recovery"]


class JobStatus(JobIdentity):
    """Executor observation, separating application result from execution safety."""

    state: Literal["pending", "preparing", "running", "stopping", "finished", "failed", "cancelled"]
    observed_at: UtcTime
    execution: Literal["unresolved", "running", "exited", "never_started"]
    startup_forbidden: bool
    container_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = None
    container_name: str | None = None
    started_at: UtcTime | None = None
    finished_at: UtcTime | None = None
    exit_code: int | None = None
    forced: bool = False
    oom_killed: bool = False
    reason: Annotated[str, StringConstraints(min_length=1, max_length=512)] | None = None

    @model_validator(mode="after")
    def _consistent_evidence(self) -> Self:
        if (self.container_id is None) != (self.container_name is None):
            raise ValueError("Docker ID and name must appear together")
        if self.container_name is not None and self.container_name != f"factory-horde-{self.job_id}":
            raise ValueError("container name does not match job identity")
        if self.execution in ("running", "exited") and self.container_id is None:
            raise ValueError("running/exited evidence needs Docker identity")
        if self.execution == "exited" and (self.exit_code is None or self.finished_at is None):
            raise ValueError("exited evidence needs finish time and exit code")
        if self.execution != "exited" and (
            self.exit_code is not None or self.finished_at is not None or self.forced or self.oom_killed
        ):
            raise ValueError("process outcome requires inspected exit evidence")
        if self.execution == "never_started" and (self.started_at is not None or self.exit_code is not None):
            raise ValueError("never-started evidence cannot claim a process outcome")
        if self.execution in ("never_started", "exited") and not self.startup_forbidden:
            raise ValueError("confirmed termination must permanently forbid startup")
        if self.state in ("pending", "preparing", "running", "stopping") and self.confirmed_stopped:
            raise ValueError("active state cannot carry terminal execution evidence")
        if self.state == "running" and self.execution != "running":
            raise ValueError("running state requires running Docker evidence")
        if self.state in ("finished", "cancelled") and not self.confirmed_stopped:
            raise ValueError("finished/cancelled requires confirmed stop evidence")
        if self.state == "finished" and not self.application_succeeded:
            raise ValueError("finished requires successful application exit")
        if self.state in ("failed", "cancelled") and self.reason is None:
            raise ValueError("failure/cancellation needs a reason")
        if self.finished_at is not None and self.finished_at > self.observed_at:
            raise ValueError("finish cannot be later than observation")
        if self.started_at is not None and (
            self.started_at > self.observed_at or self.finished_at is not None and self.started_at > self.finished_at
        ):
            raise ValueError("start cannot follow finish or observation")
        return self

    @property
    def confirmed_stopped(self) -> bool:
        """True only with permanent startup closure and terminal executor evidence."""
        return self.startup_forbidden and self.execution in ("exited", "never_started")

    @property
    def application_succeeded(self) -> bool:
        """A clean inspected exit differs from cancellation, timeout and host uncertainty."""
        return (
            self.state == "finished"
            and self.confirmed_stopped
            and self.execution == "exited"
            and self.exit_code == 0
            and not self.forced
            and not self.oom_killed
        )


class JudgeReport(JobIdentity):
    """Untrusted report, structurally parsed before task 9's acceptance gate."""

    kind: JobKind = "judge"
    checked_files: tuple[Literal["main.py", "README.md"], ...]
    score: Score | None
    failure: Annotated[str, StringConstraints(min_length=1, max_length=512)] | None

    @model_validator(mode="after")
    def _consistent_score(self) -> Self:
        if self.kind != "judge":
            raise ValueError("only a judge can produce an evaluation report")
        if (self.score is None) != (self.failure is not None):
            raise ValueError("report must contain either a successful score or a failure")
        if len(set(self.checked_files)) != len(self.checked_files):
            raise ValueError("file checks cannot be repeated")
        if self.score is not None and set(self.checked_files) != {"main.py", "README.md"}:
            raise ValueError("successful report requires both expected file checks")
        return self


class AcceptedResult(JobIdentity):
    """Immutable accepted score and projection metadata; no new randomness on replay."""

    kind: JobKind = "judge"
    score: Score
    accepted_at: UtcTime
    report_sha256: Sha256
    result_id: UUID
    processing_started: UtcTime
    processing_finished: UtcTime
    completion_block: NonnegativeInt
    completion_block_hash: BlockHash

    @model_validator(mode="after")
    def _stable_projection(self) -> Self:
        if self.kind != "judge":
            raise ValueError("only judge results carry an accepted score")
        if self.result_id != self.projection_id:
            raise ValueError("result ID does not match the business job")
        if not self.processing_started <= self.processing_finished <= self.accepted_at:
            raise ValueError("result timestamps are inconsistent")
        return self


def request_for(plan: RoundPlan, member: CohortMember, kind: JobKind) -> JobRequest:
    """Derive the only authorized immutable request for a frozen cohort execution.

    Raises:
        ValueError: If the member is not in the frozen cohort.
    """
    if member not in plan.cohort:
        raise ValueError("member is not in the frozen cohort")
    base = f"{plan.directory}/{member.miner_hotkey}"
    return JobRequest(
        round_id=plan.round_id,
        job_id=member.factory_job_id if kind == "factory" else member.judge_job_id,
        miner_hotkey=member.miner_hotkey,
        kind=kind,
        factory_job_id=member.factory_job_id,
        image=member.image if kind == "factory" else plan.judge_image,
        platform=plan.platform,
        contract="factory-v1" if kind == "factory" else "judge-v1",
        input_dir=f"{base}/input",
        output_dir=f"{base}/output",
        report_dir=f"{base}/evaluation" if kind == "judge" else None,
        created_at=plan.deadlines.start if kind == "factory" else plan.deadlines.evaluation_start,
        deadline=plan.deadlines.generation_end if kind == "factory" else plan.deadlines.judge_end,
        stop_grace_seconds=plan.stop_grace_seconds,
    )


def is_job_filename(name: str) -> bool:
    """Ignore staging files; only UUID-shaped final JSON names are discoverable."""
    return re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\.json", name) is not None
