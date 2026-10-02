"""Exercise application readiness, scraping and executor staleness on an idle localnet."""

import argparse
import json
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel
from validator.health import ExecutorHealth, Readiness
from validator.record_files import RecordFiles
from validator.round_repository import RoundRepository

from .runtime import Runtime

ROOT = Path(__file__).resolve().parents[1]
HTTP_PROBE = """import json, sys, urllib.error, urllib.request
try:
    response = urllib.request.urlopen('http://127.0.0.1:9101' + sys.argv[1], timeout=5)
except urllib.error.HTTPError as error:
    response = error
with response:
    print(json.dumps({'status': response.status, 'body': response.read().decode()}))
"""


class Response(BaseModel):
    """HTTP response from inside the common validator container."""

    status: int
    body: str


class ReadyResponse(BaseModel):
    """Disposable health projection; contains no credentials."""

    ready: bool
    checks: dict[str, bool]
    observation: Readiness | None


def request(runtime: Runtime, path: str) -> Response:
    """Use the image's Python without exposing a new host port."""
    result = subprocess.run(
        [*runtime.command, "exec", "-T", "validator", "/opt/venv/bin/python", "-c", HTTP_PROBE, path],
        capture_output=True,
        text=True,
        check=True,
    )
    return Response.model_validate_json(result.stdout)


def wait_ready(runtime: Runtime, predicate: Callable[[ReadyResponse], bool]) -> ReadyResponse:
    """Bound observations; a timeout fails the check without deleting evidence.

    Raises:
        RuntimeError: The expected health transition did not occur.
    """
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        response = request(runtime, "/readyz")
        body = ReadyResponse.model_validate_json(response.body)
        if predicate(body):
            if response.status != (200 if body.ready else 503):
                raise RuntimeError("Readiness body disagrees with HTTP status")
            return body
        time.sleep(2)
    raise RuntimeError("Readiness transition was not observed within 60 seconds")


def main() -> None:
    """Pause only an idle isolated executor; restore the service and permissions in all cases.

    Raises:
        RuntimeError: Isolation, metric exposure or health semantics are incorrect.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / "localnet/.env")
    args = parser.parse_args()
    runtime = Runtime.load(args.env_file)
    config = runtime.config
    data_root = runtime.data
    if (
        config.get("ENVIRONMENT") != "localnet"
        or config.get("NETUID") != "2"
        or config.get("FACTORY_HORDE_DATA_ROOT") != str(data_root)
        or any(
            config.get(key, "false") != "false" for key in ("VALIDATOR_DISPATCH_ENABLED", "VALIDATOR_WEIGHTS_ENABLED")
        )
    ):
        raise RuntimeError("Require idle localnet with dispatch and weights disabled")
    rounds = RoundRepository(data_root)
    for job in rounds.requests():
        status = rounds.status(job)
        if status is None or not status.confirmed_stopped:
            raise RuntimeError("Finish or reconcile active work before this monitoring check")
    initial = wait_ready(runtime, lambda body: body.ready)
    metrics = request(runtime, "/metrics")
    required = (
        "factory_horde_ready",
        "factory_horde_executor_operation_seconds_bucket",
        "factory_horde_executor_operations_total",
        "factory_horde_record_operations_total",
        "factory_horde_round_phase",
        "factory_horde_usable_round_age_seconds",
    )
    if metrics.status != 200 or any(name not in metrics.body for name in required):
        raise RuntimeError("Missing application/executor metrics")
    print("Application ready; validator and executor metrics are exposed", flush=True)
    files = RecordFiles(data_root)
    try:
        subprocess.run(["sudo", "-n", "systemctl", "stop", runtime.service], check=True)
        stopped = files.read_bytes("control/executor-health.json")
        stale = wait_ready(runtime, lambda body: not body.ready and not body.checks.get("executor", True))
        if request(runtime, "/livez").status != 200 or files.read_bytes("control/executor-health.json") != stopped:
            raise RuntimeError("Stale readiness did not preserve live HTTP and unchanged executor evidence")
    finally:
        subprocess.run(["sudo", "-n", "systemctl", "start", runtime.service], check=True)
    restored = wait_ready(runtime, lambda body: body.ready)
    print("Stopped executor became stale while HTTP stayed live; restart restored readiness", flush=True)
    projections = data_root / "control/projections"
    original_mode = projections.stat().st_mode & 0o777
    try:
        projections.chmod(0o500)
        unwritable = wait_ready(runtime, lambda body: not body.ready and not body.checks.get("writable_paths", True))
    finally:
        projections.chmod(original_mode)
    repaired = wait_ready(runtime, lambda body: body.ready)
    health = files.read("control/executor-health.json", ExecutorHealth)
    evidence = {
        "checked_at": datetime.now(UTC).isoformat(),
        "validator_image": runtime.image("validator"),
        "executor_sha256": sha256((ROOT / "executor/executor.py").read_bytes()).hexdigest(),
        "initial": initial.model_dump(mode="json"),
        "stale_executor": stale.model_dump(mode="json"),
        "restored": restored.model_dump(mode="json"),
        "unwritable_results": unwritable.model_dump(mode="json"),
        "repaired": repaired.model_dump(mode="json"),
        "executor_health": health.model_dump(mode="json"),
        "required_metrics": required,
    }
    output = runtime.state / "task14-monitoring.json"
    output.write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"Result-store permissions failed readiness and recovered; evidence: {output}", flush=True)


if __name__ == "__main__":
    main()
