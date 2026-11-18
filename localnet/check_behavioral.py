"""Run one isolated five-miner behavioral round and retain its real Claude evidence."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from uuid import uuid4

from pylon_client.artanis import Config, IdentityName, PylonAuthToken, PylonClient, PylonTimeout
from tenacity import Retrying, stop_after_attempt
from validator.records import request_for
from validator.result_repository import ResultRepository
from validator.round_repository import RoundRepository

from miner.miner import submit

from .check import Registrations
from .check_rounds import wait_for
from .runtime import Runtime


def main() -> None:
    """Execute each control once, requiring a useful ranking and attributable reports.

    Raises:
        RuntimeError: Isolation, cohort coverage or behavioral discrimination fails.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("factory_image")
    parser.add_argument("--env-file", type=Path, default=Path(__file__).parent / ".env")
    args = parser.parse_args()
    runtime = Runtime.load(args.env_file)
    rounds = RoundRepository(runtime.data)
    if rounds.rounds():
        raise RuntimeError("Behavioral acceptance requires a fresh experimental data root")
    judge = runtime.image("judge")
    registrations = Registrations.model_validate_json((runtime.state / "registrations.json").read_bytes())
    miners = [row for row in registrations.registrations if row.identity.startswith("miner")]
    variants = {row.hotkey: int(hashlib.sha256(row.hotkey.encode()).hexdigest(), 16) % 3 for row in miners}
    if set(variants.values()) != {0, 1, 2}:
        raise RuntimeError("This cohort does not cover all three deterministic controls")
    for row in miners:
        config = Config(
            address=runtime.pylon_address,
            identity_name=IdentityName(row.identity),
            identity_token=PylonAuthToken(runtime.config[f"{row.identity.upper()}_PYLON_TOKEN"]),
            retry=Retrying(stop=stop_after_attempt(1), reraise=True),
            timeout=PylonTimeout(read=150),
        )
        with PylonClient(config) as client:
            submit(client.v1.identity, args.factory_image, 2)
        print(f"Committed {row.identity}: control {variants[row.hotkey]}", flush=True)
    name = f"factory-horde-behavioral-{uuid4()}"
    command = [*runtime.command, "run", "--detach", "--no-deps", "--name", name]
    for key, value in {
        "VALIDATOR_DISPATCH_ENABLED": "true",
        "VALIDATOR_WEIGHTS_ENABLED": "false",
        "VALIDATOR_JUDGE_MODE": "review",
        "VALIDATOR_JUDGE_IMAGE": judge,
        "VALIDATOR_GENERATION_WINDOW": "60",
        "VALIDATOR_CONFIRMATION_WINDOW": "15",
        "VALIDATOR_EVALUATION_WINDOW": "1200",
        "VALIDATOR_JUDGE_STOP_RESERVE": "30",
        "VALIDATOR_STOP_GRACE_SECONDS": "5",
    }.items():
        command.extend(("--env", f"{key}={value}"))
    subprocess.run([*runtime.command, "stop", "validator"], check=True, capture_output=True)
    subprocess.run([*command, "validator"], check=True, capture_output=True)
    try:
        wait_for(lambda: len(rounds.rounds()) == 1, "No behavioral admission", 90)
        plan = rounds.rounds()[0].plan
        if len(plan.cohort) != 5 or any(member.image != args.factory_image for member in plan.cohort):
            raise RuntimeError("Unexpected frozen behavioral cohort")
        print(f"Admitted {plan.round_id}; waiting for real Claude evaluations", flush=True)
        wait_for(lambda: rounds.rounds()[0].stage == "complete", "Behavioral round did not complete", 1270)
        results = ResultRepository(rounds, "review", judge)
        selected = results.latest_usable_round()
        if selected is None or len(selected[1]) != 5:
            raise RuntimeError("Behavioral round has unavailable or missing evaluations")
        scores = {row.miner_hotkey: row.score for row in selected[1]}
        working = [score for hotkey, score in scores.items() if variants[hotkey] == 0]
        defective = [score for hotkey, score in scores.items() if variants[hotkey] != 0]
        if min(working) <= max(defective):
            raise RuntimeError(f"Working controls did not outrank defective controls: {scores}")
        reports: dict[str, object] = {}
        for member in plan.cohort:
            job = request_for(plan, member, "judge")
            evidence = runtime.data / str(job.report_dir)
            execution = json.loads((evidence / "execution.json").read_text())
            if not execution["observations"]:
                raise RuntimeError("Missing actual application observations")
            if variants[member.miner_hotkey] == 0:
                observed = {row["id"]: row for row in execution["observations"]}
                successful = {
                    check["args"][0]
                    for check in execution["checks"]
                    if check["args"] and observed[check["id"]]["exit_code"] == 0
                }
                if not {"add", "list", "done", "remove"} <= successful:
                    raise RuntimeError("Working control was not exercised through all four core operations")
            reports[member.miner_hotkey] = {
                "variant": variants[member.miner_hotkey],
                "score": scores[member.miner_hotkey],
                "review": json.loads((evidence / "review.json").read_text()),
                "execution": execution,
            }
        summary = {"round": plan.model_dump(mode="json"), "reports": reports, "working_outranks_defects": True}
        (runtime.state / "behavioral-round.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(f"PASS: real behavioral discrimination: {scores}", flush=True)
    finally:
        subprocess.run(["docker", "stop", "--time", "5", name], check=False, capture_output=True)
        logs = subprocess.run(["docker", "logs", name], check=False, capture_output=True, text=True)
        (runtime.state / "behavioral-validator.log").write_text(logs.stdout + logs.stderr)


if __name__ == "__main__":
    main()
