#!/usr/bin/env python3
"""Host-only FactoryHorde executor: standard library, shared files and Docker CLI."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import signal
import stat
import subprocess
import threading
import time
from collections.abc import Generator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import FrameType
from typing import cast
from uuid import UUID, uuid4

PROTOCOL_VERSION = 1
LIMIT = 1024 * 1024
IDENTITY_FIELDS = (
    "protocol_version",
    "round_id",
    "job_id",
    "miner_hotkey",
    "kind",
    "factory_job_id",
)
REQUEST_FIELDS = {
    *IDENTITY_FIELDS,
    "image",
    "platform",
    "contract",
    "input_dir",
    "output_dir",
    "report_dir",
    "created_at",
    "deadline",
    "stop_grace_seconds",
}
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class ProtocolError(ValueError):
    """A record or Docker observation violates the fixed protocol."""


class DockerError(RuntimeError):
    """Docker did not provide reliable evidence; this never proves termination."""


class DockerRejected(DockerError):
    """The CLI returned nonzero; Docker inspection must still establish execution."""


def object_value(value: object) -> dict[str, object]:
    """Require a JSON object with string keys.

    Raises:
        ProtocolError: The value is not an object.
    """
    if not isinstance(value, dict):
        raise ProtocolError("Expected a JSON object")
    if not all(isinstance(key, str) for key in cast(dict[object, object], value)):
        raise ProtocolError("Expected text object keys")
    return cast(dict[str, object], value)


def string(value: object) -> str:
    """Require text without implicit coercion.

    Raises:
        ProtocolError: The value is not text.
    """
    if not isinstance(value, str):
        raise ProtocolError("Expected text")
    return value


def integer(value: object) -> int:
    """Require an integer, excluding bool.

    Raises:
        ProtocolError: The value is not an integer.
    """
    if type(value) is not int:
        raise ProtocolError("Expected integer")
    return value


def timestamp(value: object) -> datetime:
    """Parse an aware timestamp and normalize it to UTC.

    Raises:
        ProtocolError: The timestamp is naive.
    """
    result = datetime.fromisoformat(string(value))
    if result.tzinfo is None:
        raise ProtocolError("Timestamp must be timezone-aware")
    return result.astimezone(UTC)


def now() -> str:
    """UTC wire timestamp."""
    return datetime.now(UTC).isoformat()


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject ambiguous object keys.

    Raises:
        ProtocolError: A key occurs more than once.
    """
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("Duplicate JSON key")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    """Reject non-JSON numeric constants.

    Raises:
        ProtocolError: Always.
    """
    raise ProtocolError(f"Invalid JSON constant {value}")


def parse(content: bytes | str) -> dict[str, object]:
    """Decode one complete, unambiguous JSON object."""
    value: object = json.loads(content, object_pairs_hook=unique_object, parse_constant=reject_constant)
    return object_value(value)


def encode(value: dict[str, object]) -> bytes:
    """Canonical representation used by immutable records and Docker labels."""
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def relative_parts(value: str) -> tuple[str, ...]:
    """Validate a relative POSIX path before any filesystem operation.

    Raises:
        ProtocolError: The path contains traversal, controls or separators outside POSIX.
    """
    parts = tuple(value.split("/"))
    if any(part in ("", ".", "..") for part in parts) or "\\" in value or any(ord(c) < 32 for c in value):
        raise ProtocolError("Invalid relative path")
    return parts


