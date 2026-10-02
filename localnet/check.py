"""Check the task-4 Compose runtime; run with the validator project's environment."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from dotenv import dotenv_values
from pydantic import BaseModel
from pylon_client.artanis import (
    Config,
    Hotkey,
    IdentityName,
    PylonAuthToken,
    PylonClient,
    PylonForbidden,
    PylonUnauthorized,
)

from validator.chain_observer import ChainObservation
from validator.record_files import RecordFiles

ROOT = Path(__file__).resolve().parent


class Registration(BaseModel):
    """Public identity snapshot produced by independent Subtensor bootstrap read-back."""

    identity: str
    uid: int
    hotkey: str
    coldkey: str


class Registrations(BaseModel):
    """Bootstrap's block-aligned membership evidence."""

    netuid: int
    mechanism_id: int
    mechanism_count: int
    block: int
    block_hash: str
    registrations: list[Registration]


class Target(BaseModel):
    """Prometheus scrape result."""

    labels: dict[str, str]
    health: str


class TargetData(BaseModel):
    """Active scrape targets returned by Prometheus."""

    activeTargets: list[Target]


class Targets(BaseModel):
    """Prometheus targets endpoint envelope."""

    data: TargetData


def main() -> None:
    """Fail if connectivity, attribution, authentication or monitoring is incomplete.

    Raises:
        RuntimeError: Runtime checks fail or configuration is not isolated localnet.
    """
    config = dotenv_values(ROOT / ".env")
    if config.get("ENVIRONMENT") != "localnet" or config.get("NETUID") != "2":
        raise RuntimeError("This check requires isolated localnet configuration")
    expected = Registrations.model_validate_json((ROOT / "state/registrations.json").read_bytes())
    files = RecordFiles(Path(config["FACTORY_HORDE_DATA_ROOT"] or ""))
    observation = files.read("control/chain-observation.json", ChainObservation)
    if (
        datetime.now(UTC) - observation.observed_at > timedelta(seconds=30)
        or observation.dispatch_enabled != ((config.get("VALIDATOR_DISPATCH_ENABLED") or "false").lower() == "true")
        or observation.mechanism_id != expected.mechanism_id
        or expected.mechanism_count <= expected.mechanism_id
    ):
        raise RuntimeError("Validator is stale or its mechanism/dispatch configuration differs")
    blocks: dict[str, int] = {}
    address = f"http://127.0.0.1:{int(config.get('PYLON_HOST_PORT') or '8000')}"
    for identity in ("validator", *(f"miner{i}" for i in range(1, 6))):
        token_key = "VALIDATOR_PYLON_IDENTITY_TOKEN" if identity == "validator" else f"{identity.upper()}_PYLON_TOKEN"
        with PylonClient(
            Config(
                address=address,
                identity_name=IdentityName(identity),
                identity_token=PylonAuthToken(config[token_key] or ""),
            )
        ) as client:
            result = client.v1.identity.get_latest_neurons()
            if client.v1.identity.netuid != expected.netuid:
                raise RuntimeError(f"Wrong subnet for {identity}")
            for registration in expected.registrations:
                neuron = result.neurons.get(Hotkey(registration.hotkey))
                if neuron is None or neuron.uid != registration.uid or neuron.coldkey != registration.coldkey:
                    raise RuntimeError(f"Pylon disagrees with direct-chain identity {registration.identity}")
            blocks[identity] = result.block.number
    with PylonClient(
        Config(
            address=address,
            identity_name=IdentityName("miner2"),
            identity_token=PylonAuthToken(config["MINER1_PYLON_TOKEN"] or ""),
        )
    ) as wrong_identity:
        try:
            wrong_identity.v1.identity.get_latest_neurons()
        except PylonForbidden, PylonUnauthorized:
            pass
        else:
            raise RuntimeError("A miner token unexpectedly authorized a different identity")
    metrics = httpx.get(
        f"http://127.0.0.1:{int(config.get('PROMETHEUS_HOST_PORT') or '9090')}/api/v1/targets", timeout=5
    )
    metrics.raise_for_status()
    targets = Targets.model_validate_json(metrics.content).data.activeTargets
    if {target.labels["job"]: target.health for target in targets} != {"host": "up", "pylon": "up", "validator": "up"}:
        raise RuntimeError("Expected healthy host, Pylon and validator Prometheus targets")
    evidence = {
        "checked_at": datetime.now(UTC).isoformat(),
        "validator_observation": observation.model_dump(mode="json"),
        "authenticated_identity_blocks": blocks,
        "cross_identity_token_rejected": True,
        "scrapes": {target.labels["job"]: target.health for target in targets},
    }
    (ROOT / "state/compose-check.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print("PASS: fresh containerized validator, six Pylon identities, token isolation, all three Prometheus targets")


if __name__ == "__main__":
    main()
