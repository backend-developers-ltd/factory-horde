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
    input/{specification.md,task.json,evaluation.json}
    output/<project files>
    evaluation/report.json
    evaluation/step-N.decision.json    # attributed next action, persisted before execution
    evaluation/step-N.observation.json # executor capture or retrieved earlier evidence
    evaluation/execution.json         # final agent-v1 step/command summary
    evaluation/assessment.json        # final verdicts with evidence references
    evaluation/review.json        # verdicts for a successful model review
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
| Report | Judge | Untrusted file checks and review or random score/failure |
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

The factory writes the project required by the task into `/output`. No fixed entry-point filenames are required. Admission requires a nonempty output directory; the judge inventories the full tree, rejecting symlinks, non-regular or unreadable files and projects larger than 64 MiB or 4096 files. `checked_files` contains unique relative project paths, nonempty for a successful score.

The judge reads `/input/task.json` for attribution and verifies the frozen specification and evaluation policy. Its tool-less Claude controller chooses one action at a time using trusted packaged testing guides. Commands are published through an executor-owned `/runner` exchange, never run by the host or judge controller. The executor executes the requested `argv` and stdin inside a separate networkless application container containing a writable project copy. Python, Node.js and POSIX shell are packaged; dependencies cannot be downloaded. The sandbox has no controller mounts, credentials or Docker socket. The legacy `args` request remains supported for already-frozen CLI judge images. Captures include exit status, stdout/stderr (16 KiB each), truncation, duration and stop status. There is no action-count or additional judging time limit; the existing round deadline still closes work and requires confirmed application termination.

Each decision is durable before its command; captured results are returned before the next decision. The judge can request older evidence when it no longer fits recent context. The final assessment must reference observed commands for every criterion, including commands that read source/documentation. Unassessed requirements make evaluation unavailable. Successful `/report/review.json` contains the verdict mean; the validator verifies identity, ordered rubric and arithmetic before accepting `/report/report.json`. Decisions, captures and final reports are retained and reused after interruptions, without replaying ambiguous commands. A directory lock serializes the judge session.

The optional `evaluation` field in a round plan contains protocol version 1, mode, ordered requirements and, when `JUDGE_MODEL` is set, the judging `model`; each task freezes its own policy. A review is accepted only if its `model` equals the frozen one. The judge records the model IDs Claude Code reported using (`resolved_models`) in each decision, `execution.json` and `review.json`. The validator copies it into `/input/evaluation.json`. Historical round plans without this field are eligible only for explicit fixture weights. Images must provide the fixed factory/judge executables. Job requests cannot supply Docker flags, wallets or arbitrary mounts. Operator settings supply UID/GID, resource limits and Docker access; only the trusted host executor operates Docker.

The executor runs at most `EXECUTOR_MAX_RUNNING_JOBS` (default 4) workload containers at once, counting created and running containers labelled with a job ID; a waiting job publishes a `pending` status with reason `Waiting for a workload slot` and keeps its frozen deadline. By default every workload runs with `--network none`, no inference environment variables and a 64 MiB `/tmp` (`EXECUTOR_TMP_SIZE`). A factory that calls a model reads two variables that its deployer supplies: `ANTHROPIC_BASE_URL` and `CLAUDE_CODE_OAUTH_TOKEN`. This executor supplies them only when the operator sets `EXECUTOR_FACTORY_NETWORK`: factory and judge containers each join a separate executor-owned internal network linked only to that network's inference proxy instead of `none` and receive `ANTHROPIC_BASE_URL` set to `EXECUTOR_INFERENCE_URL` (default `http://inference-proxy:8080`) and `CLAUDE_CODE_OAUTH_TOKEN` set to the fixed placeholder `inference-proxy`. The placeholder is not a credential; it only makes Claude Code send OAuth-style requests. The inference proxy holds the real credential and replaces the `Authorization` header, so a factory never sees it. Requests still cannot choose the network or any variable. Judges also receive `FACTORY_HORDE_JUDGE_IMAGE`, derived by the executor from the immutable request for evidence attribution. Missing `ANTHROPIC_BASE_URL` in review mode returns exit 75. Random scoring requires a frozen fixture policy.

The configured network identifies the inference proxy. Each workload gets its own labelled internal bridge with isolated gateway mode; the executor reconnects the proxy on recovery and removes that bridge after confirmed workload termination. Peers and host/internet services are unreachable. The proxy forwards `/v1/messages*` to Anthropic, provides `/healthz`, and returns 404 otherwise. All inference still shares one subscription without quotas.

`RoundRepository.prepare_round` records the plan/IDs before preparing input. It
publishes the complete specification, manifest and frozen evaluation policy, creates output/report
directories and preserves existing evidence on replay. `publish_request` requires
the persisted plan, matching specification hash, exact manifest and canonical
paths before making a request visible. The coordinator applies the stage gate before
publication; the communicator may idempotently republish the same frozen request.

`Schedule` persists the next slot and an optional `RoundSeed` before discovery. A new
seed takes its specification from `VALIDATOR_SPECIFICATION` (the operator's
`TASK_SPECIFICATION`; unset or empty keeps the built-in greeting task). The
seed freezes the round ID, sequence, judge image, specification, scoring mode, criteria
and all deadlines. A retained discovery snapshot freezes the cohort and both job IDs per miner. A partial plan or
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
judge decision. It is the authoritative record of the score used for weights.
The validator publishes the entire decision in one operation.

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

