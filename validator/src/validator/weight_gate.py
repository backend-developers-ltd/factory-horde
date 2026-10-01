"""Suppress empty application-weight opportunities before the public Nexus setter."""

from typing import override

import structlog
from nexus.v1 import (
    ActorBuilder,
    Context,
    ContextStore,
    MessagesToSend,
    PipeToBus,
    ReceiveEvent,
    SendEvent,
    SetWeightsBeat,
    Transform,
    TransformActor,
)
from prometheus_client import Counter, Histogram

from validator.weighing import NoUsableScores, Weigher

_logger = structlog.get_logger(__name__)
_events = Counter("factory_horde_weight_gate_total", "Weight opportunity outcomes", ("outcome",))
_latency = Histogram("factory_horde_weight_gate_seconds", "Usable-score and registration gate latency")


class WeightGate(Transform[SetWeightsBeat, SetWeightsBeat], ActorBuilder):
    """Check application history and membership before continuing the mandatory primary flow.

    sink sink: mechanism-specific weight opportunity
    source ok: unchanged opportunity, only when registered accepted scores exist
    source error: repository or membership failure; absence of scores is an ordinary skip
    """

    def __init__(self, weigher: Weigher):
        super().__init__("factory-horde-weight-result-gate")
        self.weigher = weigher

    @override
    def build_actor(self, *, pipe_to_bus: PipeToBus, context_store: ContextStore) -> WeightGateActor:
        return WeightGateActor(self, pipe_to_bus, context_store)


class WeightGateActor(TransformActor[SetWeightsBeat, SetWeightsBeat]):
    """The setter rechecks the same repository; passing this gate does not freeze an old calculation."""

    def __init__(self, spec: WeightGate, pipe_to_bus: PipeToBus, context_store: ContextStore):
        super().__init__(spec, pipe_to_bus, context_store)
        self.gate = spec

    @override
    def _transform(self, ctx: Context, payload: SetWeightsBeat) -> SetWeightsBeat:
        with _latency.time():
            self.gate.weigher.select()
        return payload

    @override
    def handle(self, ctx: Context, event: ReceiveEvent[SetWeightsBeat]) -> MessagesToSend:
        payload, error = self._process(ctx, event.payload)
        if isinstance(error, NoUsableScores):
            _events.labels("skipped").inc()
            _logger.info("weight_opportunity_skipped", reason=str(error))
            return ()
        if error is not None:
            _events.labels("error").inc()
            return SendEvent(ctx_id=ctx.id, source=self.gate.error, payload=error)
        if payload is None:
            raise RuntimeError("Weight gate returned neither a beat nor an error")
        _events.labels("passed").inc()
        return SendEvent(ctx_id=ctx.id, source=self.gate.ok, payload=payload)
