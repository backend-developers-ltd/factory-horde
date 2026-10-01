"""Nexus-owned scheduling and independently correlated file-task subscriptions."""

from datetime import UTC, datetime, timedelta
from typing import Any, override
from uuid import UUID

from nexus.v1 import (
    ActorBuilder,
    BlockBeat,
    Context,
    ContextStore,
    EventHandler,
    MessagesToSend,
    NexusException,
    NodeSinks,
    NodeSources,
    PipeToBus,
    ReceiveEvent,
    SendEvent,
    Sink,
    SinkName,
    Source,
    SourceName,
    Transform,
    TransformActor,
)

from validator.coordinator import RoundCoordinator, RoundTick
from validator.records import JobRequest


class RoundCoordinatorNode(Transform[datetime, RoundTick], ActorBuilder):
    """Reconcile on wall-clock ticks; publish each job on a separate context.

    sink sink: wall-clock wakeup
    sink block_beat: actual chain observation after this runtime started
    source factory: durably authorized factory job to the factory task primary
    source evaluation: gated judge job to the evaluation task primary
    source ok: disposable reconciliation summary
    source error: explicit file/discovery failure; later ticks reconcile unchanged identities
    """

    def __init__(self, coordinator: RoundCoordinator):
        super().__init__("factory-horde-round-coordinator")
        self.coordinator = coordinator
        self.block_beat = Sink[BlockBeat](f"{self.id}-block", owner_node=self)
        self.factory = Source[JobRequest](f"{self.id}-factory", owner_node=self)
        self.evaluation = Source[JobRequest](f"{self.id}-evaluation", owner_node=self)

    @override
    def sinks(self) -> NodeSinks:
        return NodeSinks({**super().sinks().sinks, SinkName("block_beat"): self.block_beat})

    @override
    def sources(self) -> NodeSources:
        return NodeSources(
            {
                **super().sources().sources,
                SourceName("factory"): self.factory,
                SourceName("evaluation"): self.evaluation,
            }
        )

    @override
    def build_actor(self, *, pipe_to_bus: PipeToBus, context_store: ContextStore) -> RoundCoordinatorActor:
        return RoundCoordinatorActor(self, pipe_to_bus, context_store)


class RoundCoordinatorActor(TransformActor[datetime, RoundTick]):
    """Lost contexts are disposable; subscriptions retry existing jobs without new executions."""

    def __init__(self, spec: RoundCoordinatorNode, pipe_to_bus: PipeToBus, context_store: ContextStore):
        super().__init__(spec, pipe_to_bus, context_store)
        self.coordinator_node = spec
        self.latest_block: BlockBeat | None = None
        self.resubscribe_after: dict[UUID, datetime] = {}

    @override
    def handlers(self) -> dict[Sink[Any], EventHandler]:
        return {**super().handlers(), self.coordinator_node.block_beat: self.handle_block}

    def handle_block(self, _ctx: Context, event: ReceiveEvent[BlockBeat]) -> MessagesToSend:
        """A retained heartbeat never substitutes for a real beat in this runtime."""
        self.latest_block = event.payload
        return ()

    @override
    def _transform(self, ctx: Context, payload: datetime) -> RoundTick:
        # Backlogged poll messages must not move a persisted stage backwards.
        return self.coordinator_node.coordinator.tick(datetime.now(UTC), self.latest_block)

    @override
    def handle(self, ctx: Context, event: ReceiveEvent[datetime]) -> MessagesToSend:
        outcome, error = self._process(ctx, event.payload)
        node = self.coordinator_node
        if error is not None:
            return SendEvent(ctx_id=ctx.id, source=node.error, payload=error)
        if outcome is None:
            raise RuntimeError("Coordinator returned neither an observation nor an error")
        events: list[SendEvent[RoundTick] | SendEvent[JobRequest] | SendEvent[NexusException]] = [
            SendEvent(ctx_id=ctx.id, source=node.ok, payload=outcome)
        ]
        now = datetime.now(UTC)
        outstanding = {job.job_id for job in outcome.jobs}
        self.resubscribe_after = {job: due for job, due in self.resubscribe_after.items() if job in outstanding}
        for job in outcome.jobs:
            if self.latest_block is None or now < self.resubscribe_after.get(job.job_id, now):
                continue
            with self.context_store.create_context() as execution:
                events.append(
                    SendEvent(
                        ctx_id=execution.id,
                        source=node.factory if job.kind == "factory" else node.evaluation,
                        payload=job,
                    )
                )
            self.resubscribe_after[job.job_id] = now + timedelta(seconds=60)
        for message in outcome.errors:
            with self.context_store.create_context() as failure:
                events.append(SendEvent(ctx_id=failure.id, source=node.error, payload=NexusException(message)))
        return tuple(events)
