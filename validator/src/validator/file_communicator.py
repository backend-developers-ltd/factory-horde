"""Nonblocking Nexus transport over immutable executor requests and durable outcomes."""

from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import Any, override

import structlog
from nexus.v1 import (
    ActorBuilder,
    BlockBeat,
    CommunicatorActor,
    Context,
    ContextId,
    ContextStore,
    EventHandler,
    ExecutorCommunicator,
    MessagesToSend,
    NexusException,
    NodeSinks,
    PipeToBus,
    ProcessedInput,
    Producer,
    ProducerActor,
    ReceiveEvent,
    Routed,
    SendEvent,
    Sink,
    SinkName,
)
from prometheus_client import Counter, Histogram

from validator.records import JobKind, JobRequest, JobStatus, RoundLocation
from validator.result_repository import ResultNotReady, ResultRepository

_logger = structlog.get_logger(__name__)
_events = Counter("factory_horde_file_transport_total", "File communicator events", ("operation", "outcome"))
_latency = Histogram("factory_horde_file_transport_seconds", "File communicator handler latency", ("operation",))


class PollClock(Producer[datetime], ActorBuilder):
    """Actor-owned wall-clock producer; every tick receives a new Nexus context."""

    def __init__(self, interval: timedelta = timedelta(seconds=2)):
        super().__init__("factory-horde-file-poll-clock")
        if interval <= timedelta():
            raise ValueError("Polling interval must be positive")
        self.interval = interval

    @override
    def build_actor(self, *, pipe_to_bus: PipeToBus, context_store: ContextStore) -> PollClockActor:
        return PollClockActor(self, pipe_to_bus, context_store)


class PollClockActor(ProducerActor[datetime]):
    """The producer's managed thread stops promptly through its lifecycle event."""

    def __init__(self, spec: PollClock, pipe_to_bus: PipeToBus, context_store: ContextStore):
        super().__init__(spec, pipe_to_bus, context_store)
        self.clock = spec
        self.stopped = Event()

    @override
    def on_stop(self) -> None:
        self.stopped.set()

    @override
    def _produce(self) -> Generator[datetime]:
        while not self.stopped.is_set():
            yield datetime.now(UTC)
            self.stopped.wait(self.clock.interval.total_seconds())


@dataclass
class Subscription:
    """Disposable runtime subscription; authorization and outcomes stay on disk."""

    request: JobRequest
    consecutive_errors: int = 0


class FileCommunicator(ExecutorCommunicator[JobRequest, JobStatus], ActorBuilder):
    """Publish or reobserve a frozen business job, then complete on later poll ticks.

    sink input: routed immutable JobRequest for the configured kind
    sink poll: independent wall-clock tick; never a Docker wait
    sink block_beat: latest observed chain block for first result acceptance
    source processed: correlated terminal success or executor failure
    source error: rejected authorization or exhausted file-observation errors
    """

    def __init__(self, kind: JobKind, repository: ResultRepository, max_observation_errors: int = 5):
        super().__init__(f"factory-horde-{kind}-files", JobRequest, JobStatus)
        if max_observation_errors < 1:
            raise ValueError("Observation retry bound must be positive")
        self.kind = kind
        self.repository = repository
        self.max_observation_errors = max_observation_errors
        self.poll = Sink[datetime](f"{self.id}-poll", owner_node=self)
        self.block_beat = Sink[BlockBeat](f"{self.id}-block-beat", owner_node=self)

    @override
    def sinks(self) -> NodeSinks:
        return NodeSinks(
            sinks={SinkName("input"): self.input, SinkName("poll"): self.poll, SinkName("block_beat"): self.block_beat}
        )

    @override
    def build_actor(self, *, pipe_to_bus: PipeToBus, context_store: ContextStore) -> FileCommunicatorActor:
        return FileCommunicatorActor(self, pipe_to_bus, context_store)


class FileCommunicatorActor(CommunicatorActor[JobRequest, JobStatus]):
    """One actor owns subscriptions; no executor thread, HTTP request or subprocess is started."""

    def __init__(self, spec: FileCommunicator, pipe_to_bus: PipeToBus, context_store: ContextStore):
        super().__init__(spec=spec, pipe_to_bus=pipe_to_bus, context_store=context_store)
        self.transport = spec
        self.pending: dict[ContextId, Subscription] = {}
        self.latest_block: BlockBeat | None = None

    @override
    def handlers(self) -> dict[Sink[Any], EventHandler]:
        return {
            **super().handlers(),
            self.transport.input: self.receive,
            self.transport.poll: self.handle_poll,
            self.transport.block_beat: self.handle_block,
        }

    def receive(self, ctx: Context, event: ReceiveEvent[Routed[JobRequest]]) -> MessagesToSend:
        """Reject context reuse before Nexus updates its saved immutable input."""
        previous = self.pending.get(ctx.id)
        if previous is not None and previous.request != event.payload.input:
            del self.pending[ctx.id]
            _events.labels("input", "error").inc()
            return self._internal_error_event(ctx.id, NexusException("Context already belongs to a different job"))
        return super().handlers()[self.transport.input](ctx, event)

    @override
    def handle_input(self, ctx: Context, event: ReceiveEvent[Routed[JobRequest]]) -> MessagesToSend:
        with _latency.labels("input").time():
            request = event.payload.input
            try:
                if request.kind != self.transport.kind:
                    raise ValueError("Request kind differs from its Nexus task")
                rounds = self.transport.repository.rounds
                location = rounds.files.read(f"control/rounds/{request.round_id}.json", RoundLocation)
                plan = rounds.read_round(location.directory).plan
                rounds.publish_request(plan, request)
                self.pending[ctx.id] = Subscription(request)
            except (OSError, ValueError) as error:
                self.pending.pop(ctx.id, None)
                _events.labels("input", "error").inc()
                return self._internal_error_event(ctx.id, NexusException(str(error)))
            _events.labels("input", "subscribed").inc()
            return ()

    def handle_block(self, _ctx: Context, event: ReceiveEvent[BlockBeat]) -> MessagesToSend:
        """Keep readiness as an observation; a restart must receive another actual block."""
        self.latest_block = event.payload
        return ()

    def handle_poll(self, _ctx: Context, _event: ReceiveEvent[datetime]) -> MessagesToSend:
        """Read each job independently and emit on its original context after durable acceptance."""
        if self.latest_block is None:
            return ()
        events: list[SendEvent[ProcessedInput[Routed[JobRequest], JobStatus]] | SendEvent[NexusException]] = []
        with _latency.labels("poll").time():
            for context_id, subscription in tuple(self.pending.items()):
                try:
                    result = self.transport.repository.finalize(subscription.request, self.latest_block)
                except ResultNotReady:
                    subscription.consecutive_errors = 0
                    _events.labels("poll", "pending").inc()
                    continue
                except (OSError, ValueError) as error:
                    subscription.consecutive_errors += 1
                    _events.labels("poll", "error").inc()
                    _logger.warning(
                        "file_observation_failed",
                        job_id=str(subscription.request.job_id),
                        attempt=subscription.consecutive_errors,
                        error=str(error),
                    )
                    if subscription.consecutive_errors >= self.transport.max_observation_errors:
                        del self.pending[context_id]
                        events.append(self._internal_error_event(context_id, NexusException(str(error))))
                    continue
                del self.pending[context_id]
                if result.failure is None:
                    events.append(self._processed_event(context_id, result.status))
                    _events.labels("poll", "success").inc()
                else:
                    events.append(self._executor_error_event(context_id, NexusException(result.failure)))
                    _events.labels("poll", "failure").inc()
        return tuple(events)
