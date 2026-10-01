"""Protocol boundaries and Linux publication/recovery checks; no Docker proof is claimed here."""

import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from prometheus_client import REGISTRY
from pydantic import ValidationError

from validator.record_files import RecordConflictError, RecordFiles, RecordFormatError, decode_record, encode_record
from validator.records import (
    AcceptedResult,
    InputManifest,
    JobRequest,
    JobStatus,
    JudgeReport,
    Record,
    RoundPlan,
    RoundRecord,
    StopRequest,
    request_for,
)
from validator.round_repository import RoundRepository

FIXTURES = Path(__file__).resolve().parents[2] / "spec/fixtures/protocol-v1"


@pytest.fixture
def plan() -> RoundPlan:
    return decode_record((FIXTURES / "round.json").read_bytes(), RoundRecord).plan


@pytest.fixture
def repository(tmp_path: Path, plan: RoundPlan) -> RoundRepository:
    repo = RoundRepository(tmp_path)
    repo.prepare_round(plan, (FIXTURES / "specification.md").read_text())
    return repo


@pytest.mark.parametrize(
    ("name", "model"),
    [
        ("round", RoundRecord),
        ("factory-request", JobRequest),
        ("judge-request", JobRequest),
        ("input-manifest", InputManifest),
        ("stop", StopRequest),
        ("status", JobStatus),
        ("report", JudgeReport),
        ("accepted", AcceptedResult),
    ],
)
def test_valid_fixtures_round_trip(name: str, model: type[Record]) -> None:
    parsed = decode_record((FIXTURES / f"{name}.json").read_bytes(), model)
    assert decode_record(encode_record(parsed), model) == parsed


@pytest.mark.parametrize("fixture", sorted(FIXTURES.glob("invalid-request-*.json")), ids=lambda p: p.stem)
def test_invalid_request_fixtures(fixture: Path) -> None:
    with pytest.raises(ValidationError):
        decode_record(fixture.read_bytes(), JobRequest)


def test_round_identity_is_not_a_timestamp(plan: RoundPlan) -> None:
    fields = plan.model_dump(exclude={"round_id"})
    rounds = [RoundPlan.model_validate(fields) for _ in range(64)]
    assert len({r.round_id for r in rounds}) == 64
    assert len({r.directory for r in rounds}) == 64
    assert all(r.deadlines.start == plan.deadlines.start for r in rounds)
    assert plan.deadlines.generation_end - plan.deadlines.start == timedelta(minutes=60)
    assert plan.deadlines.evaluation_start - plan.deadlines.generation_end == timedelta(minutes=5)
    assert plan.deadlines.round_end - plan.deadlines.evaluation_start == timedelta(minutes=55)
    assert plan.deadlines.round_end - plan.deadlines.judge_end == timedelta(minutes=5)


def test_inputs_precede_publication_and_survive_restart(repository: RoundRepository, plan: RoundPlan) -> None:
    request = request_for(plan, plan.cohort[0], "factory")
    repository.publish_request(plan, request)
    repository.publish_request(plan, request)
    assert repository.requests() == (request,)
    assert repository.files.read(f"{request.input_dir}/task.json", InputManifest).factory_job_id == request.job_id
    output = repository.files.root / request.output_dir / "main.py"
    output.write_text("persisted output")
    resumed = RoundRepository(repository.files.root)
    assert resumed.rounds() == (RoundRecord(plan=plan),)
    resumed.prepare_round(plan, (FIXTURES / "specification.md").read_text())
    assert output.read_text() == "persisted output"
    assert resumed.status(request) is None
    assert resumed.requests()[0].projection_id == request.projection_id


def test_changed_or_incomplete_inputs_never_publish(repository: RoundRepository, plan: RoundPlan) -> None:
    request = request_for(plan, plan.cohort[0], "factory")
    specification = repository.files.root / request.input_dir / "specification.md"
    specification.write_text("incomplete")
    with pytest.raises(RecordConflictError, match="specification"):
        repository.publish_request(plan, request)
    specification.unlink()
    with pytest.raises(FileNotFoundError):
        repository.publish_request(plan, request)
    assert repository.requests() == ()


