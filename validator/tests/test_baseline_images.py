"""Opt-in Docker contract checks for built or published task-5 images (no executor yet)."""

import ast
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from validator.records import InputManifest, JudgeReport

FACTORY_IMAGE = os.environ.get("TEST_FACTORY_IMAGE", "")
JUDGE_IMAGE = os.environ.get("TEST_JUDGE_IMAGE", "")
pytestmark = pytest.mark.skipif(not FACTORY_IMAGE or not JUDGE_IMAGE, reason="Set both TEST_*_IMAGE references")
FIXTURES = Path(__file__).resolve().parents[2] / "spec/fixtures/protocol-v1"


@pytest.fixture
def directories(tmp_path: Path) -> tuple[Path, Path, Path]:
    inputs, output, report = (tmp_path / name for name in ("input", "output", "report"))
    for directory in (inputs, output, report):
        directory.mkdir()
    (inputs / "task.json").write_bytes((FIXTURES / "input-manifest.json").read_bytes())
    (inputs / "specification.md").write_bytes((FIXTURES / "specification.md").read_bytes())
    return inputs, output, report


def run_image(
    image: str,
    command: str,
    mounts: list[tuple[Path, str, bool]],
    args: tuple[str, ...] = (),
    timeout: int = 90,
) -> subprocess.CompletedProcess[str]:
    argv = [
        "docker",
        "run",
        "--rm",
        "--platform",
        "linux/amd64",
        "--network",
        "none",
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
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--entrypoint",
        command,
    ]
    for source, destination, readonly in mounts:
        argv.extend(["--mount", f"type=bind,src={source},dst={destination}" + (",readonly" if readonly else "")])
    return subprocess.run([*argv, image, *args], capture_output=True, text=True, timeout=timeout, check=False)


def judge(directories: tuple[Path, Path, Path]) -> subprocess.CompletedProcess[str]:
    inputs, output, report = directories
    return run_image(
        JUDGE_IMAGE,
        "/usr/local/bin/factory-horde-judge",
        [(inputs, "/input", True), (output, "/submission", True), (report, "/report", False)],
    )


def test_real_factory_and_judge_offline(directories: tuple[Path, Path, Path]) -> None:
    inputs, output, report = directories
    started = time.monotonic()
    result = run_image(
        FACTORY_IMAGE, "/usr/local/bin/factory-horde-factory", [(inputs, "/input", True), (output, "/output", False)]
    )
    elapsed = time.monotonic() - started
    assert result.returncode == 0, result.stderr
    assert 60 <= elapsed < 90
    assert '"version":"0.87.1","inference":false' in result.stdout
    assert {path.name for path in output.iterdir()} == {"main.py", "README.md"}
    code = (output / "main.py").read_text()
    ast.parse(code)
    assert code == 'print("Hello from FactoryHorde!")\n'
    assert "python main.py" in (output / "README.md").read_text()
    outcome = judge(directories)
    assert outcome.returncode == 0, outcome.stderr
    manifest = InputManifest.model_validate_json((inputs / "task.json").read_bytes())
    parsed = JudgeReport.model_validate_json((report / "report.json").read_bytes())
    assert parsed.job_id == manifest.judge_job_id
    assert parsed.factory_job_id == manifest.factory_job_id
    assert parsed.round_id == manifest.round_id
    assert parsed.miner_hotkey == manifest.miner_hotkey
    assert parsed.score is not None and 0 <= parsed.score <= 1 and parsed.failure is None


def test_judge_never_executes_submission_and_preserves_report(directories: tuple[Path, Path, Path]) -> None:
    _, output, report = directories
    (output / "main.py").write_text('raise RuntimeError("must never execute")\n')
    (output / "README.md").write_text("readable fixture")
    assert judge(directories).returncode == 0
    before = (report / "report.json").read_bytes()
    for _ in range(3):
        assert judge(directories).returncode == 0
        assert (report / "report.json").read_bytes() == before


@pytest.mark.parametrize("broken", ["missing", "symlink", "fifo", "large", "unreadable"])
def test_broken_submission_is_linked_failure(directories: tuple[Path, Path, Path], broken: str) -> None:
    inputs, output, report = directories
    if broken == "symlink":
        (output / "main.py").symlink_to("/etc/passwd")
    elif broken == "fifo":
        os.mkfifo(output / "main.py")
    elif broken == "large":
        (output / "main.py").write_bytes(b"x" * (1024 * 1024 + 1))
    elif broken == "unreadable":
        (output / "main.py").write_text("inaccessible")
        (output / "main.py").chmod(0)
    (output / "README.md").write_text("readable")
    outcome = judge(directories)
    assert outcome.returncode == 1, outcome.stderr
    result = JudgeReport.model_validate_json((report / "report.json").read_bytes())
    assert result.score is None and result.failure is not None
    assert result.job_id == InputManifest.model_validate_json((inputs / "task.json").read_bytes()).judge_job_id


def test_judge_mounts_are_readonly_except_report(directories: tuple[Path, Path, Path]) -> None:
    inputs, output, report = directories
    (output / "main.py").write_text("original")
    proof = """
import errno
from pathlib import Path
for name in ('/input/specification.md', '/submission/main.py'):
    try:
        Path(name).write_text('changed')
    except OSError as error:
        if error.errno != errno.EROFS:
            raise
    else:
        raise RuntimeError('mount allowed modification')
Path('/report/write-proof').write_text('writable')
"""
    result = run_image(
        JUDGE_IMAGE,
        "/usr/local/bin/python3",
        [(inputs, "/input", True), (output, "/submission", True), (report, "/report", False)],
        ("-c", proof),
    )
    assert result.returncode == 0, result.stderr
    assert (report / "write-proof").read_text() == "writable"
    assert (output / "main.py").read_text() == "original"


def test_input_and_existing_report_conflicts_fail_without_rewriting(directories: tuple[Path, Path, Path]) -> None:
    inputs, output, report = directories
    (output / "main.py").write_text("readable")
    (output / "README.md").write_text("readable")
    (inputs / "specification.md").write_text("changed")
    assert judge(directories).returncode == 1
    failed = JudgeReport.model_validate_json((report / "report.json").read_bytes())
    assert failed.failure == "Specification digest mismatch"
    (report / "report.json").unlink()
    (inputs / "specification.md").write_bytes((FIXTURES / "specification.md").read_bytes())
    (report / "report.json").write_bytes((FIXTURES / "mismatched-report.json").read_bytes())
    before = hashlib.sha256((report / "report.json").read_bytes()).hexdigest()
    assert judge(directories).returncode == 1
    assert hashlib.sha256((report / "report.json").read_bytes()).hexdigest() == before
    (inputs / "task.json").write_text(json.dumps({"protocol_version": 2}))
    assert judge(directories).returncode == 1
