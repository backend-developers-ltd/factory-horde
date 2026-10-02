"""Independent Subtensor proof of Nexus-written weights; run with the miner bootstrap group."""

import argparse
import json
import math
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID, uuid4

import bittensor as bt
from bittensor.utils import get_mechid_storage_index
from pydantic import BaseModel, Field, TypeAdapter

from localnet.runtime import Runtime

ROOT = Path(__file__).resolve().parent


class StorageValue(Protocol):
    """Validate the SDK's heterogeneous SCALE payload at this external boundary."""

    @property
    def value(self) -> object: ...


class StorageReader(Protocol):
    """Narrow read-only interface for the SDK's loosely annotated storage query."""

    def query_subtensor(self, name: str, params: list[int], block: int) -> StorageValue: ...


class Registration(BaseModel):
    identity: str
    uid: int
    hotkey: str


class Registrations(BaseModel):
    netuid: int
    mechanism_id: int
    registrations: list[Registration]


class Calculation(BaseModel):
    round_id: UUID
    calculated_at: datetime
    temperature: float = Field(gt=0, allow_inf_nan=False)
    uids: dict[str, int]
    weights: dict[str, float]


class Location(BaseModel):
    directory: str


class Accepted(BaseModel):
    round_id: UUID
    miner_hotkey: str
    score: float = Field(ge=0, le=1, allow_inf_nan=False)


class Decision(BaseModel):
    accepted: Accepted | None
    failure: str | None


class ChainSnapshot(BaseModel):
    """Both mechanisms and registration are read directly at the same chain block."""

    block: int
    block_hash: str
    validator_hotkey: str
    validator_uid: int
    mapping: dict[str, int]
    mechanism_zero: list[tuple[int, list[tuple[int, int]]]]
    mechanism_one: list[tuple[int, list[tuple[int, int]]]]
    last_update: int
    tempo: int
    min_allowed_weights: int
    max_weight_limit: float
    weights_rate_limit: int


def snapshot(chain: bt.Subtensor, validator: str) -> ChainSnapshot:
    """Query SDK/Subtensor directly, with no Pylon client or validator success-log dependency.

    Raises:
        RuntimeError: Identity, supported mechanisms or required chain constraints differ.
    """
    block = chain.get_current_block()
    block_hash = chain.determine_block_hash(block)
    mapping = {neuron.hotkey: neuron.uid for neuron in chain.neurons_lite(2, block=block)}
    uid = mapping.get(validator)
    if uid is None or block_hash is None or chain.get_mechanism_count(2, block=block) < 2:
        raise RuntimeError("Registered validator and mechanism 1 are required")
    last = cast(StorageReader, chain).query_subtensor(
        "LastUpdate", params=[get_mechid_storage_index(2, 1)], block=block
    )
    updates = TypeAdapter(list[int]).validate_python(last.value)
    tempo = chain.tempo(2, block=block)
    minimum = chain.min_allowed_weights(2, block=block)
    maximum = chain.max_weight_limit(2, block=block)
    rate = chain.weights_rate_limit(2, block=block)
    if tempo is None or minimum is None or maximum is None or rate is None:
        raise RuntimeError("Required chain constraints are unavailable")
    return ChainSnapshot(
        block=block,
        block_hash=block_hash,
        validator_hotkey=validator,
        validator_uid=uid,
        mapping=mapping,
        mechanism_zero=chain.weights(2, mechid=0, block=block),
        mechanism_one=chain.weights(2, mechid=1, block=block),
        last_update=updates[uid] if uid < len(updates) else 0,
        tempo=tempo,
        min_allowed_weights=minimum,
        max_weight_limit=maximum,
        weights_rate_limit=rate,
    )


def verify(root: Path, current: ChainSnapshot, previous: ChainSnapshot, calculation: Calculation) -> dict[str, float]:
    """Recompute softmax independently from accepted records and compare with encoded chain weights.

    Raises:
        RuntimeError: Chain attribution, accepted score, normalization or mechanism isolation differs.
    """
    if current.mechanism_zero != previous.mechanism_zero:
        raise RuntimeError("Mechanism 0 changed during the mechanism-1 check")
    if current.validator_uid != previous.validator_uid or current.mapping != previous.mapping:
        raise RuntimeError("Registration changed during independent verification")
    location = Location.model_validate_json((root / f"control/rounds/{calculation.round_id}.json").read_bytes())
    scores: dict[str, float] = {}
    for hotkey, uid in calculation.uids.items():
        if current.mapping.get(hotkey) != uid:
            raise RuntimeError("Weight recipient does not match current Subtensor registration")
        result = Decision.model_validate_json((root / location.directory / hotkey / "result.json").read_bytes())
        if (
            result.failure is not None
            or result.accepted is None
            or result.accepted.round_id != calculation.round_id
            or result.accepted.miner_hotkey != hotkey
        ):
            raise RuntimeError("A weight recipient lacks a linked accepted score")
        scores[hotkey] = result.accepted.score
    if len(scores) != 5:
        raise RuntimeError("This acceptance run requires five registered successful baseline miners")
    maximum = max(scores.values())
    terms = {key: math.exp((score - maximum) / calculation.temperature) for key, score in scores.items()}
    total = math.fsum(terms.values())
    expected = {key: value / total for key, value in terms.items()}
    if expected.keys() != calculation.weights.keys() or any(
        not math.isclose(value, calculation.weights[key], rel_tol=1e-12) for key, value in expected.items()
    ):
        raise RuntimeError("Calculated request differs from independent softmax")
    raw = dict(dict(current.mechanism_one).get(current.validator_uid, []))
    if not raw or set(raw) != set(calculation.uids.values()) or sum(raw.values()) <= 0:
        raise RuntimeError("Chain vector does not identify exactly the accepted recipients")
    # TurboBT first scales by the maximum into u16; Subtensor stores quantized weights.
    # Compare relative proportions, allowing the two integer quantization steps.
    tolerance = 2 * len(raw) / 65535
    if any(abs(raw[calculation.uids[key]] / sum(raw.values()) - value) > tolerance for key, value in expected.items()):
        raise RuntimeError("Subtensor weight proportions differ beyond integer encoding tolerance")
    return expected


