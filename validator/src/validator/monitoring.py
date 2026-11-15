"""Nexus-owned health sampling and HTTP lifetime; no executor network interface."""

import json
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, override

import structlog
from nexus.v1 import (
    ActorBuilder,
    BlockBeat,
    Context,
    ContextStore,
    EventHandler,
    MessagesToSend,
    NexusException,
    NodeSinks,
    PipeToBus,
    ReceiveEvent,
    Sink,
    SinkName,
    Transform,
    TransformActor,
    WeightSettingSuccess,
)
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Histogram, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, HistogramMetricFamily, Metric
from pylon_client.artanis import Config, PylonClient

from validator.coordinator import RoundTick
from validator.health import ExecutorHealth, ExecutorMetrics, Readiness, fresh
from validator.records import Record, StopRequest, is_job_filename
from validator.result_records import JobResult, ResultProjection
from validator.result_repository import ResultRepository
from validator.weighing import WeightCalculation

_logger = structlog.get_logger(__name__)
_events = Counter("factory_horde_health_checks_total", "Application readiness observations", ("outcome",))
_latency = Histogram("factory_horde_health_check_seconds", "Application readiness sampling duration")
_submissions = Counter("factory_horde_pylon_submissions_total", "Pylon acknowledgements, not chain inclusion")
_submission_latency = Histogram("factory_horde_submission_observation_seconds", "Acknowledgement recording duration")
_CHAIN_VIEW_SECONDS = 60


@dataclass(frozen=True)
class RoundView:
    """Latest round and its usable scores for dashboards; never part of readiness."""

    sequence: int
    miners: int
    start: datetime
    end: datetime
    usable_sequence: int | None = None
    scores: Mapping[str, float] | None = None


@dataclass(frozen=True)
class ChainView:
    """What the chain reports for this subnet through Pylon; no weight row is available there."""

    block: int
    permit: bool | None
    last_update: int | None
    incentives: Mapping[str, float]


def read_chain_view(config: Config, netuid: int, validator_hotkey: str | None) -> ChainView:
    """Read current neurons with the public identity API.

    Raises:
        ValueError: The configured identity no longer selects this subnet.
    """
    with PylonClient(config) as client:
        response = client.v1.identity.get_latest_neurons()
        if client.v1.identity.netuid != netuid:
            raise ValueError("Pylon identity belongs to a different subnet")
    own = next((n for n in response.neurons.values() if n.hotkey == validator_hotkey), None)
    return ChainView(
        block=response.block.number,
        permit=None if own is None else bool(own.validator_permit),
        last_update=None if own is None else int(own.last_update),
        incentives={str(n.hotkey): float(n.incentive) for n in response.neurons.values() if n.incentive > 0},
    )


