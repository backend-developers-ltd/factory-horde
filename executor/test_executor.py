"""Standalone executor protocol, uncertainty and durable at-most-once checks."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import override
from uuid import uuid4

import pytest
from validator.health import ExecutorHealth, ExecutorMetrics
from validator.records import JobStatus

from .executor import (
    ApplicationRunner,
    Docker,
    DockerError,
    DockerRejected,
    Executor,
    Files,
    ProtocolError,
    Request,
    Settings,
    encode,
    main,
    now,
    object_value,
    parse,
    timestamp,
)

FIXTURES = Path(__file__).resolve().parents[1] / "spec/fixtures/protocol-v1"


@pytest.fixture
def job_request() -> Request:
    value = parse((FIXTURES / "factory-request.json").read_bytes())
    value.update(created_at=now(), deadline=(datetime.now(UTC) + timedelta(minutes=5)).isoformat())
    return Request.load(value, str(value["job_id"]) + ".json")


class FakeDocker(Docker):
    """A daemon whose execution survives replacement of the executor process."""

    def __init__(self, executor: Executor):
        super().__init__(executor.settings, executor.files, executor.metrics, executor.shutdown)
        self.container: dict[str, object] | None = None
        self.offline = False
        self.pull_error = False
        self.created = 0
        self.started = 0
        self.running: list[str] = []

    @override
    def inspect(self, request: Request) -> dict[str, object] | None:
        if self.offline:
            raise DockerError("daemon unavailable")
        return self.container

    @override
    def call(self, *args: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
        if args[0] == "ps":
            return subprocess.CompletedProcess(args, 0, "\n".join(self.running), "")
        return subprocess.CompletedProcess(
            args, int(self.pull_error), "", "fixture pull failure" if self.pull_error else ""
        )

    @override
    def create(self, request: Request, arguments: tuple[str, ...] | None = None) -> str:
        self.created += 1
        self.container = {"Id": "a" * 64, "State": {"Status": "created", "Running": False}}
        return "a" * 64

    @override
    def require(self, *args: str, timeout: float = 30) -> str:
        assert args[0] == "start"
        self.started += 1
        self.container = {"Id": "a" * 64, "State": {"Status": "running", "Running": True, "StartedAt": now()}}
        return ""

    def finish(self) -> None:
        self.container = {
            "Id": "a" * 64,
            "State": {
                "Status": "exited",
                "Running": False,
                "StartedAt": now(),
                "FinishedAt": now(),
                "ExitCode": 0,
                "OOMKilled": False,
            },
        }


@pytest.fixture
def executor(tmp_path: Path, job_request: Request) -> Executor:
    result = Executor(Settings(tmp_path, os.getuid(), os.getgid()))
    manifest = parse((FIXTURES / "input-manifest.json").read_bytes())
    result.files.write(f"{job_request.input_dir}/task.json", manifest)
    (tmp_path / job_request.input_dir / "specification.md").write_bytes((FIXTURES / "specification.md").read_bytes())
    (tmp_path / job_request.output_dir).mkdir()
    result.docker = FakeDocker(result)
    return result


def status(executor: Executor, job_request: Request) -> JobStatus:
    return JobStatus.model_validate_json(executor.files.read_bytes(f"control/statuses/{job_request.job_id}.json"))


def test_health_separates_heartbeat_from_daemon_and_reconciliation(executor: Executor) -> None:
    executor.health({"unobserved"}, 0)
    health = ExecutorHealth.model_validate_json(executor.files.read_bytes("control/executor-health.json"))
    assert health.observed_at >= health.started_at
    assert not health.docker_ok and not health.reconciled
    executor.observed_jobs.add("unobserved")
    executor.health({"unobserved"}, 0)
    health = ExecutorHealth.model_validate_json(executor.files.read_bytes("control/executor-health.json"))
    assert health.reconciled and not health.docker_ok
    executor.health({"unobserved"}, 1)
    health = ExecutorHealth.model_validate_json(executor.files.read_bytes("control/executor-health.json"))
    assert not health.reconciled and health.rejected_requests == 1


def test_executor_metrics_match_validator_bridge(executor: Executor) -> None:
    executor.metrics.observe("pull", "ok", 0.75)
    with ThreadPoolExecutor(max_workers=1) as pool:
        executor.poll(pool)
    metrics = ExecutorMetrics.model_validate_json(executor.files.read_bytes("control/executor-metrics.json"))
    assert metrics.factory_horde_executor_operations_total[0].value == 1
    histogram = metrics.factory_horde_executor_operation_seconds[0]
    assert histogram.count == 1 and histogram.sum == 0.75 and histogram.buckets[-1] == 1


def test_durable_exit_survives_new_executor_without_docker(executor: Executor, job_request: Request) -> None:
    daemon = FakeDocker(executor)
    executor.docker = daemon
    executor.step(job_request)
    executor.step(job_request)
    executor.step(job_request)
    assert status(executor, job_request).execution == "running"
    daemon.finish()
    executor.step(job_request)
    terminal = status(executor, job_request)
    assert terminal.application_succeeded and daemon.started == 1
    restarted = Executor(executor.settings)
    daemon.offline = True
    restarted.docker = daemon
    restarted.step(job_request)
    assert status(restarted, job_request) == terminal
    assert daemon.created == daemon.started == 1


def test_absent_or_unavailable_expected_execution_never_means_stopped(executor: Executor, job_request: Request) -> None:
    daemon = FakeDocker(executor)
    executor.docker = daemon
    executor.step(job_request)
    daemon.container = None
    executor.step(job_request)
    assert not status(executor, job_request).confirmed_stopped
    assert "missing" in (status(executor, job_request).reason or "")
    daemon.offline = True
    executor.step(job_request)
    assert status(executor, job_request).execution == "unresolved"
    assert daemon.created == 1 and daemon.started == 0


def test_pull_failure_permanently_prevents_start(executor: Executor, job_request: Request) -> None:
    daemon = FakeDocker(executor)
    daemon.pull_error = True
    executor.docker = daemon
    executor.step(job_request)
    failed = status(executor, job_request)
    assert failed.state == "failed" and failed.execution == "never_started" and failed.confirmed_stopped
    daemon.pull_error = False
    executor.step(job_request)
    assert status(executor, job_request) == failed and daemon.created == 0


def test_conflicting_request_preserves_terminal_evidence(executor: Executor, job_request: Request) -> None:
    daemon = FakeDocker(executor)
    daemon.pull_error = True
    executor.docker = daemon
    executor.step(job_request)
    original = status(executor, job_request)
    changed = Request.load(
        {**job_request.record, "image": job_request.image.replace("a" * 64, "b" * 64)}, job_request.job_id + ".json"
    )
    with pytest.raises(ProtocolError, match="Conflicting"):
        executor.step(changed)
    assert status(executor, job_request) == original


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("invalid-request-*.json")), ids=lambda path: path.stem)
def test_invalid_request_contract(path: Path) -> None:
    with pytest.raises((ValueError, KeyError)):
        Request.load(parse(path.read_bytes()), "11111111-1111-4111-8111-111111111111.json")


def test_nofollow_io_and_atomic_immutability(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    files = Files(root)
    files.write("record.json", {"value": 1}, immutable=True)
    with pytest.raises(ProtocolError):
        files.write("record.json", {"value": 2}, immutable=True)
    assert files.read_bytes("record.json") == encode({"value": 1})
    (root / "outside").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        files.write("outside/escaped.json", {"value": 2})
    (root / "link.json").symlink_to(root / "record.json")
    with pytest.raises((OSError, ProtocolError)):
        files.write("link.json", {"value": 2})
    os.mkfifo(root / "pipe")
    with pytest.raises(ProtocolError, match="regular"):
        files.read_bytes("pipe")
    assert not (tmp_path / "escaped.json").exists()


def test_strict_json() -> None:
    with pytest.raises(ProtocolError):
        parse('{"key":1,"key":2}')
    with pytest.raises(ProtocolError):
        parse('{"key":NaN}')
    with pytest.raises(json.JSONDecodeError):
        parse('{"key":')


def cancel(executor: Executor, request: Request) -> None:
    executor.files.write(
        f"control/stops/{request.job_id}.json",
        {**request.identity(), "requested_at": now(), "reason": "operator"},
        immutable=True,
    )


@pytest.mark.parametrize("phase", ["reserved", "created", "expired"])
def test_cancel_before_execution_is_permanent(executor: Executor, job_request: Request, phase: str) -> None:
    daemon = FakeDocker(executor)
    executor.docker = daemon
    if phase == "created":
        executor.step(job_request)
    if phase == "expired":
        record = {
            **job_request.record,
            "created_at": (datetime.now(UTC) - timedelta(minutes=2)).isoformat(),
            "deadline": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        }
        job_request = Request.load(record, job_request.job_id + ".json")
    else:
        cancel(executor, job_request)
    executor.step(job_request)
    terminal = status(executor, job_request)
    assert terminal.confirmed_stopped and terminal.execution == "never_started"
    replacement = Executor(executor.settings)
    replacement.docker = daemon
    replacement.step(job_request)
    assert status(replacement, job_request) == terminal and daemon.started == 0


@pytest.mark.parametrize("phase", ["created", "running", "exited"])
def test_restart_reconciles_actual_execution_without_replacement(
    executor: Executor, job_request: Request, phase: str
) -> None:
    daemon = FakeDocker(executor)
    executor.docker = daemon
    executor.step(job_request)
    if phase != "created":
        executor.step(job_request)
    if phase == "exited":
        daemon.finish()
    replacement = Executor(executor.settings)
    replacement.docker = daemon
    replacement.step(job_request)
    replacement.step(job_request)
    assert daemon.created == daemon.started == 1
    assert status(replacement, job_request).execution == ("exited" if phase == "exited" else "running")


def test_ambiguous_start_cannot_acknowledge_cancel_or_retry(executor: Executor, job_request: Request) -> None:
    daemon = FakeDocker(executor)
    executor.docker = daemon
    executor.step(job_request)
    executor.save_ledger(job_request, {"phase": "start_attempted", "container_id": "a" * 64, "closed": False})
    cancel(executor, job_request)
    executor.step(job_request)
    observed = status(executor, job_request)
    assert observed.execution == "unresolved" and observed.startup_forbidden
    assert not observed.confirmed_stopped and daemon.started == 0


def test_same_name_with_replacement_id_is_unresolved(executor: Executor, job_request: Request) -> None:
    daemon = FakeDocker(executor)
    executor.docker = daemon
    executor.step(job_request)
    daemon.container = {"Id": "b" * 64, "State": {"Status": "created", "Running": False}}
    executor.step(job_request)
    assert not status(executor, job_request).confirmed_stopped
    assert daemon.started == 0 and daemon.created == 1


class HangingDocker(FakeDocker):
    """TERM-resistant fixture whose KILL produces independently inspected evidence."""

    def __init__(self, executor: Executor):
        super().__init__(executor)
        self.signals: list[str] = []

    @override
    def call(self, *args: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
        if args[0] == "kill":
            self.signals.append(args[2])
            if args[2] == "KILL":
                self.finish()
                assert self.container is not None
                object_value(self.container["State"])["ExitCode"] = 137
        return super().call(*args, timeout=timeout)


def test_grace_survives_restart_and_force_stop_requires_inspection(executor: Executor, job_request: Request) -> None:
    daemon = HangingDocker(executor)
    executor.docker = daemon
    executor.step(job_request)
    executor.step(job_request)
    cancel(executor, job_request)
    executor.step(job_request)
    assert daemon.signals == ["TERM"] and not status(executor, job_request).confirmed_stopped
    ledger = executor.files.read(f"control/executor/{job_request.job_id}.json")
    ledger["stop_by"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    executor.save_ledger(job_request, ledger)
    replacement = Executor(executor.settings)
    replacement.docker = daemon
    replacement.step(job_request)
    terminal = status(replacement, job_request)
    assert daemon.signals == ["TERM", "KILL"]
    assert terminal.confirmed_stopped and terminal.forced and terminal.exit_code == 137
    assert not terminal.application_succeeded


class SlowPullDocker(FakeDocker):
    """A pull held in flight until the test explicitly releases its worker."""

    def __init__(self, executor: Executor):
        super().__init__(executor)
        self.pulling = threading.Event()
        self.release = threading.Event()

    @override
    def call(self, *args: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
        if args[0] == "pull":
            self.pulling.set()
            if not self.release.wait(10):
                raise TimeoutError("Fixture pull was not released")
        return super().call(*args, timeout=timeout)


def test_pending_pull_has_no_early_cancellation_ack(executor: Executor, job_request: Request) -> None:
    daemon = SlowPullDocker(executor)
    executor.docker = daemon
    executor.files.write(f"control/requests/{job_request.job_id}.json", job_request.record, immutable=True)
    with ThreadPoolExecutor(max_workers=5) as pool:
        executor.poll(pool)
        assert daemon.pulling.wait(2)
        cancel(executor, job_request)
        for _ in range(3):
            executor.poll(pool)
        assert not status(executor, job_request).confirmed_stopped
        assert len(executor.active) == 1
        daemon.release.set()
        executor.active[job_request.job_id].result(timeout=2)
    assert status(executor, job_request).confirmed_stopped and daemon.created == 0
    replacement = Executor(executor.settings)
    replacement.docker = daemon
    replacement.step(job_request)
    assert daemon.started == 0 and daemon.created == 0


@pytest.mark.parametrize("cancelled", [False, True])
def test_sigterm_interrupts_held_pull_before_restart(
    executor: Executor,
    job_request: Request,
    cancelled: bool,
) -> None:
    root = executor.settings.root
    binary = root / "bin"
    binary.mkdir()
    marker = root / "pull-held"
    docker = binary / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, sys, time\n"
        "command = sys.argv[3]\n"
        "if command == 'container':\n"
        "    print('No such container:', file=sys.stderr)\n"
        "    sys.exit(1)\n"
        "if command == 'info':\n"
        "    print('fixture-daemon')\n"
        "elif command == 'ps':\n"
        "    pass\n"
        "elif command == 'pull':\n"
        f"    pathlib.Path({str(marker)!r}).write_text('held')\n"
        "    time.sleep(600)\n"
        "else:\n"
        "    sys.exit('Unexpected Docker mutation: ' + command)\n"
    )
    docker.chmod(0o755)
    executor.files.write(f"control/requests/{job_request.job_id}.json", job_request.record)
    env = {**os.environ, "PATH": str(binary) + os.pathsep + os.defpath, "EXECUTOR_POLL_SECONDS": "0.05"}
    command = [sys.executable, "-I", str(Path(__file__).with_name("executor.py")), "--root", str(root)]
    with subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) as process:
        try:
            deadline = time.monotonic() + 5
            while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            assert marker.exists(), "Executor never entered the held pull"
            assert not status(executor, job_request).confirmed_stopped
            if cancelled:
                cancel(executor, job_request)
            process.terminate()
            _, stderr = process.communicate(timeout=3)
            assert process.returncode == 0, stderr
            assert marker.read_text() == "held"  # Never release the pull before shutdown.
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
    assert not status(executor, job_request).confirmed_stopped
    replacement = Executor(executor.settings)
    daemon = FakeDocker(replacement)
    replacement.docker = daemon
    replacement.step(job_request)
    replacement.step(job_request)
    assert daemon.created == daemon.started == (0 if cancelled else 1)
    replacement.step(job_request)
    assert status(replacement, job_request).execution == ("never_started" if cancelled else "running")


def test_signalled_docker_client_cannot_prove_pull_failure(executor: Executor, monkeypatch: pytest.MonkeyPatch) -> None:
    binary = executor.settings.root / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text(f"#!{sys.executable}\nimport os, signal\nos.kill(os.getpid(), signal.SIGTERM)\n")
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary))
    client = Docker(executor.settings, executor.files, executor.metrics, executor.shutdown)
    with pytest.raises(DockerError, match="interrupted"):
        client.call("pull", timeout=3)


def test_slow_pull_does_not_block_other_cancelled_jobs(executor: Executor, job_request: Request) -> None:
    daemon = SlowPullDocker(executor)
    executor.docker = daemon
    executor.files.write(f"control/requests/{job_request.job_id}.json", job_request.record)
    others: list[Request] = []
    for _ in range(4):
        job_id = str(uuid4())
        record = {**job_request.record, "job_id": job_id, "factory_job_id": job_id}
        request = Request.load(record, job_id + ".json")
        executor.files.write(f"control/requests/{job_id}.json", record)
        cancel(executor, request)
        others.append(request)
    with ThreadPoolExecutor(max_workers=5) as pool:
        executor.poll(pool)
        assert daemon.pulling.wait(2)
        for request in others:
            executor.active[request.job_id].result(timeout=2)
            assert status(executor, request).confirmed_stopped
        assert not executor.active[job_request.job_id].done()
        daemon.release.set()
        executor.active[job_request.job_id].result(timeout=2)
    assert daemon.created == 1


def test_future_request_cannot_start_early(executor: Executor, job_request: Request) -> None:
    record = {**job_request.record, "created_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat()}
    request = Request.load(record, job_request.job_id + ".json")
    daemon = FakeDocker(executor)
    executor.docker = daemon
    executor.step(request)
    assert status(executor, request).state == "pending" and daemon.created == 0


class SlowCreateDocker(FakeDocker):
    """A daemon may create the container before a CLI response reaches its caller."""

    def __init__(self, executor: Executor):
        super().__init__(executor)
        self.creating = threading.Event()
        self.release = threading.Event()

    @override
    def create(self, request: Request, arguments: tuple[str, ...] | None = None) -> str:
        identifier = super().create(request, arguments)
        self.creating.set()
        if not self.release.wait(10):
            raise TimeoutError("Fixture create was not released")
        return identifier


def test_cancel_during_create_never_acknowledges_outstanding_startup(executor: Executor, job_request: Request) -> None:
    daemon = SlowCreateDocker(executor)
    executor.docker = daemon
    executor.files.write(f"control/requests/{job_request.job_id}.json", job_request.record)
    with ThreadPoolExecutor(max_workers=5) as pool:
        executor.poll(pool)
        assert daemon.creating.wait(2)
        cancel(executor, job_request)
        executor.poll(pool)
        assert not status(executor, job_request).confirmed_stopped
        daemon.release.set()
        executor.active[job_request.job_id].result(timeout=2)
    executor.step(job_request)
    terminal = status(executor, job_request)
    assert terminal.confirmed_stopped and terminal.execution == "never_started"
    assert daemon.started == 0 and daemon.created == 1
    replacement = Executor(executor.settings)
    replacement.docker = daemon
    replacement.step(job_request)
    assert status(replacement, job_request) == terminal and daemon.started == 0


def test_zero_grace_still_sends_term_before_kill(executor: Executor, job_request: Request) -> None:
    job_request = Request.load({**job_request.record, "stop_grace_seconds": 0}, job_request.job_id + ".json")
    daemon = HangingDocker(executor)
    executor.docker = daemon
    executor.step(job_request)
    executor.step(job_request)
    cancel(executor, job_request)
    executor.step(job_request)
    assert daemon.signals == ["TERM"] and not status(executor, job_request).confirmed_stopped
    executor.step(job_request)
    assert daemon.signals == ["TERM", "KILL"] and status(executor, job_request).confirmed_stopped


@pytest.mark.parametrize("phase", ["start_attempted", "start_rejected"])
@pytest.mark.parametrize("cancelled", [False, True])
def test_runtime_start_rejection_is_terminal_after_restart(
    executor: Executor, job_request: Request, phase: str, cancelled: bool
) -> None:
    daemon = FakeDocker(executor)
    executor.docker = daemon
    executor.step(job_request)
    daemon.container = {
        "Id": "a" * 64,
        "State": {
            "Status": "created",
            "Running": False,
            "ExitCode": 127,
            "StartedAt": "0001-01-01T00:00:00Z",
            "FinishedAt": "0001-01-01T00:00:00Z",
            "Error": "failed to create task: executable file not found",
        },
    }
    executor.save_ledger(job_request, {"phase": phase, "container_id": "a" * 64, "closed": cancelled})
    replacement = Executor(executor.settings)
    replacement.docker = daemon
    replacement.step(job_request)
    failed = status(replacement, job_request)
    assert failed.confirmed_stopped and failed.execution == "never_started" and failed.state == "failed"
    replacement.step(job_request)
    assert status(replacement, job_request) == failed and daemon.started == 0


@pytest.mark.parametrize("operation", ["create", "start"])
def test_completed_cli_rejection_is_persisted_before_reconciliation(
    executor: Executor, job_request: Request, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    daemon = FakeDocker(executor)
    executor.docker = daemon

    def reject(*args: object) -> str:
        raise DockerRejected("daemon rejected operation")

    if operation == "start":
        executor.step(job_request)
        monkeypatch.setattr(daemon, "require", reject)
    else:
        monkeypatch.setattr(daemon, "create", reject)
    executor.step(job_request)
    ledger = executor.files.read(f"control/executor/{job_request.job_id}.json")
    assert ledger["phase"] == operation + "_rejected" and ledger["closed"] is True
    replacement = Executor(executor.settings)
    replacement.docker = daemon
    replacement.step(job_request)
    observed = status(replacement, job_request)
    # A failed start response alone cannot prove the daemon did not execute it.
    assert observed.confirmed_stopped == (operation == "create")
    assert daemon.started == 0


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("outcome", ["never_started", "exited"])
def test_completed_jobs_leave_polling_after_durable_reconciliation(
    executor: Executor,
    job_request: Request,
    monkeypatch: pytest.MonkeyPatch,
    restart: bool,
    outcome: str,
) -> None:
    daemon = FakeDocker(executor)
    executor.docker = daemon
    executor.files.write(
        f"control/requests/{job_request.job_id}.json",
        job_request.record,
        immutable=True,
    )
    if outcome == "never_started":
        daemon.pull_error = True
    executor.step(job_request)
    if outcome == "exited":
        executor.step(job_request)
        daemon.finish()
        executor.step(job_request)
    terminal = status(executor, job_request)
    assert terminal.execution == outcome
    if restart:
        executor = Executor(executor.settings)
        executor.docker = daemon
        # Reconcile a durable decision whose public status needs repair.
        (executor.settings.root / f"control/statuses/{job_request.job_id}.json").unlink()
        with ThreadPoolExecutor(max_workers=1) as pool:
            executor.poll(pool)
            executor.active[job_request.job_id].result(timeout=2)
        assert status(executor, job_request) == terminal
    else:
        executor.observed_jobs.add(job_request.job_id)
    writes: list[str] = []
    original_write = executor.files.write

    def write(relative: str, value: dict[str, object], *, immutable: bool = False) -> None:
        writes.append(relative)
        original_write(relative, value, immutable=immutable)

    monkeypatch.setattr(executor.files, "write", write)
    with ThreadPoolExecutor(max_workers=1) as pool:
        for _ in range(3):
            executor.poll(pool)
            assert not executor.active
    assert all(job_request.job_id not in name for name in writes)
    assert executor.files.read("control/executor-health.json")["reconciled"] is True


def test_terminal_publication_failure_stays_scheduled_until_repaired(
    executor: Executor, job_request: Request, monkeypatch: pytest.MonkeyPatch
) -> None:
    daemon = FakeDocker(executor)
    daemon.pull_error = True
    executor.docker = daemon
    executor.files.write(
        f"control/requests/{job_request.job_id}.json",
        job_request.record,
        immutable=True,
    )
    original_write = executor.files.write
    failing = True

    def write(relative: str, value: dict[str, object], *, immutable: bool = False) -> None:
        if (
            failing
            and relative == f"control/statuses/{job_request.job_id}.json"
            and value["execution"] == "never_started"
        ):
            raise OSError("terminal status unavailable")
        original_write(relative, value, immutable=immutable)

    monkeypatch.setattr(executor.files, "write", write)
    with ThreadPoolExecutor(max_workers=1) as pool:
        executor.poll(pool)
        executor.active[job_request.job_id].result(timeout=2)
        assert job_request.job_id not in executor.completed_jobs
        assert status(executor, job_request).execution == "unresolved"
        failing = False
        executor.poll(pool)
        executor.active[job_request.job_id].result(timeout=2)
        executor.poll(pool)
        assert not executor.active
    assert status(executor, job_request).execution == "never_started"


class RecordingDocker(Docker):
    """Captures the create command instead of contacting a daemon."""

    arguments: tuple[str, ...] = ()

    @override
    def require(self, *args: str, timeout: float = 30) -> str:
        self.arguments = args
        return "a" * 64


def create_arguments(executor: Executor, request: Request, settings: Settings) -> tuple[str, ...]:
    docker = RecordingDocker(settings, executor.files, executor.metrics, executor.shutdown)
    docker.create(request)
    return docker.arguments


def values(arguments: tuple[str, ...], flag: str) -> list[str]:
    return [arguments[index + 1] for index, argument in enumerate(arguments) if argument == flag]


@pytest.fixture
def judge_request(executor: Executor, job_request: Request) -> Request:
    value = parse((FIXTURES / "judge-request.json").read_bytes())
    value.update(created_at=now(), deadline=(datetime.now(UTC) + timedelta(minutes=5)).isoformat())
    request = Request.load(value, str(value["job_id"]) + ".json")
    assert request.report_dir is not None
    (executor.files.root / request.report_dir).mkdir()
    return request


def test_default_workloads_stay_offline_without_environment(
    executor: Executor, job_request: Request, judge_request: Request
) -> None:
    for request in (job_request, judge_request):
        arguments = create_arguments(executor, request, executor.settings)
        assert values(arguments, "--network") == ["none"]
        assert values(arguments, "--tmpfs") == ["/tmp:rw,nosuid,nodev,size=64m"]
        assert values(arguments, "--env") == (
            [f"FACTORY_HORDE_JUDGE_IMAGE={request.image}"] if request.kind == "judge" else []
        )
        assert arguments[-1] == request.image


def test_factories_and_judges_join_the_inference_network(
    executor: Executor, job_request: Request, judge_request: Request, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Docker, "inference_network", lambda self, request: request.name + "-inference")
    settings = Settings(
        executor.settings.root,
        os.getuid(),
        os.getgid(),
        tmp_size="1g",
        factory_network="factory-horde-inference",
        inference_url="http://proxy.example:8080",
    )
    factory = create_arguments(executor, job_request, settings)
    assert values(factory, "--network") == [job_request.name + "-inference"]
    assert values(factory, "--env") == [
        "ANTHROPIC_BASE_URL=http://proxy.example:8080",
        "CLAUDE_CODE_OAUTH_TOKEN=inference-proxy",
    ]
    assert values(factory, "--tmpfs") == ["/tmp:rw,nosuid,nodev,size=1g"] and factory[-1] == job_request.image
    judge = create_arguments(executor, judge_request, settings)
    assert values(judge, "--network") == [judge_request.name + "-inference"]
    assert values(judge, "--env") == [f"FACTORY_HORDE_JUDGE_IMAGE={judge_request.image}", *values(factory, "--env")]
    assert values(judge, "--tmpfs") == ["/tmp:rw,nosuid,nodev,size=1g"] and judge[-1] == judge_request.image
    unchanged = create_arguments(executor, job_request, executor.settings)
    assert [a for a in factory if a not in unchanged] == [
        job_request.name + "-inference",
        "/tmp:rw,nosuid,nodev,size=1g",
        "--env",
        "ANTHROPIC_BASE_URL=http://proxy.example:8080",
        "--env",
        "CLAUDE_CODE_OAUTH_TOKEN=inference-proxy",
    ]


WORKLOAD_KEYS = ("EXECUTOR_FACTORY_NETWORK", "EXECUTOR_INFERENCE_URL", "EXECUTOR_TMP_SIZE")


def run_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environment: dict[str, str]) -> list[Settings]:
    seen: list[Settings] = []

    def run(executor: Executor) -> None:
        seen.append(executor.settings)

    def keep_handlers(_number: int, _handler: object) -> None:
        pass

    monkeypatch.setattr(Executor, "run", run)
    monkeypatch.setattr(signal, "signal", keep_handlers)
    monkeypatch.setattr(sys, "argv", ["executor.py", "--root", str(tmp_path)])
    for key in (
        *WORKLOAD_KEYS,
        "EXECUTOR_JUDGE_RESERVE_SECONDS",
        "EXECUTOR_MAX_RUNNING_JOBS",
        "EXECUTOR_REGISTRY_NETWORK",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    main()
    return seen


def test_environment_selects_workload_network_and_tmp_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (default,) = run_main(tmp_path, monkeypatch, {})
    assert (default.factory_network, default.inference_url, default.tmp_size) == (
        "",
        "http://inference-proxy:8080",
        "64m",
    )
    configured = ("factory-horde-inference", "https://proxy.example/base", "1g")
    (settings,) = run_main(tmp_path, monkeypatch, dict(zip(WORKLOAD_KEYS, configured, strict=True)))
    assert (settings.factory_network, settings.inference_url, settings.tmp_size) == configured
    assert default.judge_reserve_seconds == 180 and default.max_running_jobs == 4
    (limited,) = run_main(tmp_path, monkeypatch, {"EXECUTOR_MAX_RUNNING_JOBS": "6"})
    assert limited.max_running_jobs == 6
    (reserved,) = run_main(tmp_path, monkeypatch, {"EXECUTOR_JUDGE_RESERVE_SECONDS": "300"})
    assert reserved.judge_reserve_seconds == 300


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("EXECUTOR_FACTORY_NETWORK", "host"),
        ("EXECUTOR_FACTORY_NETWORK", "bridge"),
        ("EXECUTOR_FACTORY_NETWORK", "none"),
        ("EXECUTOR_FACTORY_NETWORK", "container:other"),
        ("EXECUTOR_FACTORY_NETWORK", "--privileged"),
        ("EXECUTOR_FACTORY_NETWORK", "name=inference,alias=other"),
        ("EXECUTOR_FACTORY_NETWORK", "two words"),
        ("EXECUTOR_INFERENCE_URL", ""),
        ("EXECUTOR_INFERENCE_URL", "inference-proxy:8080"),
        ("EXECUTOR_INFERENCE_URL", "http://inference-proxy:8080 --privileged"),
        ("EXECUTOR_TMP_SIZE", ""),
        ("EXECUTOR_TMP_SIZE", "0m"),
        ("EXECUTOR_TMP_SIZE", "64"),
        ("EXECUTOR_TMP_SIZE", "64m,exec"),
        ("EXECUTOR_JUDGE_RESERVE_SECONDS", "-1"),
        ("EXECUTOR_MAX_RUNNING_JOBS", "0"),
        ("EXECUTOR_REGISTRY_NETWORK", "host"),
        ("EXECUTOR_REGISTRY_NETWORK", "two words"),
    ],
)
def test_invalid_workload_settings_are_rejected_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str, value: str
) -> None:
    with pytest.raises(SystemExit) as error:
        run_main(tmp_path, monkeypatch, {key: value})
    assert error.value.code == 2 and not list(tmp_path.iterdir())


class RejectedApplicationDocker(FakeDocker):
    """A running judge whose application container creation the daemon definitively rejects."""

    def __init__(self, executor: Executor):
        super().__init__(executor)
        self.app_creates = 0

    @override
    def call(self, *args: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
        if args[:2] == ("container", "inspect"):
            return subprocess.CompletedProcess(args, 1, "", f"Error: No such container: {args[2]}")
        if args[0] == "create":
            self.app_creates += 1
            return subprocess.CompletedProcess(args, 1, "", "Error response from daemon: no space left on device")
        return subprocess.CompletedProcess(args, 0, "", "")

    @override
    def require(self, *args: str, timeout: float = 30) -> str:
        return Docker.require(self, *args, timeout=timeout)


def test_rejected_application_create_answers_judge_and_allows_terminal_status(
    executor: Executor, judge_request: Request
) -> None:
    daemon = RejectedApplicationDocker(executor)
    executor.docker = daemon
    executor.save_ledger(judge_request, {"phase": "start_attempted", "closed": False, "container_id": "a" * 64})
    daemon.container = {"Id": "a" * 64, "State": {"Status": "running", "Running": True, "StartedAt": now()}}
    channel = f"control/runners/{judge_request.job_id}"
    executor.files.write(f"{channel}/1.request.json", {"id": "step-1", "argv": ["ls"], "stdin": "", "observation": "x"})
    executor.step(judge_request)
    response = executor.files.read(f"{channel}/1.response.json")
    assert "no space left on device" in str(object_value(response["result"])["failure"])
    executor.step(judge_request)
    assert executor.files.read(f"{channel}/1.response.json") == response and daemon.app_creates == 1
    daemon.finish()
    executor.step(judge_request)
    terminal = status(executor, judge_request)
    assert terminal.execution == "exited" and terminal.confirmed_stopped


@pytest.mark.parametrize(
    "failure",
    [DockerError("Expected one inference proxy on the configured network"), DockerRejected("address pools exhausted")],
)
def test_network_preparation_failure_stays_retryable(
    executor: Executor, job_request: Request, monkeypatch: pytest.MonkeyPatch, failure: DockerError
) -> None:
    executor.settings = replace(executor.settings, factory_network="factory-horde-inference")
    daemon = FakeDocker(executor)
    executor.docker = daemon
    outcomes: list[DockerError | None] = [failure, None]

    def network(_self: Docker, request: Request) -> str:
        outcome = outcomes.pop(0)
        if outcome is not None:
            raise outcome
        return request.name + "-inference"

    monkeypatch.setattr(Docker, "inference_network", network)
    executor.step(job_request)
    ledger = executor.files.read(f"control/executor/{job_request.job_id}.json")
    assert ledger["phase"] == "reserved" and ledger["closed"] is False
    assert status(executor, job_request).execution == "unresolved" and daemon.created == 0
    executor.step(job_request)
    assert daemon.created == 1
    assert executor.files.read(f"control/executor/{job_request.job_id}.json")["phase"] == "created"


def test_judge_budget_is_frozen_once_with_a_reserve(executor: Executor, judge_request: Request) -> None:
    executor.settings = replace(executor.settings, judge_reserve_seconds=60)
    docker = Docker(executor.settings, executor.files, executor.metrics, executor.shutdown)
    docker.prepare(judge_request)
    channel = f"control/runners/{judge_request.job_id}"
    budget = executor.files.read(f"{channel}/budget.json")
    assert timestamp(budget["sandbox_deadline"]) == judge_request.deadline - timedelta(seconds=60)
    replace_docker = Docker(
        replace(executor.settings, judge_reserve_seconds=1), executor.files, executor.metrics, executor.shutdown
    )
    replace_docker.prepare(judge_request)
    assert executor.files.read(f"{channel}/budget.json") == budget
    runner = ApplicationRunner(executor, judge_request, {})
    assert runner.sandbox_deadline() == judge_request.deadline - timedelta(seconds=60)


def test_reserve_never_exceeds_half_of_the_judge_duration(executor: Executor, judge_request: Request) -> None:
    executor.settings = replace(executor.settings, judge_reserve_seconds=86400)
    Docker(executor.settings, executor.files, executor.metrics, executor.shutdown).prepare(judge_request)
    created = timestamp(judge_request.record["created_at"])
    half = judge_request.deadline - (judge_request.deadline - created) / 2
    assert ApplicationRunner(executor, judge_request, {}).sandbox_deadline() == half


def test_spent_budget_returns_an_unexecuted_capture(executor: Executor, judge_request: Request) -> None:
    channel = f"control/runners/{judge_request.job_id}"
    executor.files.write(
        f"{channel}/budget.json",
        {"protocol_version": 1, "sandbox_deadline": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
    )
    outcome = ApplicationRunner(executor, judge_request, {}).execute(
        {"id": "step-3", "argv": ["ls"], "stdin": "", "observation": "x"}
    )
    assert outcome["id"] == "step-3" and outcome["budget_exhausted"] is True and outcome["exit_code"] == -1


def test_jobs_wait_for_a_workload_slot(
    executor: Executor, job_request: Request, capsys: pytest.CaptureFixture[str]
) -> None:
    executor.settings = replace(executor.settings, max_running_jobs=2)
    daemon = FakeDocker(executor)
    executor.docker = daemon
    daemon.running = [str(uuid4()), str(uuid4())]
    executor.step(job_request)
    executor.step(job_request)
    waiting = status(executor, job_request)
    assert waiting.state == "pending" and waiting.reason == "Waiting for a workload slot" and daemon.created == 0
    assert capsys.readouterr().out.count('"workload_slot_wait"') == 1
    daemon.running.pop()
    executor.step(job_request)
    assert daemon.created == 1


def test_a_job_holding_a_slot_is_never_blocked(executor: Executor, job_request: Request) -> None:
    executor.settings = replace(executor.settings, max_running_jobs=1)
    daemon = FakeDocker(executor)
    executor.docker = daemon
    daemon.running = [job_request.job_id]
    assert executor.slot_available(job_request)
    daemon.running = [str(uuid4())]
    assert not executor.slot_available(job_request)


def test_a_job_never_created_before_its_deadline_is_logged_as_error(
    executor: Executor, job_request: Request, capsys: pytest.CaptureFixture[str]
) -> None:
    executor.settings = replace(executor.settings, max_running_jobs=1)
    daemon = FakeDocker(executor)
    executor.docker = daemon
    daemon.running = [str(uuid4())]
    executor.step(job_request)
    cancel(executor, job_request)
    executor.step(job_request)
    assert status(executor, job_request).execution == "never_started"
    logged = [json.loads(line) for line in capsys.readouterr().out.splitlines() if "job_never_created" in line]
    assert logged == [
        {"event": "job_never_created", "level": "error", "job_id": job_request.job_id, "waited_for_slot": True}
    ]


def test_install_without_a_registry_network_is_reported_not_attempted(
    executor: Executor, judge_request: Request
) -> None:
    outcome = ApplicationRunner(executor, judge_request, {}).execute(
        {"id": "step-1", "install": {"ecosystem": "pypi", "manifest": "requirements.txt"}, "observation": "x"}
    )
    assert outcome["install_unavailable"] is True and outcome["exit_code"] == 1 and outcome["id"] == "step-1"


@pytest.mark.parametrize(
    "install",
    [
        {"ecosystem": "cargo", "manifest": "Cargo.toml"},
        {"ecosystem": "npm", "manifest": "requirements.txt"},
        {"ecosystem": "pypi", "manifest": "../requirements.txt"},
        {"ecosystem": "pypi"},
    ],
)
def test_invalid_install_requests_are_rejected(executor: Executor, judge_request: Request, install: object) -> None:
    with pytest.raises(ProtocolError):
        ApplicationRunner(executor, judge_request, {}).execute({"id": "step-1", "install": install, "observation": "x"})
