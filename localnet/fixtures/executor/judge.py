#!/usr/local/bin/python3
"""Localnet-only report and termination faults; never a production judge."""

import json
import os
import random
import signal
import time
from pathlib import Path
from types import FrameType
from uuid import uuid4


def ignore_term(_number: int, _frame: FrameType | None) -> None:
    """Require actual KILL/inspection in the hanging-judge fixture."""


def main() -> None:
    """Emit one deliberately selected report, then exit or hang as the image profile specifies.

    Raises:
        SystemExit: The nonzero profile leaves a valid-looking report but exits with code 7.
    """
    profile = os.environ["FIXTURE_PROFILE"]
    task = json.loads(Path("/input/task.json").read_bytes())
    directory = Path("/report")
    with (directory / "starts").open("a") as starts:
        starts.write("started\n")
    if profile == "judge-missing":
        return
    if profile == "judge-malformed":
        (directory / "report.json").write_text('{"score":')
        return
    if profile == "judge-symlink":
        (directory / "report.json").symlink_to("/etc/passwd")
        return
    score = random.SystemRandom().random()
    if profile == "judge-nan":
        score = float("nan")
    elif profile == "judge-infinite":
        score = float("inf")
    elif profile == "judge-range":
        score = 1.25
    report = {
        "protocol_version": 1,
        "round_id": task["round_id"],
        "job_id": str(uuid4()) if profile == "judge-mismatch" else task["judge_job_id"],
        "miner_hotkey": task["miner_hotkey"],
        "kind": "judge",
        "factory_job_id": task["factory_job_id"],
        "checked_files": ["main.py", "README.md"],
        "score": score,
        "failure": None,
    }
    (directory / "report.json").write_text(json.dumps(report) + "\n")
    if profile == "judge-nonzero":
        raise SystemExit(7)
    if profile == "judge-hang":
        signal.signal(signal.SIGTERM, ignore_term)
        while True:
            time.sleep(1)


if __name__ == "__main__":
    main()
