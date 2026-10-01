"""Selected-version public API checks, without a live chain or private Nexus imports."""

from collections.abc import Generator, Mapping
from datetime import timedelta
from typing import override

import httpx
import pytest
from nexus.v1 import (
    ActorBuilder,
    BlockBeat,
    BlockCount,
    BlockHash,
    BlockNumber,
    CommunicatorActor,
    Context,
    ContextId,
    ContextStore,
    ExecutorCommunicator,
    Flow,
    Hotkey,
    InMemoryTaskResultStore,
    MechanismId,
    MessagesToSend,
    NetUid,
    NexusTask,
    NexusTaskName,
    NoopPayloadCreator,
    NoopRouter,
    PipeToBus,
    ProcessedInput,
    Producer,
    ProducerActor,
    PylonClientProvider,
    ReceiveEvent,
    RetryStrategy,
    Routed,
    SendEvent,
    SetWeightsBeat,
    SetWeightsBeatNode,
    SubnetBuilder,
    TaskResultStore,
    TaskResultStoreProvider,
    Timestamp,
    Weight,
    WeightsCalculationBundle,
    WeightSetterNode,
    get_epoch_containing_block,
)
from pylon_client.artanis import Config, IdentityName, PylonAuthToken, PylonClient

from validator.main import Settings, Validator


class StoreProvider(TaskResultStoreProvider[str, str, str]):
    """Keep the same test store visible to task and weighing components."""

    def __init__(self) -> None:
        self.store = InMemoryTaskResultStore[str, str, str]()

    @override
    def get_task_result_store(self) -> TaskResultStore[str, str, str]:
        return self.store


class DeferredActor(CommunicatorActor[str, str]):
    """Probe the deferred-completion extension point used by the file transport."""

    @override
    def handle_input(self, ctx: Context, event: ReceiveEvent[Routed[str]]) -> MessagesToSend:
        return ()

    def complete(self, ctx_id: ContextId) -> SendEvent[ProcessedInput[Routed[str], str]]:
        """Construct completion after the input handler has returned."""
        return self._processed_event(ctx_id, "observed")


class DeferredCommunicator(ExecutorCommunicator[str, str], ActorBuilder):
    """Test-only transport; never starts Docker or publishes a request."""

    @override
    def build_actor(self, *, pipe_to_bus: PipeToBus, context_store: ContextStore) -> DeferredActor:
        return DeferredActor(spec=self, pipe_to_bus=pipe_to_bus, context_store=context_store)


class TickActor(ProducerActor[str]):
    """One-item producer proving the public producer extension point."""

    @override
    def _produce(self) -> Generator[str]:
        yield "tick"


class TickNode(Producer[str], ActorBuilder):
    """A finite source for graph construction, without a scheduler thread."""

    @override
    def build_actor(self, *, pipe_to_bus: PipeToBus, context_store: ContextStore) -> TickActor:
        return TickActor(self, pipe_to_bus, context_store)


def test_public_task_composition_and_deferred_completion() -> None:
    provider = StoreProvider()
    tasks = [
        NexusTask(
            name=NexusTaskName(name),
            retry=RetryStrategy[str](f"{name}-retry", max_attempts=1, delay=timedelta()),
            payload_creator=NoopPayloadCreator[str](f"{name}-payload"),
            router=NoopRouter[str](f"{name}-router"),
            executor_communicator=DeferredCommunicator(f"{name}-files", str, str),
            executor_result_converter=NoopPayloadCreator[str](f"{name}-result"),
            task_result_store_provider=provider,
        )
        for name in ("factory", "judge")
    ]
    tick = TickNode("poll")
    factory, judge = tasks
    flow = Flow.from_connectable(tick).then(taps=[factory.input, judge.input])
    builder = SubnetBuilder(nodes=[tick, *(node for task in tasks for node in task.internal_nodes())])
    runtime = builder.add_flows(flow, *(task.internal_flow for task in tasks)).build()
    assert len(runtime.actors) == 1 + sum(len(task.internal_nodes()) for task in tasks)
    for task in tasks:
        assert task.successful_task_result_storer.task_result_store_provider is provider
        assert task.executor_failure_task_result_storer.task_result_store_provider is provider
    communicator = factory.executor_communicator
    assert isinstance(communicator, DeferredCommunicator)
    actor = communicator.build_actor(pipe_to_bus=builder.pipe_to_bus, context_store=builder.context_store)
    assert isinstance(factory.router, NoopRouter)
    router = factory.router.build_actor(pipe_to_bus=builder.pipe_to_bus, context_store=builder.context_store)
    with builder.context_store.create_context() as ctx:
        routed_events = router.handlers()[factory.router.input](
            ctx, ReceiveEvent(ctx_id=ctx.id, target=factory.router.input, payload="persisted-job-id")
        )
        assert isinstance(routed_events, tuple) and len(routed_events) == 1
        routed = routed_events[0].payload
        assert (
            actor.handlers()[communicator.input](
                ctx, ReceiveEvent(ctx_id=ctx.id, target=communicator.input, payload=routed)
            )
            == ()
        )
        ctx_id = ctx.id
    completion = actor.complete(ctx_id)
    assert completion.ctx_id == ctx_id
    assert completion.payload.input.input == "persisted-job-id"
    assert completion.payload.output == "observed"