class HealthSampler:
    """Inspect records and real writable paths without Docker operations.

    The optional chain view is one throttled Pylon read for dashboards; it never affects readiness.
    """

    def __init__(
        self,
        repository: ResultRepository,
        dispatch_enabled: bool,
        max_age: float,
        chain_view: Callable[[], ChainView] | None = None,
    ):
        self.repository = repository
        self.dispatch_enabled = dispatch_enabled
        self.max_age = max_age
        self.read_chain_view = chain_view
        self.chain_view: ChainView | None = None
        self.chain_view_at: datetime | None = None
        self.round_view: RoundView | None = None
        self.weights: WeightCalculation | None = None
        self.chain_at: datetime | None = None
        self.coordinator_at: datetime | None = None
        self.coordinator_errors: tuple[str, ...] = ()
        self.snapshot: Readiness | None = None
        self.executor_metrics: ExecutorMetrics | None = None

    def sample(self, now: datetime) -> Readiness:
        """An unreadable component fails its check while other evidence stays visible."""
        with _latency.time():
            snapshot = self._sample(now)
            self._sample_chain_view(now)
            previous = self.snapshot
            self.snapshot = snapshot
            _events.labels("ready" if all(snapshot.checks.values()) else "not_ready").inc()
            if previous is None or (previous.checks, previous.phase, previous.blocked_next_round) != (
                snapshot.checks,
                snapshot.phase,
                snapshot.blocked_next_round,
            ):
                _logger.info("readiness_changed", **snapshot.model_dump(mode="json"))
            return snapshot

    def _sample_chain_view(self, now: datetime) -> None:
        """A failed read clears the view so dashboards never show stale chain state."""
        if self.read_chain_view is None or (
            self.chain_view_at is not None and 0 <= (now - self.chain_view_at).total_seconds() < _CHAIN_VIEW_SECONDS
        ):
            return
        self.chain_view_at = now
        try:
            self.chain_view = self.read_chain_view()
        except Exception as error:
            self.chain_view = None
            _logger.warning("chain_view_unavailable", error=str(error))

    def _sample(self, now: datetime) -> Readiness:
        files = self.repository.files
        checks = Readiness(
            observed_at=now,
            checks={
                "chain": fresh(self.chain_at, now, self.max_age),
                "records": False,
                "writable_paths": False,
                "executor": False,
                "docker": False,
                "reconciled": False,
                "job_observations": False,
                "coordinator": not self.dispatch_enabled
                or (fresh(self.coordinator_at, now, self.max_age) and not self.coordinator_errors),
            },
        ).checks
        errors = list(self.coordinator_errors)
        snapshot = Readiness(
            observed_at=now, checks=checks, chain_observed_at=self.chain_at, coordinator_observed_at=self.coordinator_at
        )
        paths = {
            "control",
            "rounds",
            *(
                f"control/{name}"
                for name in (
                    "requests",
                    "stops",
                    "projections",
                    "rounds",
                    "discovery",
                    "skipped-evaluations",
                    "factory-eligibility",
                )
            ),
        }
        try:
            health = files.read("control/executor-health.json", ExecutorHealth)
            metrics = files.read("control/executor-metrics.json", ExecutorMetrics)
            if metrics.pid != health.pid:
                raise ValueError("Executor health and metrics belong to different processes")
            self.executor_metrics = metrics
            checks["executor"] = fresh(health.observed_at, now, self.max_age) and fresh(
                metrics.observed_at, now, self.max_age
            )
            checks["docker"] = health.docker_ok and fresh(health.docker_observed_at, now, self.max_age)
            checks["reconciled"] = health.reconciled and health.rejected_requests == 0
            snapshot = snapshot.model_copy(
                update={"executor_observed_at": health.observed_at, "docker_observed_at": health.docker_observed_at}
            )
            if health.docker_error:
                errors.append("docker: " + health.docker_error)
        except (OSError, ValueError) as error:
            self.executor_metrics = None
            errors.append(f"executor: {error}")
        try:
            rounds = self.repository.rounds.rounds()
            plans = {record.plan.round_id: record.plan for record in rounds}
            for record in rounds:
                paths.add(record.plan.directory)
                paths.update(f"{record.plan.directory}/{m.miner_hotkey}" for m in record.plan.cohort)
            active = unresolved = 0
            blocked = False
            observed = True
            for request in self.repository.rounds.requests():
                self.repository.authorize(request)
                status = self.repository.rounds.status(request)
                self.repository.read(request)
                try:
                    stop = files.read(f"control/stops/{request.job_id}.json", StopRequest)
                    if not stop.same_job(request):
                        raise ValueError("Stop record differs from request")
                except FileNotFoundError:
                    pass
                if status is None or not status.confirmed_stopped:
                    active += 1
                    current = status is not None and fresh(status.observed_at, now, self.max_age)
                    observed = observed and (current or fresh(request.created_at, now, self.max_age))
                    if status is not None and status.state == "failed":
                        checks["reconciled"] = False
                    unresolved += int(status is None or status.execution == "unresolved" or not current)
                    blocked = blocked or now >= plans[request.round_id].deadlines.round_end
            for name in files.list_names("control/projections"):
                if is_job_filename(name):
                    projection = files.read(f"control/projections/{name}", ResultProjection)
                    if str(projection.result_id) + ".json" != name:
                        raise ValueError("Projection filename differs from result ID")
                    result = files.read(projection.result_path, JobResult)
                    if (
                        result.result_id != projection.result_id
                        or projection.result_path != self.repository.result_path(result.request)
                    ):
                        raise ValueError("Projection differs from canonical result")
                    self.repository.authorize(result.request)
            usable = self.repository.latest_usable_round()
            completed_at = next((r.completed_at for r in rounds if usable and r.plan == usable[0]), None)
            snapshot = snapshot.model_copy(
                update={
                    "phase": rounds[-1].stage if rounds else "idle",
                    "active_jobs": active,
                    "unresolved_jobs": unresolved,
                    "blocked_next_round": blocked,
                    "usable_scores": len(usable[1]) if usable else 0,
                    "usable_round_completed_at": completed_at,
                }
            )
            self.round_view = (
                RoundView(
                    sequence=rounds[-1].plan.sequence,
                    miners=len(rounds[-1].plan.cohort),
                    start=rounds[-1].plan.deadlines.start,
                    end=rounds[-1].plan.deadlines.round_end,
                    usable_sequence=usable[0].sequence if usable else None,
                    scores={r.miner_hotkey: r.score for r in usable[1]} if usable else None,
                )
                if rounds
                else None
            )
            try:
                self.weights = files.read("control/weight-calculation.json", WeightCalculation)
            except FileNotFoundError:
                self.weights = None
            checks["records"] = True
            checks["job_observations"] = observed
            checks["reconciled"] = checks["reconciled"] and not blocked
        except (OSError, ValueError) as error:
            self.round_view = self.weights = None
            errors.append(f"records: {error}")
        try:
            for path in sorted(paths):
                files.replace(f"{path}/.readiness.json", Record())
            checks["writable_paths"] = True
        except (OSError, ValueError) as error:
            errors.append(f"writable_paths: {error}")
        return snapshot.model_copy(update={"checks": checks, "errors": tuple(errors)})


