"""Run the production coordinator with five real commitments, stages and container restarts."""

import json
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values
from nexus.v1 import TaskResultId
from validator.records import RoundPlan, RoundRecord, request_for
from validator.result_records import JobResult
from validator.result_repository import ResultRepository
from validator.result_store import EVALUATION_TASK, FACTORY_TASK, FileTaskResultStore
from validator.round_repository import RoundRepository

from .check import Registrations
from .check_executor import inspect

ROOT = Path(__file__).resolve().parent


def wait_for(predicate: Callable[[], bool], message: str, seconds: float = 200) -> None:
    """Bound every stage wait so a failed actor remains a visible acceptance failure.

    Raises:
        RuntimeError: The expected real state was not observed in time.
    """
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.25)
    raise RuntimeError(message)


def main() -> None:
    """Use the common application image/entrypoint and the installed host executor.

    Raises:
        RuntimeError: Local isolation, business identity, stage or outcome verification fails.
    """
    config = dotenv_values(ROOT / ".env")
    if config.get("ENVIRONMENT") != "localnet" or config.get("NETUID") != "2":
        raise RuntimeError("Requires isolated localnet subnet 2")
    if (config.get("VALIDATOR_DISPATCH_ENABLED") or "false").lower() != "false":
        raise RuntimeError("Pause ordinary round admission before running this bounded fixture")
    subprocess.run(["systemctl", "is-active", "--quiet", "factory-horde-localnet-executor"], check=True)
    root = Path(config["FACTORY_HORDE_DATA_ROOT"] or "")
    rounds = RoundRepository(root)
    results = ResultRepository(rounds)
    existing = rounds.rounds()
    if any(
        r.unresolved_jobs or r.stage != "complete" and datetime.now(UTC) < r.plan.deadlines.round_end for r in existing
    ):
        raise RuntimeError("An existing round is still active or unresolved; reconcile it first")
    previous_rounds = {r.plan.round_id for r in existing}
    previous_requests = set(rounds.requests())
    identities = Registrations.model_validate_json((ROOT / "state/registrations.json").read_bytes())
    own = next(row.hotkey for row in identities.registrations if row.identity == "validator")
    images = dotenv_values(ROOT / "state/published-images.env")
    name = f"factory-horde-round-probe-{uuid4()}"
    compose = str(ROOT / "compose.sh")
    command = [compose, "run", "--detach", "--no-deps", "--name", name]
    for key, value in {
        "VALIDATOR_DISPATCH_ENABLED": "true",
        "VALIDATOR_HOTKEY": own,
        "VALIDATOR_JUDGE_IMAGE": images["GHCR_JUDGE_IMAGE"] or "",
        "VALIDATOR_GENERATION_WINDOW": "100",
        "VALIDATOR_CONFIRMATION_WINDOW": "15",
        "VALIDATOR_EVALUATION_WINDOW": "65",
        "VALIDATOR_JUDGE_STOP_RESERVE": "10",
        "VALIDATOR_STOP_GRACE_SECONDS": "5",
    }.items():
        command.extend(("--env", f"{key}={value}"))
    subprocess.run([compose, "stop", "validator"], check=True, capture_output=True)
    complete = False
    try:
        subprocess.run(
            [
                *command,
                "--env",
                "VALIDATOR_PYLON_SERVICE_ADDRESS=http://127.0.0.1:1",
                "validator",
            ],
            check=True,
            capture_output=True,
        )
        time.sleep(15)
        readiness_logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True, check=True)
        readiness_text = readiness_logs.stdout + readiness_logs.stderr
        if "Configuration error" in readiness_text or not any(
            term in readiness_text
            for term in ("Pylon poll failure", "Connection refused", "connect", "ConnectionError")
        ):
            raise RuntimeError("Readiness fixture did not establish an actual chain connection failure")
        if set(rounds.requests()) != previous_requests or {r.plan.round_id for r in rounds.rounds()} != previous_rounds:
            raise RuntimeError("A runtime without a valid chain beat admitted work")
        subprocess.run(["docker", "rm", "--force", name], check=True, capture_output=True)
        print("PASS: missing initial chain beat prevented discovery and dispatch", flush=True)
        subprocess.run([*command, "validator"], check=True, capture_output=True)

        def new_rounds() -> list[RoundRecord]:
            return [r for r in rounds.rounds() if r.plan.round_id not in previous_rounds]

        wait_for(lambda: len(new_rounds()) == 1, "Coordinator did not admit exactly one round", 90)
        plan: RoundPlan = new_rounds()[0].plan
        if len(plan.cohort) != 5 or any(m.image != images["GHCR_FACTORY_IMAGE"] for m in plan.cohort):
            raise RuntimeError("Coordinator did not freeze the five baseline commitments")
        factories = [request_for(plan, member, "factory") for member in plan.cohort]
        judges = [request_for(plan, member, "judge") for member in plan.cohort]
        wait_for(lambda: all(job in rounds.requests() for job in factories), "Factory publication incomplete", 30)
        request_bytes = {
            job.job_id: rounds.files.read_bytes(f"control/requests/{job.job_id}.json") for job in factories
        }
        subprocess.run(["docker", "restart", "--time", "1", name], check=True, capture_output=True)
        print("PASS: restarted application immediately after factory request publication", flush=True)
        wait_for(
            lambda: all(
                (status := rounds.status(job)) is not None and status.execution == "running" for job in factories
            ),
            "Five factories did not run concurrently",
            60,
        )
        original_ids = {
            str(job.job_id): status.container_id for job in factories if (status := rounds.status(job)) is not None
        }
        wait_for(
            lambda: all((status := rounds.status(job)) is not None and status.confirmed_stopped for job in factories),
            "Baseline factories did not finish",
            95,
        )
        if datetime.now(UTC) >= plan.deadlines.evaluation_start or any(job in rounds.requests() for job in judges):
            raise RuntimeError("Could not establish early completion before the judge gate")
        early_finished_at = datetime.now(UTC)
        print("PASS: all factories finished early; no judge was dispatched", flush=True)
        wait_for(lambda: datetime.now(UTC) >= plan.deadlines.generation_end, "Generation boundary timeout")
        subprocess.run(["docker", "restart", "--time", "1", name], check=True, capture_output=True)
        wait_for(lambda: datetime.now(UTC) >= plan.deadlines.evaluation_start, "Evaluation boundary timeout")
        subprocess.run(["docker", "restart", "--time", "1", name], check=True, capture_output=True)
        wait_for(lambda: new_rounds()[0].stage == "complete", "Round outcomes did not settle", 55)
        # Fully settled results may complete early; admission still waits for the frozen round_end.
        wait_for(
            lambda: all(
                (root / f"control/projections/{job.projection_id}.json").exists() for job in (*factories, *judges)
            ),
            "Nexus projections did not catch up",
            15,
        )
        if datetime.now(UTC) >= plan.deadlines.round_end:
            raise RuntimeError("Fixture could not stop before the next scheduled admission")
        subprocess.run(["docker", "stop", "--time", "1", name], check=True, capture_output=True)
        completed = new_rounds()
        if len(completed) != 1 or completed[0].plan != plan or completed[0].unresolved_jobs:
            raise RuntimeError("Restart changed the plan or left unresolved execution")
        store = FileTaskResultStore(results)
        outcomes: list[JobResult] = []
        for job in (*factories, *judges):
            decision = results.read(job)
            if decision is None or decision.failure is not None:
                raise RuntimeError(f"Job did not succeed: {job.job_id}")
            projected = store.get_task_result(
                FACTORY_TASK if job.kind == "factory" else EVALUATION_TASK, TaskResultId(job.projection_id)
            )
            if projected.id != job.projection_id or projected.executor_payload != job:
                raise RuntimeError("Reconstructed Nexus result differs from the business job")
            inspect(job, root, decision.status)
            if job.kind == "factory":
                if (
                    rounds.files.read_bytes(f"control/requests/{job.job_id}.json") != request_bytes[job.job_id]
                    or decision.status.container_id != original_ids[str(job.job_id)]
                ):
                    raise RuntimeError("Restart changed factory request bytes or Docker identity")
            elif decision.status.started_at is None or decision.status.started_at < plan.deadlines.evaluation_start:
                raise RuntimeError("Judge bypassed evaluation start")
            outcomes.append(decision)
        usable = results.latest_usable_round()
        if usable is None or usable[0] != plan or len(usable[1]) != 5:
            raise RuntimeError("Five stable accepted scores are not available")
        logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True, check=True)
        (ROOT / "state/task11-coordinator.log").write_text(logs.stdout + logs.stderr)
        evidence = {
            "checked_at": datetime.now(UTC).isoformat(),
            "image_id": (ROOT / "state/validator-image.id").read_text().strip(),
            "missing_initial_block_prevented_dispatch": True,
            "restart_boundaries": ["request_publication", "generation_end", "evaluation_start"],
            "early_factory_completion": early_finished_at.isoformat(),
            "round": completed[0].model_dump(mode="json"),
            "outcomes": [decision.model_dump(mode="json") for decision in outcomes],
            "result_store_reconstructed": True,
        }
        (ROOT / "state/task11-rounds.json").write_text(json.dumps(evidence, indent=2) + "\n")
        complete = True
        print(
            "PASS: production coordinator froze, ran and scored five miners through every stage with stable restarts",
            flush=True,
        )
    finally:
        # Preserve failed-run logs and all job evidence. Detached jobs retain executor deadlines.
        subprocess.run(["docker", "stop", "--time", "1", name], check=False, capture_output=True)
        if complete:
            subprocess.run(["docker", "rm", name], check=True, capture_output=True)
        subprocess.run([compose, "up", "-d", "--no-deps", "validator"], check=True, capture_output=True)


if __name__ == "__main__":
    main()
