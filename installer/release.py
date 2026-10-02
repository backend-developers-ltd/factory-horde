#!/usr/bin/env python3
"""Coherent, checksummed assets for the existing Compose/systemd installer and cron updater."""

from __future__ import annotations

import argparse
import ast
import fcntl
import grp
import hashlib
import json
import os
import pwd
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

PROTOCOL = 1
GIT_URL = "https://github.com/backend-developers-ltd/factory-horde.git"
RAW_URL = "https://raw.githubusercontent.com/backend-developers-ltd/factory-horde"
MANIFEST = "installer/release-manifest.json"
ASSETS = (
    "executor/executor.py",
    "executor/executor.sha256",
    "installer/release.py",
    "installer/install.sh",
    "installer/update_compose.sh",
    "installer/install-executor.sh",
    "installer/prepare-data-root.sh",
    "installer/factory-horde-executor.service",
    "envs/deployed/docker-compose.yml",
    "localnet/compose.yml",
)
EXECUTOR_KEYS = (
    "CONTAINER_UID",
    "CONTAINER_GID",
    "EXECUTOR_MEMORY",
    "EXECUTOR_CPUS",
    "EXECUTOR_PIDS_LIMIT",
    "EXECUTOR_POLL_SECONDS",
    "EXECUTOR_WORKERS",
)
IDENTITY_KEYS = ("FACTORY_HORDE_DATA_ROOT", "EXECUTOR_USER", "EXECUTOR_GROUP", "EXECUTOR_PYTHON")
LIMIT = 4 * 1024 * 1024


class ReleaseError(RuntimeError):
    """No usable release or operator configuration; existing evidence must remain intact."""


class UpdateBusy(ReleaseError):
    """Another installation/update owns this destination's lock."""


def run(args: Sequence[str], *, quiet: bool = False) -> str:
    """Use argument arrays and redact command output on failed secret-bearing Compose checks.

    Raises:
        ReleaseError: A required host command failed.
    """
    result = subprocess.run(args, text=True, capture_output=True, check=False)
    if result.returncode:
        detail = "See the command's own diagnostics" if quiet else result.stderr.strip()[:2000]
        raise ReleaseError(f"{args[0]} failed (exit {result.returncode}): {detail}")
    return result.stdout.strip()


def canonical(path: Path) -> Path:
    """Require unambiguous operator paths usable by systemd, shell and cron.

    Raises:
        ReleaseError: A path contains symlinks, traversal or unsafe characters.
    """
    if not path.is_absolute() or path.resolve() != path or re.fullmatch(r"/[-/._a-zA-Z0-9]+", str(path)) is None:
        raise ReleaseError(f"Use a canonical absolute path without symlinks or spaces: {path}")
    return path


def owned_directory(path: Path) -> None:
    """Create operator-owned directories without adopting another account's files.

    Raises:
        ReleaseError: Ownership differs from the executing account.
    """
    canonical(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o750)
    metadata = path.stat()
    if metadata.st_uid != os.getuid() or metadata.st_gid != os.getgid():
        raise ReleaseError(f"Directory must belong to the operator and primary group: {path}")
    path.chmod(0o750)


def atomic_write(path: Path, content: bytes, mode: int = 0o640) -> bool:
    """Replace on the destination filesystem and fsync both contents and directory.

    Raises:
        ReleaseError: The destination is a symlink or not a regular file.
    """
    canonical(path)
    owned_directory(path.parent)
    if path.is_symlink() or path.exists() and not path.is_file():
        raise ReleaseError(f"Unsafe destination: {path}")
    if path.exists() and path.read_bytes() == content and path.stat().st_mode & 0o777 == mode:
        return False
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as output:
            os.fchmod(output.fileno(), mode)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
    return True


