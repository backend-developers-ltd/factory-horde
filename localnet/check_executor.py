"""Drive a real factory/judge pair through the installed systemd executor and file protocol."""

import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel
from validator.records import CohortMember, Deadlines, JobRequest, JobStatus, RoundPlan, RoundRecord, request_for
from validator.round_repository import RoundRepository

from .check import Registrations

ROOT = Path(__file__).resolve().parent


class Mount(BaseModel):
    """Only these exact bind mounts are authorized by the fixed workload contract."""

    Type: str
    Source: str
    Destination: str
    RW: bool


class RestartPolicy(BaseModel):
    """No workload container may restart itself."""

    Name: str


class HostConfig(BaseModel):
    """Sandbox properties observed from Docker, independently of status records."""

    ReadonlyRootfs: bool
    NetworkMode: str
    RestartPolicy: RestartPolicy
    Memory: int
    NanoCpus: int
    PidsLimit: int
    CapDrop: tuple[str, ...]


class ContainerConfig(BaseModel):
    """Image command/user selected by the executor."""

    Image: str
    User: str
    Entrypoint: tuple[str, ...]


class Container(BaseModel):
    """Docker inspection used to verify mounts and execution identity."""

    Id: str
    Mounts: tuple[Mount, ...]
    HostConfig: HostConfig
    Config: ContainerConfig


