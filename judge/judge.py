#!/usr/local/bin/python3
"""Judge readable fixture files without executing them; persist one random report."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import stat
import sys
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

LIMIT = 1024 * 1024
EXPECTED_FILES = ("main.py", "README.md")
MANIFEST_FIELDS = {
    "protocol_version",
    "round_id",
    "factory_job_id",
    "judge_job_id",
    "miner_hotkey",
    "specification_sha256",
}


def read_regular(path: Path) -> bytes:
    """Read a bounded regular file without following a final symlink or blocking on a FIFO.

    Raises:
        ValueError: The file is not regular or exceeds the protocol bound.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError(f"Not a regular file: {path.name}")
        content = source.read(LIMIT + 1)
    if len(content) > LIMIT:
        raise ValueError(f"File exceeds one MiB: {path.name}")
    return content


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Decode JSON objects with unambiguous keys.

    Raises:
        ValueError: A key is repeated.
    """
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    """Reject non-finite JSON constants.

    Raises:
        ValueError: Always, because JSON has no NaN or infinity literals.
    """
    raise ValueError(f"Invalid JSON constant: {value}")


def read_object(path: Path) -> dict[str, object]:
    """Parse an unambiguous JSON object.

    Raises:
        ValueError: The top-level value is not an object.
    """
    value: object = json.loads(read_regular(path), object_pairs_hook=unique_object, parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return cast(dict[str, object], value)


def manifest_identity(input_dir: Path) -> tuple[dict[str, object], str]:
    """Validate input attribution before producing any report.

    Raises:
        ValueError: Input identity or protocol is invalid.
    """
    manifest = read_object(input_dir / "task.json")
    if set(manifest) != MANIFEST_FIELDS or type(manifest["protocol_version"]) is not int:
        raise ValueError("Invalid input manifest fields/version")
    if manifest["protocol_version"] != 1:
        raise ValueError("Unsupported input protocol")
    for key in ("round_id", "factory_job_id", "judge_job_id"):
        value = manifest[key]
        if not isinstance(value, str) or UUID(value).version != 4 or str(UUID(value)) != value:
            raise ValueError(f"Invalid {key}")
    if manifest["factory_job_id"] == manifest["judge_job_id"]:
        raise ValueError("Factory and judge job IDs must differ")
    hotkey = manifest["miner_hotkey"]
    if not isinstance(hotkey, str) or re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{48}", hotkey) is None:
        raise ValueError("Invalid miner hotkey")
    specification_hash = manifest["specification_sha256"]
    if not isinstance(specification_hash, str) or re.fullmatch(r"[0-9a-f]{64}", specification_hash) is None:
        raise ValueError("Invalid specification hash")
    return {
        "protocol_version": 1,
        "round_id": manifest["round_id"],
        "job_id": manifest["judge_job_id"],
        "factory_job_id": manifest["factory_job_id"],
        "miner_hotkey": hotkey,
        "kind": "judge",
    }, specification_hash


def report_exit_code(report: dict[str, object], identity: dict[str, object]) -> int:
    """Validate an existing report before preserving it on rerun.

    Raises:
        ValueError: Report fields, attribution or score/failure classification are invalid.
    """
    if set(report) != {*identity, "checked_files", "score", "failure"}:
        raise ValueError("Invalid existing report fields")
    if type(report["protocol_version"]) is not int or any(report[key] != value for key, value in identity.items()):
        raise ValueError("Existing report attribution mismatch")
    checked = report["checked_files"]
    if not isinstance(checked, list):
        raise ValueError("Invalid checked_files")
    checked_items = cast(list[object], checked)
    if any(item not in EXPECTED_FILES for item in checked_items) or len(checked_items) > 2:
        raise ValueError("Invalid checked_files")
    if len(checked_items) == 2 and checked_items[0] == checked_items[1]:
        raise ValueError("Repeated file check")
    score, failure = report["score"], report["failure"]
    if score is None and isinstance(failure, str) and 1 <= len(failure) <= 512:
        return 1
    if (
        type(score) in (float, int)
        and isinstance(score, (float, int))
        and math.isfinite(score)
        and 0 <= score <= 1
        and failure is None
        and set(cast(list[str], checked_items)) == set(EXPECTED_FILES)
    ):
        return 0
    raise ValueError("Invalid existing report outcome")


def publish_report(path: Path, report: dict[str, object], identity: dict[str, object]) -> int:
    """Publish once with file/directory durability; racing/repeated jobs retain the first outcome."""
    temporary = path.with_name(f".{path.name}.{uuid4()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        with os.fdopen(fd, "w") as output:
            output.write(json.dumps(report, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n")
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            report = read_object(path)
    finally:
        temporary.unlink()
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    return report_exit_code(report, identity)


def evaluate(input_dir: Path, submission_dir: Path, report_dir: Path) -> int:
    """Check only readability, then publish one uniform random score or classified failure.

    Invalid attribution fails without a report. Invalid specification/submission files
    produce a linked failure report and nonzero exit. Existing evidence is never redrawn.

    Raises:
        ValueError: Input attribution or existing report validation fails.
    """
    identity, specification_hash = manifest_identity(input_dir)
    path = report_dir / "report.json"
    try:
        existing = read_object(path)
    except FileNotFoundError:
        pass
    else:
        return report_exit_code(existing, identity)
    checked: list[str] = []
    failure: str | None = None
    score: float | None = None
    try:
        if hashlib.sha256(read_regular(input_dir / "specification.md")).hexdigest() != specification_hash:
            raise ValueError("Specification digest mismatch")
        for name in EXPECTED_FILES:
            read_regular(submission_dir / name)
            checked.append(name)
        score = secrets.SystemRandom().uniform(0.0, 1.0)
    except (OSError, ValueError) as error:
        failure = str(error)[:512]
    report = {**identity, "checked_files": checked, "score": score, "failure": failure}
    return publish_report(path, report, identity)


def main() -> int:
    """Run the fixed judge-v1 contract; never execute generated code or accept commands."""
    try:
        return evaluate(Path("/input"), Path("/submission"), Path("/report"))
    except (OSError, ValueError) as error:
        print(json.dumps({"event": "judge_failed", "reason": str(error)[:512]}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