class HealthCollector:
    """Application gauges and executor metrics on the single validator scrape path.

    Only miner hotkeys label series, bounded by the subnet's UIDs; job and round identities stay in records.
    """

    def __init__(self, sampler: HealthSampler):
        self.sampler = sampler

    def collect(self) -> Iterable[Metric]:
        """Compute freshness at scrape time so a stalled actor cannot stay ready."""
        now = datetime.now(UTC)
        snapshot = self.sampler.snapshot
        ready = GaugeMetricFamily("factory_horde_ready", "Application readiness, independent of HTTP liveness")
        checks = snapshot.current_checks(now, self.sampler.max_age, self.sampler.dispatch_enabled) if snapshot else {}
        ready.add_metric([], int(bool(checks) and all(checks.values())))
        yield ready
        if snapshot is None:
            return
        check_metric = GaugeMetricFamily(
            "factory_horde_readiness_check", "Individual readiness checks", labels=["check"]
        )
        for name, ok in checks.items():
            check_metric.add_metric([name], int(ok))
        yield check_metric
        phase = GaugeMetricFamily("factory_horde_round_phase", "Latest persisted round phase", labels=["phase"])
        for name in ("idle", "generation", "stopping", "evaluation", "complete"):
            phase.add_metric([name], int(snapshot.phase == name))
        yield phase
        for name, value in (
            ("active_jobs", snapshot.active_jobs),
            ("unresolved_jobs", snapshot.unresolved_jobs),
            ("blocked_next_round", int(snapshot.blocked_next_round)),
            ("usable_scores", snapshot.usable_scores),
        ):
            yield GaugeMetricFamily("factory_horde_" + name, name.replace("_", " "), value=value)
        for name, observed in (
            ("executor", snapshot.executor_observed_at),
            ("docker", snapshot.docker_observed_at),
            ("chain", snapshot.chain_observed_at),
            ("monitor", snapshot.observed_at),
            ("usable_round", snapshot.usable_round_completed_at),
        ):
            yield GaugeMetricFamily(
                "factory_horde_" + name + "_age_seconds",
                "Age in seconds; -1 means absent",
                value=(now - observed).total_seconds() if observed else -1,
            )
        yield from self._detail(now)
        metrics = self.sampler.executor_metrics
        if metrics is None:
            return
        counter = CounterMetricFamily(
            "factory_horde_executor_operations", "Host Docker operations", labels=["operation", "outcome"]
        )
        for item in metrics.factory_horde_executor_operations_total:
            counter.add_metric([item.operation, item.outcome], item.value)
        yield counter
        histogram = HistogramMetricFamily(
            "factory_horde_executor_operation_seconds", "Host Docker latency", labels=["operation"]
        )
        for item in metrics.factory_horde_executor_operation_seconds:
            histogram.add_metric(
                [item.operation], [(str(b), n) for b, n in zip(item.bounds, item.buckets, strict=True)], item.sum
            )
        yield histogram

    def _detail(self, now: datetime) -> Iterable[Metric]:
        """Latest round, scores, calculated weights and chain view; absent evidence yields no series."""

        def gauge(name: str, text: str, value: float) -> Metric:
            return GaugeMetricFamily("factory_horde_" + name, text, value=value)

        def by_miner(name: str, text: str, values: Mapping[str, float]) -> Metric:
            family = GaugeMetricFamily("factory_horde_" + name, text, labels=["miner_hotkey"])
            for hotkey, value in sorted(values.items()):
                family.add_metric([hotkey], value)
            return family

        view = self.sampler.round_view
        if view is not None:
            yield gauge("round_sequence", "Latest persisted round number", view.sequence)
            yield gauge("round_miners", "Miners admitted to the latest round", view.miners)
            yield gauge("round_start_timestamp_seconds", "Latest round start", view.start.timestamp())
            yield gauge("round_end_timestamp_seconds", "Latest round end", view.end.timestamp())
            if view.usable_sequence is not None and view.scores is not None:
                yield gauge("usable_round_sequence", "Round that supplies current scores", view.usable_sequence)
                yield by_miner("usable_score", "Accepted score in the latest usable round", view.scores)
        weights = self.sampler.weights
        if weights is not None:
            yield by_miner("calculated_weight", "Latest calculated weight, not chain inclusion", weights.weights)
            yield gauge(
                "weight_calculation_age_seconds",
                "Age of the latest weight calculation",
                (now - weights.calculated_at).total_seconds(),
            )
        chain = self.sampler.chain_view
        if chain is not None:
            yield gauge("chain_view_block", "Block of the latest chain view", chain.block)
            if chain.permit is not None and chain.last_update is not None:
                yield gauge("chain_validator_permit", "Validator permit on chain", int(chain.permit))
                yield gauge("chain_validator_last_update_block", "Validator's last weight update", chain.last_update)
            yield by_miner("chain_incentive", "Nonzero incentive on chain", chain.incentives)