@contextmanager
def update_lock(directory: Path) -> Generator[None]:
    """Serialize install/update/restart; nonblocking contention exits with a distinct status.

    Raises:
        UpdateBusy: Another updater is already running.
    """
    owned_directory(directory)
    fd = os.open(directory / ".update.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise UpdateBusy("Another updater holds the installation lock") from error
        yield
    finally:
        os.close(fd)


def object_value(value: object) -> dict[str, object]:
    """Accept a JSON object without weakening stdlib boundary typing.

    Raises:
        ReleaseError: The document is not an object.
    """
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in cast(dict[object, object], value)):
        raise ReleaseError("Expected a JSON object")
    return cast(dict[str, object], value)


def string_value(value: object) -> str:
    """Require a text field.

    Raises:
        ReleaseError: The field is not text.
    """
    if not isinstance(value, str):
        raise ReleaseError("Expected text")
    return value


def json_bytes(value: object) -> bytes:
    """Stable human-readable installation metadata; never contains tokens."""
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def protocol_from_source(source: bytes) -> int:
    """Inspect the declared wire version without executing an unverified file.

    Raises:
        ReleaseError: The script has no constant integer protocol declaration.
    """
    values = [
        node.value.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "PROTOCOL_VERSION" for target in node.targets)
        and isinstance(node.value, ast.Constant)
    ]
    if len(values) != 1 or type(values[0]) is not int:
        raise ReleaseError("Executor must declare one integer PROTOCOL_VERSION")
    return values[0]


def manifest(source: Path, *, check: bool) -> None:
    """Publish deterministic checksums before committing a release, or verify no source drift.

    Raises:
        ReleaseError: Published metadata differs from the current sources.
    """
    executor = (source / "executor/executor.py").read_bytes()
    checksum = (hashlib.sha256(executor).hexdigest() + "  executor.py\n").encode()
    if not check:
        atomic_write(source / "executor/executor.sha256", checksum, 0o644)
    elif (source / "executor/executor.sha256").read_bytes() != checksum:
        raise ReleaseError("Executor checksum needs regeneration")
    content = json_bytes(
        {
            "schema": 1,
            "protocol": protocol_from_source(executor),
            "python_min": [3, 14],
            "platform": "linux/amd64",
            "files": {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in ASSETS},
        }
    )
    if check:
        if (source / MANIFEST).read_bytes() != content:
            raise ReleaseError("Release manifest needs regeneration")
    else:
        atomic_write(source / MANIFEST, content, 0o644)


@dataclass(frozen=True)
class Selection:
    """An explicit local snapshot or one branch resolved once to a Git revision."""

    ref: str
    source: str = RAW_URL
    git_url: str = GIT_URL

    def resolve(self) -> tuple[str, str]:
        """Freeze the URL before any asset is fetched.

        Raises:
            ReleaseError: The selected source or ref cannot identify one revision.
        """
        if self.source.startswith("file://"):
            if self.ref != "snapshot":
                raise ReleaseError("Local source exports use --ref snapshot")
            canonical(Path(self.source.removeprefix("file://")))
            return self.source.rstrip("/"), "snapshot"
        if not self.source.startswith("https://") and not self.source.startswith("http://127.0.0.1:"):
            raise ReleaseError("Use HTTPS or a loopback test server for release downloads")
        revision = self.ref
        if not re.fullmatch(r"[a-f0-9]{40}", revision):
            if not re.fullmatch(r"deploy-config-[a-z0-9-]+", revision):
                raise ReleaseError("Select a full commit SHA or a deploy-config-* branch")
            response = run(["git", "ls-remote", "--exit-code", self.git_url, f"refs/heads/{revision}"])
            revision = response.split()[0]
        if not re.fullmatch(r"[a-f0-9]{40}", revision):
            raise ReleaseError("Release did not resolve to a full commit SHA")
        return f"{self.source.rstrip('/')}/{revision}", revision


def fetch(url: str) -> bytes:
    """Bound download size and duration before validating the release manifest.

    Raises:
        ReleaseError: The asset is too large.
    """
    with urllib.request.urlopen(url, timeout=30) as response:
        content = response.read(LIMIT + 1)
    if len(content) > LIMIT:
        raise ReleaseError("Release asset exceeds four MiB")
    return content