def test_round_and_job_conflicts(repository: RoundRepository, plan: RoundPlan) -> None:
    spec = (FIXTURES / "specification.md").read_text()
    request = request_for(plan, plan.cohort[0], "factory")
    repository.publish_request(plan, request)
    altered = request.model_copy(update={"image": plan.judge_image})
    with pytest.raises(RecordConflictError):
        repository.publish_request(plan, altered)
    with pytest.raises(RecordConflictError):
        repository.prepare_round(plan.model_copy(update={"sequence": 2}), spec)
    with pytest.raises(RecordConflictError):
        repository.save_round(RoundRecord(plan=plan.model_copy(update={"judge_image": request.image})))


@pytest.mark.parametrize("path", ["../escape", "/tmp/escape", "a/../../escape", "a//b", "a/./b", "a\\b", "a/", ""])
def test_paths_reject_escape(tmp_path: Path, path: str) -> None:
    files = RecordFiles(tmp_path)
    with pytest.raises(ValidationError):
        files.write_bytes(path, b"{}", immutable=True)
    with pytest.raises(ValidationError):
        files.read_bytes(path)


def test_symlinks_cannot_escape_reads_or_writes(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    victim = outside / "data.json"
    victim.write_bytes(b"original")
    files = RecordFiles(root)
    (root / "linked").symlink_to(outside, target_is_directory=True)
    (root / "file.json").symlink_to(victim)
    for path in ("linked/data.json", "file.json"):
        with pytest.raises(OSError):
            files.read_bytes(path)
        with pytest.raises((OSError, RecordFormatError)):
            files.write_bytes(path, b"changed", immutable=False)
        with pytest.raises(OSError):
            files.write_bytes(path, b"changed", immutable=True)
    with pytest.raises(OSError):
        RecordFiles(root / "linked")
    assert victim.read_bytes() == b"original"


def test_nonregular_files_fail_without_blocking(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "fifo")
    with pytest.raises(RecordFormatError, match="regular"):
        RecordFiles(tmp_path).read_bytes("fifo")


def test_temporary_and_partial_records(repository: RoundRepository, plan: RoundPlan) -> None:
    request = request_for(plan, plan.cohort[0], "factory")
    directory = repository.files.root / "control/requests"
    (directory / f".{request.job_id}.json.staging.tmp").write_text('{"protocol_version":')
    assert repository.requests() == ()
    committed = directory / f"{request.job_id}.json"
    committed.write_text('{"protocol_version":')
    with pytest.raises(json.JSONDecodeError):
        repository.requests()
    committed.write_bytes(
        encode_record(
            request.model_copy(
                update={"job_id": uuid4(), "kind": "judge", "contract": "judge-v1", "report_dir": "reports/judge"}
            )
        )
    )
    with pytest.raises(RecordFormatError, match="filename"):
        repository.requests()


def test_concurrent_immutable_publication_across_repository_instances(tmp_path: Path, plan: RoundPlan) -> None:
    def publish(index: int) -> bool:
        try:
            RecordFiles(tmp_path).publish("round.json", RoundRecord(plan=plan.model_copy(update={"sequence": index})))
        except RecordConflictError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(publish, range(16))) == 1
    winner = RecordFiles(tmp_path).read("round.json", RoundRecord)
    RecordFiles(tmp_path).publish("round.json", winner)
    assert list(tmp_path.iterdir()) == [tmp_path / "round.json"]


def test_atomic_replacement_never_exposes_partial_json(tmp_path: Path, plan: RoundPlan) -> None:
    writer = RecordFiles(tmp_path)
    reader = RecordFiles(tmp_path)
    writer.publish("round.json", RoundRecord(plan=plan))

    def write() -> None:
        for _ in range(40):
            writer.replace("round.json", RoundRecord(plan=plan))

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(write)
        while not pending.done():
            assert reader.read("round.json", RoundRecord).plan == plan
        pending.result()