class MonitoringNode(Transform[datetime, Readiness], ActorBuilder):
    """Own sampling and an HTTP server with read-only operational endpoints.

    sink sink: wall-clock sampling tick
    sink block: real block beat in this runtime
    sink round: coordinator reconciliation outcome
    sink failure: coordinator failure
    sink submission: Pylon acknowledgement, never independent chain proof
    source ok: current readiness snapshot
    source error: unexpected monitor failure
    """

    def __init__(
        self,
        repository: ResultRepository,
        dispatch_enabled: bool,
        host: str,
        port: int,
        max_age: float,
        chain_view: Callable[[], ChainView] | None = None,
    ):
        super().__init__("factory-horde-monitoring")
        self.sampler = HealthSampler(repository, dispatch_enabled, max_age, chain_view)
        self.host, self.port = host, port
        self.block = Sink[BlockBeat](f"{self.id}-block", owner_node=self)
        self.round = Sink[RoundTick](f"{self.id}-round", owner_node=self)
        self.failure = Sink[NexusException](f"{self.id}-failure", owner_node=self)
        self.submission = Sink[WeightSettingSuccess](f"{self.id}-submission", owner_node=self)

    @override
    def sinks(self) -> NodeSinks:
        return NodeSinks(
            {
                **super().sinks().sinks,
                SinkName("block"): self.block,
                SinkName("round"): self.round,
                SinkName("failure"): self.failure,
                SinkName("submission"): self.submission,
            }
        )

    @override
    def build_actor(self, *, pipe_to_bus: PipeToBus, context_store: ContextStore) -> MonitoringActor:
        return MonitoringActor(self, pipe_to_bus, context_store)


