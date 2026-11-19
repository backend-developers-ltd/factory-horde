"""Admission freezes operator task files before discovery and survives replacement."""

import json
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from nexus.v1 import Hotkey

from validator.coordinator import RoundCoordinator, Schedule
from validator.records import CriterionVerdict, JobIdentity, ReviewEvidence, request_for
from validator.result_repository import ResultRepository
from validator.result_store import FileTaskResultStore
from validator.round_repository import RoundRepository
from validator.weighing import Membership, Weigher

from .test_coordinator import BEAT, JUDGE, START, TIMING, Fixture


def test_task_file_freezes_before_interrupted_discovery(tmp_path: Path) -> None:
    source = tmp_path / "task.json"
    source.write_text(json.dumps({"specification": "first", "requirements": ["persist data"]}))
    (tmp_path / "data").mkdir()
    fixture = Fixture(ResultRepository(RoundRepository(tmp_path / "data")))

    def interrupted(_: UUID):
        raise OSError("discovery interrupted")

    coordinator = RoundCoordinator(fixture.repository, TIMING, JUDGE, interrupted, task_directory=source.parent)
    with pytest.raises(OSError):
        coordinator.tick(START, BEAT)
    seed = fixture.repository.files.read("control/schedule.json", Schedule).pending
    assert seed is not None and seed.specification == "first"
    source.write_text('{"specification":"second"}')
    resumed = RoundCoordinator(fixture.repository, TIMING, JUDGE, fixture.discovery, task_directory=source.parent)
    resumed.tick(START, BEAT)
    plan = fixture.repository.rounds.rounds()[0].plan
    assert plan.round_id == seed.round_id and plan.evaluation == seed.evaluation
    assert (
        fixture.repository.files.read_bytes(
            f"{request_for(plan, plan.cohort[0], 'factory').input_dir}/specification.md"
        )
        == b"first"
    )


@pytest.mark.parametrize("content", ["{", '{"specification":" "}', '{"specification":"ok","requirements":[""]}'])
def test_invalid_file_blocks_new_admission(tmp_path: Path, content: str) -> None:
    source = tmp_path / "task.json"
    source.write_text(content)
    (tmp_path / "data").mkdir()
    fixture = Fixture(ResultRepository(RoundRepository(tmp_path / "data")))
    coordinator = RoundCoordinator(fixture.repository, TIMING, JUDGE, fixture.discovery, task_directory=source.parent)
    with pytest.raises(ValueError):
        coordinator.tick(START, BEAT)
    assert not fixture.repository.rounds.rounds() and fixture.discoveries == 0
    source.write_text('{"specification":"repaired","requirements":[]}')
    coordinator.tick(START, BEAT)
    plan = fixture.repository.rounds.rounds()[0].plan
    assert plan.evaluation is not None and plan.evaluation.requirements == ("repaired",)


def test_task_replacement_changes_only_next_round(tmp_path: Path) -> None:
    source = tmp_path / "task.json"
    source.write_text('{"specification":"first"}')
    fixture = Fixture(ResultRepository(RoundRepository(tmp_path)), count=0)
    coordinator = RoundCoordinator(fixture.repository, TIMING, JUDGE, fixture.discovery, task_directory=source.parent)
    coordinator.tick(START, BEAT)
    first = fixture.repository.rounds.rounds()[0].plan
    source.write_text('{"specification":"second"}')
    coordinator.tick(START + TIMING.interval, BEAT)
    second = fixture.repository.rounds.rounds()[-1].plan
    assert second.sequence == 2 and first.specification_sha256 != second.specification_sha256
    assert first.evaluation is not None and first.evaluation.requirements == ("first",)
    assert second.evaluation is not None and second.evaluation.requirements == ("second",)


