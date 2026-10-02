"""Collect public acceptance evidence without wallets, token files or dereferenced workload symlinks."""

import argparse
import hashlib
import io
import json
import os
import platform
import stat
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel
from validator.result_records import JobResult

from installer.release import read_env

from .runtime import SOURCE, Runtime


class InstallerRun(BaseModel):
    """Location recorded by the actual installer acceptance check."""

    root: Path


class RoundRun(BaseModel):
    """Immutable decisions retained by the real five-miner coordinator check."""

    outcomes: list[JobResult]


def collect_workload_logs(runtime: Runtime) -> None:
    """Retain real Docker logs and verify the baseline Pi invocation and elapsed stub.

    Raises:
        RuntimeError: The retained round does not prove five baseline minute-long factories.
    """
    run = RoundRun.model_validate_json((runtime.state / "task11-rounds.json").read_bytes())
    destination = runtime.state / "workload-logs"
    destination.mkdir(exist_ok=True)
    durations: dict[str, float] = {}
    version_event = '{"event":"pi_version_verified","version":"0.87.1","inference":false}'
    for outcome in run.outcomes:
        status, request = outcome.status, outcome.request
        result = subprocess.run(
            ["docker", "logs", request.container_name],
            capture_output=True,
            text=True,
            check=True,
        )
        log = result.stdout + result.stderr
        (destination / f"{request.job_id}.log").write_text(log)
        if request.kind != "factory":
            continue
        if (
            request.image != runtime.image("factory")
            or version_event not in log.splitlines()
            or status.started_at is None
            or status.finished_at is None
            or (status.finished_at - status.started_at).total_seconds() < 60
        ):
            raise RuntimeError(f"Baseline Pi/stub evidence is incomplete for {request.job_id}")
        durations[str(request.job_id)] = (status.finished_at - status.started_at).total_seconds()
    if len(durations) != 5:
        raise RuntimeError("Expected five baseline factory executions")
    (destination / "baseline.json").write_text(
        json.dumps(
            {"pi_version": "0.87.1", "inference": False, "elapsed_seconds": durations},
            indent=2,
        )
        + "\n"
    )


class Archive:
    """A bounded public file inventory; symlink targets are evidence, never archive links."""

    def __init__(self, archive: tarfile.TarFile, secrets: tuple[bytes, ...]):
        self.archive = archive
        self.secrets = secrets
        self.files: dict[str, dict[str, str | int]] = {}

    def add(self, path: Path, name: str) -> None:
        """Include regular files or record a symlink without opening its target.

        Raises:
            RuntimeError: A file contains an operator secret or is not a supported artifact.
        """
        if path.is_symlink():
            self.files[name] = {"type": "symlink", "target": os.readlink(path)}
            return
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise RuntimeError(f"Unsupported artifact: {path}")
            payload = source.read()
        if any(secret in payload for secret in self.secrets):
            raise RuntimeError(f"Operator secret found in public evidence: {path}")
        info = tarfile.TarInfo(name)
        info.size, info.mode = len(payload), 0o640
        self.archive.addfile(info, io.BytesIO(payload))
        self.files[name] = {
            "type": "file",
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }

    def tree(self, root: Path, prefix: str) -> None:
        """Retain final data files and rejected-link metadata, ignoring incomplete atomic writes."""
        for path in sorted(root.rglob("*")):
            if path.name.startswith(".") or path.name.endswith(".tmp"):
                continue
            if path.is_symlink() or path.is_file():
                self.add(path, f"{prefix}/{path.relative_to(root)}")


def retained_root(path: Path, parent: Path) -> Path:
    """Keep discovery of fixture evidence beneath its declared localnet state directory.

    Raises:
        RuntimeError: A metadata path escapes the selected run's retained artifacts.
    """
    if path.resolve() != path or not path.is_relative_to(parent):
        raise RuntimeError(f"Evidence root escapes its local installation: {path}")
    return path


