"""Real Docker boundaries for the executor-owned application runner."""

import os
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from executor.executor import ApplicationRunner, Executor, Request, Settings, parse

IMAGE = "docker.io/library/python@sha256:35caba83af2fe6da9a5d922f9b0156f21d1246a48278c58a08bd253303ba42e8"
FIXTURES = Path(__file__).resolve().parents[1] / "spec/fixtures/protocol-v1"
pytestmark = pytest.mark.skipif(os.environ.get("FH_RUNNER_IMAGE_TEST") != "1", reason="Explicit real Docker test")


@pytest.fixture
def runner(tmp_path: Path):
    record = parse((FIXTURES / "judge-request.json").read_bytes())
    record.update(
        created_at=datetime.now(UTC).isoformat(), deadline=(datetime.now(UTC) + timedelta(minutes=3)).isoformat()
    )
    request = replace(Request.load(record, str(record["job_id"]) + ".json"), image=IMAGE)
    executor = Executor(Settings(tmp_path, os.getuid(), os.getgid()))
    (tmp_path / request.output_dir).mkdir(parents=True)
    (tmp_path / "sentinel-report").write_text("secret")
    result = ApplicationRunner(executor, request, {})
    yield result
    subprocess.run(["docker", "rm", "-f", result.name], check=False, capture_output=True)


def command(*args: str) -> dict[str, object]:
    return {"id": "probe", "args": list(args), "stdin": "", "observation": "boundary probe"}


def test_workspace_boundaries_and_persistence(runner: ApplicationRunner):
    source = runner.files.root / runner.request.output_dir / "main.py"
    source.write_text("""import os, socket, pathlib, sys
for name in ('/report', '/input', '/runner', '/var/run/docker.sock'):
    assert not pathlib.Path(name).exists(), name
assert 'ANTHROPIC_BASE_URL' not in os.environ
assert 'CLAUDE_CODE_OAUTH_TOKEN' not in os.environ
assert not pathlib.Path('/proc/1/root/report').exists()
try:
    socket.create_connection(('1.1.1.1', 80), timeout=1)
except OSError:
    pass
else:
    raise RuntimeError('network accessible')
p = pathlib.Path('state')
if sys.argv[1] == 'add': p.write_text('persisted')
print(p.read_text())
""")
    original = source.read_bytes()
    runner.start()
    first = runner.execute(command("add"))
    second = runner.execute(command("list"))
    assert first["exit_code"] == second["exit_code"] == 0
    assert first["stdout"] == second["stdout"] == "persisted\n"
    assert source.read_bytes() == original
    runner.stop()
    assert runner.ledger["app_stopped"] is True


def test_capture_bounds_and_descendant_stop(runner: ApplicationRunner):
    source = runner.files.root / runner.request.output_dir / "main.py"
    source.write_text("""import os, time
if os.fork() == 0:
    os.setsid()
    while True: time.sleep(1)
print('x' * 100000, flush=True)
while True: time.sleep(1)
""")
    runner.start()
    # Exercise the real absolute deadline without a minute-long test.
    runner.request = replace(runner.request, deadline=datetime.now(UTC) + timedelta(seconds=7))
    outcome = runner.execute(command())
    assert outcome["timed_out"] is True
    assert outcome["stdout_truncated"] is True
    assert len(str(outcome["stdout"])) == 16384
    assert runner.ledger["app_stopped"] is True
    inspected = runner.inspect()
    assert inspected is not None and inspected["State"]["Running"] is False
