# FactoryHorde shared-file protocol v1

Implemented validator types are in `validator/src/validator/records.py`, with atomic I/O in
`validator/src/validator/record_files.py` and round/job publication in `validator/src/validator/round_repository.py`.
The standalone executor implements the same wire contract without importing those modules.
Concurrent cancellation and fault-recovery evidence are recorded under task 8. JSON fixtures are in [fixtures/protocol-v1](fixtures/protocol-v1/README.md).
This contract defines records and publication. Docker observations are implemented;
report acceptance and automated task/stage scheduling are implemented.

## Roots, identity and ownership

The installer-selected absolute host root defaults to `<installation>/data`;
choosing `<operator-home>/factory-horde` gives `<operator-home>/factory-horde/data`.
The validator mount defaults to `/var/lib/factory-horde`. They refer to the same
tree. Repository constructors take the local absolute mount path. Every request
path is relative to that tree, never a validator-container absolute path passed to
host Docker. Root and relative path components must not be symlinks.

```text
control/
  schedule.json                   # coordinator cadence and optional pending admission seed
  discovery/<round-id>.json        # immutable block-aligned cohort and reserved job IDs
  rounds/<round-id>.json           # immutable UTC directory pointer
  requests/<job-id>.json           # immutable execution authorization
  stops/<job-id>.json              # immutable permanent cancellation
  statuses/<job-id>.json           # executor's current observation
  executor/<job-id>.request.json  # executor-retained immutable authorization
  executor/<job-id>.json          # durable Docker intent, identity, terminal evidence
  executor/service.lock          # host process lock for this root
  executor/docker-config/        # deliberately empty anonymous registry configuration
  executor-metrics.json          # atomic process counters/latency buckets
  executor-health.json           # atomic heartbeat, Docker probe, initial reconciliation
  projections/<result-id>.json   # immutable Nexus routing metadata and business-result pointer
  skipped-evaluations/<job-id>.json # immutable reason an intended judge was never authorized
  factory-eligibility/<job-id>.json # fixed finish cutoff and pending/timely/late/failed decision
  weight-calculation.json         # latest derived request; not proof of chain inclusion
rounds/<YYYY-MM-DD>/round-<sequence>-<HH-MM-SS>-<round-id>/
  round.json                      # frozen plan plus mutable stage/unresolved jobs
  specification.md
  <miner-hotkey>/
    input/{specification.md,task.json}
    output/{main.py,README.md}
    evaluation/report.json
    factory-result.json           # immutable factory JobResult
    result.json                   # immutable judge JobResult, containing AcceptedResult on success
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
paths before making a request visible. The coordinator applies the stage gate before
publication; the communicator may idempotently republish the same frozen request.

`Schedule` persists the next slot and an optional `RoundSeed` before discovery. The
seed freezes round ID, sequence, judge, specification and all deadlines; a retained
discovery snapshot freezes the cohort and both job IDs per miner. A partial plan or
input publication is repaired using those same identities. The schedule advances
only after preparation succeeds. No new round is admitted before an actual block
beat in the current runtime or while old work remains unresolved. Expired intended
factory requests may be published during recovery solely to obtain permanent
never-started confirmation from the executor.

The single coordinator writer reconciles canonical files on actor-owned ticks.
Stops are published at the common factory/judge deadlines for unconfirmed work,
including when a status cannot be parsed. Separate judge authorization requires a
successful retained factory decision, readable required output, and the evaluation
window. At the confirmation cutoff (minute 65 by default), `FactoryEligibility`
persists the frozen cutoff and a pending decision if termination evidence is absent.
On the first terminal observation it retains the Docker finish time and a final
timely, late or failed decision. A clean exit at or before the cutoff may qualify,
even when first observed after restart; an actual later exit never qualifies.
Missing evidence remains pending and continues to hold new round slots. This gate
runs even without a chain beat; judging still requires a retained factory result.
`SkippedEvaluation` records failed or late factories, missing output or expired
evaluation without claiming that a judge executed. A fully settled round can expose
its results early during evaluation; the next admission still waits for its frozen
slot. At round end, partial usable results and unresolved work coexist. Any unresolved
work holds/skips future slots until reconciled; callback delivery never opens that gate.

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
`ResultRepository.finalize` additionally checks confirmed clean factory and judge
termination, factory completion by the confirmation cutoff, complete attribution
and execution ordering.
Accepted metadata uses a deterministic UUID5 of `factory-horde:v1:<kind>:<job-id>`
under the standard URL namespace, plus the original completion block/times and
SHA-256 of the report's canonical JSON encoding. Fresh framework contexts do not
produce a new execution or result identity.

## Accepted decisions and Nexus projections

`JobResult` in `result_records.py` is the atomically published business outcome.
It contains the complete immutable request, terminal executor status, original
Docker start/finish times (observation time for never-started jobs), first acceptance
block number/hash/timestamp, stable result ID and either a failure or an accepted
judge score. The existing `AcceptedResult` schema is embedded in the successful
judge decision. This is the only persisted copy of the score. The entire decision
publishes in one operation; score and outcome cannot become partially committed.

Unconfirmed execution raises `ResultNotReady`; it is never finalized as stopped.
After confirmed clean judge termination, a missing, malformed or misattributed report
becomes an immutable failed evaluation. A report arriving later does not turn that
failure into a new run or score. Once accepted, a decision is reused even if raw
reports/statuses are later lost. Publication conflicts fail and preserve the original.

Unsafe or unreadable workload artifacts (including symlinks, nonregular/oversized
files and denied file permissions) are rejected. Ineligible factory output records
an `invalid_output` skipped evaluation; a bad final report records a failed judge
decision. Ordinary host I/O failures remain unresolved and cannot invent a terminal
execution or a score.

`FileTaskResultStore` implements the public Nexus store/provider contracts with fixed
task names `factory-horde-factory` and `factory-horde-evaluation`. Projections contain
only routing metadata and the canonical result path. They are written after the
business decision; a failed projection save can be retried without accepting again.
Fresh framework times/blocks/contexts never overwrite the original decision.
A process restart rebuilds by-ID and ordered block indexes from these references.
Epoch queries use inclusive original completion-block ranges. Returned mutable
Pylon routing models are copied to preserve thread-safe cached observations.

`latest_usable_round` reads completed rounds and skips empty ones, including when
other jobs remain unresolved. This query supplies score history; it does not grant
permission to start another round. Synthetic routing identities never attribute
scores: use `AcceptedResult.miner_hotkey`, not Nexus routed-neuron count helpers.
The weight gate and setter read this same provider/repository. They filter accepted
hotkeys against current registration, apply stable softmax at the configured positive
temperature and recheck registration after calculation. A detected membership change
aborts that attempt. `WeightCalculation` records the source round, membership block,
epoch, UID mapping, temperature and derived weights without copying accepted scores.
Absent usable history/recipients suppresses the opportunity before the setter;
an empty mapping is never used as a skip signal. Only an independent Subtensor read
proves the resulting mechanism-specific chain vector.
Counters and latency histograms use `factory_horde_result_operations_total` and
`factory_horde_result_operation_seconds` with bounded operation/outcome labels.

The two public Nexus tasks share this store/provider and use one file communicator
implementation, with distinct node IDs and a one-attempt task policy. Input handlers
authorize/publish frozen requests and return; an actor-owned wall-clock producer
feeds independent poll sinks. Terminal outcomes are correlated to the original
request context. Only an actual block beat permits first acceptance. Five consecutive
file-observation failures end that runtime subscription with an explicit framework
error; they do not declare execution stopped or authorize another run. A fresh
context can reobserve the same business job. Metrics use
`factory_horde_file_transport_total` and `factory_horde_file_transport_seconds`.

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
alone replaces statuses. The executor service uses one in-flight worker per job and one process lock per
root. Different jobs pull independently. TERM intent stores a fixed `stop_by` time;
service replacement does not grant another grace period. KILL intent is persisted
before the signal, and only subsequent Docker inspection can establish exit.

The executor retains create/start intent before each Docker side effect and binds
the original container ID to the immutable request fingerprint. A crash after
create can recover that same created container. An ambiguous attempted start is
never repeated: a running/exited container supplies evidence; an unchanged created
container remains unresolved. Missing expected execution never authorizes a new
container. A delayed pull/create worker cannot publish a cancellation acknowledgement
until its startup work has completed and later startup is permanently closed.
Terminal records are restored from the ledger without querying or recreating Docker
containers, including after an operator deletes a finalized container.

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
dependency, now explicit in this project. A Nexus-owned validator endpoint exposes
these and the executor metrics at `/metrics`; there is no executor HTTP server or
node-exporter textfile path. `executor-health.json` is an executor-owned version-one
record with UTC `started_at`, `observed_at`, `docker_observed_at`, `pid`, `docker_ok`,
nullable `docker_error`, `reconciled`, `rejected_requests` and `active_workers`.
Reconciliation means that this process has observed each current request at least
once without a rejected request/worker error. It does not imply job termination;
the validator separately checks active/unresolved jobs and observation freshness.
The Docker probe has a three-second timeout and runs after each poll's scheduling;
the job workers continue independently. Failures publish `docker_ok=false`.
Both health and metrics are replaced atomically and include the executor PID so
a validator can reject mixed snapshots during a service replacement.

`executor.py --protocol-version` prints `1` without opening a data root or starting
workers. Installer release metadata, this executable declaration and existing
request/stop/status headers must agree before replacement. Checksums and the fixed
asset manifest are verified before executing that probe. Updates replace only
installation assets and resource configuration; they never migrate or remove shared
execution records. A current systemd PID and fresh Docker health are required after
replacement. A health failure remains visible and requires ordinary repair; it is
not permission to roll back or rerun a workload.

Validator readiness is a disposable in-memory projection, rebuilt on actor polls.
Hidden `.readiness.json` files probe atomic writes/fsync in validator-owned control
and result directories; all record discovery ignores these non-identity filenames.
They contain only the protocol version and never authorize work. Health records,
metrics and readiness probes must never be used as workload stop evidence.

Run the protocol and dependency checks from `validator/`:

```sh
env -u UV_EXCLUDE_NEWER uv run ruff check --fix
env -u UV_EXCLUDE_NEWER uv run ruff format
env -u UV_EXCLUDE_NEWER uv run basedpyright
env -u UV_EXCLUDE_NEWER uv run pytest -q --tb=line -r f
```
