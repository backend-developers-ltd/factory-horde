"""Real Docker boundaries for the executor-owned application runner."""

import os
import socket
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from executor.executor import ApplicationRunner, DockerError, Executor, Request, Settings, object_value, parse

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
    executor.docker.require(
        "run",
        "-d",
        "--name",
        request.name,
        "--network",
        "none",
        "--user",
        "1000:1000",
        "--mount",
        f"type=bind,src={tmp_path / 'sentinel-report'},dst=/report/sentinel,readonly",
        "--label",
        f"factory_horde.request_sha256={request.digest}",
        "--label",
        f"factory_horde.job_id={request.job_id}",
        IMAGE,
        "python",
        "-c",
        "import time; time.sleep(86400)",
    )
    yield result
    subprocess.run(["docker", "rm", "-f", request.name], check=False, capture_output=True)
    subprocess.run(["docker", "rm", "-f", result.name], check=False, capture_output=True)


def command(*args: str) -> dict[str, object]:
    return {"id": "probe", "args": list(args), "stdin": "", "observation": "boundary probe"}


def test_workspace_boundaries_and_persistence(runner: ApplicationRunner):
    assert runner.docker.require("exec", runner.request.name, "cat", "/report/sentinel") == "secret"
    source = runner.files.root / runner.request.output_dir / "main.py"
    source.write_text("""import os, socket, pathlib, sys
for name in ('/report', '/input', '/runner', '/var/run/docker.sock'):
    assert not pathlib.Path(name).exists(), name
assert 'ANTHROPIC_BASE_URL' not in os.environ
assert 'CLAUDE_CODE_OAUTH_TOKEN' not in os.environ
assert pathlib.Path.home() == pathlib.Path.cwd()
assert not pathlib.Path('/proc/1/root/report').exists()
try:
    socket.create_connection(('1.1.1.1', 80), timeout=1)
except OSError:
    pass
else:
    raise RuntimeError('network accessible')
p = pathlib.Path.home() / 'state'
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
    assert inspected is not None and object_value(inspected["State"])["Running"] is False


def test_workloads_reach_proxy_but_not_peers_or_internet(runner: ApplicationRunner) -> None:
    network = os.environ.get("FH_TEST_PROXY_NETWORK")
    if not network:
        pytest.skip("Requires a test proxy on its configured template network")
    settings = replace(runner.executor.settings, factory_network=network)
    runner.executor.settings = settings
    runner.docker.settings = settings
    first = runner.request
    second = replace(first, job_id=str(uuid4()))
    names = [first.name + "-probe", second.name + "-probe"]
    listener = socket.socket()
    listener.bind(("0.0.0.0", 0))
    listener.listen(4)
    host_port = listener.getsockname()[1]
    try:
        runner.docker.require(
            "run",
            "--rm",
            "--network",
            "bridge",
            IMAGE,
            "python",
            "-c",
            f"import socket; socket.create_connection(('172.17.0.1', {host_port}), timeout=2)",
        )
        net1 = runner.docker.inference_network(first)
        net2 = runner.docker.inference_network(second)
        for name, net in zip(names, [net1, net2], strict=True):
            runner.docker.require(
                "run",
                "-d",
                "--name",
                name,
                "--network",
                net,
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                IMAGE,
                "python",
                "-m",
                "http.server",
                "8081",
            )
        peer = runner.docker.require(
            "inspect", names[1], "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"
        )
        code = """import socket, urllib.request
assert urllib.request.urlopen('http://inference-proxy:8080/healthz', timeout=5).status == 200
for host, port in [(PEER, 8081), ('1.1.1.1', 80), ('172.17.0.1', HOST_PORT)]:
    try: socket.create_connection((host, port), timeout=1)
    except OSError: pass
    else: raise RuntimeError('Forbidden connection: ' + host)
print('proxy-only verified')
""".replace("PEER", repr(peer)).replace("HOST_PORT", str(host_port))
        assert "proxy-only verified" in runner.docker.require("exec", names[0], "python", "-c", code)
    finally:
        listener.close()
        for name in names:
            runner.docker.call("rm", "-f", name)
        runner.docker.release_network(first)
        runner.docker.release_network(second)


def test_judge_stop_terminates_application(runner: ApplicationRunner) -> None:
    source = runner.files.root / runner.request.output_dir / "main.py"
    source.write_text("import os,time\nif os.fork() == 0: os.setsid()\nwhile True: time.sleep(1)\n")
    runner.start()
    runner.docker.require("kill", runner.request.name)
    with pytest.raises(DockerError, match="Judge stopped"):
        runner.execute(command())
    assert runner.ledger["app_stopped"] is True


def test_interrupted_command_is_never_replayed(runner: ApplicationRunner) -> None:
    source = runner.files.root / runner.request.output_dir / "main.py"
    source.write_text("print('only once')\n")
    runner.start()
    runner.files.write(runner.channel + "/1.request.json", command())
    runner.ledger["app_command"] = 1
    runner.save()
    replacement = ApplicationRunner(runner.executor, runner.request, dict(runner.ledger))
    replacement.tick()
    outcome = runner.files.read(runner.channel + "/1.response.json")
    assert "cannot be replayed" in str(outcome)
    assert replacement.ledger["app_stopped"] is True
    replacement.tick()
    assert runner.files.read(runner.channel + "/1.response.json") == outcome


def test_application_exit_codes_and_stdin_are_grading_evidence(runner: ApplicationRunner) -> None:
    source = runner.files.root / runner.request.output_dir / "main.py"
    source.write_text("import sys\nprint(sys.stdin.read(), end='')\nsys.exit(127)\n")
    runner.start()
    request = command()
    request["stdin"] = "expected input\n"
    outcome = runner.execute(request)
    assert outcome["exit_code"] == 127 and outcome["stdout"] == "expected input\n"