@pytest.mark.parametrize("failure", [None, "factory", "judge"])
def test_every_task_runs_for_every_miner_and_scores_are_averaged(tmp_path: Path, failure: str | None) -> None:
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tmp_path / "data").mkdir()
    for name in ("alpha", "beta"):
        (tasks / f"{name}.json").write_text(json.dumps({"specification": name}))
    (tasks / "ignored.md").write_text("not a task")
    (tasks / ".pending.json").write_text("incomplete")
    fixture = Fixture(ResultRepository(RoundRepository(tmp_path / "data"), "review", JUDGE))
    coordinator = RoundCoordinator(fixture.repository, TIMING, JUDGE, fixture.discovery, task_directory=tasks)
    assert len(coordinator.tick(START, BEAT).jobs) == 4
    plan = fixture.repository.rounds.rounds()[0].plan
    assert [task.filename for task in plan.tasks] == ["alpha.json", "beta.json"]
    assert len({m.factory_job_id for m in plan.cohort}) == 4
    for member in plan.cohort:
        factory = request_for(plan, member, "factory")
        task = plan.task_for(member)
        assert task is not None
        assert (
            fixture.repository.files.read_bytes(f"{factory.input_dir}/specification.md") == task.specification.encode()
        )
        failed = failure == "factory" and member == plan.cohort[0]
        fixture.status(factory, code=1 if failed else 0)
    assert not coordinator.tick(START + timedelta(seconds=10), BEAT).errors
    assert not coordinator.tick(plan.deadlines.evaluation_start, BEAT).errors
    for member in plan.cohort:
        judge = request_for(plan, member, "judge")
        if failure == "factory" and member == plan.cohort[0]:
            continue
        fixture.status(judge, code=75 if failure == "judge" and member == plan.cohort[0] else 0)
        report_path = tmp_path / "data" / str(judge.report_dir) / "report.json"
        report = json.loads(report_path.read_text())
        report["score"] = 0.0 if member.task_id == plan.tasks[0].task_id else 1.0
        report_path.write_text(json.dumps(report))
        task = plan.task_for(member)
        assert task is not None
        fixture.repository.files.publish(
            f"{judge.report_dir}/review.json",
            ReviewEvidence(
                **judge.model_dump(include=set(JobIdentity.model_fields)),
                model="test",
                score=report["score"],
                requirements=(
                    CriterionVerdict(
                        id=1,
                        requirement=task.specification,
                        verdict="met" if report["score"] else "unmet",
                        reason="observed",
                    ),
                ),
            ),
        )
    assert not coordinator.tick(plan.deadlines.evaluation_start + timedelta(seconds=5), BEAT).errors
    usable = fixture.repository.latest_usable_round()
    # A factory or judge failure on one task excludes only that miner from the round.
    assert usable is not None and len(usable[1]) == (2 if failure else 4)
    membership = Membership(1000, {Hotkey(m.miner_hotkey): m.uid for m in plan.cohort})
    weigher = Weigher(FileTaskResultStore(fixture.repository), lambda: membership)
    assert weigher.select()[2] == {
        hotkey: 0.5 for hotkey in membership.uids if not failure or hotkey != plan.cohort[0].miner_hotkey
    }


def test_task_set_and_execution_ids_survive_interrupted_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tmp_path / "data").mkdir()
    for name in ("alpha", "beta"):
        (tasks / f"{name}.json").write_text(json.dumps({"specification": name}))
    fixture = Fixture(ResultRepository(RoundRepository(tmp_path / "data")))
    prepare = fixture.repository.rounds.prepare_round

    def interrupted(*args: object, **kwargs: object):
        raise OSError("publication interrupted")

    monkeypatch.setattr(fixture.repository.rounds, "prepare_round", interrupted)
    coordinator = RoundCoordinator(fixture.repository, TIMING, JUDGE, fixture.discovery, task_directory=tasks)
    with pytest.raises(OSError):
        coordinator.tick(START, BEAT)
    seed = fixture.repository.files.read("control/schedule.json", Schedule).pending
    assert seed is not None and seed.plan is not None
    (tasks / "alpha.json").unlink()
    (tasks / "beta.json").write_text("broken update")
    (tasks / "new.json").write_text('{"specification":"new"}')
    monkeypatch.setattr(fixture.repository.rounds, "prepare_round", prepare)
    resumed = RoundCoordinator(fixture.repository, TIMING, JUDGE, fixture.discovery, task_directory=tasks)
    resumed.tick(START, BEAT)
    assert fixture.repository.rounds.rounds()[0].plan == seed.plan


def test_empty_directory_never_falls_back_to_environment_text(tmp_path: Path) -> None:
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tmp_path / "data").mkdir()
    fixture = Fixture(ResultRepository(RoundRepository(tmp_path / "data")))
    coordinator = RoundCoordinator(fixture.repository, TIMING, JUDGE, fixture.discovery, task_directory=tasks)
    with pytest.raises(ValueError, match="no JSON tasks"):
        coordinator.tick(START, BEAT)
    assert fixture.discoveries == 0
