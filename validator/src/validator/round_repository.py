"""Canonical shared-tree round and job repository; no secondary database."""

from hashlib import sha256
from pathlib import Path

from validator.record_files import RecordConflictError, RecordFiles, RecordFormatError, decode_record
from validator.records import (
    InputManifest,
    JobIdentity,
    JobRequest,
    JobStatus,
    JudgeReport,
    RoundLocation,
    RoundPlan,
    RoundRecord,
    StopRequest,
    is_job_filename,
    request_for,
)


class RoundRepository:
    """One actor-shared repository rooted at the validator's mount of the host data tree."""

    def __init__(self, root: Path) -> None:
        self.files = RecordFiles(root)

    @staticmethod
    def _manifest(plan: RoundPlan, request: JobRequest) -> InputManifest:
        member = next(m for m in plan.cohort if m.miner_hotkey == request.miner_hotkey)
        return InputManifest(
            round_id=plan.round_id,
            miner_hotkey=member.miner_hotkey,
            factory_job_id=member.factory_job_id,
            judge_job_id=member.judge_job_id,
            specification_sha256=plan.specification_sha256,
        )

    def prepare_round(self, plan: RoundPlan, specification: str) -> RoundRecord:
        """Persist IDs/plan, then immutable complete inputs; never clear existing output.

        Raises:
            ValueError: The specification differs from its frozen hash.
            RecordConflictError: The same round identity has another plan.
        """
        content = specification.encode("utf-8")
        if sha256(content).hexdigest() != plan.specification_sha256:
            raise ValueError("specification differs from the frozen plan")
        with self.files.lock:
            self.files.publish(
                f"control/rounds/{plan.round_id}.json",
                RoundLocation(round_id=plan.round_id, directory=plan.directory),
            )
            try:
                record = self.read_round(plan.directory)
            except FileNotFoundError:
                record = RoundRecord(plan=plan)
                self.files.publish(f"{plan.directory}/round.json", record)
            if record.plan != plan:
                raise RecordConflictError("round identity already has a different plan")
            self.files.write_bytes(f"{plan.directory}/specification.md", content, immutable=True)
            for member in plan.cohort:
                request = request_for(plan, member, "factory")
                self.files.write_bytes(f"{request.input_dir}/specification.md", content, immutable=True)
                self.files.publish(f"{request.input_dir}/task.json", self._manifest(plan, request))
                self.files.mkdir(request.output_dir)
                self.files.mkdir(f"{plan.directory}/{member.miner_hotkey}/evaluation")
            for control in ("requests", "stops", "statuses"):
                self.files.mkdir(f"control/{control}")
            return record

    def read_round(self, directory: str) -> RoundRecord:
        """Load stage/plan, verifying identity against the canonical location.

        Raises:
            RecordFormatError: The record belongs in a different directory.
        """
        record = self.files.read(f"{directory}/round.json", RoundRecord)
        if record.plan.directory != directory:
            raise RecordFormatError("round identity does not match its directory")
        return record

    def rounds(self) -> tuple[RoundRecord, ...]:
        """Load discoverable rounds for restart reconciliation, ignoring staging files.

        Raises:
            RecordFormatError: A round pointer disagrees with its filename or target.
        """
        try:
            names = self.files.list_names("control/rounds")
        except FileNotFoundError:
            return ()
        result: list[RoundRecord] = []
        for name in names:
            if not is_job_filename(name):
                continue
            location = self.files.read(f"control/rounds/{name}", RoundLocation)
            if name != f"{location.round_id}.json":
                raise RecordFormatError("round pointer identity differs from filename")
            try:
                record = self.read_round(location.directory)
            except FileNotFoundError:
                # Pointer publication may precede a crash; no request is authorized yet.
                continue
            if record.plan.round_id != location.round_id:
                raise RecordFormatError("round pointer identity differs from target")
            result.append(record)
        return tuple(sorted(result, key=lambda r: (r.plan.deadlines.start, r.plan.sequence, r.plan.round_id)))

    def save_round(self, record: RoundRecord) -> None:
        """Atomically change stage/evidence while preserving the immutable cohort plan.

        Raises:
            RecordConflictError: The frozen plan was changed.
        """
        with self.files.lock:
            current = self.read_round(record.plan.directory)
            if current.plan != record.plan:
                raise RecordConflictError("cannot change a frozen round plan")
            self.files.replace(f"{record.plan.directory}/round.json", record)

    def publish_request(self, plan: RoundPlan, request: JobRequest) -> None:
        """Require frozen attribution and complete inputs before external visibility.

        Raises:
            RecordConflictError: The request or its inputs differ from the persisted plan.
        """
        with self.files.lock:
            if self.read_round(plan.directory).plan != plan:
                raise RecordConflictError("request does not use the persisted round plan")
            member = next((m for m in plan.cohort if m.miner_hotkey == request.miner_hotkey), None)
            if member is None or request != request_for(plan, member, request.kind):
                raise RecordConflictError("request differs from its frozen cohort execution")
            specification = self.files.read_bytes(f"{request.input_dir}/specification.md")
            if sha256(specification).hexdigest() != plan.specification_sha256:
                raise RecordConflictError("incomplete or changed specification")
            manifest = self.files.read(f"{request.input_dir}/task.json", InputManifest)
            if manifest != self._manifest(plan, request):
                raise RecordConflictError("task manifest differs from its frozen cohort")
            self.files.list_names(request.output_dir)
            if request.report_dir is not None:
                self.files.list_names(request.report_dir)
            self.files.publish(f"control/requests/{request.job_id}.json", request)

    def requests(self) -> tuple[JobRequest, ...]:
        """Discover committed requests; partial temporary files never become jobs.

        Raises:
            RecordFormatError: A committed filename disagrees with its job identity.
        """
        result: list[JobRequest] = []
        for name in self.files.list_names("control/requests"):
            if not is_job_filename(name):
                continue
            record = self.files.read(f"control/requests/{name}", JobRequest)
            if name != f"{record.job_id}.json":
                raise RecordFormatError("request identity does not match its filename")
            result.append(record)
        return tuple(result)

    def publish_stop(self, request: JobRequest, stop: StopRequest) -> None:
        """Retain permanent stop intent; conflicting replay is an error.

        Raises:
            RecordConflictError: The stop refers to a different request.
        """
        self._check_job(request, stop)
        persisted = self.files.read(f"control/requests/{request.job_id}.json", JobRequest)
        if persisted != request:
            raise RecordConflictError("stop must refer to the persisted request")
        self.files.publish(f"control/stops/{request.job_id}.json", stop)

    def status(self, request: JobRequest) -> JobStatus | None:
        """Missing status means no observation; it never means confirmed stop."""
        try:
            status = self.files.read(f"control/statuses/{request.job_id}.json", JobStatus)
        except FileNotFoundError:
            return None
        self._check_job(request, status)
        return status

    def report(self, request: JobRequest) -> JudgeReport:
        """Parse the linked judge report; this does not yet authorize score acceptance.

        Raises:
            ValueError: A factory request has no report.
        """
        if request.report_dir is None:
            raise ValueError("only judges have a report directory")
        report = decode_record(self.files.read_artifact(f"{request.report_dir}/report.json"), JudgeReport)
        self._check_job(request, report)
        return report

    @staticmethod
    def _check_job(request: JobRequest, record: JobIdentity) -> None:
        if not request.same_job(record):
            raise RecordFormatError("record attribution does not match the expected job")
