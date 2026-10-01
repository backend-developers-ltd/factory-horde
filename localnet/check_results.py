"""Accept retained real factory/judge evidence and rebuild Nexus projections without new execution."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import dotenv_values
from nexus.v1 import (
    BlockBeat,
    BlockHash,
    BlockNumber,
    Epoch,
    Hotkey,
    ProcessedInput,
    Routed,
    SubnetBuilder,
    SuccessfulTaskResultToPersist,
    TaskResultId,
    Timestamp,
    Timestamped,
)
from pylon_client.artanis import Config, IdentityName, PylonAuthToken, PylonClient
from validator.records import JobStatus, RoundPlan, request_for
from validator.result_repository import ResultRepository
from validator.result_store import EVALUATION_TASK, FACTORY_TASK, FileTaskResultStore
from validator.round_repository import RoundRepository

ROOT = Path(__file__).resolve().parent


def main() -> None:
    """Use task 7's retained real pair; no Docker start or request publication occurs.

    Raises:
        RuntimeError: Evidence, original score or stable query metadata changes.
    """
    config = dotenv_values(ROOT / ".env")
    if config.get("ENVIRONMENT") != "localnet" or config.get("NETUID") != "2":
        raise RuntimeError("Requires isolated localnet subnet 2")
    evidence = json.loads((ROOT / "state/task7-executor-pair.json").read_bytes())
    plan = RoundPlan.model_validate_json(json.dumps(evidence["plan"]))
    rounds = RoundRepository(Path(config["FACTORY_HORDE_DATA_ROOT"] or ""))
    repository = ResultRepository(rounds)
    (member,) = plan.cohort
    factory, judge = (request_for(plan, member, kind) for kind in ("factory", "judge"))
    expected = {
        factory.job_id: JobStatus.model_validate_json(json.dumps(evidence["factory_status"])),
        judge.job_id: JobStatus.model_validate_json(json.dumps(evidence["judge_status"])),
    }
    before_requests = rounds.requests()
    before_report = rounds.files.read_bytes(f"{judge.report_dir}/report.json")
    with PylonClient(
        Config(
            address="http://127.0.0.1:8000",
            identity_name=IdentityName("validator"),
            identity_token=PylonAuthToken(config["VALIDATOR_PYLON_IDENTITY_TOKEN"] or ""),
            open_access_token=PylonAuthToken(config["VALIDATOR_PYLON_OPEN_ACCESS_TOKEN"] or ""),
        )
    ) as client:
        latest = client.v1.open_access.get_latest_block_info()
        target = client.v1.identity.get_neurons(BlockNumber(latest.number)).neurons[Hotkey(member.miner_hotkey)]
    beat = BlockBeat(BlockNumber(latest.number), Timestamp(latest.timestamp), BlockHash(latest.hash))
    store = FileTaskResultStore(repository)
    builder = SubnetBuilder(nodes=[])
    for job, name in ((factory, FACTORY_TASK), (judge, EVALUATION_TASK)):
        status = rounds.status(job)
        if status != expected[job.job_id] or status is None:
            raise RuntimeError("Retained Docker evidence differs from verified task-7 execution")
        payload = SuccessfulTaskResultToPersist(
            result=Timestamped(
                executor_output=ProcessedInput(input=Routed(job, target), output=status),
                processing_started=datetime.now(UTC),
                processing_finished=datetime.now(UTC),
                block_at_finish=beat,
            ),
            executor_public_output=status,
        )
        with builder.context_store.create_context() as ctx:
            original = store.add_successful_task_result(ctx, name, payload)
        delayed = SuccessfulTaskResultToPersist(
            result=Timestamped(
                executor_output=ProcessedInput(input=Routed(job, target), output=status),
                processing_started=datetime.now(UTC) + timedelta(days=1),
                processing_finished=datetime.now(UTC) + timedelta(days=1),
                block_at_finish=beat,
            ),
            executor_public_output=status,
        )
        with builder.context_store.create_context() as ctx:
            replay = store.add_successful_task_result(ctx, name, delayed)
        recovered = FileTaskResultStore(ResultRepository(RoundRepository(rounds.files.root)))
        if (
            original != replay
            or recovered.get_task_result(name, TaskResultId(job.projection_id)) != original
            or recovered.get_successful_tasks_for_epoch(
                name, Epoch(original.block_at_finish.block_number, original.block_at_finish.block_number)
            )
            != (original,)
        ):
            raise RuntimeError("Replay or reconstructed Nexus query changed original result")
    result = repository.read(judge)
    if result is None or result.accepted is None or result.accepted.score != evidence["report"]["score"]:
        raise RuntimeError("Accepted score differs from the actual original random judge report")
    if (
        rounds.requests() != before_requests
        or rounds.files.read_bytes(f"{judge.report_dir}/report.json") != before_report
    ):
        raise RuntimeError("Acceptance changed original execution requests or report")
    usable = repository.latest_usable_round()
    if usable is None or usable[0] != plan or usable[1] != (result.accepted,):
        raise RuntimeError("Completed usable-round query did not recover the accepted score")
    output = {
        "checked_at": datetime.now(UTC).isoformat(),
        "source_evidence": "task7-executor.json",
        "factory_result": repository.finalize(factory, beat).model_dump(mode="json"),
        "judge_result": result.model_dump(mode="json"),
        "request_and_report_bytes_preserved": True,
        "new_context_replay_identical": True,
        "reconstructed_nexus_queries_identical": True,
    }
    (ROOT / "state/task9-results.json").write_text(json.dumps(output, indent=2) + "\n")
    print(
        "PASS: original real score accepted once; Nexus replay/rebuild retained IDs, times, blocks and evidence"
    )


if __name__ == "__main__":
    main()
