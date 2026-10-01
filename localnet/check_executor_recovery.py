"""Exercise real Docker races through a separately installed localnet systemd executor."""

import argparse
import hashlib
import json
import shutil
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values
from pydantic import BaseModel
from validator.records import CohortMember, Deadlines, JobRequest, JobStatus, RoundPlan, StopRequest, request_for
from validator.round_repository import RoundRepository

from .check import Registrations
from .check_executor import inspect, wait_status

ROOT = Path(__file__).resolve().parent
SERVICE = "factory-horde-recovery-executor"


class DockerState(BaseModel):
    """Actual execution evidence independent of executor status."""

    Status: str
    Running: bool
    ExitCode: int
    StartedAt: str
    FinishedAt: str


class Inspection(BaseModel):
    """Container identity and lifetime used for crash acceptance."""

    Id: str
    State: DockerState


def docker(*args: str) -> str:
    """Run the genuine CLI, bypassing the executor's fault wrapper."""
    return subprocess.run(["/usr/bin/docker", *args], capture_output=True, text=True, check=True).stdout.strip()


def systemctl(*args: str) -> None:
    """Control only the isolated acceptance service."""
    subprocess.run(["sudo", "systemctl", *args, SERVICE], check=True)


def until(check: Callable[[], bool], reason: str, seconds: float = 90) -> None:
    """Wait for a concrete observable boundary, with an explicit failure deadline.

    Raises:
        RuntimeError: The expected evidence did not appear.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.25)
    raise RuntimeError(reason)


class Check:
    """Separate data root retains deliberate unresolved evidence without blocking normal rounds."""

    def __init__(self, image: str):
        config = dotenv_values(ROOT / ".env")
        if config.get("ENVIRONMENT") != "localnet" or config.get("NETUID") != "2":
            raise RuntimeError("Requires isolated localnet")
        self.base = ROOT / "state/executor-acceptance"
        self.root = self.base / "data"
        self.root.mkdir(parents=True, exist_ok=True)
        self.repository = RoundRepository(self.root)
        self.image = image
        self.fault_root = self.root / "control/executor-faults"
        self.fault_root.mkdir(parents=True, exist_ok=True)
        self.dropin = Path("/etc/systemd/system") / (SERVICE + ".service.d") / "faults.conf"
        self.evidence: dict[str, object] = {"checked_at": datetime.now(UTC).isoformat(), "fixture_image": image}
        self.registrations = Registrations.model_validate_json((ROOT / "state/registrations.json").read_bytes())
        self.plan_by_job: dict[str, RoundPlan] = {}
        self.jobs: list[JobRequest] = []
        selected = {
            key: config[key]
            for key in (
                "EXECUTOR_USER",
                "EXECUTOR_GROUP",
                "EXECUTOR_PYTHON",
                "CONTAINER_UID",
                "CONTAINER_GID",
                "EXECUTOR_MEMORY",
                "EXECUTOR_CPUS",
                "EXECUTOR_PIDS_LIMIT",
                "EXECUTOR_POLL_SECONDS",
            )
        }
        selected["FACTORY_HORDE_DATA_ROOT"] = str(self.root)
        env_path = self.base / "executor.env"
        env_path.write_text("".join(f"{key}={value}\n" for key, value in selected.items()))
        env_path.chmod(0o600)
        subprocess.run([str(ROOT.parent / "installer/install-executor.sh"), str(env_path), SERVICE], check=True)
        binary = self.base / "fault-bin/docker"
        binary.parent.mkdir(exist_ok=True)
        staged_binary = binary.with_name(f".docker-{uuid4()}.tmp")
        shutil.copyfile(ROOT / "executor_faults.py", staged_binary)
        staged_binary.chmod(0o555)
        staged_binary.replace(binary)
        staged = self.base / "faults.conf"
        staged.write_text(
            f'[Service]\nEnvironment="PATH={binary.parent}:/usr/bin:/bin"\n'
            f'Environment="FACTORY_HORDE_FAULT_ROOT={self.fault_root}"\n'
        )
        subprocess.run(["sudo", "mkdir", "-p", str(self.dropin.parent)], check=True)
        subprocess.run(["sudo", "install", "-m", "0644", str(staged), str(self.dropin)], check=True)
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
        self.clear_fault()
        systemctl("restart")

    def clear_fault(self) -> None:
        """Release any delayed response and remove this test's interception."""
        (self.fault_root / "release").touch()
        (self.fault_root / "fault.json").unlink(missing_ok=True)

    def arm(self, operation: str, match: str, behavior: str = "hold") -> None:
        """Intercept only one selected operation/reference or Docker job name."""
        self.clear_fault()
        for name in ("entered.json", "release"):
            (self.fault_root / name).unlink(missing_ok=True)
        temporary = self.fault_root / ".fault.tmp"
        temporary.write_text(json.dumps({"operation": operation, "match": match, "behavior": behavior}))
        temporary.replace(self.fault_root / "fault.json")

    def entered(self) -> None:
        """Wait until the real Docker command returned but the executor has not received it."""
        until(lambda: (self.fault_root / "entered.json").exists(), "Fault boundary not reached")

    def crash(self) -> None:
        """Kill the executor and CLI children, leaving detached Docker state intact.

        Raises:
            RuntimeError: Systemd could not kill the selected service.
        """
        systemctl("stop", "--no-block")
        result = subprocess.run(
            ["sudo", "systemctl", "kill", "--signal=SIGKILL", "--kill-whom=all", SERVICE],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode and "not running" not in result.stderr:
            raise RuntimeError(result.stderr)
        until(
            lambda: subprocess.run(["systemctl", "is-active", "--quiet", SERVICE], check=False).returncode != 0,
            "Executor did not stop",
            10,
        )
        self.clear_fault()

    def make(self, mode: str, count: int = 1, image: str | None = None) -> tuple[JobRequest, ...]:
        """Prepare real protocol inputs with fresh IDs and five-minute fixture deadlines."""
        started = datetime.now(UTC)
        miners = tuple(row for row in self.registrations.registrations if row.identity.startswith("miner"))[:count]
        cohort = tuple(CohortMember(miner_hotkey=m.hotkey, uid=m.uid, image=image or self.image) for m in miners)
        plan = RoundPlan(
            sequence=max((r.plan.sequence for r in self.repository.rounds()), default=0) + 1,
            deadlines=Deadlines(
                start=started,
                generation_end=started + timedelta(minutes=5),
                evaluation_start=started + timedelta(minutes=6),
                judge_end=started + timedelta(minutes=7),
                round_end=started + timedelta(minutes=8),
            ),
            discovery_block=self.registrations.block,
            discovery_block_hash=self.registrations.block_hash,
            membership_snapshot="separate_reads",
            cohort=cohort,
            judge_image=self.image,
            stop_grace_seconds=5,
            specification_sha256=hashlib.sha256(mode.encode()).hexdigest(),
        )
        self.repository.prepare_round(plan, mode)
        jobs = tuple(request_for(plan, member, "factory") for member in cohort)
        for job in jobs:
            self.plan_by_job[str(job.job_id)] = plan
        self.jobs.extend(jobs)
        return jobs

    def publish(self, job: JobRequest) -> None:
        self.repository.publish_request(self.plan_by_job[str(job.job_id)], job)

    def stop(self, job: JobRequest) -> None:
        if not (self.root / f"control/stops/{job.job_id}.json").exists():
            self.repository.publish_stop(
                job,
                StopRequest(
                    **job.model_dump(
                        include={"protocol_version", "round_id", "job_id", "miner_hotkey", "kind", "factory_job_id"}
                    ),
                    requested_at=datetime.now(UTC),
                    reason="operator",
                ),
            )

    def running(self, job: JobRequest) -> bool:
        observed = self.repository.status(job)
        return observed is not None and observed.execution == "running"

    def actual(self, job: JobRequest) -> Inspection:
        return Inspection.model_validate_json(docker("inspect", job.container_name, "--format", "{{json .}}"))

    def one_start(self, job: JobRequest) -> None:
        if (self.root / job.output_dir / "starts").read_text() != "started\n":
            raise RuntimeError("Fixture executed more or less than once")

    def terminal(self, job: JobRequest) -> JobStatus:
        observed = wait_status(self.repository, job, 30)
        inspect(job, self.root, observed)
        self.one_start(job)
        return observed

    def concurrent_stops(self) -> None:
        jobs = self.make("ignore-term", 5)
        for job in jobs:
            self.publish(job)
        until(lambda: all(self.running(job) for job in jobs), "Five factories did not run concurrently")
        starts = [datetime.fromisoformat(self.actual(job).State.StartedAt.replace("Z", "+00:00")) for job in jobs]
        spread = (max(starts) - min(starts)).total_seconds()
        if spread > 12:
            raise RuntimeError("Five jobs were artificially batched")
        stop_start = time.monotonic()
        for job in jobs:
            self.stop(job)

        def term_sent() -> bool:
            return all(
                b'"term_sent":true' in (self.root / f"control/executor/{job.job_id}.json").read_bytes() for job in jobs
            )

        until(term_sent, "Graceful signals were not sent", 15)
        before = [(self.root / f"control/executor/{j.job_id}.json").read_bytes() for j in jobs]
        systemctl("restart")
        statuses = [self.terminal(job) for job in jobs]
        elapsed = time.monotonic() - stop_start
        for job, previous in zip(jobs, before, strict=True):
            retained = json.loads((self.root / f"control/executor/{job.job_id}.json").read_bytes())
            if retained["stop_by"] != json.loads(previous)["stop_by"]:
                raise RuntimeError("Service restart reset the stop grace")
        if elapsed > 20 or any(not s.forced or s.exit_code != 137 or s.application_succeeded for s in statuses):
            raise RuntimeError("Concurrent grace/force stop evidence failed")
        self.evidence["concurrent_stops"] = {
            "start_spread_seconds": spread,
            "stop_seconds": elapsed,
            "pre_restart_ledgers": [json.loads(raw) for raw in before],
            "statuses": [s.model_dump(mode="json") for s in statuses],
        }
        print(
            "PASS: five concurrent TERM-resistant jobs, durable grace across restart, inspected KILL exits", flush=True
        )

    def crash_recovery(self, operation: str, mode: str) -> None:
        (job,) = self.make(mode)
        self.arm(operation, job.container_name)
        self.publish(job)
        self.entered()
        if operation == "start" and mode == "exit":
            until(lambda: self.actual(job).State.Status == "exited", "Fixture did not exit before executor restart")
        before = self.actual(job)
        self.crash()
        if operation == "create" and before.State.Status != "created":
            raise RuntimeError("Crash did not occur after create/before start")
        systemctl("start")
        if mode == "ignore-term":
            until(lambda: self.running(job), "Running container was not recovered")
            self.stop(job)
        observed = self.terminal(job)
        if observed.container_id != before.Id:
            raise RuntimeError("Restart replaced the original container")
        self.publish(job)
        systemctl("restart")
        time.sleep(3)
        if self.repository.status(job) != observed:
            raise RuntimeError("Finalized status changed on replay")
        docker("rm", job.container_name)
        systemctl("restart")
        time.sleep(3)
        if self.repository.status(job) != observed:
            raise RuntimeError("Deleted finalized container lost retained outcome")
        self.one_start(job)
        self.evidence[f"crash_{operation}_{mode}"] = {
            "before": before.model_dump(mode="json"),
            "status": observed.model_dump(mode="json"),
        }
        print(f"PASS: crash after {operation}, {mode}, duplicate/restart/finalized deletion without rerun", flush=True)

    def pending_pull(self) -> None:
        baseline = dotenv_values(ROOT / "state/published-images.env")["GHCR_FACTORY_IMAGE"]
        if baseline is None:
            raise RuntimeError("Missing verified baseline image")
        (slow,) = self.make("unused", image=baseline)
        self.arm("pull", baseline)
        self.publish(slow)
        self.entered()
        (fast,) = self.make("exit")
        self.publish(fast)
        fast_status = self.terminal(fast)
        self.stop(slow)
        time.sleep(3)
        pending = self.repository.status(slow)
        if pending is None or pending.confirmed_stopped:
            raise RuntimeError("Outstanding startup worker acknowledged cancellation prematurely")
        self.clear_fault()
        cancelled = wait_status(self.repository, slow, 15)
        if cancelled.execution != "never_started":
            raise RuntimeError("Cancelled pull created a workload")
        systemctl("restart")
        time.sleep(3)
        missing = subprocess.run(["/usr/bin/docker", "inspect", slow.container_name], capture_output=True, check=False)
        if missing.returncode == 0 or self.repository.status(slow) != cancelled:
            raise RuntimeError("Cancelled pending job later started")
        self.evidence["slow_pull"] = {
            "fast_status": fast_status.model_dump(mode="json"),
            "during_pull": pending.model_dump(mode="json"),
            "cancelled": cancelled.model_dump(mode="json"),
        }
        print(
            "PASS: slow real pull does not starve other work; cancellation closes startup only after worker exits",
            flush=True,
        )

    def unavailable_and_missing(self) -> None:
        (job,) = self.make("ignore-term")
        self.publish(job)
        until(lambda: self.running(job), "Factory did not start")
        actual = self.actual(job)
        self.arm("container", job.container_name, "unavailable")

        def unresolved() -> bool:
            value = self.repository.status(job)
            return value is not None and value.execution == "unresolved"

        until(unresolved, "Unavailable Docker did not become unresolved")
        unavailable = self.repository.status(job)
        if unavailable is None or unavailable.confirmed_stopped:
            raise RuntimeError("Docker failure was mistaken for stop")
        self.crash()
        docker("rm", "--force", job.container_name)
        systemctl("start")
        time.sleep(4)
        missing = self.repository.status(job)
        if missing is None or missing.confirmed_stopped or "missing" not in (missing.reason or ""):
            raise RuntimeError("Missing expected execution was replaced or marked stopped")
        self.one_start(job)
        self.stop(job)
        self.evidence["unavailable_and_missing"] = {
            "actual_before": actual.model_dump(mode="json"),
            "unavailable": unavailable.model_dump(mode="json"),
            "missing": missing.model_dump(mode="json"),
        }
        print("PASS: unavailable/deleted expected execution remains unresolved, with no replacement", flush=True)

    def close(self) -> None:
        """Remove interception, stop every authorized job, then leave deliberate evidence isolated."""
        self.clear_fault()
        for job in self.jobs:
            if (self.root / f"control/requests/{job.job_id}.json").exists():
                self.stop(job)
        systemctl("start")
        time.sleep(10)
        systemctl("stop")
        subprocess.run(["sudo", "rm", "-f", str(self.dropin)], check=True)
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
        subprocess.run(["sudo", "systemctl", "disable", SERVICE], check=True)


def main() -> None:
    """Run the focused real execution acceptance and retain inspectable evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture_image")
    args = parser.parse_args()
    check = Check(str(args.fixture_image))
    try:
        check.concurrent_stops()
        check.crash_recovery("create", "exit")
        check.crash_recovery("start", "exit")
        check.crash_recovery("start", "ignore-term")
        check.pending_pull()
        check.unavailable_and_missing()
        check.evidence["executor_sha256"] = hashlib.sha256(
            (check.base / "executor/executor.py").read_bytes()
        ).hexdigest()
        (ROOT / "state/task8-recovery.json").write_text(json.dumps(check.evidence, indent=2) + "\n")
    finally:
        check.close()


if __name__ == "__main__":
    main()