class HttpProvider(PylonClientProvider):
    """Use real client serialization with HTTP intercepted by each test."""

    @override
    def get_client(self) -> PylonClient:
        return PylonClient(
            Config(
                address="http://pylon.test",
                identity_name=IdentityName("validator"),
                identity_token=PylonAuthToken("test"),
            )
        )


@pytest.mark.parametrize("mechanism_id", [MechanismId(0), MechanismId(1)])
def test_mechanism_reaches_status_and_write(monkeypatch: pytest.MonkeyPatch, mechanism_id: MechanismId) -> None:
    requests: list[httpx.Request] = []

    def send(_client: httpx.Client, request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/identities"):
            return httpx.Response(200, json={"identities": {"validator": 2}}, request=request)
        if request.url.path.endswith("/weights/status"):
            return httpx.Response(200, json={"weights_submitted": False}, request=request)
        return httpx.Response(200, json={}, request=request)

    monkeypatch.setattr(httpx.Client, "send", send)
    provider = HttpProvider()
    store_provider = StoreProvider()

    def weigh(bundle: WeightsCalculationBundle) -> Mapping[Hotkey, Weight]:
        assert bundle.tasks_result_store is store_provider.store
        return {Hotkey("miner"): Weight(1.0)}

    poll = SetWeightsBeatNode(
        "opportunity",
        netuid=NetUid(2),
        mechanism_id=mechanism_id,
        epoch_start_offset=BlockCount(0),
        pylon_client_provider=provider,
    )
    setter = WeightSetterNode(
        "setter",
        mechanism_id=mechanism_id,
        weighing_func=weigh,
        pylon_client_provider=provider,
        task_result_store_provider=store_provider,
    )
    builder = SubnetBuilder(nodes=[poll, setter])
    poller = poll.build_actor(pipe_to_bus=builder.pipe_to_bus, context_store=builder.context_store)
    writer = setter.build_actor(pipe_to_bus=builder.pipe_to_bus, context_store=builder.context_store)
    block = BlockBeat(BlockNumber(500), Timestamp(1_000), BlockHash("0x" + "0" * 64))
    with builder.context_store.create_context() as ctx:
        assert poller.handlers()[poll.block_beat](
            ctx, ReceiveEvent(ctx_id=ctx.id, target=poll.block_beat, payload=block)
        )
        events = writer.handlers()[setter.sink](
            ctx,
            ReceiveEvent(
                ctx_id=ctx.id,
                target=setter.sink,
                payload=SetWeightsBeat(
                    get_epoch_containing_block(block.block_number, netuid=NetUid(2)), block.block_number
                ),
            ),
        )
    assert isinstance(events, tuple) and events[0].source == setter.ok
    assert [(r.method, r.url.path) for r in requests if not r.url.path.endswith("/identities")] == [
        ("GET", f"/api/_unstable/identity/validator/subnet/2/mechanism/{mechanism_id}/block/500/weights/status"),
        ("PUT", f"/api/_unstable/identity/validator/subnet/2/mechanism/{mechanism_id}/weights"),
    ]
    assert requests[-1].content == b'{"weights":{"miner":1.0}}'


def test_scaffold_settings_and_graph_import() -> None:
    settings = Settings.model_validate(
        {
            "NETUID": 2,
            "MECHANISM_ID": 1,
            "pylon_service_address": "http://pylon.test",
            "pylon_open_access_token": "test",
        }
    )
    assert settings.mechanism_id == 1
    assert Validator(settings).settings is settings