def main() -> None:
    """Start the common validator image, verify two submissions, then restore its ordinary configuration.

    Raises:
        RuntimeError: The host is not isolated localnet or chain proof does not arrive.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    runtime = Runtime.load(args.env_file)
    config = runtime.config
    registrations = Registrations.model_validate_json((runtime.state / "registrations.json").read_bytes())
    if (
        config.get("ENVIRONMENT") != "localnet"
        or config.get("NETUID") != "2"
        or registrations.netuid != 2
        or registrations.mechanism_id != 1
    ):
        raise RuntimeError("Requires the isolated local subnet 2 configured for mechanism 1")
    root = Path(config["FACTORY_HORDE_DATA_ROOT"] or "")
    validator = next(row.hotkey for row in registrations.registrations if row.identity == "validator")
    compose = runtime.command
    name = f"factory-horde-weight-probe-{uuid4()}"
    complete = False
    with bt.Subtensor(network=f"ws://127.0.0.1:{int(config.get('SUBTENSOR_HOST_PORT') or '9944')}") as chain:
        before = snapshot(chain, validator)
        observed: list[ChainSnapshot] = []
        calculations: list[Calculation] = []
        expected: dict[str, float] = {}
        subprocess.run([*compose, "stop", "--timeout", "1", "validator"], check=True, capture_output=True)
        try:
            started = datetime.now(UTC)
            subprocess.run(
                [
                    *compose,
                    "run",
                    "--detach",
                    "--no-deps",
                    "--name",
                    name,
                    "--env",
                    "VALIDATOR_DISPATCH_ENABLED=false",
                    "--env",
                    "VALIDATOR_WEIGHTS_ENABLED=true",
                    "--env",
                    "MECHANISM_ID=1",
                    "validator",
                ],
                check=True,
                capture_output=True,
            )
            last_update = before.last_update
            end = time.monotonic() + 300
            while time.monotonic() < end and len(observed) < 2:
                current = snapshot(chain, validator)
                if current.last_update > last_update:
                    calculation = Calculation.model_validate_json(
                        (root / "control/weight-calculation.json").read_bytes()
                    )
                    if calculation.calculated_at < started:
                        raise RuntimeError("Readback refers to an older weight calculation")
                    expected = verify(root, current, before, calculation)
                    observed.append(current)
                    calculations.append(calculation)
                    last_update = current.last_update
                    print(
                        f"PASS: independent mechanism-1 weights at block {current.block}, last update {last_update}",
                        flush=True,
                    )
                    if len(observed) == 1:
                        subprocess.run(["docker", "restart", "--time", "1", name], check=True, capture_output=True)
                time.sleep(2)
            if len(observed) != 2:
                raise RuntimeError(f"Two chain weight updates did not arrive; inspect docker logs {name}")
            if (
                calculations[0].round_id != calculations[1].round_id
                or calculations[0].weights != calculations[1].weights
            ):
                raise RuntimeError("Repeated opportunities did not reuse the same accepted round and weights")
            logs = subprocess.run(["docker", "logs", name], check=True, capture_output=True, text=True)
            (runtime.state / "task12-validator.log").write_text(logs.stdout + logs.stderr)
            evidence = {
                "checked_at": datetime.now(UTC).isoformat(),
                "image_id": runtime.image("validator"),
                "netuid": 2,
                "mechanism_id": 1,
                "before": before.model_dump(mode="json"),
                "after": [entry.model_dump(mode="json") for entry in observed],
                "calculations": [entry.model_dump(mode="json") for entry in calculations],
                "independently_calculated_weights": expected,
                "mechanism_zero_unchanged": True,
                "validator_restarted_between_updates": True,
                "encoding": (
                    "Compared normalized Subtensor u16 vector with independent stable softmax; "
                    "absolute tolerance 2 * recipients / 65535."
                ),
            }
            (runtime.state / "task12-weights.json").write_text(json.dumps(evidence, indent=2) + "\n")
            complete = True
        finally:
            subprocess.run(["docker", "stop", "--time", "1", name], check=False, capture_output=True)
            if complete:
                subprocess.run(["docker", "rm", name], check=True, capture_output=True)
            subprocess.run(
                [*compose, "up", "-d", "--no-deps", "--wait", "--wait-timeout", "180", "validator"],
                check=True,
                capture_output=True,
            )


if __name__ == "__main__":
    main()
