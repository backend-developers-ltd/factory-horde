"""Acceptance tools must not select another chain or an escaped wallet/data tree."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from installer.release import Installation, Selection, environment
from localnet.runtime import Runtime


def installation(directory: Path, **overrides: str) -> Path:
    directory.mkdir()
    config = {
        "ENVIRONMENT": "localnet",
        "NETUID": "2",
        "BITTENSOR_NETWORK": "ws://subtensor:9944",
        "FACTORY_HORDE_DATA_ROOT": str(directory / "data"),
        "HOST_WALLET_DIR": str(directory / "wallets"),
        "VALIDATOR_IMAGE": "ghcr.io/example/validator@sha256:" + "1" * 64,
        **overrides,
    }
    path = directory / ".env"
    path.write_bytes(environment(config))
    selected = Installation("factory-horde-test-executor", "factory-horde-test", True, Selection("a" * 40), {})
    (directory / "installation.json").write_text(json.dumps(asdict(selected)))
    return path


def test_installed_selection_keeps_compose_images_ports_and_data_together(tmp_path: Path) -> None:
    env = installation(tmp_path / "installed", PYLON_HOST_PORT="18000")
    runtime = Runtime.load(env)
    assert runtime.data == env.parent / "data"
    assert runtime.pylon_address == "http://127.0.0.1:18000"
    assert runtime.command[:4] == ("docker", "compose", "--project-name", "factory-horde-test")
    assert str(env) in runtime.command
    assert runtime.image("validator") == "ghcr.io/example/validator@sha256:" + "1" * 64
    assert runtime.service == "factory-horde-test-executor"


@pytest.mark.parametrize(
    ("key", "value"), [("NETUID", "12"), ("BITTENSOR_NETWORK", "finney"), ("ENVIRONMENT", "production")]
)
def test_public_configuration_cannot_reach_acceptance_actions(tmp_path: Path, key: str, value: str) -> None:
    env = installation(tmp_path / "installed", **{key: value})
    with pytest.raises(RuntimeError, match="isolated local subnet"):
        Runtime.load(env)


@pytest.mark.parametrize("name", ["data", "wallets"])
def test_installed_paths_cannot_escape_through_symlinks(tmp_path: Path, name: str) -> None:
    env = installation(tmp_path / "installed")
    outside = tmp_path / "outside"
    outside.mkdir()
    (env.parent / name).symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError, match="must stay inside"):
        Runtime.load(env)