class MonitoringActor(TransformActor[datetime, Readiness]):
    """The actor starts and joins its HTTP thread; handlers never access Docker."""

    def __init__(self, spec: MonitoringNode, pipe_to_bus: PipeToBus, context_store: ContextStore):
        super().__init__(spec, pipe_to_bus, context_store)
        self.node = spec
        self.server: HTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.collector = HealthCollector(spec.sampler)

    @override
    def handlers(self) -> dict[Sink[Any], EventHandler]:
        return {
            **super().handlers(),
            self.node.block: self.handle_block,
            self.node.round: self.handle_round,
            self.node.failure: self.handle_failure,
            self.node.submission: self.handle_submission,
        }

    def handle_block(self, _ctx: Context, _event: ReceiveEvent[BlockBeat]) -> MessagesToSend:
        """Ignore retained chain files when establishing initial connectivity."""
        self.node.sampler.chain_at = datetime.now(UTC)
        return ()

    def handle_round(self, _ctx: Context, event: ReceiveEvent[RoundTick]) -> MessagesToSend:
        """Observe reconciliation independently of task callbacks."""
        self.node.sampler.coordinator_at = datetime.now(UTC)
        self.node.sampler.coordinator_errors = event.payload.errors
        return ()

    def handle_failure(self, _ctx: Context, event: ReceiveEvent[NexusException]) -> MessagesToSend:
        """A failed coordinator remains unready until its next successful reconciliation."""
        self.node.sampler.coordinator_errors = (str(event.payload),)
        return ()

    def handle_submission(self, _ctx: Context, _event: ReceiveEvent[WeightSettingSuccess]) -> MessagesToSend:
        """Expose the scope of the upstream success signal without claiming chain inclusion."""
        with _submission_latency.time():
            _submissions.inc()
            _logger.info("pylon_weight_submission_acknowledged", independently_verified=False)
        return ()

    @override
    def _transform(self, ctx: Context, payload: datetime) -> Readiness:
        return self.node.sampler.sample(datetime.now(UTC))

    @override
    def on_start(self) -> None:
        sampler = self.node.sampler

        class Handler(BaseHTTPRequestHandler):
            @override
            def setup(self) -> None:
                self.request.settimeout(3)
                super().setup()

            def do_GET(self) -> None:
                """Only disposable snapshots and metrics are served; no file mutations."""
                status, content_type = 200, "application/json"
                if self.path == "/metrics":
                    body, content_type = generate_latest(), CONTENT_TYPE_LATEST
                elif self.path == "/livez":
                    body = b'{"live":true}\n'
                elif self.path == "/readyz":
                    snapshot = sampler.snapshot
                    checks = (
                        snapshot.current_checks(datetime.now(UTC), sampler.max_age, sampler.dispatch_enabled)
                        if snapshot
                        else {}
                    )
                    status = 200 if checks and all(checks.values()) else 503
                    body = json.dumps(
                        {
                            "ready": status == 200,
                            "checks": checks,
                            "observation": snapshot.model_dump(mode="json") if snapshot else None,
                        }
                    ).encode()
                else:
                    status, body = 404, b'{"error":"not found"}\n'
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            @override
            def log_message(self, format: str, *args: object) -> None:
                pass

        self.server = HTTPServer((self.node.host, self.node.port), Handler)
        REGISTRY.register(self.collector)
        self.thread = threading.Thread(target=self.server.serve_forever, name="factory-horde-monitoring-http")
        self.thread.start()

    @override
    def on_stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=5)
        REGISTRY.unregister(self.collector)
