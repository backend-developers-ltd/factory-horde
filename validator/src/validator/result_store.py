"""Public Nexus result-store adapter over immutable shared-tree business outcomes."""

from bisect import bisect_left, bisect_right, insort
from copy import deepcopy
from typing import override
from uuid import UUID

from nexus.v1 import (
    BlockBeat,
    BlockHash,
    BlockNumber,
    Context,
    Epoch,
    ExecutorFailureException,
    ExecutorFailureTaskResult,
    ExecutorFailureTaskResultToPersist,
    Neuron,
    NexusException,
    NexusTaskName,
    SuccessfulTaskResult,
    SuccessfulTaskResultToPersist,
    TaskResultId,
    TaskResultNotFoundException,
    TaskResultStore,
    TaskResultStoreProvider,
    Timestamp,
)

from validator.record_files import RecordConflictError, RecordFormatError
from validator.records import JobRequest, JobStatus, is_job_filename
from validator.result_records import JobResult, ResultProjection
from validator.result_repository import ResultRejected, ResultRepository, result_operation

FACTORY_TASK = NexusTaskName("factory-horde-factory")
EVALUATION_TASK = NexusTaskName("factory-horde-evaluation")
type Success = SuccessfulTaskResult[JobRequest, JobStatus, JobStatus]
type Failure = ExecutorFailureTaskResult[JobRequest]
type Entry = Success | Failure


def task_name_for(request: JobRequest) -> NexusTaskName:
    """Business job kind fixes its Nexus category across contexts and restarts."""
    return FACTORY_TASK if request.kind == "factory" else EVALUATION_TASK