def stage_release(selection: Selection, stage: Path) -> tuple[str, bytes]:
    """Download and verify the entire fixed asset set before changing installed files.

    Raises:
        ReleaseError: Release metadata, protocol or a checksum is incompatible.
    """
    base, revision = selection.resolve()
    content = fetch(f"{base}/{MANIFEST}")
    metadata = object_value(json.loads(content))
    if (
        set(metadata) != {"schema", "protocol", "python_min", "platform", "files"}
        or type(metadata["schema"]) is not int
        or metadata["schema"] != 1
        or type(metadata["protocol"]) is not int
        or metadata["protocol"] != PROTOCOL
        or metadata["python_min"] != [3, 14]
        or metadata["platform"] != "linux/amd64"
    ):
        raise ReleaseError("Incompatible release schema/protocol/Python/platform")
    checksums = object_value(metadata["files"])
    if set(checksums) != set(ASSETS):
        raise ReleaseError("Release asset set differs from the supported installer contract")
    for name in ASSETS:
        expected = string_value(checksums[name])
        payload = fetch(f"{base}/{name}")
        if not re.fullmatch(r"[a-f0-9]{64}", expected) or hashlib.sha256(payload).hexdigest() != expected:
            raise ReleaseError(f"Bad release checksum: {name}")
        atomic_write(stage / name, payload, 0o555 if name.endswith((".sh", ".py")) else 0o644)
    executor = (stage / "executor/executor.py").read_bytes()
    if (
        protocol_from_source(executor) != PROTOCOL
        or (stage / "executor/executor.sha256").read_text() != hashlib.sha256(executor).hexdigest() + "  executor.py\n"
    ):
        raise ReleaseError("Executor protocol/checksum disagrees with release manifest")
    return revision, content


def read_env(path: Path) -> dict[str, str]:
    """Read literal KEY=value settings; never execute downloaded or operator shell code.

    Raises:
        ReleaseError: Configuration contains duplicate keys, expansions or unsupported syntax.
    """
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        fields = shlex.split(line, comments=True)
        if not fields:
            continue
        key, separator, value = fields[0].partition("=")
        if (
            len(fields) != 1
            or not separator
            or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
            or key in values
            or any(character in value for character in ("$", "`", "\n", "\r"))
        ):
            raise ReleaseError("Configuration must use unique literal KEY=value lines without expansions")
        values[key] = value
    return values


def environment(config: dict[str, str]) -> bytes:
    """Encode values for both Compose dotenv and systemd EnvironmentFile."""
    return "".join(f"{key}={shlex.quote(value)}\n" for key, value in sorted(config.items())).encode()