class Files:
    """Descriptor-relative no-follow I/O with durable atomic record publication."""

    def __init__(self, root: Path):
        if not root.is_absolute() or ".." in root.parts or any(c in str(root) for c in (",", "\n", "\r")):
            raise ProtocolError("Data root must be absolute and canonical")
        self.root = root
        with self.directory((), create=False):
            pass

    @contextmanager
    def directory(self, parts: tuple[str, ...], *, create: bool = False) -> Generator[int]:
        """Open each component without following symlinks."""
        fd = os.open("/", DIRECTORY_FLAGS)
        try:
            for component in self.root.parts[1:]:
                child = os.open(component, DIRECTORY_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = child
            for component in parts:
                if create:
                    try:
                        os.mkdir(component, mode=0o750, dir_fd=fd)
                    except FileExistsError:
                        pass
                    os.fsync(fd)
                child = os.open(component, DIRECTORY_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = child
            yield fd
        finally:
            os.close(fd)

    def read_bytes(self, relative: str) -> bytes:
        """Read a bounded regular file without symlink or FIFO traversal.

        Raises:
            ProtocolError: The file is not bounded and regular.
        """
        parts = relative_parts(relative)
        with self.directory(parts[:-1]) as directory:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, "rb") as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise ProtocolError("Expected regular file")
                content = source.read(LIMIT + 1)
        if len(content) > LIMIT:
            raise ProtocolError("Record exceeds one MiB")
        return content

    def read(self, relative: str) -> dict[str, object]:
        """Read an unambiguous record."""
        return parse(self.read_bytes(relative))

    def write(self, relative: str, value: dict[str, object], *, immutable: bool = False) -> None:
        """Atomically publish and fsync a record; preserve conflicting evidence.

        Raises:
            ProtocolError: Immutable identity already contains different content.
        """
        payload = encode(value)
        parts = relative_parts(relative)
        with self.directory(parts[:-1], create=True) as directory:
            temporary = f".{parts[-1]}.{uuid4()}.tmp"
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640, dir_fd=directory)
            try:
                with os.fdopen(fd, "wb") as output:
                    output.write(payload)
                    output.flush()
                    os.fsync(output.fileno())
                if immutable:
                    try:
                        os.link(
                            temporary,
                            parts[-1],
                            src_dir_fd=directory,
                            dst_dir_fd=directory,
                            follow_symlinks=False,
                        )
                    except FileExistsError as error:
                        if self.read_bytes(relative) != payload:
                            raise ProtocolError("Conflicting immutable job identity") from error
                else:
                    try:
                        existing = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        if not stat.S_ISREG(existing.st_mode):
                            raise ProtocolError("Cannot replace a nonregular record")
                    os.replace(temporary, parts[-1], src_dir_fd=directory, dst_dir_fd=directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
                os.fsync(directory)

    def names(self, relative: str) -> tuple[str, ...]:
        """List committed names in a protocol directory."""
        with self.directory(relative_parts(relative), create=True) as fd:
            return tuple(sorted(os.listdir(fd)))

    def mount(self, relative: str) -> str:
        """Resolve an existing directory for a fixed Docker mount."""
        parts = relative_parts(relative)
        with self.directory(parts):
            return str(self.root.joinpath(*parts))


@dataclass(frozen=True)
class Request:
    """Validated immutable execution authorization."""

    record: dict[str, object]
    job_id: str
    kind: str
    input_dir: str
    output_dir: str
    report_dir: str | None
    image: str
    deadline: datetime
    grace: int

    @classmethod
    def load(cls, value: dict[str, object], filename: str) -> Request:
        """Validate the version-one request before any Docker side effect.

        Raises:
            ProtocolError: Identity, registry, paths or the fixed contract is invalid.
        """
        if set(value) != REQUEST_FIELDS or integer(value["protocol_version"]) != PROTOCOL_VERSION:
            raise ProtocolError("Unsupported request fields or version")
        for key in ("job_id", "round_id", "factory_job_id"):
            identity = string(value[key])
            if UUID(identity).version != 4 or str(UUID(identity)) != identity:
                raise ProtocolError("Expected canonical UUID4 identity")
        job_id, kind = string(value["job_id"]), string(value["kind"])
        if filename != f"{job_id}.json" or kind not in ("factory", "judge"):
            raise ProtocolError("Request filename or kind mismatch")
        if (job_id == value["factory_job_id"]) != (kind == "factory"):
            raise ProtocolError("Factory linkage mismatch")
        if value["platform"] != "linux/amd64" or value["contract"] != f"{kind}-v1":
            raise ProtocolError("Unsupported platform or execution contract")
        hotkey = string(value["miner_hotkey"])
        if re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{48}", hotkey) is None:
            raise ProtocolError("Invalid miner hotkey")
        image = string(value["image"])
        pattern = r"ghcr\.io/[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+@sha256:[0-9a-f]{64}"
        if len(image) > 1024 or re.fullmatch(pattern, image) is None:
            raise ProtocolError("Expected immutable GHCR reference")
        input_dir, output_dir = string(value["input_dir"]), string(value["output_dir"])
        parts = relative_parts(input_dir)
        if len(parts) != 5 or parts[0] != "rounds" or parts[-2:] != (hotkey, "input"):
            raise ProtocolError("Input must belong to this job's canonical round/miner directory")
        if not parts[2].endswith("-" + string(value["round_id"])):
            raise ProtocolError("Round directory identity mismatch")
        base = "/".join(parts[:-1])
        report_dir = None if value["report_dir"] is None else string(value["report_dir"])
        if output_dir != f"{base}/output" or report_dir != (f"{base}/evaluation" if kind == "judge" else None):
            raise ProtocolError("Output/report paths differ from the fixed job contract")
        created, deadline = timestamp(value["created_at"]), timestamp(value["deadline"])
        grace = integer(value["stop_grace_seconds"])
        if created >= deadline or not 0 <= grace <= 60:
            raise ProtocolError("Invalid execution deadline or stop grace")
        return cls(
            value,
            job_id,
            kind,
            input_dir,
            output_dir,
            report_dir,
            image,
            deadline,
            grace,
        )

    @property
    def name(self) -> str:
        """Stable Docker name; never replace it with a new execution."""
        return f"factory-horde-{self.job_id}"

    @property
    def digest(self) -> str:
        """Bind Docker identity to the complete immutable request."""
        return hashlib.sha256(encode(self.record)).hexdigest()

    def identity(self) -> dict[str, object]:
        """Copy only protocol correlation fields into an observation."""
        return {key: self.record[key] for key in IDENTITY_FIELDS}


@dataclass(frozen=True)
class Settings:
    """Host-selected resources; requests cannot override these."""

    root: Path
    uid: int
    gid: int
    memory: str = "512m"
    cpus: float = 1
    pids: int = 128
    poll_seconds: float = 2
    workers: int = 32


class Metrics:
    """Standard-library event counters and cumulative latency histogram buckets."""

    bounds = (0.1, 0.5, 1, 5, 30, 120, 600)

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.counts: dict[tuple[str, str], int] = {}
        self.totals: dict[str, float] = {}
        self.buckets: dict[str, list[int]] = {}

    def observe(self, operation: str, outcome: str, seconds: float) -> None:
        """Record one event and its duration with bounded-cardinality labels."""
        with self.lock:
            key = (operation, outcome)
            self.counts[key] = self.counts.get(key, 0) + 1
            self.totals[operation] = self.totals.get(operation, 0) + seconds
            buckets = self.buckets.setdefault(operation, [0] * (len(self.bounds) + 1))
            for i, bound in enumerate((*self.bounds, float("inf"))):
                buckets[i] += int(seconds <= bound)

    def snapshot(self) -> dict[str, object]:
        """Expose counters, histogram data and heartbeat through the shared tree."""
        with self.lock:
            return {
                "protocol_version": PROTOCOL_VERSION,
                "observed_at": now(),
                "pid": os.getpid(),
                "factory_horde_executor_operations_total": [
                    {"operation": operation, "outcome": outcome, "value": count}
                    for (operation, outcome), count in sorted(self.counts.items())
                ],
                "factory_horde_executor_operation_seconds": [
                    {
                        "operation": operation,
                        "sum": self.totals[operation],
                        "count": buckets[-1],
                        "bounds": [*self.bounds, "+Inf"],
                        "buckets": list(buckets),
                    }
                    for operation, buckets in sorted(self.buckets.items())
                ],
            }


class Docker:
    """Docker is operated only here, using argument arrays and fixed commands."""

    def __init__(self, settings: Settings, files: Files, metrics: Metrics, shutdown: threading.Event):
        self.settings, self.files = settings, files
        self.metrics = metrics
        self.shutdown = shutdown
        with files.directory(("control", "executor", "docker-config"), create=True):
            pass
        self.config_dir = files.mount("control/executor/docker-config")
        if files.names("control/executor/docker-config"):
            raise ProtocolError("Executor Docker configuration must remain empty for anonymous pulls")

    def call(self, *args: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
        """Run a bounded, interruptible CLI call; interruption proves nothing about Docker state.

        Raises:
            DockerError: Executor shutdown interrupted the client operation.
            subprocess.TimeoutExpired: The operation exceeded its timeout.
        """
        started = time.monotonic()
        code: int | None = None
        try:
            if self.shutdown.is_set():
                raise DockerError("Executor shutting down")
            with subprocess.Popen(
                ["docker", "--config", self.config_dir, *args],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            ) as process:
                try:
                    while True:
                        if self.shutdown.is_set():
                            raise DockerError("Executor shutting down during Docker operation")
                        remaining = timeout - (time.monotonic() - started)
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(process.args, timeout)
                        try:
                            stdout, stderr = process.communicate(timeout=min(0.2, remaining))
                            break
                        except subprocess.TimeoutExpired:
                            continue
                    code = process.wait()
                    if self.shutdown.is_set() or code < 0:
                        raise DockerError("Docker client interrupted; execution requires reconciliation")
                    return subprocess.CompletedProcess(process.args, code, stdout, stderr)
                finally:
                    # Only the local CLI group is killed. Detached workloads belong to dockerd;
                    # durable create/start/stop intent is reconciled by the next executor.
                    if code is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    process.communicate()
        finally:
            seconds = time.monotonic() - started
            self.metrics.observe(args[0], "ok" if code == 0 else "error", seconds)
            print(
                json.dumps(
                    {
                        "event": "docker_operation",
                        "operation": args[0],
                        "exit_code": code,
                        "seconds": seconds,
                    }
                ),
                flush=True,
            )

    def require(self, *args: str, timeout: float = 30) -> str:
        """Require CLI success, preserving failures as uncertainty.

        Raises:
            DockerRejected: The CLI completed with a failure response.
        """
        result = self.call(*args, timeout=timeout)
        if result.returncode:
            raise DockerRejected(result.stderr.strip()[:512] or "Docker command failed")
        return result.stdout.strip()

    def inspect(self, request: Request) -> dict[str, object] | None:
        """Only an explicit not-found response means no named container exists.

        Raises:
            DockerError: Docker is unavailable or gives malformed evidence.
            ProtocolError: Docker name/labels refer to another authorization.
        """
        result = self.call("container", "inspect", request.name, "--format", "{{json .}}")
        if result.returncode:
            if "No such container:" in result.stderr or "No such object:" in result.stderr:
                return None
            raise DockerError(result.stderr.strip()[:512])
        value = parse(result.stdout)
        config = object_value(value["Config"])
        labels = object_value(config["Labels"])
        if (
            value["Name"] != "/" + request.name
            or labels.get("factory_horde.request_sha256") != request.digest
            or labels.get("factory_horde.job_id") != request.job_id
            or config["Image"] != request.image
        ):
            raise ProtocolError("Existing Docker container conflicts with immutable request")
        return value

    def create(self, request: Request) -> str:
        """Create a detached, non-restarting workload with only its fixed job mounts."""
        destination = "/output" if request.kind == "factory" else "/submission,readonly"
        mounts = [
            f"type=bind,src={self.files.mount(request.input_dir)},dst=/input,readonly",
            f"type=bind,src={self.files.mount(request.output_dir)},dst={destination}",
        ]
        if request.report_dir is not None:
            mounts.append(f"type=bind,src={self.files.mount(request.report_dir)},dst=/report")
        args = [
            "create",
            "--name",
            request.name,
            "--platform",
            "linux/amd64",
            "--pull",
            "never",
            "--restart",
            "no",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            f"{self.settings.uid}:{self.settings.gid}",
            "--memory",
            self.settings.memory,
            "--cpus",
            str(self.settings.cpus),
            "--pids-limit",
            str(self.settings.pids),
            "--log-driver",
            "local",
            "--log-opt",
            "max-size=10m",
            "--log-opt",
            "max-file=3",
            "--label",
            f"factory_horde.job_id={request.job_id}",
            "--label",
            f"factory_horde.request_sha256={request.digest}",
            "--entrypoint",
            f"/usr/local/bin/factory-horde-{request.kind}",
        ]
        for mount in mounts:
            args.extend(("--mount", mount))
        return self.require(*args, request.image)


class Executor:
    """Persist intent before Docker effects; reconstruct outcomes from Docker after restart."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.files = Files(settings.root)
        self.metrics = Metrics()
        self.shutdown = threading.Event()
        self.docker = Docker(settings, self.files, self.metrics, self.shutdown)
        self.active: dict[str, Future[None]] = {}
        self.started_at = now()
        self.observed_jobs: set[str] = set()
        self.completed_jobs: set[str] = set()

    def cancelled(self, request: Request) -> bool:
        """A valid permanent stop or elapsed deadline closes startup.

        Raises:
            ProtocolError: A stop record refers to different work.
        """
        try:
            stop = self.files.read(f"control/stops/{request.job_id}.json")
        except FileNotFoundError:
            return datetime.now(UTC) >= request.deadline
        if (
            set(stop) != {*IDENTITY_FIELDS, "requested_at", "reason"}
            or integer(stop["protocol_version"]) != PROTOCOL_VERSION
            or any(stop[key] != value for key, value in request.identity().items())
        ):
            raise ProtocolError("Stop identity mismatch")
        timestamp(stop["requested_at"])
        if stop["reason"] not in ("deadline", "operator", "recovery"):
            raise ProtocolError("Unsupported stop reason")
        return True

    def save_ledger(self, request: Request, ledger: dict[str, object]) -> None:
        """Persist side-effect intent and startup closure before acting."""
        self.files.write(f"control/executor/{request.job_id}.json", ledger)

    def status(self, request: Request, ledger: dict[str, object], **facts: object) -> dict[str, object]:
        """Publish a current observation; terminal evidence is retained in the executor ledger."""
        value = {
            **request.identity(),
            "state": "failed",
            "execution": "unresolved",
            "startup_forbidden": bool(ledger.get("closed", False)),
            "observed_at": now(),
            "container_id": ledger.get("container_id"),
            "container_name": request.name if ledger.get("container_id") else None,
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "forced": False,
            "oom_killed": False,
            "reason": None,
            **facts,
        }
        if value["execution"] in ("exited", "never_started"):
            ledger.update(closed=True, terminal=value)
            self.save_ledger(request, ledger)
        self.files.write(f"control/statuses/{request.job_id}.json", value)
        if value["execution"] in ("exited", "never_started"):
            self.completed_jobs.add(request.job_id)
            print(json.dumps({"event": "job_terminal", **value}), flush=True)
        return value

    def observe(self, request: Request, ledger: dict[str, object], container: dict[str, object]) -> None:
        """Record facts from Docker, never infer stop from a timeout or an absent process.

        Raises:
            ProtocolError: Docker identity or timestamps do not prove the claimed execution.
        """
        identifier = string(container["Id"])
        if re.fullmatch(r"[a-f0-9]{64}", identifier) is None:
            raise ProtocolError("Invalid Docker container ID")
        state = object_value(container["State"])
        ledger["container_id"] = identifier
        self.save_ledger(request, ledger)
        if (
            state["Status"] == "created"
            and state["Running"] is False
            and state.get("Error")
            and integer(state["ExitCode"]) != 0
            and timestamp(state["StartedAt"]).year == 1
            and timestamp(state["FinishedAt"]).year == 1
        ):
            ledger.update(phase="start_rejected", closed=True)
            self.status(
                request,
                ledger,
                state="failed",
                execution="never_started",
                startup_forbidden=True,
                reason=("Docker rejected startup: " + string(state["Error"]))[:512],
            )
        elif state["Status"] in ("exited", "dead"):
            started, finished = (
                timestamp(state["StartedAt"]),
                timestamp(state["FinishedAt"]),
            )
            code = integer(state["ExitCode"])
            if finished.year == 1 or started.year == 1 or finished < started:
                raise ProtocolError("Docker exit timestamps do not prove a completed execution")
            forced, oom = (
                ledger.get("forced", False) is True,
                state["OOMKilled"] is True,
            )
            clean = code == 0 and not forced and not oom
            self.status(
                request,
                ledger,
                state="finished" if clean else "failed",
                execution="exited",
                startup_forbidden=True,
                started_at=started.isoformat(),
                finished_at=finished.isoformat(),
                exit_code=code,
                forced=forced,
                oom_killed=oom,
                reason=None if clean else "Factory/judge did not exit cleanly",
            )
        elif state["Running"] is True:
            self.status(
                request,
                ledger,
                state="stopping" if ledger.get("closed") else "running",
                execution="running",
                started_at=timestamp(state["StartedAt"]).isoformat(),
            )
        else:
            self.status(request, ledger, state="preparing", execution="unresolved")

    def stop(self, request: Request, ledger: dict[str, object], container: dict[str, object]) -> None:
        """Advance durable TERM/KILL intent without blocking another job's polling.

        Raises:
            DockerError: The expected container disappeared instead of confirming stop.
        """
        ledger["closed"] = True
        self.save_ledger(request, ledger)
        state = object_value(container["State"])
        if state["Status"] == "created" and ledger.get("phase") not in ("start_attempted", "start_rejected"):
            ledger["container_id"] = string(container["Id"])
            self.status(
                request,
                ledger,
                state="cancelled",
                execution="never_started",
                startup_forbidden=True,
                reason="Cancelled before startup",
            )
            return
        if state["Running"] is True:
            stop_by = ledger.get("stop_by")
            first_stop = stop_by is None
            if stop_by is None:
                stop_by = (datetime.now(UTC) + timedelta(seconds=request.grace)).isoformat()
                ledger["stop_by"] = stop_by
                self.save_ledger(request, ledger)
            if not first_stop and datetime.now(UTC) >= timestamp(stop_by):
                ledger["forced"] = True
                self.save_ledger(request, ledger)
                self.docker.call("kill", "--signal", "KILL", request.name)
            elif ledger.get("term_sent") is not True:
                result = self.docker.call("kill", "--signal", "TERM", request.name)
                if result.returncode == 0:
                    ledger["term_sent"] = True
                    self.save_ledger(request, ledger)
        observed = self.docker.inspect(request)
        if observed is None:
            raise DockerError("Expected container disappeared during stop")
        self.observe(request, ledger, observed)

    def step(self, request: Request) -> None:
        """Advance one job without substituting an image or rerunning an observed execution.

        Raises:
            ProtocolError: Immutable authorization conflicts, or status cannot be persisted.
            DockerError: Missing execution evidence, normally converted to an unresolved status.
            DockerRejected: A completed CLI failure, recorded for reconciliation.
        """
        if self.shutdown.is_set():
            return
        self.files.write(
            f"control/executor/{request.job_id}.request.json",
            request.record,
            immutable=True,
        )
        ledger: dict[str, object] = {}
        try:
            try:
                ledger = self.files.read(f"control/executor/{request.job_id}.json")
            except FileNotFoundError:
                ledger = {"phase": "reserved", "closed": False}
                self.save_ledger(request, ledger)
            terminal = ledger.get("terminal")
            if terminal is not None:
                terminal = object_value(terminal)
                try:
                    current = self.files.read(f"control/statuses/{request.job_id}.json")
                except FileNotFoundError:
                    current = None
                if current != terminal:
                    self.files.write(f"control/statuses/{request.job_id}.json", terminal)
                self.completed_jobs.add(request.job_id)
                return
            container = self.docker.inspect(request)
            closed = self.cancelled(request) or ledger.get("closed") is True
            if not closed and datetime.now(UTC) < timestamp(request.record["created_at"]):
                self.status(request, ledger, state="pending", execution="unresolved")
                return
            if container is not None:
                expected = ledger.get("container_id")
                if (expected is not None and expected != container["Id"]) or ledger.get("phase") == "reserved":
                    raise DockerError("Unexpected Docker identity; replacement is forbidden")
                state = object_value(container["State"])
                if state["Status"] == "created" and state.get("Error"):
                    self.observe(request, ledger, container)
                elif closed:
                    self.stop(request, ledger, container)
                elif state["Status"] == "created" and ledger.get("phase") != "start_attempted":
                    ledger.update(phase="start_attempted", container_id=string(container["Id"]))
                    self.save_ledger(request, ledger)
                    if self.shutdown.is_set() or self.cancelled(request):
                        ledger["phase"] = "created"
                        self.save_ledger(request, ledger)
                        if not self.shutdown.is_set():
                            self.stop(request, ledger, container)
                        return
                    try:
                        self.docker.require("start", request.name)
                    except DockerRejected:
                        ledger["phase"] = "start_rejected"
                        ledger["closed"] = True
                        self.save_ledger(request, ledger)
                        raise
                else:
                    self.observe(request, ledger, container)
                return
            if ledger.get("phase") == "create_rejected":
                self.status(
                    request,
                    ledger,
                    state="failed",
                    execution="never_started",
                    startup_forbidden=True,
                    reason="Docker rejected creation",
                )
                return
            if ledger.get("phase") in (
                "create_attempted",
                "created",
                "start_attempted",
            ) or ledger.get("container_id"):
                raise DockerError("Expected Docker execution is missing; replacement is forbidden")
            if closed:
                self.status(
                    request,
                    ledger,
                    state="cancelled",
                    execution="never_started",
                    startup_forbidden=True,
                    reason="Cancelled before creation",
                )
                return
            self.status(request, ledger, state="preparing", execution="unresolved")
            manifest = self.files.read(f"{request.input_dir}/task.json")
            if (
                set(manifest)
                != {
                    "protocol_version",
                    "round_id",
                    "miner_hotkey",
                    "factory_job_id",
                    "judge_job_id",
                    "specification_sha256",
                }
                or integer(manifest["protocol_version"]) != PROTOCOL_VERSION
            ):
                raise ProtocolError("Invalid input manifest fields or version")
            for key in ("round_id", "miner_hotkey", "factory_job_id"):
                if manifest.get(key) != request.record[key]:
                    raise ProtocolError("Input manifest attribution mismatch")
            if request.kind == "judge" and manifest.get("judge_job_id") != request.job_id:
                raise ProtocolError("Input judge linkage mismatch")
            content = self.files.read_bytes(f"{request.input_dir}/specification.md")
            if hashlib.sha256(content).hexdigest() != manifest.get("specification_sha256"):
                raise ProtocolError("Input specification hash mismatch")
            pull = self.docker.call("pull", "--platform", "linux/amd64", request.image, timeout=600)
            if pull.returncode:
                self.status(
                    request,
                    ledger,
                    state="failed",
                    execution="never_started",
                    startup_forbidden=True,
                    reason=("Image pull failed: " + pull.stderr.strip())[:512],
                )
                return
            if self.shutdown.is_set():
                return
            if self.cancelled(request):
                self.status(
                    request,
                    ledger,
                    state="cancelled",
                    execution="never_started",
                    startup_forbidden=True,
                    reason="Startup closed after pull",
                )
                return
            ledger["phase"] = "create_attempted"
            self.save_ledger(request, ledger)
            try:
                identifier = self.docker.create(request)
            except DockerRejected:
                ledger.update(phase="create_rejected", closed=True)
                self.save_ledger(request, ledger)
                raise
            ledger.update(phase="created", container_id=identifier)
            self.save_ledger(request, ledger)
        except (OSError, ValueError, KeyError, DockerError, subprocess.SubprocessError) as error:
            self.status(request, ledger, reason=str(error)[:512] or type(error).__name__)
            print(
                json.dumps(
                    {
                        "event": "job_unresolved",
                        "job_id": request.job_id,
                        "round_id": request.record["round_id"],
                        "reason": str(error)[:512],
                    }
                ),
                flush=True,
            )

    def poll(self, pool: ThreadPoolExecutor) -> None:
        """Reconcile retained jobs once, then schedule only work without durable terminal status."""
        rejected = 0
        for job_id, future in tuple(self.active.items()):
            if future.done():
                del self.active[job_id]
                try:
                    future.result()
                    self.observed_jobs.add(job_id)
                except (OSError, ValueError) as error:
                    self.observed_jobs.discard(job_id)
                    rejected += 1
                    print(
                        json.dumps({"event": "request_rejected", "job_id": job_id, "reason": str(error)[:512]}),
                        flush=True,
                    )
        requested: set[str] = set()
        for name in self.files.names("control/requests"):
            if self.shutdown.is_set():
                break
            if re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\.json", name) is None:
                continue
            if name.removesuffix(".json") in self.completed_jobs:
                requested.add(name.removesuffix(".json"))
                continue
            try:
                request = Request.load(self.files.read(f"control/requests/{name}"), name)
                requested.add(request.job_id)
                if request.job_id not in self.active:
                    self.active[request.job_id] = pool.submit(self.step, request)
            except (OSError, ValueError) as error:
                rejected += 1
                print(
                    json.dumps({"event": "request_rejected", "filename": name, "reason": str(error)[:512]}), flush=True
                )
        snapshot = self.metrics.snapshot()
        snapshot["active_workers"] = sum(not future.done() for future in self.active.values())
        self.files.write("control/executor-metrics.json", snapshot)
        self.health(requested, rejected)

    def health(self, requested: set[str], rejected: int) -> None:
        """Publish bounded daemon evidence even when there are no jobs to inspect.

        This probe never proves that any workload stopped. Job status and the
        permanent ledger remain the only execution evidence.
        """
        docker_ok = False
        reason: str | None = None
        try:
            result = self.docker.call("info", "--format", "{{.ServerVersion}}", timeout=3)
            docker_ok = result.returncode == 0 and bool(result.stdout.strip())
            if not docker_ok:
                reason = result.stderr.strip()[:512] or "No Docker server response"
        except (OSError, DockerError, subprocess.SubprocessError) as error:
            reason = str(error)[:512] or type(error).__name__
        self.files.write(
            "control/executor-health.json",
            {
                "protocol_version": PROTOCOL_VERSION,
                "started_at": self.started_at,
                "observed_at": now(),
                "pid": os.getpid(),
                "docker_observed_at": now(),
                "docker_ok": docker_ok,
                "docker_error": reason,
                "reconciled": requested <= self.observed_jobs and rejected == 0,
                "rejected_requests": rejected,
                "active_workers": sum(not future.done() for future in self.active.values()),
            },
        )

    def run(self) -> None:
        """Poll concurrently; detached Docker jobs survive service termination and replacement."""
        with ThreadPoolExecutor(max_workers=self.settings.workers, thread_name_prefix="job") as pool:
            try:
                while not self.shutdown.is_set():
                    self.poll(pool)
                    self.shutdown.wait(self.settings.poll_seconds)
            finally:
                self.shutdown.set()
                pool.shutdown(wait=True, cancel_futures=True)


def main() -> None:
    """Run one systemd-owned executor instance for an explicitly configured host root."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol-version", action="version", version=str(PROTOCOL_VERSION))
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings(
        root=cast(Path, args.root),
        uid=int(os.environ.get("CONTAINER_UID", str(os.getuid()))),
        gid=int(os.environ.get("CONTAINER_GID", str(os.getgid()))),
        memory=os.environ.get("EXECUTOR_MEMORY", "512m"),
        cpus=float(os.environ.get("EXECUTOR_CPUS", "1")),
        pids=int(os.environ.get("EXECUTOR_PIDS_LIMIT", "128")),
        poll_seconds=float(os.environ.get("EXECUTOR_POLL_SECONDS", "2")),
        workers=int(os.environ.get("EXECUTOR_WORKERS", "32")),
    )
    if (
        settings.uid < 1
        or settings.gid < 1
        or settings.cpus <= 0
        or not math.isfinite(settings.cpus)
        or settings.pids < 1
        or settings.poll_seconds <= 0
        or not math.isfinite(settings.poll_seconds)
        or not 5 <= settings.workers <= 256
    ):
        parser.error("Require non-root UID/GID and positive CPU, PID and polling settings")
    executor = Executor(settings)

    def stop_signal(_number: int, _frame: FrameType | None) -> None:
        executor.shutdown.set()

    with executor.files.directory(("control", "executor"), create=True) as directory:
        lock = os.open(
            "service.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o640,
            dir_fd=directory,
        )
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        signal.signal(signal.SIGTERM, stop_signal)
        signal.signal(signal.SIGINT, stop_signal)
        executor.run()
    finally:
        os.close(lock)


if __name__ == "__main__":
    main()
