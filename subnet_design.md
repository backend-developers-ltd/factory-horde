# FactoryHorde V2 working design

The localnet prototype exercises the orchestration contract with random fixture
scores. In review mode the judge grades each factory's output against criteria
fixed at the start of the round. The [V2 specification](spec/FactoryHorde-initial-prototype-specification-v2.md)
is authoritative; the [task list](spec/FactoryHorde-v2-sequential-implementation-tasks.md)
tracks implementation and evidence. This document records the implemented shape and defaults;
[acceptance evidence](spec/evidence/task17-acceptance.json) states what was exercised.
The existing rendered scaffold must not be rendered again.

## Roles and placement

| Component | Responsibility | Implementation location |
|---|---|---|
| Miner submitter | Validate a public digest and commit its FactoryHorde envelope using its own Pylon identity; read-back of the envelope; exit | `miner/miner.py` and `miner/Dockerfile` |
| Baseline factory | Invoke pinned Pi help/version without inference, wait approximately 60 seconds, write fresh greeting project | `miner/factory/` |
| Judge | Adaptively inspect and test projects in an isolated application sandbox, then grade captured observations against frozen criteria, or draw a uniform random score in explicit fixture mode; write one report | `judge/` |
| Validator | Discover/freeze cohort, schedule, publish jobs/stops, accept scores, calculate weights | `validator/src/validator/` |
| Host executor | Poll files, pull/create/start/inspect/stop Docker jobs, reconcile after restart | `executor/executor.py` (one standard-library-only file) |
| Operator installation | Configure ownership, host paths, executor system unit, Compose and coherent updates | `installer/`, `envs/deployed/` |
| Protocol fixtures | Versioned valid/invalid JSON examples shared by component tests | `spec/fixtures/protocol-v1/` |
| Acceptance/fault tools | Local-chain bootstrap, approximately five miner identities, actual execution and independent read-back | `localnet/` |

These components are implemented. Keep the two independent uv projects. Judge and executor add no general
platform package or root workspace. The validator operator owns the judge selection
and execution host; miners supply factory software, not persistent HTTP servers.
The prototype publishes and accepts GHCR references only, as selected by the user.
The selected Pylon writer permits 128 UTF-8 bytes per complete digest reference.
Factory and judge containers receive neither wallets nor control-root access.

## Nexus composition

```text
actor-owned wall-clock producer
  -> persisted round coordinator -> factory NexusTask
  -> file communicator poll sinks       -> shared-tree result adapter
                                       -> coordinator stop/evaluation gate
                                       -> evaluation NexusTask
                                       -> shared-tree result adapter

Nexus chain clock -> SetWeightsBeatNode -> usable-result gate
  -> WeightSetterNode + FactoryHorde 90/10 callback -> Pylon

shared files <-> host executor <-> detached factory / judge containers
```

Both generic tasks use the same custom `ExecutorCommunicator` / `CommunicatorActor`
implementation, distinct stable node/task names, public `NoopRouter`, and one task
attempt. Real miner hotkey, digest, round ID and business job ID stay in payloads;
the router's synthetic neuron is never score attribution. Input handlers publish or
observe durable requests and return promptly; poll ticks deliver later outcomes.
Docker calls and waits belong solely to the host executor.

One small coordinator owns application timing and a repository over the shared
tree. One thread-safe `TaskResultStore` adapter/provider projects those records into
Nexus queries and is supplied to both tasks and the weight setter. Stable
`(task category, business job ID)` result IDs deduplicate replay. Nexus runtime
contexts may be recreated; no required state depends on in-memory contexts or
callback counts. Errors feed explicit outcome handling and structured log sinks.
Application imports use `nexus.v1` and public Pylon namespaces only. Selected-version
runtime support, including primary/tap flows, is verified by task 2; framework
documentation is not evidence of an enabled durable disk backend.

## Selected protocol and lifecycle defaults

Use protocol-versioned JSON and the V2 illustrated filenames, in UTC:

```text
<host-data-root>/
  control/{requests,stops,statuses}/<job-id>.json
  rounds/<YYYY-MM-DD>/round-<sequence>-<HH-MM-SS>-<unique-round-id>/
    round.json
    specification.md
    <miner-hotkey>/{input,output,evaluation}/
    <miner-hotkey>/result.json
```

The installer defaults the absolute host root to `<installation>/data`; choosing
`<operator-home>/factory-horde` gives `<operator-home>/factory-horde/data`. The validator mount is
`/var/lib/factory-horde`. Localnet chooses an isolated absolute root. Requests carry
relative locations resolved only against the executor's host root; traversal,
absolute paths and symlink escape fail validation. Docker receives only per-job
input read-only/output writable, or judge specification/submission read-only and
report writable. A job-kind-specific fixed command/mount contract replaces arbitrary
shell commands. Operator configuration supplies target platform, resources and
UID/GID; Linux mount and ownership evidence is recorded in tasks 4, 7 and 17.

Persist unique round/factory/judge IDs, cohort, deadlines and complete inputs before
request publication. Validator owns immutable requests/stops, round state and
accepted results; executor alone owns replaceable current status; judge reports
are untrusted until accepted. Use same-directory temporary files, atomic publication
and filesystem durability checks. Retain final evidence. Task 3 defines exact types,
immutability/conflict checks and crash-durability handling.

Poll every two seconds. Dispatch all eligible factories concurrently, with no
deliberate batching; dispatch judges concurrently as well. Docker containers are
detached, deterministically named/labelled by job, and have no automatic restart.
Reconciliation observes an existing execution instead of rerunning it. Permanent
stop intent defeats late pull/start and survives executor replacement. Missing
expected containers or failed Docker queries remain unresolved, never proof of stop.