def configuration(path: Path, directory: Path, *, application: bool, localnet: bool = False) -> dict[str, str]:
    """Apply explicit safe defaults and validate the actual service/cron account.

    Raises:
        ReleaseError: Required identity, path or topology settings are missing or inconsistent.
    """
    if os.getuid() == 0:
        raise ReleaseError("Run the installer/updater as the selected non-root Docker operator")
    config = read_env(path)
    user, group = pwd.getpwuid(os.getuid()).pw_name, grp.getgrgid(os.getgid()).gr_name
    defaults = {
        "FACTORY_HORDE_DATA_ROOT": str(directory / "data"),
        "CONTAINER_UID": str(os.getuid()),
        "CONTAINER_GID": str(os.getgid()),
        "EXECUTOR_USER": user,
        "EXECUTOR_GROUP": group,
        "EXECUTOR_PYTHON": "/usr/bin/python3.14",
        "EXECUTOR_MEMORY": "512m",
        "EXECUTOR_CPUS": "1",
        "EXECUTOR_PIDS_LIMIT": "128",
        "EXECUTOR_POLL_SECONDS": "2",
        "EXECUTOR_WORKERS": "32",
        "VALIDATOR_DATA_ROOT": "/var/lib/factory-horde",
        "MECHANISM_ID": "0",
        "VALIDATOR_DISPATCH_ENABLED": "false",
        "VALIDATOR_WEIGHTS_ENABLED": "false",
    }
    config = defaults | config
    if os.uname().machine != "x86_64" or config.get("EXECUTOR_PLATFORM", "linux/amd64") != "linux/amd64":
        raise ReleaseError("The prototype release requires a Linux amd64 host")
    if (
        config["EXECUTOR_USER"] != user
        or config["EXECUTOR_GROUP"] != group
        or config["CONTAINER_UID"] != str(os.getuid())
        or config["CONTAINER_GID"] != str(os.getgid())
    ):
        raise ReleaseError("Executor and container identity must match the installing operator/primary group")
    for name in IDENTITY_KEYS[:1] + ("EXECUTOR_PYTHON", "VALIDATOR_DATA_ROOT"):
        canonical(Path(config[name]))
    if application:
        if localnet:
            config.setdefault("HOST_WALLET_DIR", str(directory / "wallets"))
            if (
                config.get("ENVIRONMENT") != "localnet"
                or config.get("NETUID") != "2"
                or config.get("BITTENSOR_NETWORK") != "ws://subtensor:9944"
                or config["HOST_WALLET_DIR"] != str(directory / "wallets")
                or config["FACTORY_HORDE_DATA_ROOT"] != str(directory / "data")
            ):
                raise ReleaseError(
                    "Localnet requires subnet 2, the local chain and wallets/data below its installation"
                )
        for key in ("ENVIRONMENT", "NETUID", "BITTENSOR_NETWORK", "HOST_WALLET_DIR"):
            if not config.get(key):
                raise ReleaseError(f"Explicit operator setting required: {key}")
        canonical(Path(config["HOST_WALLET_DIR"]))
        token_keys = ["VALIDATOR_PYLON_OPEN_ACCESS_TOKEN", "VALIDATOR_PYLON_IDENTITY_TOKEN", "PYLON_METRICS_TOKEN"]
        if localnet:
            token_keys.extend(f"MINER{i}_PYLON_TOKEN" for i in range(1, 6))
        for key in token_keys:
            if not config.get(key):
                config[key] = secrets.token_hex(24)
    run([config["EXECUTOR_PYTHON"], "-I", "-c", "import sys; sys.exit(0 if sys.version_info >= (3,14) else 1)"])
    run(["docker", "info"], quiet=True)
    return config


@dataclass(frozen=True)
class Installation:
    """Operator selection, separate from release checksums and runtime evidence."""

    service: str
    project: str
    localnet: bool
    selection: Selection
    identity: dict[str, str]

    def validate(self) -> None:
        """Bound names before embedding them in systemd/sudoers/cron commands.

        Raises:
            ReleaseError: A service or project name is unsafe.
        """
        if not re.fullmatch(r"factory-horde-[a-z0-9-]+", self.service) or not re.fullmatch(
            r"[a-z][a-z0-9-]+", self.project
        ):
            raise ReleaseError("Use a factory-horde-* service and a lowercase Compose project name")

    @classmethod
    def read(cls, directory: Path) -> Installation:
        """Load the operator's persistent updater selection."""
        value = object_value(json.loads((directory / "installation.json").read_bytes()))
        selection = object_value(value["selection"])
        result = cls(
            string_value(value["service"]),
            string_value(value["project"]),
            value["localnet"] is True,
            Selection(*(string_value(selection[key]) for key in ("ref", "source", "git_url"))),
            {key: string_value(item) for key, item in object_value(value["identity"]).items()},
        )
        result.validate()
        return result


