"""Run five real jobs through containerized Nexus tasks and restart during detached execution."""

import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values
from pylon_client.artanis import Config, IdentityName, PylonAuthToken, PylonClient
from validator.discovery import freeze_discovery
from validator.records import Deadlines, JobRequest, RoundPlan, RoundRecord, request_for
from validator.result_records import JobResult
from validator.result_repository import ResultRepository
from validator.round_repository import RoundRepository

from .check import Registrations
from .check_executor import inspect
from .task_probe import ProbePlan

ROOT = Path(__file__).resolve().parent


def main() -> None:
    """Use common Compose services, the application image and the installed systemd executor.

    Raises:
        RuntimeError: Frozen fixture identity, execution, restart, stages or Nexus outcomes differ.
    """
    config = dotenv_values(ROOT / ".env")
    if config.get("ENVIRONMENT") != "localnet" or config.get("NETUID") != "2":
        raise RuntimeError("Requires isolated localnet subnet 2")
    subprocess.run(["systemctl", "is-active", "--quiet", "factory-horde-localnet-executor"], check=True)
    root = Path(config["FACTORY_HORDE_DATA_ROOT"] or "")
    rounds = RoundRepository(root)
    results = ResultRepository(rounds)
    registrations = Registrations.model_validate_json((ROOT / "state/registrations.json").read_bytes())
    own = next(row.hotkey for row in registrations.registrations if row.identity == "validator")
    images = dotenv_values(ROOT / "state/published-images.env")
    with PylonClient(
        Config(
            address="http://127.0.0.1:8000",
            identity_name=IdentityName("validator"),
            identity_token=PylonAuthToken(config["VALIDATOR_PYLON_IDENTITY_TOKEN"] or ""),
        )
    ) as client:
        snapshot = freeze_discovery(rounds.files, uuid4(), client.v1.identity, 2, own)
    if len(snapshot.cohort) != 5 or any(m.image != images["GHCR_FACTORY_IMAGE"] for m in snapshot.cohort):
        raise RuntimeError("Expected the five registered baseline commitments")
    start = datetime.now(UTC) + timedelta(seconds=10)
    specification = "Produce the fixed FactoryHorde greeting project. No model inference.\n"
    plan = RoundPlan(
        sequence=max(r.plan.sequence for r in rounds.rounds()) + 1,
        deadlines=Deadlines(
            start=start,
            generation_end=start + timedelta(seconds=100),
            evaluation_start=start + timedelta(seconds=105),
            judge_end=start + timedelta(seconds=150),
            round_end=start + timedelta(seconds=160),
        ),
        discovery_block=snapshot.block,
        discovery_block_hash=snapshot.block_hash,
        membership_snapshot="commitment_block",
        cohort=snapshot.cohort,
        judge_image=images["GHCR_JUDGE_IMAGE"] or "",
        stop_grace_seconds=5,
        specification_sha256=hashlib.sha256(specification.encode()).hexdigest(),
    )
    rounds.prepare_round(plan, specification)
    failed = json.loads((ROOT / "state/task7-pull-failure.json").read_bytes())
    historical = JobRequest.model_validate_json(json.dumps(failed["request"]))
    fixture = ProbePlan(plan=plan, historical_failed=historical)
    plan_file = ROOT / f"state/task10-plan-{plan.round_id}.json"
    plan_file.write_text(fixture.model_dump_json(indent=2))
    name = f"factory-horde-task-probe-{plan.round_id}"
    launched = subprocess.run(
        [
            str(ROOT / "compose.sh"),
            "run",
            "--detach",
            "--no-deps",
            "--name",
            name,
            "--volume",
            f"{ROOT / 'task_probe.py'}:/probe.py:ro",
            "--volume",
            f"{plan_file}:/probe-plan.json:ro",
            "validator",
            "python",
            "/probe.py",
            "/probe-plan.json",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    factory_jobs = tuple(request_for(plan, member, "factory") for member in plan.cohort)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        statuses = [rounds.status(job) for job in factory_jobs]
        if all(status is not None and status.execution == "running" for status in statuses):
            break
        code = subprocess.run(
            ["docker", "inspect", name, "--format", "{{.State.Status}}"], capture_output=True, text=True, check=True
        )
        if code.stdout.strip() == "exited":
            raise RuntimeError(f"Probe exited early; inspect docker logs {name}")
        time.sleep(0.5)
    else:
        raise RuntimeError(f"Five jobs did not become outstanding; inspect docker logs {name}")
    original_ids = {
        str(job.job_id): status.container_id for job, status in zip(factory_jobs, statuses, strict=True) if status
    }
    subprocess.run(["docker", "restart", name], check=True, capture_output=True)
    print("PASS: five actual factory jobs were outstanding; restarted the containerized Nexus validator", flush=True)
    marker = root / f"control/task10-probe-{plan.round_id}.json"
    deadline = time.monotonic() + 210
    while not marker.exists() and time.monotonic() < deadline:
        code = subprocess.run(
            ["docker", "inspect", name, "--format", "{{.State.Status}}"], capture_output=True, text=True, check=True
        )
        if code.stdout.strip() == "exited":
            raise RuntimeError(f"Probe exited before Nexus outcomes; inspect docker logs {name}")
        time.sleep(1)
    if not marker.exists():
        raise RuntimeError(f"Probe did not persist every outcome; inspect docker logs {name}")
    outcomes: list[JobResult] = []
    for member in plan.cohort:
        factory, judge = (request_for(plan, member, kind) for kind in ("factory", "judge"))
        for job in (factory, judge):
            result = results.read(job)
            if result is None or result.failure is not None:
                raise RuntimeError("A real task did not produce a successful durable result")
            inspect(job, root, result.status)
            outcomes.append(result)
        factory_result, judge_result = outcomes[-2:]
        if factory_result.status.container_id != original_ids[str(factory.job_id)]:
            raise RuntimeError("Validator restart replaced a factory execution")
        if (
            judge_result.status.started_at is None
            or judge_result.status.started_at < plan.deadlines.evaluation_start
            or factory_result.status.finished_at is None
            or judge_result.status.started_at < factory_result.status.finished_at
        ):
            raise RuntimeError("Judge ran before its evaluation boundary or confirmed factory exit")
    failed_result = results.read(historical)
    if failed_result is None or failed_result.failure is None:
        raise RuntimeError("The real failed-pull outcome was not delivered through the failure task branch")
    while datetime.now(UTC) < plan.deadlines.round_end:
        time.sleep(0.5)
    rounds.save_round(RoundRecord(plan=plan, stage="complete", completed_at=datetime.now(UTC)))
    logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True, check=True)
    (ROOT / f"state/task10-probe-{plan.round_id}.log").write_text(logs.stdout + logs.stderr)
    evidence = {
        "checked_at": datetime.now(UTC).isoformat(),
        "image_id": (ROOT / "state/validator-image.id").read_text().strip(),
        "probe_container": name,
        "probe_container_id": launched.stdout.strip(),
        "plan": plan.model_dump(mode="json"),
        "original_factory_ids": original_ids,
        "validator_restarted_during_generation": True,
        "outcomes": [result.model_dump(mode="json") for result in outcomes],
        "historical_failed_pull": failed_result.model_dump(mode="json"),
        "nexus_runtime": json.loads(marker.read_bytes()),
    }
    (ROOT / "state/task10-tasks.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(
        "PASS: both real Nexus tasks, five linked judge scores, failure branch and stable restart identities",
        flush=True,
    )


if __name__ == "__main__":
    main()