## Rounds and eligibility

The application clock is independent of chain tempo. Defaults are a two-hour round:
generation at 0–60 minutes, stop confirmation at 60–65, evaluation at 65–120.
Reserve the final five minutes for judge stop/confirmation (judge deadline at minute
115). Graceful stop lasts at most 60 seconds, followed by forced termination and
Docker confirmation. Shortened demo durations preserve all gates. Pull/start delays
consume the shared generation window. Early factory completion never starts judging
before minute 65.

Freeze registered miner hotkeys, image references and chain block/mapping before
dispatch. Prefer commitment-block-aligned membership reads when the selected Pylon
API supports them; disclose weaker semantics if not. Exclude the validator identity
and require a valid submission; lack of validator permit alone does not define a
miner. New commitments affect the next cohort. Initial chain readiness and recovery
of every old unresolved job precede new-round admission.

Chosen eligibility policy: only a confirmed successful factory exit (code zero,
without forced termination or Docker OOM failure) with the required readable output
may be judged. A graceful deadline stop that produces a clean zero exit may qualify;
a confirmed nonzero exit, force kill, failed pull/start or cancelled-before-start
never qualifies, even with partial output. Host/executor uncertainty remains a
separate unresolved state. At minute 65 the coordinator persists each factory's
fixed finish cutoff and pending/timely/late/failed eligibility. A successful Docker
finish at or before that cutoff may be observed later after restart; a finish after
the cutoff is permanently excluded. Pending evidence never extends the cutoff and
unresolved execution still holds future rounds. Judge acceptance also rechecks the
factory finish cutoff and requires confirmed clean
zero exit and a valid report linked to the exact judge/factory/round/miner. Forced,
nonzero, malformed, missing or unconfirmed judge outcomes receive no score.

In fixture mode, confirmed eligible jobs may yield a usable completed round result while another
job stays unresolved. Unresolved factories or judges still hold/skip every new
generation slot until reconciled. Safety comes from durable stop intent and Docker
evidence, not Nexus success branches or callback counts.

## Scores and chain weights

Review mode averages verdicts over the frozen rubric. Explicit fixture mode draws
once from a uniform distribution on `[0,1]`. Both modes persist finite scores in
that range, including zero. The weight gate uses only the newest completed round in
the selected mode; older rounds never stand in for it. A miner without an accepted
score for every task, including one whose reviewer was unavailable, is excluded from
that round while the other miners are still weighted. Without a usable round the
gate skips the weight submission.

After filtering accepted scores to registered hotkeys, give one highest-scoring miner 90% and divide 10% equally among the rest; a singleton receives 100%. Ties, including all-zero scores, choose the smallest SHA-256 digest of compact UTF-8 JSON `[str(round_id), miner_hotkey]`, with hotkey as the final collision tiebreaker. This is the accepted first-version implementation choice. Invalid submissions remain excluded, and registration is revalidated before returning the vector. A review round requires the configured judge image digest. No eligible recipients means a skipped write.

Pass configured `MECHANISM_ID` to both weight-status gating and actual writes; retain
the scaffold default `0` and explicitly exercise `1` on a capable local runtime.

## Acceptance and operational boundaries

Use common application images/Compose and the same systemd executor in localnet
and deployment. Localnet adds isolated wallets, Subtensor and bootstrap only.
Choose minimal monitoring without required unconfigured remote credentials;
tracing remains explicitly disabled until configured. Every new subsystem needs
event and latency metrics plus useful structured failures. Executor stays file-only.

Task 12 demonstrates local-chain weights independently of Nexus/Pylon success
logging. Tasks 13–17 establish failures, restart/update behavior and reproducibility
from an immutable candidate on the existing user-selected Linux VM, using a separate
localnet installation. The user prohibited preparing another VM; no clean-OS proof
is claimed. Record exact dependencies, digest
references, protocol records, outputs, scores and direct chain evidence outside
source commits. Executor updates serialize, verify checksum/protocol, replace
atomically and restart without rerunning detached jobs; no automatic rollback.
The selected implementation publishes `installer/release-manifest.json` and
`executor/executor.sha256`. A configuration branch resolves once to a Git SHA;
all files are validated before replacement under one installation lock. The host
cron runs as the operator with an exact-unit restart grant. System-unit changes
require installation privileges, and failed replacement health remains visible
for ordinary repair. Localnet bootstrap accepts the installer's isolated `.env`
with its own adjacent wallets and registration evidence.

Generic HTTP, validator-only, inference and host-tmux recipes yield to V2. Mainnet,
subnet 12 / Compute Horde changes, public emissions, judge quality calibration,
private/encrypted submissions, stronger isolation, TEE and scale systems are future
work. A random-score demonstration is neither economic nor security validation.

## Behavioral judging extension

Every JSON task discovered in the configured server directory is frozen at admission before discovery. Every miner runs every task in separate factory/judge executions within the round; eligible miners receive the equal-weight mean of their task scores. The host executor owns a networkless application container and a narrow file command channel for each judge. It captures bounded results outside the application, preserves shared disposable state across checks, never replays ambiguous commands, and confirms child termination before parent acceptance. There is no action-count cap or fixed command/model/session timeout; existing round stops remain authoritative. Each factory/judge has a separate internal network connected only to the inference proxy. The [judge guide](judge/README.md) specifies evidence and failure handling. Historical V2 acceptance remains evidence for its original revisions.
