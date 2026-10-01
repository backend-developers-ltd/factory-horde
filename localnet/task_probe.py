"""Container-only Nexus fixture: scheduled inputs for one fixed plan, without a round coordinator."""

import json
import sys
import time
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import cast, override

from nexus.v1 import (
    ActorBuilder,
    BlockNumber,
    ContextStore,
    Epoch,
    PipeToBus,
    Producer,
    ProducerActor,
    TimestamperActor,
)
from validator.file_communicator import FileCommunicatorActor
from validator.logging_config import LoggingSettings, configure_logging
from validator.main import Settings, Validator
from validator.records import JobKind, JobRequest, Record, RoundPlan, request_for
from validator.result_repository import ResultRepository
from validator.result_store import EVALUATION_TASK, FACTORY_TASK


class ProbePlan(Record):
    """Explicit immutable fixture inputs; this source does not choose or schedule new rounds."""

    plan: RoundPlan
    historical_failed: JobRequest


class FixtureJobs(Producer[JobRequest], ActorBuilder):
    """One bounded set of fixture jobs, each emitted on its own context by Nexus."""

    def __init__(self, kind: JobKind, fixture: ProbePlan, repository: ResultRepository):
        super().__init__(f"fixture-{kind}-requests")
        self.kind: JobKind = kind
        self.fixture, self.repository = fixture, repository

    @override
    def build_actor(self, *, pipe_to_bus: PipeToBus, context_store: ContextStore) -> FixtureJobsActor:
        return FixtureJobsActor(self, pipe_to_bus, context_store)


class FixtureJobsActor(ProducerActor[JobRequest]):
    """An interruptible test producer exercises real task inputs; it never calls Docker."""

    def __init__(self, spec: FixtureJobs, pipe_to_bus: PipeToBus, context_store: ContextStore):
        super().__init__(spec, pipe_to_bus, context_store)
        self.fixture = spec
        self.stopped = Event()

    @override
    def on_stop(self) -> None:
        self.stopped.set()

    @override
    def _produce(self) -> Generator[JobRequest]:
        plan = self.fixture.fixture.plan
        start = plan.deadlines.start if self.fixture.kind == "factory" else plan.deadlines.evaluation_start
        while not self.stopped.is_set() and datetime.now(UTC) < start:
            self.stopped.wait(0.25)
        if self.stopped.is_set():
            return
        if self.fixture.kind == "factory":
            yield self.fixture.fixture.historical_failed
        for member in plan.cohort:
            if self.stopped.is_set():
                return
            if self.fixture.kind == "judge":
                factory = self.fixture.repository.read(request_for(plan, member, "factory"))
                if factory is None or factory.failure is not None:
                    raise RuntimeError("Fixture refuses to judge an unsuccessful/unconfirmed factory")
            yield request_for(plan, member, self.fixture.kind)


def main() -> None:
    """Drive the exact application tasks inside the application image and save runtime evidence.

    Raises:
        RuntimeError: Tasks do not complete, or automatic Nexus clock discovery fails.
    """
    configure_logging(LoggingSettings())
    fixture = ProbePlan.model_validate_json(Path(sys.argv[1]).read_bytes())
    validator = Validator(Settings.model_validate({}))
    factories = FixtureJobs("factory", fixture, validator.results)
    judges = FixtureJobs("judge", fixture, validator.results)
    validator.connect(factories.source, validator.tasks.factory.input)
    validator.connect(judges.source, validator.tasks.evaluation.input)
    expected = {fixture.historical_failed.projection_id}
    for member in fixture.plan.cohort:
        expected.update(request_for(fixture.plan, member, kind).projection_id for kind in ("factory", "judge"))
    with validator.start_runtime() as runtime:
        deadline = time.monotonic() + 220
        store = validator.tasks.store_provider.get_task_result_store()
        epoch = Epoch(BlockNumber(0), BlockNumber(2**63 - 1))
        while time.monotonic() < deadline:
            entries = [
                entry
                for task in (FACTORY_TASK, EVALUATION_TASK)
                for entry in (
                    *store.get_successful_tasks_for_epoch(task, epoch),
                    *store.get_executor_failures_for_epoch(task, epoch),
                )
            ]
            if expected <= {entry.id for entry in entries}:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Nexus task outcomes did not all persist before the fixture timeout")
        clocks = [
            cast(TimestamperActor[object, object], actor).latest_block_beat
            for actor in runtime.actors
            if isinstance(actor, TimestamperActor)
        ]
        if len(clocks) != 2 or any(clock is None for clock in clocks):
            raise RuntimeError("Nexus did not automatically install both task block clocks")
        transports = [actor for actor in runtime.actors if isinstance(actor, FileCommunicatorActor)]
        result = {
            "protocol_version": 1,
            "round_id": str(fixture.plan.round_id),
            "runtime_actor_ids": [str(actor.actor_id) for actor in runtime.actors],
            "task_result_ids": sorted(str(entry.id) for entry in entries if entry.id in expected),
            "block_clocks_ready": len(clocks),
            "pending_contexts": [len(actor.pending) for actor in transports],
        }
        validator.results.files.write_bytes(
            f"control/task10-probe-{fixture.plan.round_id}.json", json.dumps(result, indent=2).encode(), immutable=False
        )
    print("PASS: real Nexus factory/evaluation tasks persisted all outcomes and discovered both clocks", flush=True)


if __name__ == "__main__":
    main()
