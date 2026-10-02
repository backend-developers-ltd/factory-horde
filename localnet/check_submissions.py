"""Exercise the containerized submitter and immutable discovery on isolated localnet."""

import argparse
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel
from pylon_client.artanis import Config, IdentityName, PylonAuthToken, PylonClient
from validator.discovery import freeze_discovery
from validator.record_files import RecordFiles

from .check import Registrations
from .runtime import Runtime

ROOT = Path(__file__).resolve().parent


class Submission(BaseModel):
    """The submitter's public JSON confirmation, never containing its token."""

    outcome: str
    netuid: int
    hotkey: str
    image: str
    commitment_block: int
    observed_block: int
    observed_block_hash: str
    elapsed_seconds: float


def main() -> None:
    """Run five actual identity-bound containers and preserve frozen discovery evidence.

    Raises:
        RuntimeError: Isolation, publication, identity access or discovery checks fail.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    runtime = Runtime.load(args.env_file)
    config = runtime.config
    if config.get("ENVIRONMENT") != "localnet" or config.get("NETUID") != "2":
        raise RuntimeError("Requires isolated localnet subnet 2")
    factory, judge = runtime.image("factory"), runtime.image("judge")
    submitter = runtime.image("submitter")
    registrations = Registrations.model_validate_json((runtime.state / "registrations.json").read_bytes())
    identities = {row.identity: row for row in registrations.registrations}

    def run(identity: str, image: str, *, token_identity: str | None = None) -> subprocess.CompletedProcess[str]:
        token = config[f"{(token_identity or identity).upper()}_PYLON_TOKEN"] or ""
        return subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--platform",
                "linux/amd64",
                "--read-only",
                "--tmpfs",
                "/tmp",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--memory",
                "512m",
                "--cpus",
                "1",
                "--pids-limit",
                "128",
                "--network",
                f"{runtime.project}_default",
                "--env",
                "PYLON_IDENTITY_TOKEN",
                "--env",
                "PYLON_ADDRESS=http://pylon:8000",
                "--env",
                f"PYLON_IDENTITY={identity}",
                "--env",
                "NETUID=2",
                submitter,
                image,
            ],
            env={**os.environ, "PYLON_IDENTITY_TOKEN": token},
            capture_output=True,
            text=True,
            timeout=330,
            check=False,
        )

    def publish(identity: str, image: str) -> Submission:
        completed = run(identity, image)
        if completed.returncode:
            raise RuntimeError(f"{identity} submitter failed: {completed.stderr}")
        response = Submission.model_validate_json(completed.stdout)
        if response.hotkey != identities[identity].hotkey or response.image != image or response.netuid != 2:
            raise RuntimeError("Submitter confirmed the wrong identity, image or subnet")
        return response

    publications = [publish(f"miner{i}", factory) for i in range(1, 6)]
    repeated = publish("miner1", factory)
    if repeated.outcome != "unchanged" or repeated.commitment_block != publications[0].commitment_block:
        raise RuntimeError("Unchanged submission rewrote the commitment")
    unauthorized = run("miner2", factory, token_identity="miner1")
    if unauthorized.returncode == 0 or not any(word in unauthorized.stderr for word in ("Unauthorized", "Forbidden")):
        raise RuntimeError("Cross-identity token did not fail authentication")
    files = RecordFiles(Path(config["FACTORY_HORDE_DATA_ROOT"] or ""))
    own_hotkey = identities["validator"].hotkey
    with PylonClient(
        Config(
            address=runtime.pylon_address,
            identity_name=IdentityName("validator"),
            identity_token=PylonAuthToken(config["VALIDATOR_PYLON_IDENTITY_TOKEN"] or ""),
        )
    ) as client:
        snapshot_id = uuid4()
        before = freeze_discovery(files, snapshot_id, client.v1.identity, 2, own_hotkey)
        expected = {identities[f"miner{i}"].hotkey: identities[f"miner{i}"].uid for i in range(1, 6)}
        if {member.miner_hotkey: member.uid for member in before.cohort} != expected:
            raise RuntimeError("Discovery disagrees with independent bootstrap attribution")
        if any(member.image != factory for member in before.cohort):
            raise RuntimeError("Unexpected image in initial discovery")
        try:
            updated = publish("miner5", judge)
            recovered = freeze_discovery(files, snapshot_id, client.v1.identity, 2, own_hotkey)
            if recovered != before:
                raise RuntimeError("An active frozen cohort changed")
            after = freeze_discovery(files, uuid4(), client.v1.identity, 2, own_hotkey)
            changed = next(member for member in after.cohort if member.miner_hotkey == identities["miner5"].hotkey)
            if changed.image != judge:
                raise RuntimeError("A new snapshot missed the commitment update")
        finally:
            _ = publish("miner5", factory)
        final = freeze_discovery(files, uuid4(), client.v1.identity, 2, own_hotkey)
        if any(member.image != factory for member in final.cohort):
            raise RuntimeError("Fixture image restoration failed")
    evidence = {
        "checked_at": datetime.now(UTC).isoformat(),
        "submitter_local_image_id": submitter,
        "publications": [item.model_dump() for item in publications],
        "repeat": repeated.model_dump(),
        "cross_identity_write_rejected": True,
        "frozen": before.model_dump(mode="json"),
        "updated": updated.model_dump(),
        "after_update": after.model_dump(mode="json"),
        "frozen_unchanged": True,
        "restored": final.model_dump(mode="json"),
    }
    _ = (runtime.state / "task6-submission-check.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(
        "PASS: five container submissions, exact identities, cross-token rejection, immutable discovery and restoration"
    )


if __name__ == "__main__":
    main()
