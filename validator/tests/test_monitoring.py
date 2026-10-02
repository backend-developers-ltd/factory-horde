"""Readiness must distinguish liveness, durable execution safety and write availability."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from nexus.v1 import NexusException, ReceiveEvent, SubnetBuilder
from prometheus_client import CollectorRegistry, generate_latest

from validator.health import ExecutorHealth, ExecutorMetrics, OperationCount, OperationHistogram
from validator.monitoring import HealthCollector, HealthSampler, MonitoringNode
from validator.records import request_for
from validator.result_repository import ResultRepository
from validator.round_repository import RoundRepository

from .test_coordinator import START, Fixture
from .test_results import BEAT


def executor_health(sampler: HealthSampler, now: datetime, *, docker_ok: bool = True) -> None:
    files = sampler.repository.files
    files.mkdir("control/projections")
    files.mkdir("control/requests")
    files.replace(
        "control/executor-health.json",
        ExecutorHealth(
            started_at=now,
            observed_at=now,
            pid=123,
            docker_observed_at=now,
            docker_ok=docker_ok,
            docker_error=None if docker_ok else "daemon unavailable",
            reconciled=True,
            rejected_requests=0,
            active_workers=0,
        ),
    )
    files.replace(
        "control/executor-metrics.json",
        ExecutorMetrics(
            observed_at=now,
            pid=123,
            active_workers=0,
            factory_horde_executor_operations_total=(OperationCount(operation="pull", outcome="ok", value=1),),
            factory_horde_executor_operation_seconds=(
                OperationHistogram(
                    operation="pull",
                    sum=0.75,
                    count=1,
                    bounds=(0.1, 0.5, 1.0, 5.0, 30.0, 120.0, 600.0, "+Inf"),
                    buckets=(0, 0, 1, 1, 1, 1, 1, 1),
                ),
            ),
        ),
    )


@pytest.fixture
def sampler(tmp_path: Path) -> HealthSampler:
    result = HealthSampler(ResultRepository(RoundRepository(tmp_path)), False, 30)
    executor_health(result, START)
    result.chain_at = START
    return result


def test_fresh_readiness_and_metrics_forward_executor_histograms(sampler: HealthSampler) -> None:
    now = datetime.now(UTC)
    executor_health(sampler, now)
    sampler.chain_at = now
    snapshot = sampler.sample(now)
    assert all(snapshot.current_checks(now, 30, False).values())
    assert snapshot.phase == "idle" and snapshot.usable_scores == 0
    registry = CollectorRegistry()
    registry.register(HealthCollector(sampler))
    metrics = generate_latest(registry).decode()
    assert 'factory_horde_executor_operations_total{operation="pull",outcome="ok"} 1.0' in metrics
    assert 'factory_horde_executor_operation_seconds_bucket{le="+Inf",operation="pull"} 1.0' in metrics
    assert "factory_horde_ready 1.0" in metrics
    assert "job_id=" not in metrics and "hotkey=" not in metrics and "round_id=" not in metrics


def test_stale_executor_and_failed_or_stale_docker_are_distinct(sampler: HealthSampler) -> None:
    sampler.chain_at = START + timedelta(seconds=40)
    stale = sampler.sample(sampler.chain_at)
    assert not stale.checks["executor"] and not stale.checks["docker"] and stale.checks["chain"]
    executor_health(sampler, sampler.chain_at, docker_ok=False)
    failed = sampler.sample(sampler.chain_at)
    assert failed.checks["executor"] and not failed.checks["docker"]
    assert "docker: daemon unavailable" in failed.errors
    executor_health(sampler, sampler.chain_at)
    files = sampler.repository.files
    health = files.read("control/executor-health.json", ExecutorHealth)
    files.replace("control/executor-health.json", health.model_copy(update={"docker_observed_at": START}))
    stale_docker = sampler.sample(sampler.chain_at)
    assert stale_docker.checks["executor"] and not stale_docker.checks["docker"]


def test_incompatible_or_malformed_records_fail_readiness(sampler: HealthSampler) -> None:
    files = sampler.repository.files
    files.write_bytes("control/executor-health.json", b'{"protocol_version":2}', immutable=False)
    assert not sampler.sample(START).checks["executor"]
    executor_health(sampler, START)
    files.write_bytes("control/requests/11111111-1111-4111-8111-111111111111.json", b"{}", immutable=False)
    assert not sampler.sample(START).checks["records"]


def test_blocked_next_round_and_stale_job_observations_are_visible(sampler: HealthSampler) -> None:
    fixture = Fixture(sampler.repository, count=1)
    plan = fixture.start()
    fixture.status(request_for(plan, plan.cohort[0], "factory"), finished=False)
    fixture.coordinator().tick(plan.deadlines.round_end, BEAT)
    executor_health(sampler, plan.deadlines.round_end)
    sampler.chain_at = plan.deadlines.round_end
    snapshot = sampler.sample(plan.deadlines.round_end)
    assert snapshot.blocked_next_round and snapshot.active_jobs == snapshot.unresolved_jobs == 1
    assert snapshot.phase == "complete"
    assert not snapshot.checks["reconciled"] and not snapshot.checks["job_observations"]


def test_actual_result_projection_write_failure_and_repair(sampler: HealthSampler) -> None:
    path = sampler.repository.files.root / "control/projections"
    path.chmod(0o500)
    try:
        failed = sampler.sample(START)
        assert not failed.checks["writable_paths"]
        assert any("writable_paths:" in error for error in failed.errors)
    finally:
        path.chmod(0o750)
    assert all(sampler.sample(START).checks.values())


def test_latest_usable_round_includes_successful_zero_scores(sampler: HealthSampler) -> None:
    fixture = Fixture(sampler.repository, count=1)
    plan = fixture.start()
    member = plan.cohort[0]
    fixture.status(request_for(plan, member, "factory"))
    fixture.coordinator().tick(plan.deadlines.evaluation_start, BEAT)
    fixture.status(request_for(plan, member, "judge"))
    completed = plan.deadlines.round_end - timedelta(seconds=1)
    fixture.coordinator().tick(completed, BEAT)
    executor_health(sampler, completed)
    sampler.chain_at = completed
    snapshot = sampler.sample(completed)
    assert snapshot.usable_scores == 1 and snapshot.usable_round_completed_at == completed
    assert snapshot.active_jobs == snapshot.unresolved_jobs == 0
    assert all(snapshot.checks.values())


def test_http_lifecycle_needs_current_chain_and_reconciliation_and_expires(sampler: HealthSampler) -> None:
    now = datetime.now(UTC)
    node = MonitoringNode(sampler.repository, True, "127.0.0.1", 0, 30)
    executor_health(node.sampler, now)
    builder = SubnetBuilder(nodes=[node])
    actor = node.build_actor(pipe_to_bus=builder.pipe_to_bus, context_store=builder.context_store)
    actor.on_start()
    try:
        assert actor.server is not None
        address = f"http://127.0.0.1:{actor.server.server_port}"
        assert httpx.get(address + "/livez").status_code == 200
        assert httpx.get(address + "/readyz").status_code == 503
        node.sampler.sample(now)
        assert not node.sampler.snapshot or not node.sampler.snapshot.checks["chain"]
        with builder.context_store.create_context() as ctx:
            actor.handle_block(ctx, ReceiveEvent(ctx_id=ctx.id, target=node.block, payload=BEAT))
            node.sampler.coordinator_at = datetime.now(UTC)
            node.sampler.sample(datetime.now(UTC))
            assert httpx.get(address + "/readyz").status_code == 200
            actor.handle_failure(
                ctx,
                ReceiveEvent(ctx_id=ctx.id, target=node.failure, payload=NexusException("result-store unavailable")),
            )
            node.sampler.sample(datetime.now(UTC))
            assert httpx.get(address + "/readyz").status_code == 503
            node.sampler.coordinator_errors = ()
            node.sampler.sample(datetime.now(UTC))
        snapshot = node.sampler.snapshot
        assert snapshot is not None
        node.sampler.snapshot = snapshot.model_copy(update={"observed_at": now - timedelta(seconds=60)})
        assert httpx.get(address + "/readyz").status_code == 503
        assert httpx.get(address + "/livez").status_code == 200
        assert "factory_horde_ready 0.0" in httpx.get(address + "/metrics").text
    finally:
        actor.on_stop()
    assert actor.thread is not None and not actor.thread.is_alive()
