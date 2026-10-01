"""Deferred input, per-context completion, polling errors and task-composition contracts."""

from datetime import UTC, datetime, timedelta

import pytest
from nexus.v1 import ContextId, ExecutorFailureException, ReceiveEvent, Routed, SubnetBuilder

from validator.file_communicator import FileCommunicator, FileCommunicatorActor, PollClock
from validator.records import JobRequest, JobStatus
from validator.result_records import JobResult
from validator.tasks import FileTasks

from .test_results import BEAT, Pair
from .test_results import pair as pair


def build(pair: Pair) -> tuple[FileCommunicator, FileCommunicatorActor, SubnetBuilder]:
    node = FileCommunicator("factory", pair.repo, max_observation_errors=2)
    builder = SubnetBuilder(nodes=[node])
    return node, node.build_actor(pipe_to_bus=builder.pipe_to_bus, context_store=builder.context_store), builder


def subscribe(node: FileCommunicator, actor: FileCommunicatorActor, builder: SubnetBuilder, pair: Pair) -> ContextId:
    with builder.context_store.create_context() as ctx:
        assert (
            actor.handlers()[node.input](
                ctx, ReceiveEvent(ctx_id=ctx.id, target=node.input, payload=Routed(pair.factory, pair.target))
            )
            == ()
        )
        return ctx.id


def test_input_returns_without_waiting_and_poll_correlates_fresh_contexts(pair: Pair) -> None:
    node, actor, builder = build(pair)
    (pair.repo.files.root / f"control/requests/{pair.factory.job_id}.json").unlink()
    first = subscribe(node, actor, builder, pair)
    second = subscribe(node, actor, builder, pair)
    assert first != second and set(actor.pending) == {first, second}
    assert pair.repo.rounds.requests()[0].job_id == pair.factory.job_id
    with builder.context_store.create_context() as tick:
        pulse = ReceiveEvent(ctx_id=tick.id, target=node.poll, payload=datetime.now(UTC))
        assert actor.handle_poll(tick, pulse) == ()
        actor.handle_block(tick, ReceiveEvent(ctx_id=tick.id, target=node.block_beat, payload=BEAT))
        events = actor.handle_poll(tick, pulse)
        assert isinstance(events, tuple) and {event.ctx_id for event in events} == {first, second}
        assert all(event.ctx_id != tick.id for event in events)
        for event in events:
            assert event.source == node.processed
            assert event.payload.input.input == pair.factory
            assert event.payload.output == pair.factory_status
        assert not actor.pending
        assert actor.handle_poll(tick, pulse) == ()
    _, replacement, fresh = build(pair)
    # Use the replacement's own node through the public spec supplied by the helper.
    new_node = replacement.transport
    resumed = subscribe(new_node, replacement, fresh, pair)
    with fresh.context_store.create_context() as tick:
        replacement.handle_block(tick, ReceiveEvent(ctx_id=tick.id, target=new_node.block_beat, payload=BEAT))
        events = replacement.handle_poll(
            tick, ReceiveEvent(ctx_id=tick.id, target=new_node.poll, payload=datetime.now(UTC))
        )
        assert isinstance(events, tuple) and len(events) == 1 and events[0].ctx_id == resumed
        assert events[0].payload.output == pair.factory_status


def test_running_execution_stays_subscribed(pair: Pair) -> None:
    node, actor, builder = build(pair)
    context_id = subscribe(node, actor, builder, pair)
    running = JobStatus.model_validate(
        pair.factory_status.model_dump()
        | {
            "state": "running",
            "execution": "running",
            "finished_at": None,
            "exit_code": None,
            "startup_forbidden": False,
        }
    )
    pair.repo.files.replace(f"control/statuses/{pair.factory.job_id}.json", running)
    with builder.context_store.create_context() as tick:
        actor.handle_block(tick, ReceiveEvent(ctx_id=tick.id, target=node.block_beat, payload=BEAT))
        for _ in range(4):
            assert (
                actor.handle_poll(tick, ReceiveEvent(ctx_id=tick.id, target=node.poll, payload=datetime.now(UTC))) == ()
            )
    assert context_id in actor.pending and actor.pending[context_id].consecutive_errors == 0
    assert pair.repo.read(pair.factory) is None


def test_file_failure_has_bounded_observation_retries_without_false_terminal(
    pair: Pair, monkeypatch: pytest.MonkeyPatch
) -> None:
    node, actor, builder = build(pair)
    context_id = subscribe(node, actor, builder, pair)

    def unavailable(_request: JobRequest, _beat: object) -> JobResult:
        raise OSError("injected file read failure")

    monkeypatch.setattr(pair.repo, "finalize", unavailable)
    with builder.context_store.create_context() as tick:
        actor.handle_block(tick, ReceiveEvent(ctx_id=tick.id, target=node.block_beat, payload=BEAT))
        pulse = ReceiveEvent(ctx_id=tick.id, target=node.poll, payload=datetime.now(UTC))
        assert actor.handle_poll(tick, pulse) == ()
        events = actor.handle_poll(tick, pulse)
        assert isinstance(events, tuple) and len(events) == 1
        assert events[0].ctx_id == context_id and events[0].source == node.error
        assert not actor.pending
    assert pair.repo.read(pair.factory) is None


def test_terminal_execution_failure_uses_processed_branch(pair: Pair) -> None:
    node, actor, builder = build(pair)
    context_id = subscribe(node, actor, builder, pair)
    failed = pair.factory_status.model_copy(update={"state": "failed", "exit_code": 7, "reason": "fixture nonzero"})
    pair.repo.files.replace(f"control/statuses/{pair.factory.job_id}.json", failed)
    with builder.context_store.create_context() as tick:
        actor.handle_block(tick, ReceiveEvent(ctx_id=tick.id, target=node.block_beat, payload=BEAT))
        events = actor.handle_poll(tick, ReceiveEvent(ctx_id=tick.id, target=node.poll, payload=datetime.now(UTC)))
        assert isinstance(events, tuple) and len(events) == 1
        assert events[0].ctx_id == context_id and events[0].source == node.processed
        assert isinstance(events[0].payload.output, ExecutorFailureException)
    assert pair.repo.read(pair.factory) is not None


def test_reused_context_with_other_job_is_rejected_and_unsubscribed(pair: Pair) -> None:
    node, actor, builder = build(pair)
    context_id = subscribe(node, actor, builder, pair)
    with builder.context_store.get_context(context_id) as ctx:
        event = actor.handlers()[node.input](
            ctx, ReceiveEvent(ctx_id=ctx.id, target=node.input, payload=Routed(pair.judge, pair.target))
        )
        assert not isinstance(event, tuple) and event.source == node.error
    assert not actor.pending


def test_shared_provider_unique_tasks_and_interruptible_poll_clock(pair: Pair) -> None:
    tasks = FileTasks(pair.repo)
    assert tasks.factory.name != tasks.evaluation.name
    for task in (tasks.factory, tasks.evaluation):
        assert task.input.owner_task is task
        assert task.retry.max_attempts == 1
        assert task.successful_task_result_storer.task_result_store_provider is tasks.store_provider
        assert task.executor_failure_task_result_storer.task_result_store_provider is tasks.store_provider
    clock = PollClock(timedelta(milliseconds=1))
    builder = SubnetBuilder(nodes=[clock])
    actor = clock.build_actor(pipe_to_bus=builder.pipe_to_bus, context_store=builder.context_store)
    actor.on_stop()
    with pytest.raises(ValueError):
        PollClock(timedelta())
    assert actor.stopped.is_set()
