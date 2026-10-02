"""Real Linux installer/update faults with isolated Compose, systemd and detached Docker jobs."""

import hashlib
import json
import shutil
import socket
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import override
from uuid import uuid4

from dotenv import dotenv_values
from installer import release
from nexus.v1 import BlockBeat, BlockHash, BlockNumber, Timestamp
from pylon_client.artanis import Config, PylonAuthToken, PylonClient

from validator.records import CohortMember, Deadlines, JobRequest, RoundPlan, RoundRecord, StopRequest, request_for
from validator.result_repository import ResultRepository
from validator.round_repository import RoundRepository

from .check import Registrations
from .check_executor import wait_status
from .check_executor_recovery import Inspection, docker, until

REPO = Path(__file__).resolve().parents[1]


def port() -> int:
    """Choose an unused loopback port for this isolated Compose project."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class Server(ThreadingHTTPServer):
    """A revision-addressed local release origin with one controlled slow-download boundary."""

    def __init__(self, source: Path):
        self.hold = threading.Event()
        self.entered = threading.Event()
        self.released = threading.Event()
        owner = self

        class Handler(SimpleHTTPRequestHandler):
            @override
            def do_GET(self) -> None:
                if owner.hold.is_set() and self.path.endswith(release.MANIFEST):
                    owner.entered.set()
                    owner.released.wait(timeout=30)
                super().do_GET()

            @override
            def log_message(self, format: str, *args: object) -> None:
                pass

        super().__init__(("127.0.0.1", 0), partial(Handler, directory=str(source)))


class Check:
    """Retain each run's files/volumes while removing only its active services and cron job."""

    def __init__(self) -> None:
        suffix = uuid4().hex[:8]
        self.base = REPO / f"localnet/state/installer-{suffix}"
        self.directory = self.base / "installation"
        self.sources = self.base / "releases"
        self.sources.mkdir(parents=True)
        self.directory.mkdir()
        self.service = "factory-horde-installer-" + suffix
        self.project = "factory-horde-installer-" + suffix
        ordinary = dotenv_values(REPO / "localnet/.env")
        if ordinary.get("NETUID") != "2" or ordinary.get("ENVIRONMENT") != "localnet":
            raise RuntimeError("Requires isolated localnet configuration")
        self.config = dict(
            ENVIRONMENT="localnet",
            NETUID="2",
            MECHANISM_ID="1",
            SUBNET_TEMPO="360",
            BITTENSOR_NETWORK="ws://subtensor:9944",
            BLOCK_DURATION_SECONDS="0.25",
            SUBTENSOR_HOST_PORT=str(port()),
            PYLON_HOST_PORT=str(port()),
            PROMETHEUS_HOST_PORT=str(port()),
            VALIDATOR_IMAGE=(REPO / "localnet/state/validator-image.id").read_text().strip(),
            VALIDATOR_DISPATCH_ENABLED="false",
            VALIDATOR_WEIGHTS_ENABLED="false",
        )
        self.env_file = self.base / "operator.env"
        self.env_file.write_bytes(release.environment(self.config))
        self.env_file.chmod(0o600)
        self.revisions: dict[str, str] = {}
        for name in ("old", "current", "missing", "checksum", "protocol", "unhealthy"):
            self.make_release(name)
        self.server = Server(self.sources)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.rounds = RoundRepository(self.directory / "data") if (self.directory / "data").exists() else None
        self.jobs: list[JobRequest] = []
        self.evidence: dict[str, object] = {
            "checked_at": datetime.now(UTC).isoformat(),
            "root": str(self.base),
            "service": self.service,
            "project": self.project,
            "release_source": "revision-addressed loopback HTTP fixtures",
        }

    def make_release(self, name: str) -> None:
        revision = hashlib.sha256(name.encode()).hexdigest()[:40]
        self.revisions[name] = revision
        source = self.sources / revision
        for asset in release.ASSETS:
            destination = source / asset
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / asset, destination)
        executor = source / "executor/executor.py"
        if name == "old":
            executor.write_text(executor.read_text() + "\n# Previous compatible localnet update fixture.\n")
        elif name == "unhealthy":
            executor.write_text(
                executor.read_text().replace("executor.run()", 'raise RuntimeError("unhealthy release fixture")')
            )
        release.manifest(source, check=False)
        if name == "missing":
            (source / "envs/deployed/docker-compose.yml").unlink()
        elif name == "checksum":
            executor.write_text(executor.read_text() + "\n# Unpublished checksum change.\n")
        elif name == "protocol":
            manifest_path = source / release.MANIFEST
            value = release.object_value(json.loads(manifest_path.read_bytes()))
            value["protocol"] = 2
            manifest_path.write_bytes(release.json_bytes(value))

    def command(self, name: str, revision: str = "current", *, prepare: bool = False) -> list[str]:
        script = REPO / "installer/install.sh" if name == "install" else self.directory / "installer/update_compose.sh"
        command = [str(script), str(self.directory), "--ref", self.revisions[revision], "--source", self.origin]
        if name == "install":
            command += [
                "--env-file",
                str(self.env_file),
                "--service",
                self.service,
                "--project",
                self.project,
                "--localnet",
            ]
        if prepare:
            command.append("--prepare-only")
        return command

    def invoke(self, name: str, revision: str = "current", *, prepare: bool = False, expected: int = 0) -> str:
        started = time.monotonic()
        result = subprocess.run(
            self.command(name, revision, prepare=prepare), text=True, capture_output=True, check=False
        )
        if result.returncode != expected:
            raise RuntimeError(
                f"{name}/{revision}: {result.returncode}, stdout={result.stdout}, stderr={result.stderr}"
            )
        print(f"PASS: {name}/{revision} returned {expected} in {time.monotonic() - started:.1f}s", flush=True)
        return result.stdout + result.stderr

    def compose(self, *args: str) -> str:
        return release.run(release.compose(self.directory, release.Installation.read(self.directory), *args))

    def pid(self) -> str:
        return release.run(["systemctl", "show", self.service, "--property=MainPID", "--value"])

    def setup(self) -> None:
        self.invoke("install", "old", prepare=True)
        self.config = release.read_env(self.directory / ".env")
        self.rounds = RoundRepository(self.directory / "data")
        self.compose("up", "-d", "--wait", "subtensor")
        with (self.base / "bootstrap.log").open("w") as output:
            subprocess.run(
                [
                    "env",
                    "-u",
                    "UV_EXCLUDE_NEWER",
                    "uv",
                    "run",
                    "--project",
                    str(REPO / "miner"),
                    "--group",
                    "bootstrap",
                    "python",
                    str(REPO / "localnet/bootstrap.py"),
                    "--env-file",
                    str(self.directory / ".env"),
                ],
                check=True,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        self.invoke("update", "old")
        before = (self.directory / "executor/executor.py").stat()
        pid = self.pid()
        self.invoke("install", "old")
        self.invoke("update", "old")
        after = (self.directory / "executor/executor.py").stat()
        if before.st_ino != after.st_ino or before.st_mtime_ns != after.st_mtime_ns or self.pid() != pid:
            raise RuntimeError("Repeated install/no-change update replaced or restarted an unchanged executor")
        self.evidence["clean_repeated_no_change"] = {
            "pid": pid,
            "inode": after.st_ino,
            "compose": self.compose("ps", "--format", "json"),
        }
        for name in ("missing", "checksum", "protocol"):
            self.invoke("update", name, expected=1)
            if (self.directory / "executor/executor.py").stat().st_ino != before.st_ino or self.pid() != pid:
                raise RuntimeError("Failed validation changed installed execution")
        self.evidence["preflight_failures_preserved_executor"] = [
            "missing HTTP asset",
            "bad checksum",
            "protocol mismatch",
        ]

    def overlap(self) -> None:
        self.server.hold.set()
        self.server.released.clear()
        self.server.entered.clear()
        with subprocess.Popen(
            self.command("update", "old"), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        ) as first:
            try:
                if not self.server.entered.wait(timeout=10) or first.poll() is not None:
                    raise RuntimeError("First updater did not reach a live pending download")
                self.invoke("update", "old", expected=75)
            finally:
                self.server.hold.clear()
                self.server.released.set()
            output, errors = first.communicate(timeout=60)
            if first.returncode:
                raise RuntimeError(output + errors)
        self.evidence["overlap"] = (
            "Second actual updater exited 75 while the first held a live HTTP request; first completed"
        )

    def repository(self) -> RoundRepository:
        if self.rounds is None:
            raise RuntimeError("Install must finish before publishing jobs")
        return self.rounds

    def make_plan(self, image: str, judge: str, specification: str, *, seconds: int = 30, count: int = 1) -> RoundPlan:
        registrations = Registrations.model_validate_json((self.directory / "state/registrations.json").read_bytes())
        miners = [row for row in registrations.registrations if row.identity.startswith("miner")][:count]
        now = datetime.now(UTC)
        plan = RoundPlan(
            sequence=len(self.repository().rounds()) + 1,
            deadlines=Deadlines(
                start=now,
                generation_end=now + timedelta(seconds=seconds),
                evaluation_start=now + timedelta(seconds=seconds + 5),
                judge_end=now + timedelta(seconds=seconds + 30),
                round_end=now + timedelta(seconds=seconds + 35),
            ),
            discovery_block=registrations.block,
            discovery_block_hash=registrations.block_hash,
            membership_snapshot="separate_reads",
            judge_image=judge,
            stop_grace_seconds=5,
            cohort=tuple(CohortMember(miner_hotkey=m.hotkey, uid=m.uid, image=image) for m in miners),
            specification_sha256=hashlib.sha256(specification.encode()).hexdigest(),
        )
        self.repository().prepare_round(plan, specification)
        return plan

    def accepted_score(self, factory_image: str, judge_image: str) -> tuple[JobRequest, bytes]:
        plan = self.make_plan(factory_image, judge_image, "fixture")
        factory, judge = (request_for(plan, plan.cohort[0], kind) for kind in ("factory", "judge"))
        rounds = self.repository()
        rounds.publish_request(plan, factory)
        self.jobs.append(factory)
        if not wait_status(rounds, factory, 60).application_succeeded:
            raise RuntimeError("Fixture factory failed")
        until(lambda: datetime.now(UTC) >= plan.deadlines.evaluation_start, "Evaluation did not arrive", 40)
        rounds.save_round(RoundRecord(plan=plan, stage="evaluation"))
        rounds.publish_request(plan, judge)
        self.jobs.append(judge)
        if not wait_status(rounds, judge, 30).application_succeeded:
            raise RuntimeError("Fixture judge failed")
        with PylonClient(
            Config(
                address=f"http://127.0.0.1:{self.config['PYLON_HOST_PORT']}",
                open_access_token=PylonAuthToken(self.config["VALIDATOR_PYLON_OPEN_ACCESS_TOKEN"]),
            )
        ) as client:
            latest = client.v1.open_access.get_latest_block_info()
        beat = BlockBeat(BlockNumber(latest.number), Timestamp(latest.timestamp), BlockHash(latest.hash))
        results = ResultRepository(rounds)
        results.finalize(factory, beat)
        result = results.finalize(judge, beat)
        if result.accepted is None:
            raise RuntimeError("Real linked judge report did not produce an accepted score")
        rounds.save_round(RoundRecord(plan=plan, stage="complete", completed_at=datetime.now(UTC)))
        self.evidence["accepted_result"] = result.model_dump(mode="json")
        return judge, rounds.files.read_bytes(results.result_path(judge))

    def stop(self, job: JobRequest) -> None:
        rounds = self.repository()
        if not (rounds.files.root / f"control/stops/{job.job_id}.json").exists():
            rounds.publish_stop(
                job,
                StopRequest(
                    round_id=job.round_id,
                    job_id=job.job_id,
                    miner_hotkey=job.miner_hotkey,
                    kind=job.kind,
                    factory_job_id=job.factory_job_id,
                    requested_at=datetime.now(UTC),
                    reason="operator",
                ),
            )

    def active_replacement(self) -> None:
        profiles = release.object_value(json.loads((REPO / "spec/evidence/task13-adversarial.json").read_bytes()))
        images = release.object_value(profiles["profiles"])

        def image(name: str) -> str:
            return release.string_value(release.object_value(images[name])["image"])

        judge, accepted = self.accepted_score(image("factory-valid"), image("judge-valid"))
        plan = self.make_plan(image("lifecycle"), image("judge-valid"), "ignore-term", seconds=180, count=2)
        jobs = tuple(request_for(plan, member, "factory") for member in plan.cohort)
        rounds = self.repository()
        for job in jobs:
            rounds.publish_request(plan, job)
            self.jobs.append(job)

        def running() -> bool:
            return all((status := rounds.status(job)) is not None and status.execution == "running" for job in jobs)

        until(running, "Detached jobs did not start", 60)
        before = [
            Inspection.model_validate_json(docker("inspect", job.container_name, "--format", "{{json .}}"))
            for job in jobs
        ]
        self.stop(jobs[0])
        retained = {
            str(path.relative_to(rounds.files.root)): path.read_bytes()
            for directory in ("requests", "stops")
            for path in (rounds.files.root / "control" / directory).glob("*.json")
        }
        old_pid = self.pid()
        binary = self.directory / "executor/executor.py"
        old_inode = binary.stat().st_ino
        started = time.monotonic()
        with binary.open("rb") as old_file:
            old_bytes = old_file.read()
            self.invoke("update", "current")
            old_file.seek(0)
            if old_file.read() != old_bytes or binary.read_bytes() == old_bytes or binary.stat().st_ino == old_inode:
                raise RuntimeError("Executor was not replaced atomically")
        elapsed = time.monotonic() - started
        after = [
            Inspection.model_validate_json(docker("inspect", job.container_name, "--format", "{{json .}}"))
            for job in jobs
        ]
        if self.pid() == old_pid or not after[1].State.Running or elapsed >= 40:
            raise RuntimeError("Update did not restart while detached work continued")
        for original, current, job in zip(before, after, jobs, strict=True):
            if original.Id != current.Id or original.State.StartedAt != current.State.StartedAt:
                raise RuntimeError("Update reran an existing Docker execution")
            if (rounds.files.root / job.output_dir / "starts").read_text() != "started\n":
                raise RuntimeError("Fixture start count changed")
        if any(rounds.files.read_bytes(path) != value for path, value in retained.items()):
            raise RuntimeError("Update changed a request or permanent stop")
        if rounds.files.read_bytes(ResultRepository.result_path(judge)) != accepted:
            raise RuntimeError("Update changed the previously accepted score")
        self.stop(jobs[1])
        statuses = [wait_status(rounds, job, 30).model_dump(mode="json") for job in jobs]
        self.evidence["active_replacement"] = {
            "seconds": elapsed,
            "old_pid": old_pid,
            "new_pid": self.pid(),
            "old_inode": old_inode,
            "new_inode": binary.stat().st_ino,
            "before": [item.model_dump(mode="json") for item in before],
            "after": [item.model_dump(mode="json") for item in after],
            "terminal_statuses": statuses,
            "accepted_score_bytes_preserved": True,
            "request_stop_bytes_preserved": True,
        }
        print(
            "PASS: atomic replacement/restart preserved active Docker executions, stops and accepted score", flush=True
        )

    def unhealthy_release(self) -> None:
        self.invoke("update", "unhealthy", expected=1)
        binary = self.directory / "executor/executor.py"
        failed = (self.sources / self.revisions["unhealthy"] / "executor/executor.py").read_bytes()
        if binary.read_bytes() != failed or "failed" not in (self.directory / "applied-release.json").read_text():
            raise RuntimeError("Health failure was hidden or caused an excluded rollback")
        self.evidence["failed_health"] = json.loads((self.directory / "applied-release.json").read_bytes())
        self.invoke("update", "current")
        self.evidence["ordinary_repair"] = json.loads((self.directory / "applied-release.json").read_bytes())

    def cron_permissions(self) -> None:
        """Use a real cron invocation under a fresh account with only the exact restart sudo grant.

        Raises:
            RuntimeError: Cron failed or the fixture account has excessive systemd privileges.
        """
        suffix = uuid4().hex[:8]
        user = "fhupdate-" + suffix
        service = "factory-horde-permissions-" + suffix
        home = Path("/home") / user
        directory = home / "factory-horde"
        root = directory / "data"
        seed = self.base / "permissions-seed"
        seed.mkdir()
        subprocess.run(
            ["sudo", "useradd", "--create-home", "--shell", "/bin/bash", "--groups", "docker", user], check=True
        )
        try:
            uid = release.run(["id", "-u", user])
            gid = release.run(["id", "-g", user])
            config = release.read_env(self.directory / ".env") | {
                "FACTORY_HORDE_DATA_ROOT": str(root),
                "HOST_WALLET_DIR": str(directory / "wallets"),
                "EXECUTOR_USER": user,
                "EXECUTOR_GROUP": user,
                "CONTAINER_UID": uid,
                "CONTAINER_GID": gid,
            }
            selection = release.Selection(self.revisions["current"], self.origin)
            installation = release.Installation(
                service, service, True, selection, {key: config[key] for key in release.IDENTITY_KEYS}
            )
            for name in (*release.ASSETS, release.MANIFEST):
                destination = seed / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.sources / self.revisions["old"] / name, destination)
                destination.chmod(0o555 if name.endswith((".sh", ".py")) else 0o640)
            (seed / ".env").write_bytes(release.environment(config))
            (seed / ".env").chmod(0o600)
            (seed / "executor/executor.env").write_bytes(
                release.environment({key: config[key] for key in release.EXECUTOR_KEYS})
            )
            (seed / "executor/executor.env").chmod(0o600)
            (seed / "installation.json").write_bytes(
                release.json_bytes(
                    {
                        "service": service,
                        "project": service,
                        "localnet": True,
                        "selection": {"ref": selection.ref, "source": selection.source, "git_url": selection.git_url},
                        "identity": installation.identity,
                    }
                )
            )
            for name in ("control/requests", "control/stops", "control/statuses", "control/rounds", "rounds"):
                (seed / "data" / name).mkdir(parents=True, exist_ok=True)
            (seed / "wallets").mkdir()
            subprocess.run(["sudo", "cp", "-a", str(seed), str(directory)], check=True)
            subprocess.run(["sudo", "chown", "-R", f"{user}:{user}", str(directory)], check=True)
            unit = self.base / (service + ".service")
            unit.write_bytes(release.unit_content(REPO, directory, config))
            grant = self.base / "permissions-sudoers"
            grant.write_text(f"{user} ALL=(root) NOPASSWD: /usr/bin/systemctl restart {service}.service\n")
            subprocess.run(
                ["sudo", "install", "-m", "0644", str(unit), f"/etc/systemd/system/{service}.service"], check=True
            )
            subprocess.run(["sudo", "install", "-m", "0440", str(grant), f"/etc/sudoers.d/{service}"], check=True)
            subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
            subprocess.run(["sudo", "systemctl", "start", service], check=True)
            previous_pid = release.run(["systemctl", "show", service, "--property=MainPID", "--value"])
            denied = subprocess.run(
                ["sudo", "-u", user, "sudo", "-n", "-l", "/usr/bin/systemctl", "restart", "docker.service"],
                text=True,
                capture_output=True,
                check=False,
            )
            if denied.returncode == 0:
                raise RuntimeError("Fixture account unexpectedly has broad systemd privileges")
            cron = self.base / "permissions-cron"
            cron.write_text(
                "SHELL=/bin/bash\nPATH=/usr/bin:/bin\n"
                f"* * * * * {user} test -e {directory}/cron-exit || {{ id -u > {directory}/cron-uid; "
                f"{directory}/installer/update_compose.sh {directory} --prepare-only > {directory}/cron.log 2>&1; "
                f"echo $? > {directory}/cron-exit; }}\n"
            )
            subprocess.run(["sudo", "install", "-m", "0644", str(cron), f"/etc/cron.d/{service}"], check=True)
            subprocess.run(["systemctl", "is-active", "--quiet", "cron"], check=True)
            print(f"Waiting for an actual cron update as restricted UID {uid}", flush=True)
            until(
                lambda: (
                    subprocess.run(["sudo", "test", "-f", str(directory / "cron-exit")], check=False).returncode == 0
                ),
                "Actual cron update did not finish",
                85,
            )
            exit_code = release.run(["sudo", "cat", str(directory / "cron-exit")])
            actual_uid = release.run(["sudo", "cat", str(directory / "cron-uid")])
            log = release.run(["sudo", "cat", str(directory / "cron.log")])
            current_pid = release.run(["systemctl", "show", service, "--property=MainPID", "--value"])
            if exit_code != "0" or actual_uid != uid or current_pid == previous_pid:
                raise RuntimeError(f"Restricted cron update failed: exit={exit_code}, uid={actual_uid}, log={log}")
            self.evidence["cron_permissions"] = {
                "user": user,
                "uid": uid,
                "gid": gid,
                "cron_uid": actual_uid,
                "cron_exit": exit_code,
                "old_pid": previous_pid,
                "new_pid": current_pid,
                "other_unit_restart_denied": denied.returncode != 0,
                "sudoers": grant.read_text(),
                "cron_log": log,
                "retained_home": str(home),
            }
            print("PASS: actual non-root cron updated and restarted only its permitted system unit", flush=True)
        finally:
            subprocess.run(["sudo", "systemctl", "stop", service], check=False, capture_output=True)
            for path in (
                f"/etc/cron.d/{service}",
                f"/etc/sudoers.d/{service}",
                f"/etc/systemd/system/{service}.service",
            ):
                subprocess.run(["sudo", "rm", "-f", path], check=True)
            subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
            subprocess.run(["sudo", "userdel", user], check=True)

    def cleanup(self) -> None:
        if self.rounds is not None:
            for job in self.jobs:
                status = self.rounds.status(job)
                if status is None or not status.confirmed_stopped:
                    self.stop(job)
        if (self.directory / "installation.json").exists():
            self.compose("down")
        subprocess.run(["sudo", "systemctl", "disable", "--now", self.service], check=False, capture_output=True)
        for path in (f"/etc/cron.d/{self.service}", f"/etc/sudoers.d/{self.service}"):
            subprocess.run(["sudo", "rm", "-f", path], check=True)
        self.server.released.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def main() -> None:
    """Run actual installation, failure and update checks without changing the ordinary localnet."""
    check = Check()
    try:
        check.setup()
        check.overlap()
        check.active_replacement()
        check.unhealthy_release()
        check.cron_permissions()
        check.evidence["executor_sha256"] = hashlib.sha256(
            (check.directory / "executor/executor.py").read_bytes()
        ).hexdigest()
        check.evidence["sudoers"] = subprocess.check_output(
            ["sudo", "cat", f"/etc/sudoers.d/{check.service}"], text=True
        )
        check.evidence["cron"] = Path(f"/etc/cron.d/{check.service}").read_text()
        output = REPO / "localnet/state/task15-installer.json"
        output.write_text(json.dumps(check.evidence, indent=2) + "\n")
        print(f"PASS: real installer/update acceptance; evidence {output}", flush=True)
    finally:
        (check.base / "evidence.json").write_text(json.dumps(check.evidence, indent=2) + "\n")
        check.cleanup()


if __name__ == "__main__":
    main()