def compose(directory: Path, installation: Installation, *args: str, source: Path | None = None) -> list[str]:
    """The same maintained application file is used with an optional local-chain overlay."""
    source = source or directory
    command = [
        "docker",
        "compose",
        "--project-name",
        installation.project,
        "--env-file",
        str(directory / ".env"),
        "-f",
        str(source / "envs/deployed/docker-compose.yml"),
    ]
    if installation.localnet:
        command += ["-f", str(source / "localnet/compose.yml")]
    return [*command, *args]


def unit_content(source: Path, directory: Path, config: dict[str, str]) -> bytes:
    """Render only fixed validated service paths/identity, never wallet or Pylon credentials."""
    unit = (source / "installer/factory-horde-executor.service").read_text()
    values = {
        "EXECUTOR_USER": config["EXECUTOR_USER"],
        "EXECUTOR_GROUP": config["EXECUTOR_GROUP"],
        "EXECUTOR_PYTHON": config["EXECUTOR_PYTHON"],
        "INSTALL_ROOT": str(directory / "executor"),
        "DATA_ROOT": config["FACTORY_HORDE_DATA_ROOT"],
    }
    for key, value in values.items():
        unit = unit.replace(f"@{key}@", value)
    return unit.encode()


def prepare_system_files(stage: Path, directory: Path, config: dict[str, str], installation: Installation) -> None:
    """Prepare reviewable unit, exact restart grant and an operator-owned cron command."""
    unit = stage / f"{installation.service}.service"
    atomic_write(unit, unit_content(stage, directory, config), 0o644)
    # ExecStart names the installed Python interpreter; the script need not exist yet.
    run(["systemd-analyze", "verify", str(unit)])
    sudoers = (
        f"{config['EXECUTOR_USER']} ALL=(root) NOPASSWD: /usr/bin/systemctl restart {installation.service}.service\n"
    )
    atomic_write(stage / "sudoers", sudoers.encode(), 0o440)
    run(["/usr/sbin/visudo", "-cf", str(stage / "sudoers")])
    cron = (
        "SHELL=/bin/bash\nPATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
        f"*/15 * * * * {config['EXECUTOR_USER']} {directory}/installer/update_compose.sh {directory}"
        f" >> {directory}/update.log 2>&1\n"
    )
    atomic_write(stage / "cron", cron.encode(), 0o644)


def install_system(stage: Path, directory: Path, installation: Installation, *, cron: bool) -> None:
    """Initial administrator setup; the recurring updater receives only one restart grant."""
    service = installation.service
    run(["sudo", "install", "-m", "0644", str(stage / f"{service}.service"), f"/etc/systemd/system/{service}.service"])
    run(["sudo", "/usr/bin/systemctl", "daemon-reload"])
    run(["sudo", "/usr/bin/systemctl", "enable", service + ".service"])
    if cron:
        run(["sudo", "install", "-m", "0440", str(stage / "sudoers"), f"/etc/sudoers.d/{service}"])
        run(["sudo", "install", "-m", "0644", str(stage / "cron"), f"/etc/cron.d/{service}"])


def check_protocol_records(root: Path) -> None:
    """Reject incompatible headers before replacing an executor; job semantics stay executor-owned.

    Raises:
        ReleaseError: Existing authoritative request/stop/status records need migration.
    """
    for category in ("requests", "stops", "statuses"):
        directory = root / "control" / category
        if not directory.exists():
            continue
        canonical(directory)
        for path in directory.glob("*.json"):
            if path.name.startswith("."):
                continue
            canonical(path)
            with path.open("rb") as source:
                content = source.read(LIMIT + 1)
            value = object_value(json.loads(content))
            if (
                len(content) > LIMIT
                or type(value.get("protocol_version")) is not int
                or value["protocol_version"] != PROTOCOL
            ):
                raise ReleaseError(f"Incompatible existing record: {path}")