class FileTaskResultStore(TaskResultStore[JobRequest, JobStatus, JobStatus]):
    """Rebuildable by-ID and ordered block indexes; scores remain in the business result file."""

    def __init__(self, repository: ResultRepository):
        self.repository = repository
        self.files = repository.files
        self.by_id: dict[tuple[NexusTaskName, TaskResultId], Entry] = {}
        self.by_block: dict[NexusTaskName, list[tuple[int, str]]] = {}
        self.rebuild()

    @staticmethod
    def _entry(result: JobResult, projection: ResultProjection) -> Entry:
        block = BlockBeat(
            BlockNumber(result.completion.number),
            Timestamp(result.completion.timestamp),
            BlockHash(result.completion.hash),
        )
        if result.failure is not None:
            return ExecutorFailureTaskResult(
                id=TaskResultId(result.result_id),
                processing_started=result.processing_started,
                processing_finished=result.processing_finished,
                block_at_finish=block,
                executor_payload=result.request,
                target=projection.target.model_copy(deep=True),
                executor_failure=ExecutorFailureException(NexusException(result.failure)),
            )
        return SuccessfulTaskResult(
            id=TaskResultId(result.result_id),
            processing_started=result.processing_started,
            processing_finished=result.processing_finished,
            block_at_finish=block,
            executor_payload=result.request,
            target=projection.target.model_copy(deep=True),
            executor_output=result.status,
            executor_public_output=result.status,
        )

    def _read_projection(self, projection: ResultProjection) -> Entry:
        result = self.files.read(projection.result_path, JobResult)
        self.repository.authorize(result.request)
        if projection.result_id != result.result_id or projection.result_path != self.repository.result_path(
            result.request
        ):
            raise RecordFormatError("Projection does not reference its canonical business result")
        return self._entry(result, projection)

    def rebuild(self) -> None:
        """Atomically reconstruct in-memory query indexes from durable references.

        Raises:
            RecordFormatError: A projection filename conflicts with its stable identity.
        """
        with self.files.lock, result_operation("rebuild"):
            self.files.mkdir("control/projections")
            entries: dict[tuple[NexusTaskName, TaskResultId], Entry] = {}
            blocks: dict[NexusTaskName, list[tuple[int, str]]] = {}
            for filename in self.files.list_names("control/projections"):
                if not is_job_filename(filename):
                    continue
                projection = self.files.read(f"control/projections/{filename}", ResultProjection)
                if filename != f"{projection.result_id}.json":
                    raise RecordFormatError("Projection filename differs from stable result identity")
                entry = self._read_projection(projection)
                name = task_name_for(entry.executor_payload)
                entries[(name, entry.id)] = entry
                blocks.setdefault(name, []).append((entry.block_at_finish.block_number, str(entry.id)))
            for ordered in blocks.values():
                ordered.sort()
            self.by_id, self.by_block = entries, blocks

    def _save(
        self,
        ctx: Context,
        name: NexusTaskName,
        request: JobRequest,
        target: Neuron,
        beat: BlockBeat,
        successful: bool,
        observed: JobStatus | None,
    ) -> Entry:
        if name != task_name_for(request):
            raise RecordConflictError("Task category differs from business job kind")
        with self.files.lock, result_operation("project"):
            result = self.repository.finalize(request, beat)
            if (result.failure is None) != successful:
                raise ResultRejected(result.failure or "Confirmed successful work cannot become an executor failure")
            if observed is not None and observed != result.status:
                raise RecordConflictError("Framework output conflicts with the retained executor evidence")
            path = f"control/projections/{result.result_id}.json"
            try:
                projection = self.files.read(path, ResultProjection)
            except FileNotFoundError:
                projection = ResultProjection(
                    result_id=result.result_id, result_path=self.repository.result_path(request), target=target
                )
            entry = self._read_projection(projection)
            if entry.executor_payload != request:
                raise RecordConflictError("Projection request conflicts with the business execution")
            self.files.publish(path, projection)
            key = (name, entry.id)
            if key not in self.by_id:
                insort(self.by_block.setdefault(name, []), (entry.block_at_finish.block_number, str(entry.id)))
            self.by_id[key] = entry
            ctx.append_user_note(f"Persisted {name} result {entry.id} for business job {request.job_id}")
            return deepcopy(entry)

    @override
    def add_successful_task_result(
        self,
        ctx: Context,
        task_name: NexusTaskName,
        result: SuccessfulTaskResultToPersist[JobRequest, JobStatus, JobStatus],
    ) -> Success:
        """Validate real durable success and project it with original metadata.

        Raises:
            RecordConflictError: Converted framework output differs from its observation.
            ResultRejected: The canonical decision is a failure.
        """
        execution = result.result
        observed = execution.executor_output.output
        if not isinstance(observed, JobStatus) or result.executor_public_output != observed:
            raise RecordConflictError("Successful task requires an unchanged attributed JobStatus")
        entry = self._save(
            ctx,
            task_name,
            execution.executor_output.input.input,
            execution.executor_output.input.target,
            execution.block_at_finish,
            True,
            observed,
        )
        if not isinstance(entry, SuccessfulTaskResult):
            raise ResultRejected("Result is not successful")
        return entry

    @override
    def add_executor_failure(
        self, ctx: Context, task_name: NexusTaskName, result: ExecutorFailureTaskResultToPersist[JobRequest]
    ) -> Failure:
        """Persist a classified terminal failure; framework exceptions cannot invent failure facts.

        Raises:
            ResultRejected: The canonical decision is a success.
        """
        execution = result.result
        entry = self._save(
            ctx,
            task_name,
            execution.executor_output.input.input,
            execution.executor_output.input.target,
            execution.block_at_finish,
            False,
            None,
        )
        if not isinstance(entry, ExecutorFailureTaskResult):
            raise ResultRejected("Result is not an executor failure")
        return entry

    @override
    def get_task_result(self, task_name: NexusTaskName, task_result_id: TaskResultId) -> Entry:
        """Fetch a stable business result by category and ID.

        Raises:
            TaskResultNotFoundException: No projection exists for this name/ID pair.
        """
        with self.files.lock, result_operation("query"):
            entry = self.by_id.get((task_name, task_result_id))
            if entry is None:
                raise TaskResultNotFoundException(task_name, task_result_id)
            return deepcopy(entry)

    def _epoch(self, name: NexusTaskName, epoch: Epoch) -> tuple[Entry, ...]:
        blocks = self.by_block.get(name, [])
        first = bisect_left(blocks, (epoch.first_block, ""))
        last = bisect_right(blocks, (epoch.last_block, "\uffff"))
        return tuple(self.by_id[(name, TaskResultId(UUID(identity)))] for _, identity in blocks[first:last])

    @override
    def get_successful_tasks_for_epoch(self, task_name: NexusTaskName, epoch: Epoch) -> tuple[Success, ...]:
        """Return successes ordered by original completion block using a range index."""
        with self.files.lock, result_operation("query"):
            return deepcopy(tuple(e for e in self._epoch(task_name, epoch) if isinstance(e, SuccessfulTaskResult)))

    @override
    def get_executor_failures_for_epoch(self, task_name: NexusTaskName, epoch: Epoch) -> tuple[Failure, ...]:
        """Return failures ordered by original completion block, never live uncertainty."""
        with self.files.lock, result_operation("query"):
            return deepcopy(tuple(e for e in self._epoch(task_name, epoch) if isinstance(e, ExecutorFailureTaskResult)))


class FileResultStoreProvider(TaskResultStoreProvider[JobRequest, JobStatus, JobStatus]):
    """Inject the same thread-safe store into both Nexus tasks and the eventual weight setter."""

    def __init__(self, store: FileTaskResultStore):
        self.store = store

    @override
    def get_task_result_store(self) -> FileTaskResultStore:
        """Return the shared adapter; no background runtime or second database is started."""
        return self.store