`latest_usable_round` reads completed rounds in the configured scoring mode.
A review round needs at least one accepted score and an outcome for every miner.
A reviewer outage or missing evidence skips the whole round. An attributed invalid
submission excludes only that miner. Without a usable review round the weight gate
skips submission. Explicit fixtures can use a subset of accepted scores.
After filtering accepted scores to registered hotkeys, give one highest-scoring miner 90% and divide 10% equally among the rest; a singleton receives 100%. Ties, including all-zero scores, choose the smallest SHA-256 digest of compact UTF-8 JSON `[str(round_id), miner_hotkey]`, with hotkey as the final collision tiebreaker. This is the accepted first-version implementation choice. Invalid submissions remain excluded, and registration is revalidated before returning the vector. Review fallback requires the configured judge image digest. No eligible recipients means a skipped write. `WeightCalculation` records policy `winner-90-10`, winner, tie rule, source round, membership block, epoch, UID mapping and weights. Legacy temperature-based diagnostic files are ignored by monitoring; historical accepted scores are preserved.
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

## Internal application runner channel

A judge receives `/runner`, a job-local executor channel that is never mounted into the application. For each command, numbered consecutively from 1 without a limit, it publishes `<index>.request.json` with exactly `id`, `argv`, `stdin` and `observation`. Already-frozen jobs from the earlier CLI judge may instead send `args`, which the executor runs as arguments to `/app/main.py`. The executor persists command intent and a private immutable result before exposing `<index>.response.json` containing the original `command` and `result`. An interrupted ambiguous command is unavailable and never repeated. This extends internal execution only; miner submissions and protocol-v1 reports retain their existing schemas.

The executor records the application container identity and permanent stop intent in the parent ledger. Application code sees only its runtime, read-only submission, 256 MiB writable `/app` (also `HOME`) and 256 MiB `/tmp`, without a network or inference variables. Both writable mounts allow execution, so project scripts, built binaries and bundled native extensions can run. Captures are limited to 16 KiB per stream; commands have no individual duration limit; all of a judge's commands share the sandbox budget described below. When the budget ends or the job is stopped, the full container is killed and no further checks run. Parent terminal publication waits for confirmed child termination, including recovery after executor interruption. Runner counters and latency use the existing executor metric transport with bounded `application` operation labels.

The model chooses one action at a time and receives each capture before its next decision. There is no action-count limit. Verification of state changes is required by the judge's instructions and trusted guides rather than by controller-checked metadata: a claimed change counts only after a later observation of the resulting state. The instructions also tell the model to keep every command bounded (background long-lived processes, wrap submitted code in its own `timeout`), because one command that never returns consumes the remaining sandbox time. Only the four command fields cross the runner channel, except for an install request `{id, install: {ecosystem, manifest}, observation}`: with `EXECUTOR_REGISTRY_NETWORK` set, the executor runs a fixed `pip install --only-binary :all: --target /app/.deps` or `npm install --ignore-scripts` while the sandbox is attached to a job-owned network whose only peer is the registry proxy, then restores and verifies `--network none`. Interrupted installs are detached before anything else runs. Without the setting, the capture reports `install_unavailable: true`. When preparing a judge, the executor writes `budget.json` with a frozen `sandbox_deadline`, which ends `EXECUTOR_JUDGE_RESERVE_SECONDS` (default 180, at most half the judge duration) before the judge deadline. All commands share that budget. A command still running when it ends is stopped with its container, and its capture reports `budget_exhausted: true`; commands requested later return the same flag without executing. The judge then finishes with the evidence it has. A `reject` decision, allowed only after an observed command, reports a project that cannot run at all as `Invalid submission: Project cannot run: …`. `assessment.json` retains final criterion evidence references and unassessed reasons before score publication. Missing evidence produces an unavailable evaluation, never a partial or zero score; protocol-v1 accepted reviews remain unchanged.


### Multiple tasks in one round

A directory-backed round freezes every discovered non-hidden JSON task in `plan.tasks`, including its filename, specification and evaluation policy. Each cohort execution carries a `task_id` (SHA-256 of its filename); all miners cover the complete same set, with distinct factory/judge IDs. Task-scoped paths are `<round>/<hotkey>/tasks/<task_id>/{input,output,evaluation}`. Existing single-task records and paths remain readable. Protocol-v1 job requests, manifests and reports retain their existing fields; job IDs and paths distinguish tasks. Each task supplies its own specification hash and rubric. The round’s legacy specification/evaluation fields mirror the first task for compatibility; admission and acceptance use the task-specific fields.

A pending admission persists the complete plan before input publication, preserving task membership and execution IDs across restart. Per-task accepted results remain immutable. Only miners with accepted scores for every task enter the equal-weight mean used by weighting and monitoring. An unavailable judge or an attributed submission failure excludes only the affected miner; weights use the newest completed round when at least one miner was fully evaluated, and never an older round. Round completion logs every validator-side exclusion, and a round without any scored miner, at error level. No task is silently dropped from its denominator.
