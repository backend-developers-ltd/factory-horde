#!/usr/bin/python3.14
"""Localnet-only Docker CLI fault wrapper; the production executor has no fault hooks."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import cast


def main() -> None:
    """Hold a real CLI response or simulate unavailable observation under explicit test control."""
    root = Path(os.environ["FACTORY_HORDE_FAULT_ROOT"])
    args = sys.argv[1:]
    try:
        config = cast(dict[str, str], json.loads((root / "fault.json").read_text()))
    except FileNotFoundError:
        os.execv("/usr/bin/docker", ["docker", *args])
    command = args[2] if args[:1] == ["--config"] else args[0]
    match = config.get("match", "")
    operation = config.get("operation", "")
    if command != operation or not match or not any(match in arg for arg in args):
        os.execv("/usr/bin/docker", ["docker", *args])
    if config.get("behavior") == "unavailable":
        print("Fixture: Docker observation unavailable", file=sys.stderr)
        sys.exit(125)
    result = subprocess.run(["/usr/bin/docker", *args], capture_output=True, text=True, check=False)
    temporary = root / f".entered-{os.getpid()}.tmp"
    temporary.write_text(json.dumps({"operation": operation, "stdout": result.stdout, "returncode": result.returncode}))
    temporary.replace(root / "entered.json")
    while not (root / "release").exists():
        time.sleep(0.1)
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