def check_health(root: Path, service: str, since: datetime) -> dict[str, object]:
    """Verify the current service process published fresh health; never roll back a failed update.

    Raises:
        ReleaseError: Replacement is running without current compatible Docker health evidence.
    """
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        try:
            health = object_value(json.loads((root / "control/executor-health.json").read_bytes()))
            observed = datetime.fromisoformat(string_value(health["observed_at"]))
            pid = run(["/usr/bin/systemctl", "show", service + ".service", "--property=MainPID", "--value"])
            if (
                health.get("protocol_version") == PROTOCOL
                and str(health.get("pid")) == pid
                and pid != "0"
                and observed >= since
                and (datetime.now(UTC) - observed).total_seconds() < 15
                and health.get("docker_ok") is True
            ):
                return health
        except FileNotFoundError, ValueError, KeyError:
            pass
        time.sleep(1)
    raise ReleaseError("Installed release failed executor health; repair it normally (no automatic rollback)")


def apply_release(
    directory: Path,
    installation: Installation,
    config: dict[str, str],
    *,
    install: bool,
    application: bool,
    prepare_only: bool,
) -> None:
    """Validate everything first, replace atomically, restart once and check actual service health.

    Raises:
        ReleaseError: Unit/identity changes require installation, or preflight validation fails.
    """
    installation.validate()
    if any(config[key] != installation.identity[key] for key in IDENTITY_KEYS):
        raise ReleaseError("Service identity/path changes require a fresh explicit installation")
    with tempfile.TemporaryDirectory(prefix=".release-", dir=directory) as temporary:
        stage = Path(temporary)
        revision, metadata = stage_release(installation.selection, stage)
        root = Path(config["FACTORY_HORDE_DATA_ROOT"])
        check_protocol_records(root)
        if run([config["EXECUTOR_PYTHON"], "-I", str(stage / "executor/executor.py"), "--protocol-version"]) != str(
            PROTOCOL
        ):
            raise ReleaseError("Candidate executor does not execute with the advertised protocol")
        if application:
            run(compose(directory, installation, "config", "--quiet", source=stage), quiet=True)
        expected_unit = unit_content(stage, directory, config)
        installed_unit = Path(f"/etc/systemd/system/{installation.service}.service")
        if installed_unit.exists() and f"{directory}/executor/executor.py" not in installed_unit.read_text():
            raise ReleaseError("System service belongs to another installation; select a distinct service name")
        if not install and (not installed_unit.exists() or installed_unit.read_bytes() != expected_unit):
            raise ReleaseError("System unit changed; rerun install.sh with administrator privileges before updating")
        prepare_system_files(stage, directory, config, installation)
        run(["bash", str(stage / "installer/prepare-data-root.sh"), str(root)])
        executor_changed = atomic_write(
            directory / "executor/executor.py", (stage / "executor/executor.py").read_bytes(), 0o555
        )
        env_changed = atomic_write(
            directory / "executor/executor.env", environment({key: config[key] for key in EXECUTOR_KEYS}), 0o600
        )
        application_changed = False
        for name in ASSETS:
            if name == "executor/executor.py":
                continue
            changed = atomic_write(
                directory / name, (stage / name).read_bytes(), 0o555 if name.endswith((".sh", ".py")) else 0o644
            )
            if name.endswith(".yml"):
                application_changed = application_changed or changed
        atomic_write(directory / MANIFEST, metadata, 0o644)
        atomic_write(directory / "installation.json", json_bytes(asdict(installation)), 0o600)
        if install:
            install_system(stage, directory, installation, cron=application)
        started = datetime.now(UTC)
        previous: dict[str, object] = {}
        if (directory / "applied-release.json").exists():
            previous = object_value(json.loads((directory / "applied-release.json").read_bytes()))
        config_sha256 = hashlib.sha256((directory / ".env").read_bytes()).hexdigest()
        application_changed = application_changed or previous.get("config_sha256") != config_sha256
        state: dict[str, object] = {
            "revision": revision,
            "source": installation.selection.source,
            "manifest_sha256": hashlib.sha256(metadata).hexdigest(),
            "applied_at": started.isoformat(),
            "executor_changed": executor_changed,
            "health": "checking",
            "config_sha256": config_sha256,
        }
        atomic_write(directory / "applied-release.json", json_bytes(state))
        try:
            active = (
                subprocess.run(
                    ["/usr/bin/systemctl", "is-active", "--quiet", installation.service + ".service"], check=False
                ).returncode
                == 0
            )
            if executor_changed or env_changed or not active:
                run(["sudo", "-n", "/usr/bin/systemctl", "restart", installation.service + ".service"])
            health = check_health(root, installation.service, started)
            if (
                application
                and not prepare_only
                and (install or application_changed or previous.get("application_started") is not True)
            ):
                run(compose(directory, installation, "up", "-d", "--wait", "--wait-timeout", "180"), quiet=True)
            state.update(
                health="healthy", executor_pid=health["pid"], application_started=application and not prepare_only
            )
        except Exception:
            state["health"] = "failed; ordinary repair required"
            atomic_write(directory / "applied-release.json", json_bytes(state))
            raise
        atomic_write(directory / "applied-release.json", json_bytes(state))
        print(json.dumps({"event": "release_applied", **state}), flush=True)


