"""Structured outcome listeners for FactoryHorde actor pipelines."""

from __future__ import annotations

from typing import override

import structlog
from nexus.v1 import (
    Actor,
    ActorBuilder,
    ConsumerActor,
    Context,
    ContextStore,
    NexusException,
    Node,
    NodeSinks,
    NodeSources,
    PipeToBus,
    Sink,
    SinkName,
)

logger = structlog.get_logger(__name__)


class MessageLoggerNode[T](Node, ActorBuilder):
    """Consume successful pipeline outcomes without leaving an unconnected source.

    sink sink: outcome to record at debug level
    """

    def __init__(self, _id: str) -> None:
        super().__init__(_id)
        self.sink = Sink[T](f"{self.id}-sink", owner_node=self)

    @override
    def sinks(self) -> NodeSinks:
        return NodeSinks(sinks={SinkName("sink"): self.sink})

    @override
    def sources(self) -> NodeSources:
        return NodeSources(sources={})

    @override
    def build_actor(self, *, pipe_to_bus: PipeToBus, context_store: ContextStore) -> Actor:
        return MessageLoggerActor[T](spec=self.sink, pipe_to_bus=pipe_to_bus, context_store=context_store)


class MessageLoggerActor[T](ConsumerActor[T]):
    """Record terminal pipeline outcomes through structlog."""

    @override
    def _consume(self, ctx: Context, payload: T) -> None:
        logger.debug("pipeline_outcome", context_id=str(ctx.id), outcome=str(payload))


class ErrorLoggerNode(Node, ActorBuilder):
    """Sink-only node that logs framework/internal errors emitted by upstream actors.

    sink sink: NexusException from any upstream actor's error source
    """

    sink: Sink[NexusException]

    def __init__(self, _id: str) -> None:
        super().__init__(_id)
        self.sink = Sink(f"{self.id}-sink", owner_node=self)

    @override
    def sinks(self) -> NodeSinks:
        return NodeSinks(sinks={SinkName("sink"): self.sink})

    @override
    def sources(self) -> NodeSources:
        return NodeSources(sources={})

    @override
    def build_actor(self, *, pipe_to_bus: PipeToBus, context_store: ContextStore) -> Actor:
        return ErrorLoggerActor(spec=self.sink, pipe_to_bus=pipe_to_bus, context_store=context_store)


class ErrorLoggerActor(ConsumerActor[NexusException]):
    """Logs framework-level errors received from any upstream actor's error source."""

    @override
    def _consume(self, ctx: Context, payload: NexusException) -> None:
        logger.error("pipeline_error", context_id=str(ctx.id), error=str(payload), exc_info=payload)
