"""Standalone executor protocol, uncertainty and durable at-most-once checks."""

import json
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import override
from uuid import uuid4

import pytest
from validator.records import JobStatus

from .executor import (
    Docker,
    DockerError,
    Executor,
    Files,
    ProtocolError,
    Request,
    Settings,
    encode,
    now,
    object_value,
    parse,
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
        super().__init__(executor.settings, executor.files, executor.metrics)
        self.container: dict[str, object] | None = None
        self.offline = False
        self.pull_error = False
        self.created = 0
        self.started = 0

    @override
    def inspect(self, request: Request) -> dict[str, object] | None:
        if self.offline:
            raise DockerError("daemon unavailable")
        return self.container

    @override
    def call(self, *args: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, int(self.pull_error), "", "fixture pull failure" if self.pull_error else ""
        )

    @override
    def create(self, request: Request) -> str:
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
    def create(self, request: Request) -> str:
        identifier = super().create(request)
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