def main() -> None:
    """CLI behind maintained shell entrypoints; the host needs no Python package installation.

    Raises:
        ReleaseError: Missing explicit installation options, caught and reported without a traceback.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("manifest")
    publish.add_argument("source", type=Path)
    publish.add_argument("--check", action="store_true")
    for name in ("install", "update", "executor"):
        command = commands.add_parser(name)
        command.add_argument("directory", type=Path, nargs="?" if name == "executor" else None)
        command.add_argument("--env-file", type=Path)
        command.add_argument("--ref")
        command.add_argument("--source")
        command.add_argument("--git-url", default=GIT_URL)
        command.add_argument("--service", default="factory-horde-executor")
        command.add_argument("--project", default="factory-horde")
        command.add_argument("--localnet", action="store_true")
        command.add_argument("--prepare-only", action="store_true")
    arguments = parser.parse_args()
    try:
        if arguments.command == "manifest":
            manifest(canonical(cast(Path, arguments.source)), check=bool(arguments.check))
            return
        selected_directory = cast(Path | None, arguments.directory)
        if selected_directory is None:
            executor_env = read_env(canonical(cast(Path, arguments.env_file)))
            selected_directory = Path(executor_env["FACTORY_HORDE_DATA_ROOT"]).parent
        directory = canonical(selected_directory)
        with update_lock(directory):
            install = arguments.command != "update"
            application = arguments.command != "executor"
            if not install:
                previous = Installation.read(directory)
                selection = Selection(
                    arguments.ref or previous.selection.ref,
                    arguments.source or previous.selection.source,
                    previous.selection.git_url,
                )
                installation = Installation(
                    previous.service, previous.project, previous.localnet, selection, previous.identity
                )
                config = configuration(directory / ".env", directory, application=True, localnet=previous.localnet)
            else:
                env_path = (
                    directory / ".env" if (directory / ".env").exists() else cast(Path | None, arguments.env_file)
                )
                if env_path is None or not arguments.ref:
                    raise ReleaseError("Installation requires --env-file and an explicit --ref")
                config = configuration(
                    canonical(env_path), directory, application=application, localnet=bool(arguments.localnet)
                )
                installation = Installation(
                    arguments.service,
                    arguments.project,
                    bool(arguments.localnet),
                    Selection(arguments.ref, arguments.source or RAW_URL, arguments.git_url),
                    {key: config[key] for key in IDENTITY_KEYS},
                )
                atomic_write(directory / ".env", environment(config), 0o600)
            apply_release(
                directory,
                installation,
                config,
                install=install,
                application=application,
                prepare_only=bool(arguments.prepare_only),
            )
    except UpdateBusy as error:
        print(str(error), file=sys.stderr)
        sys.exit(75)
    except (OSError, ValueError, KeyError, ReleaseError) as error:
        print(f"Release failed: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
