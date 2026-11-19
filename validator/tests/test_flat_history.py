"""Steady-state coordinator and monitoring work must not grow with completed history."""

from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest
from nexus.v1 import Neuron

from validator.coordinator import RoundCoordinator, Schedule
from validator.monitoring import HealthSampler
from validator.record_files import RecordFiles
from validator.records import RoundPlan, request_for
from validator.result_records import ResultProjection
from validator.result_repository import ResultRepository
from validator.round_repository import RoundRepository

from .test_coordinator import Fixture
from .test_monitoring import executor_health
from .test_results import BEAT, FIXTURES

TARGET = Neuron.model_validate_json((FIXTURES.parent / "discovery-v1/neuron.json").read_bytes())
KINDS: tuple[Literal["factory", "judge"], ...] = ("factory", "judge")


def project(repository: ResultRepository, plan: RoundPlan) -> None:
    """Finalize and project every job, as the file communicator does in production."""
    for member in plan.cohort:
        for kind in KINDS:
            request = request_for(plan, member, kind)
            result = repository.finalize(request, BEAT)
            projection = ResultProjection(
                result_id=result.result_id, result_path=repository.result_path(request), target=TARGET
            )
            repository.files.publish(f"control/projections/{result.result_id}.json", projection)


def history(root: Path, rounds: int) -> tuple[Fixture, RoundCoordinator]:
    """Complete `rounds` fixture rounds for three miners with every result projected."""
    root.mkdir()
    fixture = Fixture(ResultRepository(RoundRepository(root), "fixture"), count=3)
    coordinator = fixture.coordinator()
    repo = fixture.repository
    now: datetime | None = None
    for _ in range(rounds):
        if now is None:
            plan = fixture.start()
        else:
            coordinator.tick(now, BEAT)
            plan = repo.rounds.rounds()[-1].plan
        for member in plan.cohort:
            fixture.status(request_for(plan, member, "factory"))
        coordinator.tick(plan.deadlines.evaluation_start, BEAT)
        for member in plan.cohort:
            fixture.status(request_for(plan, member, "judge"))
        project(repo, plan)
        coordinator.tick(plan.deadlines.evaluation_start + timedelta(seconds=5), BEAT)
        now = plan.deadlines.round_end
    return fixture, coordinator


def operations(monkeypatch: pytest.MonkeyPatch, call: Callable[[], object]) -> Counter[str]:
    """Count record reads, writes and listings performed by one call."""
    counts: Counter[str] = Counter()
    for name in ("read_bytes", "write_bytes", "list_names"):
        original: Callable[..., object] = getattr(RecordFiles, name)

        def wrapper(
            self: RecordFiles,
            *args: object,
            _name: str = name,
            _original: Callable[..., object] = original,
            **kwargs: object,
        ) -> object:
            counts[_name] += 1
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(RecordFiles, name, wrapper)
    call()
    monkeypatch.undo()
    return counts


def steady_state(tmp_path: Path, rounds: int, monkeypatch: pytest.MonkeyPatch) -> tuple[Counter[str], Counter[str]]:
    fixture, coordinator = history(tmp_path / str(rounds), rounds)
    repo = fixture.repository
    now = repo.files.read("control/schedule.json", Schedule).next_start - timedelta(seconds=1)
    sampler = HealthSampler(repo, True, 1e9)
    executor_health(sampler, now)
    sampler.chain_at = sampler.coordinator_at = now
    coordinator.tick(now, BEAT)
    sampler.sample(now)  # The first pass after startup verifies everything once.
    return (
        operations(monkeypatch, lambda: coordinator.tick(now, BEAT)),
        operations(monkeypatch, lambda: sampler.sample(now)),
    )


def test_tick_and_sample_io_is_independent_of_completed_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert steady_state(tmp_path, 12, monkeypatch) == steady_state(tmp_path, 2, monkeypatch)