def main() -> None:
    """Create a public tarball and checksummed inventory after the acceptance checks finish.

    Raises:
        RuntimeError: Required checks have no retained evidence or output already exists.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument(
        "--installer-evidence",
        type=Path,
        default=SOURCE / "state/task15-installer.json",
    )
    args = parser.parse_args()
    runtime = Runtime.load(args.env_file)
    installer_evidence = Path(args.installer_evidence)
    installer = InstallerRun.model_validate_json(installer_evidence.read_bytes())
    installed = retained_root(installer.root, SOURCE / "state") / "installation"
    adversarial = retained_root(Path((runtime.state / "task13-latest.txt").read_text().strip()), runtime.state)
    output = Path(args.output).absolute()
    inventory = output.with_suffix(output.suffix + ".json")
    if output.exists() or inventory.exists():
        raise RuntimeError("Choose a new evidence bundle name; previous evidence is retained")
    configs = (runtime.config, read_env(installed / ".env"))
    secrets = tuple(
        value.encode() for config in configs for key, value in config.items() if "TOKEN" in key and len(value) >= 16
    )
    host = {
        "checked_at": datetime.now(UTC).isoformat(),
        "scope": "Existing user-selected VM, separate localnet installation; no new VM or clean-OS claim.",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "docker": subprocess.check_output(["docker", "version", "--format", "{{.Server.Version}}"], text=True).strip(),
        "compose": subprocess.check_output(["docker", "compose", "version", "--short"], text=True).strip(),
        "systemd": subprocess.check_output(["systemd", "--version"], text=True).splitlines()[0],
        "base_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SOURCE, text=True).strip(),
        "configuration": {
            key: runtime.config[key]
            for key in (
                "ENVIRONMENT",
                "NETUID",
                "MECHANISM_ID",
                "FACTORY_HORDE_DATA_ROOT",
                "CONTAINER_UID",
                "CONTAINER_GID",
                "EXECUTOR_PYTHON",
                "EXECUTOR_USER",
                "EXECUTOR_GROUP",
                "GENERATION_SECONDS",
                "CONFIRMATION_SECONDS",
                "EVALUATION_SECONDS",
                "JUDGE_STOP_RESERVE_SECONDS",
                "STOP_GRACE_SECONDS",
                "VALIDATOR_DISPATCH_ENABLED",
                "VALIDATOR_WEIGHTS_ENABLED",
            )
        },
        "source_files": {
            str(path.relative_to(SOURCE.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(SOURCE.glob("*.py"))
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    collect_workload_logs(runtime)
    try:
        with tarfile.open(output, "x:gz") as archive:
            bundle = Archive(archive, secrets)
            for name in ("release.json", "localnet.env"):
                bundle.add(SOURCE.parent / "envs/candidate" / name, "candidate/" + name)
            for name in (
                "applied-release.json",
                "installer/release-manifest.json",
                "executor/executor.sha256",
            ):
                bundle.add(runtime.directory / name, "installation/" + name)
            for name in (
                "registrations.json",
                "compose-check.json",
                "task6-submission-check.json",
                "task11-rounds.json",
                "task11-coordinator.log",
                "task12-weights.json",
                "task12-validator.log",
                "task14-monitoring.json",
            ):
                bundle.add(runtime.state / name, "checks/" + name)
            bundle.tree(runtime.data, "main/data")
            bundle.tree(runtime.state / "workload-logs", "main/workload-logs")
            bundle.add(adversarial / "evidence.json", "adversarial/evidence.json")
            bundle.add(adversarial / "validator.log", "adversarial/validator.log")
            bundle.tree(adversarial / "data", "adversarial/data")
            bundle.add(installer_evidence, "installer/evidence.json")
            bundle.tree(installed / "data", "installer/data")
            manifest = json.dumps({"host": host, "files": bundle.files}, indent=2).encode() + b"\n"
            info = tarfile.TarInfo("inventory.json")
            info.size, info.mode = len(manifest), 0o640
            archive.addfile(info, io.BytesIO(manifest))
        output.chmod(0o640)
        inventory.write_text(
            json.dumps(
                {
                    "archive_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                    "host": host,
                    "files": bundle.files,
                },
                indent=2,
            )
            + "\n"
        )
    except Exception:
        output.unlink(missing_ok=True)
        raise
    print(f"Public evidence: {output}; inventory: {inventory}; {len(bundle.files)} artifacts")


if __name__ == "__main__":
    main()
