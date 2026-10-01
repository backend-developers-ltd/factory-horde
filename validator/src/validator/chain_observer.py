"""Persist chain connectivity and the configured dispatch mode."""

from datetime import UTC, datetime
from pathlib import Path
from typing import override

from nexus.v1 import ActorBuilder, BlockBeat, Context, ContextStore, PipeToBus, Transform, TransformActor
from prometheus_client import Counter, Histogram

from validator.record_files import RecordFiles
from validator.records import BlockHash, NonnegativeInt, Record, UtcTime

_events = Counter("factory_horde_chain_observations_total", "Observed chain beats", ("outcome",))
_latency = Histogram("factory_horde_chain_observation_seconds", "Chain observation persistence duration")


class ChainObservation(Record):
    """Last actual chain beat; this alone is not application/executor readiness."""

    observed_at: UtcTime
    block: NonnegativeInt
    block_hash: BlockHash
    mechanism_id: NonnegativeInt
    dispatch_enabled: bool = False


class ChainObserverNode(Transform[BlockBeat, ChainObservation], ActorBuilder):
    """Record the chain clock through the Nexus runtime, without dispatching miner work.

    sink sink: a real chain block beat
    source ok: persisted chain observation
    source error: publication failure, requiring the runtime's error listener
    """

    def __init__(self, data_root: Path, mechanism_id: int, dispatch_enabled: bool = False) -> None:
        super().__init__("factory-horde-chain-observer")
        self.files = RecordFiles(data_root)
        self.mechanism_id = mechanism_id
        self.dispatch_enabled = dispatch_enabled

    @override
    def build_actor(self, *, pipe_to_bus: PipeToBus, context_store: ContextStore) -> ChainObserverActor:
        return ChainObserverActor(spec=self, pipe_to_bus=pipe_to_bus, context_store=context_store)


class ChainObserverActor(TransformActor[BlockBeat, ChainObservation]):
    """Nexus owns the thread that publishes the last observed chain state."""

    def __init__(self, spec: ChainObserverNode, pipe_to_bus: PipeToBus, context_store: ContextStore) -> None:
        super().__init__(spec=spec, pipe_to_bus=pipe_to_bus, context_store=context_store)
        self.observer = spec

    @override
    def _transform(self, ctx: Context, payload: BlockBeat) -> ChainObservation:
        with _latency.time():
            try:
                observation = ChainObservation(
                    observed_at=datetime.now(UTC),
                    block=payload.block_number,
                    block_hash=payload.block_hash,
                    mechanism_id=self.observer.mechanism_id,
                    dispatch_enabled=self.observer.dispatch_enabled,
                )
                self.observer.files.replace("control/chain-observation.json", observation)
            except Exception:
                _events.labels("error").inc()
                raise
            _events.labels("ok").inc()
            return observation
