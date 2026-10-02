"""Release preflight, publication and lock invariants; real systemd coverage lives in localnet."""

import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from . import release

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    for name in (*release.ASSETS, release.MANIFEST):
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / name, target)
    return source


def test_repository_manifest_matches_committed_asset_bytes() -> None:
    release.manifest(REPO, check=True)


def test_one_resolved_revision_supplies_every_asset(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolutions: list[tuple[str, ...]] = []
    urls: list[str] = []
    revision = "a" * 40

    def resolve(args: release.Sequence[str], *, quiet: bool = False) -> str:
        resolutions.append(tuple(args))
        return revision + "\trefs/heads/deploy-config-candidate"

    def download(url: str) -> bytes:
        urls.append(url)
        prefix = "https://release.example/" + revision + "/"
        assert url.startswith(prefix)
        return (source / url.removeprefix(prefix)).read_bytes()

    monkeypatch.setattr(release, "run", resolve)
    monkeypatch.setattr(release, "fetch", download)
    selected, _ = release.stage_release(
        release.Selection("deploy-config-candidate", "https://release.example"), tmp_path / "stage"
    )
    assert selected == revision and len(resolutions) == 1 and len(urls) == len(release.ASSETS) + 1
    assert (tmp_path / "stage/executor/executor.py").read_bytes() == (source / "executor/executor.py").read_bytes()


@pytest.mark.parametrize(
    "failure", ("download", "checksum", "protocol", "executor_protocol", "existing_protocol", "unit")
)
def test_validation_failures_leave_installed_executor_untouched(source: Path, tmp_path: Path, failure: str) -> None:
    destination = tmp_path / "installed"
    installed = destination / "executor/executor.py"
    release.atomic_write(installed, b"original installed executor\n", 0o555)
    before = installed.stat()
    config = {
        "FACTORY_HORDE_DATA_ROOT": str(destination / "data"),
        "EXECUTOR_USER": "operator",
        "EXECUTOR_GROUP": "operator",
        "EXECUTOR_PYTHON": "/usr/bin/python3.14",
    }
    installation = release.Installation(
        "factory-horde-test", "factory-horde-test", False, release.Selection("snapshot", source.as_uri()), config
    )
    metadata = json.loads((source / release.MANIFEST).read_bytes())
    if failure == "download":
        (source / "envs/deployed/docker-compose.yml").unlink()
    elif failure == "checksum":
        (source / "executor/executor.py").write_bytes(b"changed but not checksummed\n")
    elif failure == "protocol":
        metadata["protocol"] = 2
        (source / release.MANIFEST).write_text(json.dumps(metadata))
    elif failure == "executor_protocol":
        executor = source / "executor/executor.py"
        executor.write_text(executor.read_text().replace("PROTOCOL_VERSION = 1", "PROTOCOL_VERSION = 2"))
        release.manifest(source, check=False)
        metadata = json.loads((source / release.MANIFEST).read_bytes())
        metadata["protocol"] = 1
        (source / release.MANIFEST).write_text(json.dumps(metadata))
    elif failure == "unit":
        (source / "installer/factory-horde-executor.service").write_text("[Service]\nType=simple\n")
        release.manifest(source, check=False)
    else:
        record = Path(config["FACTORY_HORDE_DATA_ROOT"]) / "control/statuses/job.json"
        record.parent.mkdir(parents=True)
        record.write_text('{"protocol_version":2}\n')
    with pytest.raises((release.ReleaseError, OSError)):
        release.apply_release(
            destination, installation, config, install=failure == "unit", application=False, prepare_only=False
        )
    assert installed.read_bytes() == b"original installed executor\n"
    assert installed.stat().st_ino == before.st_ino and installed.stat().st_mtime_ns == before.st_mtime_ns
    assert not list(destination.glob(".release-*"))


def test_atomic_replacement_keeps_open_old_file_and_no_change_preserves_inode(tmp_path: Path) -> None:
    destination = tmp_path / "executor.py"
    release.atomic_write(destination, b"old\n", 0o555)
    with destination.open("rb") as old:
        before = destination.stat()
        assert release.atomic_write(destination, b"new\n", 0o555)
        assert old.read() == b"old\n" and destination.read_bytes() == b"new\n"
        assert destination.stat().st_dev == before.st_dev and destination.stat().st_ino != before.st_ino
    after = destination.stat()
    assert not release.atomic_write(destination, b"new\n", 0o555)
    assert destination.stat().st_ino == after.st_ino
    assert not list(tmp_path.glob(".executor.py.*"))


def test_overlapping_cli_update_exits_busy_without_mutation(tmp_path: Path) -> None:
    destination = tmp_path / "installation"
    with release.update_lock(destination):
        result = subprocess.run(
            ["/usr/bin/python3.14", "-I", str(REPO / "installer/release.py"), "update", str(destination)],
            text=True,
            capture_output=True,
            check=False,
        )
    assert result.returncode == 75 and "lock" in result.stderr
    assert sorted(path.name for path in destination.iterdir()) == [".update.lock"]


def test_env_parser_does_not_execute_expansions_and_preserves_literal_values(tmp_path: Path) -> None:
    path = tmp_path / "operator.env"
    path.write_text("NETUID=2\nNAME='value with spaces'\nEMPTY=\n")
    assert release.read_env(path) == {"NETUID": "2", "NAME": "value with spaces", "EMPTY": ""}
    path.write_bytes(release.environment(release.read_env(path)))
    assert release.read_env(path)["NAME"] == "value with spaces"
    for content in ("NAME=$(touch marker)", "NAME=`id`", "NETUID=2\nNETUID=12", "export NETUID=2"):
        path.write_text(content)
        with pytest.raises(release.ReleaseError):
            release.read_env(path)
    assert not (tmp_path / "marker").exists()


def test_symlink_destinations_and_unsafe_system_names_are_rejected(tmp_path: Path) -> None:
    original = tmp_path / "original"
    original.write_text("keep")
    link = tmp_path / "linked"
    link.symlink_to(original)
    with pytest.raises(release.ReleaseError):
        release.atomic_write(link, b"replace")
    assert original.read_text() == "keep"
    installation = release.Installation(
        "factory-horde-valid", "valid-project", False, release.Selection("snapshot"), {}
    )
    for service in ("docker", "factory-horde-*", "factory-horde-foo\nroot ALL=(ALL) ALL"):
        with pytest.raises(release.ReleaseError):
            replace(installation, service=service).validate()


def test_installed_executor_needs_no_package_environment(source: Path) -> None:
    executable = source / "executor/executor.py"
    result = subprocess.run(
        ["/usr/bin/python3.14", "-I", str(executable), "--protocol-version"],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert result.stdout.strip() == "1" and os.getuid() != 0


def test_clean_localnet_configuration_generates_isolated_paths_and_distinct_tokens(tmp_path: Path) -> None:
    path = tmp_path / "operator.env"
    path.write_text("ENVIRONMENT=localnet\nNETUID=2\nBITTENSOR_NETWORK=ws://subtensor:9944\n")
    config = release.configuration(path, tmp_path / "installation", application=True, localnet=True)
    assert config["HOST_WALLET_DIR"] == str(tmp_path / "installation/wallets")
    assert config["FACTORY_HORDE_DATA_ROOT"] == str(tmp_path / "installation/data")
    tokens = [value for key, value in config.items() if "TOKEN" in key]
    assert len(tokens) == len(set(tokens)) == 8
    assert all(len(value) == 48 for value in tokens)
    path.write_bytes(release.environment(config))
    assert release.configuration(path, tmp_path / "installation", application=True, localnet=True) == config
    config["HOST_WALLET_DIR"] = str(tmp_path / "outside-wallets")
    path.write_bytes(release.environment(config))
    with pytest.raises(release.ReleaseError, match="Localnet requires"):
        release.configuration(path, tmp_path / "installation", application=True, localnet=True)
