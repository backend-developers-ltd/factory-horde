"""Run published fault profiles through the production coordinator, real chain and systemd executor."""

import argparse
import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from dotenv import dotenv_values
from nexus.v1 import Hotkey
from pydantic import BaseModel
from pylon_client.artanis import (
    CommitmentDataBytes,
    Config,
    IdentityName,
    PylonAuthToken,
    PylonClient,
)
from tenacity import Retrying, stop_after_attempt
from validator.discovery import decode_reference
from validator.records import RoundPlan, RoundRecord, request_for
from validator.result_repository import ResultRepository
from validator.result_store import FileTaskResultStore
from validator.weighing import Weigher, read_membership, softmax

from .check_executor import inspect
from .check_executor_recovery import Check, docker, until

ROOT = Path(__file__).resolve().parent


class PublishedProfile(BaseModel):
    """Public immutable image identity emitted by the fixture build workflow."""

    kind: str
    image: str
    commit: str
    platform: str
    run: str


class Adversarial(Check):
    """One isolated root and real application runtime; never manufacture executor statuses."""

    def __init__(self, metadata: Path):
        self.config = dotenv_values(ROOT / ".env")
        if (self.config.get("VALIDATOR_DISPATCH_ENABLED") or "false").lower() != "false":
            raise RuntimeError("Pause ordinary dispatch before changing localnet commitments")
        self.profiles = {
            profile.kind: profile
            for path in sorted(metadata.glob("fixture-*/*-image.json"))
            for profile in (PublishedProfile.model_validate_json(path.read_bytes()),)
        }
        if len(self.profiles) != 16 or len({p.commit for p in self.profiles.values()}) != 1:
            raise RuntimeError("Requires all sixteen profiles from one published fixture revision")
        with TemporaryDirectory(prefix="factory-horde-anonymous-") as anonymous:
            for profile in self.profiles.values():
                subprocess.run(
                    ["docker", "--config", anonymous, "pull", "--platform", "linux/amd64", profile.image],
                    check=True,
                    capture_output=True,
                )
        self.name = f"factory-horde-adversarial-{uuid4()}"
        super().__init__(
            self.profiles["lifecycle"].image,
            service="factory-horde-adversarial-executor",
            base=ROOT / "state" / self.name,
        )
        self.results = ResultRepository(self.repository)
        self.miners = [row for row in self.registrations.registrations if row.identity.startswith("miner")]
        self.own = next(row.hotkey for row in self.registrations.registrations if row.identity == "validator")
        self.original: dict[str, bytes] = {}
        self.evidence.update(
            {
                "profiles": {name: value.model_dump() for name, value in self.profiles.items()},
                "anonymous_pulls": len(self.profiles),
                "data_root": str(self.root),
                "validator_image": (ROOT / "state/validator-image.id").read_text().strip(),
            }
        )

    def client_config(self, identity: str) -> Config:
        """Use only the explicitly configured local Pylon identity, with no write retries."""
        key = "VALIDATOR_PYLON_IDENTITY_TOKEN" if identity == "validator" else f"{identity.upper()}_PYLON_TOKEN"
        return Config(
            address="http://127.0.0.1:8000",
            identity_name=IdentityName(identity),
            identity_token=PylonAuthToken(self.config[key] or ""),
            retry=Retrying(stop=stop_after_attempt(1), reraise=True),
        )

    def publish_bytes(self, identity: str, content: bytes) -> None:
        """Publish malformed or valid localnet bytes and require exact public v1 readback.

        Raises:
            RuntimeError: Identity attribution differs from the isolated subnet.
        """
        with PylonClient(self.client_config(identity)) as client:
            client.v1.identity.get_latest_neurons()
            if client.v1.identity.netuid != 2:
                raise RuntimeError("Refusing to publish outside localnet subnet 2")
            current = client.v1.identity.get_own_commitment()
            previous = bytes(CommitmentDataBytes.fromhex(current.commitment))
            self.original.setdefault(identity, previous)
            if previous == content:
                return
            client.v1.identity.set_commitment(CommitmentDataBytes(content))
            until(
                lambda: (
                    bytes(CommitmentDataBytes.fromhex(client.v1.identity.get_own_commitment().commitment)) == content
                ),
                "Commitment readback differs",
                90,
            )

    def commitments(self, profiles: list[str]) -> None:
        """Set the five actual miner commitments before a production discovery snapshot."""
        for miner, profile in zip(self.miners, profiles, strict=True):
            self.publish_bytes(miner.identity, self.profiles[profile].image.encode())

    def start_validator(self, judge: str, *, disconnected: bool = False) -> None:
        """Run the common Compose service with its production entrypoint on this separate root."""
        command = [
            str(ROOT / "compose.sh"),
            "run",
            "--detach",
            "--no-deps",
            "--name",
            self.name,
            "--volume",
            f"{self.root}:/var/lib/factory-horde",
        ]
        settings = {
            "VALIDATOR_DISPATCH_ENABLED": "true",
            "VALIDATOR_WEIGHTS_ENABLED": "false",
            "VALIDATOR_HOTKEY": self.own,
            "VALIDATOR_JUDGE_IMAGE": self.profiles[judge].image,
            "VALIDATOR_GENERATION_WINDOW": "20",
            "VALIDATOR_CONFIRMATION_WINDOW": "5",
            "VALIDATOR_EVALUATION_WINDOW": "30",
            "VALIDATOR_JUDGE_STOP_RESERVE": "5",
            "VALIDATOR_STOP_GRACE_SECONDS": "2",
        }
        if disconnected:
            settings["VALIDATOR_PYLON_SERVICE_ADDRESS"] = "http://127.0.0.1:1"
        for key, value in settings.items():
            command.extend(("--env", f"{key}={value}"))
        subprocess.run([*command, "validator"], check=True, capture_output=True)

    def stop_validator(self) -> None:
        """Keep logs and business evidence while removing only this bounded probe container."""
        logs = subprocess.run(["docker", "logs", self.name], capture_output=True, text=True, check=False)
        if logs.returncode == 0:
            with (self.base / "validator.log").open("a") as output:
                output.write(logs.stdout + logs.stderr)
            docker("rm", "--force", self.name)

    def admitted(self, previous: int) -> RoundPlan:
        """Wait for one genuinely discovered cohort and record all published jobs for cleanup."""
        until(lambda: len(self.repository.rounds()) > previous, "No round admitted", 90)
        plan = self.repository.rounds()[-1].plan
        until(
            lambda: len([j for j in self.repository.requests() if j.round_id == plan.round_id]) >= len(plan.cohort),
            "Factory requests not published",
            20,
        )
        return plan

    def record(self, plan: RoundPlan) -> RoundRecord:
        """Read a round by its retained identity."""
        return next(row for row in self.repository.rounds() if row.plan.round_id == plan.round_id)

    def finished(self, plan: RoundPlan) -> None:
        """Wait until this round settles, without waiting for the next admission slot."""
        until(
            lambda: self.record(plan).stage == "complete" and not self.record(plan).unresolved_jobs,
            "Round remained incomplete or unresolved",
            90,
        )

    def verify(self, plan: RoundPlan, expected_scores: int) -> dict[str, object]:
        """Check actual container lifetimes, single starts, gates and immutable accepted scores.

        Raises:
            RuntimeError: Execution or accepted-score evidence violates the scenario.
        """
        decisions: list[dict[str, object]] = []
        accepted = 0
        for job in self.repository.requests():
            if job.round_id != plan.round_id:
                continue
            self.jobs.append(job)
            result = self.results.read(job)
            if result is None:
                raise RuntimeError("A published execution has no canonical outcome")
            actual = None
            if result.status.container_id is not None:
                inspect(job, self.root, result.status)
                actual = self.actual(job)
                if actual.State.Running or actual.State.Status != "exited":
                    raise RuntimeError("Executor reported termination without actual Docker exit")
                marker = (
                    self.root
                    / (job.report_dir if job.kind == "judge" and job.report_dir else job.output_dir)
                    / "starts"
                )
                if marker.read_text() != "started\n":
                    raise RuntimeError("A business job executed more than once")
                if (
                    job.kind == "judge"
                    and datetime.fromisoformat(actual.State.StartedAt) < plan.deadlines.evaluation_start
                ):
                    raise RuntimeError("Premature judging")
            if result.accepted is not None:
                accepted += 1
            decisions.append(
                {"decision": result.model_dump(mode="json"), "docker": None if actual is None else actual.model_dump()}
            )
        if accepted != expected_scores:
            raise RuntimeError(f"Expected {expected_scores} accepted scores, observed {accepted}")
        return {"round": self.record(plan).model_dump(mode="json"), "outcomes": decisions}

    def missing_chain(self) -> None:
        """Prove initial connection failure cannot authorize a new request.

        Raises:
            RuntimeError: The disconnected runtime admitted work or did not establish a connection error.
        """
        self.start_validator("judge-valid", disconnected=True)
        time.sleep(12)
        logs = docker("logs", self.name)
        if self.repository.requests() or self.repository.rounds() or "Pylon poll failure" not in logs:
            raise RuntimeError("Missing initial chain connection did not gate admission")
        self.stop_validator()
        self.evidence["missing_initial_chain"] = True

    def mixed(self) -> None:
        """Combine success, missing files, nonzero exit, hanging work and unsafe paths.

        Raises:
            RuntimeError: Restart, discovery isolation or failure exclusion is incorrect.
        """
        self.commitments(["factory-valid", "factory-missing", "factory-nonzero", "factory-hang", "factory-symlink"])
        self.start_validator("judge-valid")
        plan = self.admitted(0)
        retained = {
            job.job_id: self.repository.files.read_bytes(f"control/requests/{job.job_id}.json")
            for job in self.repository.requests()
        }
        docker("kill", self.name)
        docker("start", self.name)
        # The chain value changes now, but this round's frozen request remains unchanged.
        self.publish_bytes(self.miners[-1].identity, self.profiles["factory-valid"].image.encode())
        self.finished(plan)
        self.stop_validator()
        if any(
            self.repository.files.read_bytes(f"control/requests/{job}.json") != data for job, data in retained.items()
        ):
            raise RuntimeError("Commitment update or validator crash rewrote an active request")
        result = self.verify(plan, 1)
        store = FileTaskResultStore(self.results)
        weigher = Weigher(store, lambda: read_membership(self.client_config("validator"), 2))
        selected, _, scores = weigher.select()
        weights = softmax(scores)
        if selected != plan.round_id or set(weights) != {Hotkey(self.miners[0].hotkey)}:
            raise RuntimeError("A failed miner acquired positive softmax weight")
        result["weights"] = {str(key): float(value) for key, value in weights.items()}
        self.evidence["mixed"] = result
        # Re-enable the same production schedule for the next ordinary slot.
        self.start_validator("judge-valid")
        next_plan = self.admitted(1)
        changed = next(m for m in next_plan.cohort if m.miner_hotkey == self.miners[-1].hotkey)
        if (
            changed.image != self.profiles["factory-valid"].image
            or next_plan.deadlines.start < plan.deadlines.round_end
        ):
            raise RuntimeError("Commitment change was not confined to the next cohort")
        self.finished(next_plan)
        self.stop_validator()
        self.evidence["next_cohort"] = self.verify(next_plan, 2)
        print(
            "PASS: mixed outcomes, crash after publication, failed-weight exclusion and next-cohort commitment change",
            flush=True,
        )

    def rejected_commitments(self) -> None:
        """Prove actual invalid chain values cannot dispatch and a failed pull cannot score.

        Raises:
            RuntimeError: Invalid commitments or a failed image pull became eligible work.
        """
        self.commitments(["factory-valid"] * 5)
        missing = "ghcr.io/backend-developers-ltd/factory-horde-fixture@sha256:" + "0" * 64
        self.publish_bytes(self.miners[0].identity, missing.encode())
        self.publish_bytes(self.miners[1].identity, b"\xff")
        self.publish_bytes(self.miners[2].identity, b"ghcr.io/backend-developers-ltd/factory-horde-fixture:latest")
        overlong = "ghcr.io/a/" + "x" * 60 + "@sha256:" + "0" * 64
        submitter = (ROOT / "state/submitter-image.id").read_text().strip()
        with PylonClient(self.client_config(self.miners[3].identity)) as client:
            before = client.v1.identity.get_own_commitment()
            rejected = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--read-only",
                    submitter,
                    overlong,
                    "--pylon-address",
                    "http://127.0.0.1:1",
                    "--identity",
                    self.miners[3].identity,
                    "--netuid",
                    "2",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if rejected.returncode == 0 or "128" not in rejected.stderr:
                raise RuntimeError("Production submitter did not reject the overlong reference before network I/O")
            try:
                decode_reference(overlong.encode().hex())
            except ValueError as error:
                if "128" not in str(error):
                    raise RuntimeError("Unexpected overlong discovery rejection") from error
            else:
                raise RuntimeError("Discovery accepted an overlong commitment")
            after = client.v1.identity.get_own_commitment()
            if before.commitment != after.commitment:
                raise RuntimeError("Overlong commitment was not rejected atomically")
        count = len(self.repository.rounds())
        self.start_validator("judge-valid")
        plan = self.admitted(count)
        self.finished(plan)
        self.stop_validator()
        if len(plan.cohort) != 3 or any(
            m.miner_hotkey in {self.miners[1].hotkey, self.miners[2].hotkey} for m in plan.cohort
        ):
            raise RuntimeError("Malformed/tag-only submission entered the frozen cohort")
        evidence = self.verify(plan, 2)
        evidence["discovery"] = json.loads(self.repository.files.read_bytes(f"control/discovery/{plan.round_id}.json"))
        evidence["overlong_rejected"] = {
            "bytes": len(overlong.encode()),
            "submitter_error": rejected.stderr.strip(),
            "discovery_rejected": True,
            "chain_commitment_unchanged": True,
        }
        self.evidence["commitment_and_pull_failures"] = evidence
        print("PASS: malformed/tag-only/overlong commitment and actual failed image pull", flush=True)

    def delayed_results(self) -> None:
        """Hold a real pull across cancellation, deny projection writes and kill after acceptance.

        Raises:
            RuntimeError: Startup, durable acceptance, admission or result-store recovery is incorrect.
        """
        self.commitments(["factory-valid"] * 5)
        baseline = dotenv_values(ROOT / "state/published-images.env")["GHCR_FACTORY_IMAGE"] or ""
        self.publish_bytes(self.miners[0].identity, baseline.encode())
        self.arm("pull", baseline)
        projections = self.root / "control/projections"
        projections.mkdir(exist_ok=True)
        projections.chmod(0o555)
        previous = len(self.repository.rounds())
        self.start_validator("judge-valid")
        try:
            plan = self.admitted(previous)
            self.entered()
            delayed = request_for(
                plan, next(m for m in plan.cohort if m.miner_hotkey == self.miners[0].hotkey), "factory"
            )
            judges = [request_for(plan, m, "judge") for m in plan.cohort if m.miner_hotkey != delayed.miner_hotkey]
            until(
                lambda: all((r := self.results.read(job)) is not None and r.accepted is not None for job in judges),
                "Unaffected miners did not retain accepted scores",
                65,
            )
            originals = {
                str(job.job_id): self.repository.files.read_bytes(self.results.result_path(job)) for job in judges
            }
            if any((projections / f"{job.projection_id}.json").exists() for job in judges):
                raise RuntimeError("The actual validator could still write to the denied projection directory")
            logs = docker("logs", self.name)
            if "PermissionError" not in logs or "result_operation_failed" not in logs:
                raise RuntimeError("Result-store write failure did not appear on an actor error path")
            docker("kill", self.name)
            docker("start", self.name)
            until(
                lambda: datetime.now(UTC) > plan.deadlines.round_end + timedelta(seconds=5),
                "Did not reach the held next slot",
                65,
            )
            unresolved = self.repository.status(delayed)
            if len(self.repository.rounds()) != previous + 1 or unresolved is None or unresolved.confirmed_stopped:
                raise RuntimeError(
                    "Pending startup allowed overlapping admission or premature cancellation acknowledgement"
                )
            during = self.record(plan).model_dump(mode="json")
            self.clear_fault()
            until(
                lambda: (s := self.repository.status(delayed)) is not None and s.execution == "never_started",
                "Late pull started after permanent cancellation",
                20,
            )
            projections.chmod(0o750)
            docker("kill", self.name)
            docker("start", self.name)
            until(
                lambda: all((projections / f"{job.projection_id}.json").exists() for job in judges),
                "Delayed Nexus results were not recovered",
                25,
            )
            self.finished(plan)
            self.stop_validator()
            if any(
                self.repository.files.read_bytes(self.results.result_path(job)) != originals[str(job.job_id)]
                for job in judges
            ):
                raise RuntimeError("Restart redrew or replaced an already accepted score")
            absent = subprocess.run(["docker", "inspect", delayed.container_name], capture_output=True, check=False)
            if absent.returncode == 0:
                raise RuntimeError("A permanently cancelled startup created a container")
            evidence = self.verify(plan, 4)
            evidence.update(
                {
                    "held_next_slot": during,
                    "unresolved_during_pull": unresolved.model_dump(mode="json"),
                    "projection_write_denied": True,
                    "accepted_bytes_unchanged_after_kill": True,
                }
            )
            self.evidence["delayed_results_and_cancellation"] = evidence
        finally:
            projections.chmod(0o750)
            self.clear_fault()
        print(
            "PASS: blocked next slot, late-pull cancellation, result-store failure and stable accepted scores",
            flush=True,
        )

    def reports(self) -> None:
        """Run every report profile through real separate judges and production acceptance."""
        self.commitments(["factory-valid"] * 5)
        evidence: dict[str, object] = {}
        for profile in sorted(p for p in self.profiles if p.startswith("judge-")):
            previous = len(self.repository.rounds())
            self.start_validator(profile)
            plan = self.admitted(previous)
            self.finished(plan)
            self.stop_validator()
            evidence[profile] = self.verify(plan, 5 if profile == "judge-valid" else 0)
            self.evidence["reports"] = evidence
            self.save()
            print(f"PASS: {profile} real Docker exits and canonical decisions", flush=True)

    def save(self) -> None:
        """Retain incremental evidence even if a later scenario exposes a defect."""
        self.evidence["executor_sha256"] = hashlib.sha256((self.base / "executor/executor.py").read_bytes()).hexdigest()
        (self.base / "evidence.json").write_text(json.dumps(self.evidence, indent=2) + "\n")
        (ROOT / "state/task13-latest.txt").write_text(str(self.base) + "\n")

    def cleanup(self) -> None:
        """Restore original real commitments and stop only this suite's workloads/services."""
        self.stop_validator()
        for identity, content in self.original.items():
            self.publish_bytes(identity, content)
        self.evidence["commitments_restored"] = True
        self.jobs = list(self.repository.requests())
        self.close()
        self.evidence["executor_stopped"] = True
        self.save()


def main() -> None:
    """Exercise a clean isolated root while retaining all earlier acceptance evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("metadata", type=Path)
    args = parser.parse_args()
    check = Adversarial(Path(args.metadata))
    try:
        check.missing_chain()
        check.mixed()
        check.rejected_commitments()
        check.delayed_results()
        check.reports()
        check.crash_recovery("create", "exit")
        check.crash_recovery("start", "ignore-term")
        check.unavailable_and_missing()
        check.evidence["passed"] = True
        check.save()
    finally:
        check.cleanup()


if __name__ == "__main__":
    main()