def wait_status(repository: RoundRepository, job: JobRequest, seconds: float = 180) -> JobStatus:
    """Wait for actual terminal evidence; fail explicitly on deadline.

    Raises:
        RuntimeError: The executor does not confirm termination in time.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = repository.status(job)
        if value is not None and value.confirmed_stopped:
            return value
        time.sleep(1)
    raise RuntimeError(f"Executor did not confirm termination for {job.job_id}")


def inspect(job: JobRequest, root: Path, expected: JobStatus) -> Container:
    """Check actual Docker properties and the exact job-only bind mounts.

    Raises:
        RuntimeError: Docker identity, sandbox or bind mounts differ from the contract.
    """
    result = subprocess.run(
        ["docker", "inspect", job.container_name, "--format", "{{json .}}"], capture_output=True, text=True, check=True
    )
    value = Container.model_validate_json(result.stdout)
    mounts = {
        "/input": (str(root / job.input_dir), False),
        "/output" if job.kind == "factory" else "/submission": (str(root / job.output_dir), job.kind == "factory"),
    }
    if job.report_dir is not None:
        mounts["/report"] = (str(root / job.report_dir), True)
    if any(m.Type != "bind" for m in value.Mounts) or {m.Destination: (m.Source, m.RW) for m in value.Mounts} != mounts:
        raise RuntimeError("Container mounts differ from its exact job contract")
    if (
        value.Id != expected.container_id
        or value.Config.Image != job.image
        or value.Config.Entrypoint != (f"/usr/local/bin/factory-horde-{job.kind}",)
        or value.Config.User != "1000:1000"
        or value.HostConfig.RestartPolicy.Name != "no"
        or not value.HostConfig.ReadonlyRootfs
        or value.HostConfig.NetworkMode != "none"
        or value.HostConfig.Memory != 512 * 1024 * 1024
        or value.HostConfig.NanoCpus != 1_000_000_000
        or value.HostConfig.PidsLimit != 128
        or value.HostConfig.CapDrop != ("ALL",)
    ):
        raise RuntimeError("Container identity, command or sandbox differs from localnet configuration")
    return value


def main() -> None:
    """Exercise the installed service without enabling validator orchestration.

    Raises:
        RuntimeError: Isolation, execution, report, restart or pull-failure evidence fails.
    """
    config = dotenv_values(ROOT / ".env")
    if config.get("ENVIRONMENT") != "localnet" or config.get("NETUID") != "2":
        raise RuntimeError("Requires isolated localnet subnet 2")
    subprocess.run(["systemctl", "is-active", "--quiet", "factory-horde-localnet-executor"], check=True)
    registrations = Registrations.model_validate_json((ROOT / "state/registrations.json").read_bytes())
    miner = next(row for row in registrations.registrations if row.identity == "miner1")
    images = dotenv_values(ROOT / "state/published-images.env")
    root = Path(config["FACTORY_HORDE_DATA_ROOT"] or "")
    repository = RoundRepository(root)
    started = datetime.now(UTC)
    deadlines = Deadlines(
        start=started,
        generation_end=started + timedelta(seconds=100),
        evaluation_start=started + timedelta(seconds=105),
        judge_end=started + timedelta(seconds=135),
        round_end=started + timedelta(seconds=140),
    )
    member = CohortMember(miner_hotkey=miner.hotkey, uid=miner.uid, image=images["GHCR_FACTORY_IMAGE"] or "")
    specification = "Produce the fixed FactoryHorde greeting project. No model inference.\n"
    plan = RoundPlan(
        sequence=max((r.plan.sequence for r in repository.rounds()), default=0) + 1,
        deadlines=deadlines,
        discovery_block=registrations.block,
        discovery_block_hash=registrations.block_hash,
        membership_snapshot="separate_reads",
        cohort=(member,),
        judge_image=images["GHCR_JUDGE_IMAGE"] or "",
        stop_grace_seconds=5,
        specification_sha256=hashlib.sha256(specification.encode()).hexdigest(),
    )
    record = repository.prepare_round(plan, specification)
    factory, judge = request_for(plan, member, "factory"), request_for(plan, member, "judge")
    if repository.files.list_names(factory.output_dir):
        raise RuntimeError("New factory output directory is not empty")
    repository.publish_request(plan, factory)
    factory_status = wait_status(repository, factory)
    if (
        not factory_status.application_succeeded
        or factory_status.started_at is None
        or factory_status.finished_at is None
    ):
        raise RuntimeError(f"Factory did not complete successfully: {factory_status}")
    elapsed = (factory_status.finished_at - factory_status.started_at).total_seconds()
    if not 60 <= elapsed < 90:
        raise RuntimeError("Factory did not perform its intended 60-second wait")
    factory_container = inspect(factory, root, factory_status)
    if repository.files.read_bytes(f"{factory.output_dir}/main.py") != b'print("Hello from FactoryHorde!")\n':
        raise RuntimeError("Factory greeting differs")
    print("PASS: systemd factory produced the greeting after its real 60-second wait", flush=True)
    while datetime.now(UTC) < deadlines.evaluation_start:
        time.sleep(1)
    repository.save_round(record.model_copy(update={"stage": "evaluation"}))
    repository.publish_request(plan, judge)
    judge_status = wait_status(repository, judge)
    if not judge_status.application_succeeded:
        raise RuntimeError(f"Judge did not complete successfully: {judge_status}")
    judge_container = inspect(judge, root, judge_status)
    report = repository.report(judge)
    if report.score is None or report.failure is not None:
        raise RuntimeError("Judge report has no usable fixture score")
    while datetime.now(UTC) < deadlines.round_end:
        time.sleep(1)
    repository.save_round(RoundRecord(plan=plan, stage="complete", completed_at=datetime.now(UTC)))
    subprocess.run(["sudo", "systemctl", "restart", "factory-horde-localnet-executor"], check=True)
    time.sleep(3)
    if (
        repository.status(factory) != factory_status
        or repository.status(judge) != judge_status
        or repository.report(judge) != report
    ):
        raise RuntimeError("Restart changed retained execution/report evidence")
    inspect(factory, root, factory_status)
    inspect(judge, root, judge_status)
    evidence = {
        "checked_at": datetime.now(UTC).isoformat(),
        "service": "factory-horde-localnet-executor",
        "executor_sha256": hashlib.sha256((ROOT / "state/executor/executor.py").read_bytes()).hexdigest(),
        "plan": plan.model_dump(mode="json"),
        "factory_status": factory_status.model_dump(mode="json"),
        "judge_status": judge_status.model_dump(mode="json"),
        "factory_elapsed_seconds": elapsed,
        "factory_container": factory_container.model_dump(mode="json"),
        "judge_container": judge_container.model_dump(mode="json"),
        "report": report.model_dump(mode="json"),
        "restart_preserved_status_and_report": True,
        "metrics": json.loads(repository.files.read_bytes("control/executor-metrics.json")),
    }
    (ROOT / "state/task7-executor-pair.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print("PASS: separate judge, exact mounts/resources, valid linked report and stable service restart", flush=True)


if __name__ == "__main__":
    main()
