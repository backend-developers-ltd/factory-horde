"""Two public Nexus task compositions sharing one file transport and result provider."""

from datetime import timedelta

from nexus.v1 import NexusTask, NexusTaskName, NexusValidator, NoopPayloadCreator, NoopRouter, RetryStrategy

from validator.file_communicator import FileCommunicator, PollClock
from validator.records import JobRequest, JobStatus
from validator.response_logger import ErrorLoggerNode, MessageLoggerNode
from validator.result_repository import ResultRepository
from validator.result_store import (
    EVALUATION_TASK,
    FACTORY_TASK,
    Failure,
    FileResultStoreProvider,
    FileTaskResultStore,
    Success,
)

type JobTask = NexusTask[JobRequest, JobRequest, JobStatus, JobStatus]


class FileTasks:
    """Declare distinct one-attempt tasks; the round coordinator alone will supply business inputs."""

    def __init__(self, repository: ResultRepository, poll_interval: timedelta = timedelta(seconds=2)):
        self.store_provider = FileResultStoreProvider(FileTaskResultStore(repository))
        self.factory_files = FileCommunicator("factory", repository)
        self.judge_files = FileCommunicator("judge", repository)
        self.factory = self._task(FACTORY_TASK, self.factory_files)
        self.evaluation = self._task(EVALUATION_TASK, self.judge_files)
        self.poll_clock = PollClock(poll_interval)

    def _task(self, name: NexusTaskName, communicator: FileCommunicator) -> JobTask:
        return NexusTask(
            name=name,
            retry=RetryStrategy[JobRequest](f"{name}-retry", max_attempts=1, delay=timedelta()),
            payload_creator=NoopPayloadCreator[JobRequest](f"{name}-payload"),
            router=NoopRouter[JobRequest](f"{name}-router"),
            executor_communicator=communicator,
            executor_result_converter=NoopPayloadCreator[JobStatus](f"{name}-result"),
            task_result_store_provider=self.store_provider,
        )

    def connect(self, validator: NexusValidator, errors: ErrorLoggerNode) -> None:
        """Expose task endpoints to Nexus discovery and tap every error/clock source explicitly."""
        successes = MessageLoggerNode[Success]("factory-horde-task-successes")
        failures = MessageLoggerNode[Failure]("factory-horde-task-failures")
        statuses = MessageLoggerNode[JobStatus]("factory-horde-task-outputs")
        validator.connect(self.poll_clock.source, taps=(self.factory_files.poll, self.judge_files.poll))
        validator.connect(
            validator.subnet_clock.source, taps=(self.factory_files.block_beat, self.judge_files.block_beat)
        )
        for task in (self.factory, self.evaluation):
            validator.connect(task.successful_task_result, taps=(successes.sink,))
            validator.connect(task.executor_failure, taps=(failures.sink,))
            validator.connect(task.executor_output, taps=(statuses.sink,))
            validator.connect(task.error, errors.sink)
            for source in (
                task.payload_creator.error,
                task.router.error,
                task.executor_communicator.error,
                task.executor_result_converter.error,
                task.task_result_preparer.error,
                task.successful_task_result_storer.error,
                task.executor_failure_task_result_storer.error,
                task.retry.error,
            ):
                validator.connect(source, taps=(errors.sink,))