def test_failed_fsync_never_reports_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plan: RoundPlan) -> None:
    files = RecordFiles(tmp_path)
    real_fsync = os.fsync

    def fail_file(fd: int) -> None:
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("injected file fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_file)
    with pytest.raises(OSError, match="injected"):
        files.publish("round.json", RoundRecord(plan=plan))
    assert not (tmp_path / "round.json").exists()

    def fail_after_publish(fd: int) -> None:
        if (tmp_path / "round.json").exists() and stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("injected directory fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_after_publish)
    with pytest.raises(OSError, match="injected"):
        files.publish("round.json", RoundRecord(plan=plan))
    assert files.read("round.json", RoundRecord).plan == plan
    monkeypatch.setattr(os, "fsync", real_fsync)
    files.publish("round.json", RoundRecord(plan=plan))
    assert REGISTRY.get_sample_value(
        "factory_horde_record_operations_total", {"operation": "publish", "outcome": "error"}
    )
    assert REGISTRY.get_sample_value("factory_horde_record_operation_seconds_count", {"operation": "publish"})


def test_status_identity_and_stop_intent(repository: RoundRepository, plan: RoundPlan) -> None:
    request = request_for(plan, plan.cohort[0], "judge")
    repository.publish_request(plan, request)
    stop = decode_record((FIXTURES / "stop.json").read_bytes(), StopRequest)
    repository.publish_stop(request, stop)
    repository.publish_stop(request, stop)
    with pytest.raises(RecordConflictError):
        repository.publish_stop(request, stop.model_copy(update={"reason": "operator"}))
    status = decode_record((FIXTURES / "status.json").read_bytes(), JobStatus)
    repository.files.replace(f"control/statuses/{request.job_id}.json", status)
    assert repository.status(request) == status
    assert status.confirmed_stopped and status.application_succeeded
    wrong = status.model_copy(update={"round_id": uuid4()})
    repository.files.replace(f"control/statuses/{request.job_id}.json", wrong)
    with pytest.raises(RecordFormatError, match="attribution"):
        repository.status(request)


@pytest.mark.parametrize(
    ("changes", "stopped", "succeeded"),
    [
        (
            {
                "state": "failed",
                "execution": "unresolved",
                "startup_forbidden": False,
                "exit_code": None,
                "finished_at": None,
                "reason": "Docker unavailable",
            },
            False,
            False,
        ),
        ({"state": "failed", "exit_code": 7, "reason": "nonzero exit"}, True, False),
        ({"state": "failed", "exit_code": 137, "forced": True, "reason": "forced stop"}, True, False),
        (
            {
                "state": "cancelled",
                "execution": "never_started",
                "started_at": None,
                "finished_at": None,
                "exit_code": None,
                "reason": "cancelled before start",
            },
            True,
            False,
        ),
    ],
)
def test_failure_safety_is_separate_from_success(changes: dict[str, object], stopped: bool, succeeded: bool) -> None:
    original = decode_record((FIXTURES / "status.json").read_bytes(), JobStatus)
    status = JobStatus.model_validate(original.model_dump() | changes)
    assert status.confirmed_stopped is stopped
    assert status.application_succeeded is succeeded


def test_terminal_label_without_evidence_rejected() -> None:
    status = decode_record((FIXTURES / "status.json").read_bytes(), JobStatus)
    with pytest.raises(ValidationError):
        JobStatus.model_validate(status.model_dump() | {"execution": "unresolved"})
    with pytest.raises(ValidationError):
        JobStatus.model_validate(status.model_dump() | {"startup_forbidden": False})


def test_reports_and_results_keep_original_identity(repository: RoundRepository, plan: RoundPlan) -> None:
    request = request_for(plan, plan.cohort[0], "judge")
    report_path = f"{request.report_dir}/report.json"
    repository.files.write_bytes(report_path, (FIXTURES / "mismatched-report.json").read_bytes(), immutable=True)
    with pytest.raises(RecordFormatError):
        repository.report(request)
    accepted = decode_record((FIXTURES / "accepted.json").read_bytes(), AcceptedResult)
    assert accepted.score == 0
    assert accepted.result_id == request.projection_id
    with pytest.raises(ValidationError):
        AcceptedResult.model_validate(accepted.model_dump() | {"result_id": uuid4()})


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -float("inf"), -0.1, 1.1])
def test_invalid_score(score: float) -> None:
    report = decode_record((FIXTURES / "report.json").read_bytes(), JudgeReport)
    with pytest.raises(ValidationError):
        JudgeReport.model_validate(report.model_dump() | {"score": score})


def test_strict_version_and_ambiguous_json() -> None:
    request = (FIXTURES / "factory-request.json").read_bytes()
    with pytest.raises(RecordFormatError, match="duplicate"):
        decode_record(
            request.replace(b'"protocol_version": 1', b'"protocol_version": 1, "protocol_version": 1'), JobRequest
        )
    with pytest.raises(ValidationError):
        decode_record(request.replace(b'"protocol_version": 1', b'"protocol_version": true'), JobRequest)
    report = (FIXTURES / "report.json").read_bytes().replace(b'"score": 0.0', b'"score": NaN')
    with pytest.raises(RecordFormatError, match="non-finite"):
        decode_record(report, JudgeReport)
