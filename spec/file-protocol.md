# FactoryHorde shared-file protocol v1

Implemented validator types are in `validator/records.py`, with atomic I/O in
`validator/record_files.py` and round/job publication in `validator/round_repository.py`.
The standalone executor implements the same wire contract in tasks 7–8 without
importing those modules. JSON fixtures are in [fixtures/protocol-v1](fixtures/protocol-v1/README.md).
This contract defines records and publication; stage scheduling, Docker observation
and report acceptance are implemented by later tasks.

## Roots, identity and ownership

The installer-selected absolute host root defaults to `<operator-home>/factory-horde/data`;
the validator mount defaults to `/var/lib/factory-horde`. They refer to the same
tree. Repository constructors take the local absolute mount path. Every request
path is relative to that tree, never a validator-container absolute path passed to
host Docker. Root and relative path components must not be symlinks.

```text
control/
  rounds/<round-id>.json           # immutable UTC directory pointer
  requests/<job-id>.json           # immutable execution authorization
  stops/<job-id>.json              # immutable permanent cancellation
  statuses/<job-id>.json           # executor's current observation
rounds/<YYYY-MM-DD>/round-<sequence>-<HH-MM-SS>-<round-id>/
  round.json                      # frozen plan plus mutable stage/unresolved jobs
  specification.md
  <miner-hotkey>/
    input/{specification.md,task.json}
    output/{main.py,README.md}
    evaluation/report.json
    result.json                   # accepted score, implemented by task 9
```

Use UTC ISO-8601 timestamps and UUID4 round/job IDs. A round has two reserved IDs
per cohort member, even if that member never becomes eligible for judging. A
factory's `factory_job_id` equals its `job_id`; a judge's ID differs. Hotkeys, not
UIDs or synthetic Nexus routing identities, attribute jobs and scores. The round
pointer prevents reusing a round ID at a different time or path. Discovery ignores
an orphan pointer if a crash occurred before the round plan was published: no job
request is authorized at that point.

| Record | Writer | Meaning |
|---|---|---|
| Round plan/stage, task manifest, request, stop | Validator | Frozen cohort, authorization and application state |
| Status | Executor | Docker observation and startup closure |
| Report | Judge | Untrusted file checks and random score/failure |
| Accepted result | Validator | One validated score and stable Nexus projection metadata |

`protocol_version` is integer `1`; extra fields, unsupported versions, duplicate
JSON keys and non-finite JSON constants are rejected. Records and input text are
limited to one MiB each. References require an explicit `ghcr.io` owner/repository
and lowercase `sha256` digest; tag-only references fail. The 1,024-character parser
bound accommodates operator image references. Miner submission and discovery apply
the selected Pylon writer's verified 128-byte UTF-8 bound separately.
Selected platform is `linux/amd64`, matching task 2's Pylon image.

## Fixed execution contracts

| Contract | Fixed executable (image entrypoint overridden) | Mounts |
|---|---|---|
| `factory-v1` | `/usr/local/bin/factory-horde-factory` | `input_dir` → `/input:ro`, `output_dir` → `/output:rw` |
| `judge-v1` | `/usr/local/bin/factory-horde-judge` | `input_dir` → `/input:ro`, `output_dir` → `/submission:ro`, `report_dir` → `/report:rw` |

The factory writes `/output/main.py` and `/output/README.md`. The judge reads
`/input/task.json` for round/miner and both job IDs, checks the specification and
submission, and writes `/report/report.json`. No generated code is executed.
Images must provide these executables; task 5 supplies their implementation.
Requests cannot supply shell commands, Docker flags, wallets or arbitrary mount
destinations. Operator settings supply UID/GID, resource limits and Docker access;
task 4 establishes actual ownership, with installer verification in task 15.

`RoundRepository.prepare_round` records the plan/IDs before preparing input. It
publishes the complete specification and manifest immutably, creates output/report
directories and preserves existing evidence on replay. `publish_request` requires
the persisted plan, matching specification hash, exact manifest and canonical
paths before making a request visible. It does not implement the stage gate;
the coordinator must authorize dispatch using these records in task 11.

