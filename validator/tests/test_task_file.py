"""Admission freezes operator task files before discovery and survives replacement."""

import json
from pathlib import Path
from uuid import UUID

import pytest

from validator.coordinator import RoundCoordinator, Schedule
from validator.records import request_for
from validator.result_repository import ResultRepository
from validator.round_repository import RoundRepository

from .test_coordinator import BEAT, JUDGE, START, TIMING, Fixture


def test_task_file_freezes_before_interrupted_discovery(tmp_path: Path) -> None:
    source = tmp_path / "task.json"
    source.write_text(json.dumps({"specification": "first", "requirements": ["persist data"]}))
    (tmp_path / "data").mkdir()
    fixture = Fixture(ResultRepository(RoundRepository(tmp_path / "data")))

    def interrupted(_: UUID):
        raise OSError("discovery interrupted")

    coordinator = RoundCoordinator(fixture.repository, TIMING, JUDGE, interrupted, task_file=source)
    with pytest.raises(OSError):
        coordinator.tick(START, BEAT)
    seed = fixture.repository.files.read("control/schedule.json", Schedule).pending
    assert seed is not None and seed.specification == "first"
    source.write_text('{"specification":"second"}')
    resumed = RoundCoordinator(fixture.repository, TIMING, JUDGE, fixture.discovery, task_file=source)
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
    coordinator = RoundCoordinator(fixture.repository, TIMING, JUDGE, fixture.discovery, task_file=source)
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
    coordinator = RoundCoordinator(fixture.repository, TIMING, JUDGE, fixture.discovery, task_file=source)
    coordinator.tick(START, BEAT)
    first = fixture.repository.rounds.rounds()[0].plan
    source.write_text('{"specification":"second"}')
    coordinator.tick(START + TIMING.interval, BEAT)
    second = fixture.repository.rounds.rounds()[-1].plan
    assert second.sequence == 2 and first.specification_sha256 != second.specification_sha256
    assert first.evaluation is not None and first.evaluation.requirements == ("first",)
    assert second.evaluation is not None and second.evaluation.requirements == ("second",)
