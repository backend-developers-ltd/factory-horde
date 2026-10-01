#!/usr/local/bin/python3
"""Localnet-only lifecycle fixture; never a submitted baseline or production factory."""

import os
import signal
import time
from pathlib import Path
from types import FrameType


def ignore_term(_number: int, _frame: FrameType | None) -> None:
    """Deliberately require KILL so executor grace can be measured."""


def main() -> None:
    """Record one real start, then exit or deliberately ignore graceful termination.

    Raises:
        ValueError: The fixture scenario is unknown.
        SystemExit: The nonzero profile deliberately exits with code 7.
    """
    profile = os.environ.get("FIXTURE_PROFILE", "lifecycle")
    mode = (
        Path("/input/specification.md").read_text().strip()
        if profile == "lifecycle"
        else ("ignore-term" if profile == "factory-hang" else "exit")
    )
    if mode not in ("exit", "ignore-term"):
        raise ValueError("Unknown lifecycle fixture mode")
    if mode == "ignore-term":
        signal.signal(signal.SIGTERM, ignore_term)
    with Path("/output/starts").open("a") as output:
        output.write("started\n")
        output.flush()
    if profile != "lifecycle":
        destination = Path("/output/main.py")
        if profile == "factory-symlink":
            destination.symlink_to("/etc/passwd")
        else:
            destination.write_text('print("Hello from the adversarial fixture")\n')
        if profile != "factory-missing":
            Path("/output/README.md").write_text("Localnet fault fixture.\n")
        if profile == "factory-nonzero":
            raise SystemExit(7)
    if mode == "ignore-term":
        while True:
            time.sleep(1)


if __name__ == "__main__":
    main()