## Execution facts and eligibility

Status has separate `state`, `execution`, `startup_forbidden`, Docker identity,
observation/start/finish timestamps, exit code, forced/OOM flags and bounded reason.

- `state` classifies pending, preparing, running, stopping, finished, failed or
  cancelled. `failed` alone does not prove termination.
- `execution=unresolved` is uncertainty, including unavailable Docker. Missing or
  malformed status is likewise no terminal evidence.
- `execution=exited` requires inspected Docker identity, exit code and finish time.
- `execution=never_started` requires the executor to have permanently closed startup,
  including all outstanding pull/create/start work. Merely seeing no container at
  one instant is insufficient. A created-but-unstarted container may retain its ID.
- `confirmed_stopped` requires `exited` or `never_started` **and** permanent startup
  closure. `application_succeeded` additionally requires `state=finished`, code zero,
  and neither forced termination nor OOM. Partial output from failed jobs is not success.

Docker name is `factory-horde-<job-id>`; mismatched status/container names are
rejected. Stop intent permanently forbids later startup and is not deleted at
completion. The executor must establish those facts; the record parser cannot
verify an assertion against Docker. Tasks 7–8 supply actual execution evidence.

Reports link the precise judge, factory, miner and round. A successful report has
exactly the expected file checks and one finite score in `[0,1]`, including zero;
a failed report has a reason and no score. Parsing a report is not acceptance:
task 9 additionally checks confirmed clean judge termination and factory eligibility.
Accepted metadata uses a deterministic UUID5 of `factory-horde:v1:<kind>:<job-id>`
under the standard URL namespace, plus the original completion block/times and
SHA-256 of the report's canonical JSON encoding. Fresh framework contexts do not
produce a new execution or result identity.

## Atomicity, concurrency and recovery

Writers use a uniquely named same-directory `.tmp` file, flush and fsync its content,
then publish. Immutable records use a hard link with create-if-absent semantics;
identical bytes are idempotent and different bytes raise `RecordConflictError`.
Mutable observations use atomic replace. After temporary-name cleanup, fsync the
parent directory before returning success. New directory entries are also fsynced.
Directory traversal uses descriptor-relative `O_NOFOLLOW` opens, and file readers
reject symlinks, nonregular files and oversized content. Readers ignore temporary
names; malformed committed JSON is an explicit error.

An in-process reentrant lock serializes repository writes between actor threads;
immutable publication additionally arbitrates concurrent repository instances at
the filesystem level. Mutable round state has one validator writer; the executor
alone replaces statuses. The executor service must serialize its own observations.

An fsync failure propagates even if the new name is already visible. Callers must
not infer successful publication or trigger follow-up effects from an exception.
Reconciliation retries an identical immutable record and fsyncs again. Never delete
requests, permanent stops or terminal/accepted evidence to force another execution.
This contract assumes a local Linux filesystem with working hard links, atomic rename
and directory fsync; NFS/object storage is not supported. Tests exercised these
operations on this host's ext-family filesystem, including injected file/directory
fsync failures. They are not a physical power-loss experiment.

Record I/O emits `factory_horde_record_operations_total{operation,outcome}` and
`factory_horde_record_operation_seconds{operation}` plus structured failure logs.
No job IDs appear in metric labels. The installed Nexus version has no reusable
actor Counter/Histogram registry; these use its installed Prometheus client
dependency, now explicit in this project. Scrape exposure remains task 14.

Run the protocol and dependency checks from `validator/`:

```sh
env -u UV_EXCLUDE_NEWER uv run ruff check --fix
env -u UV_EXCLUDE_NEWER uv run ruff format
env -u UV_EXCLUDE_NEWER uv run basedpyright
env -u UV_EXCLUDE_NEWER uv run pytest -q --tb=line -r f
```
