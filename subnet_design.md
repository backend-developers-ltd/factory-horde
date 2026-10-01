# FactoryHorde V2 working design

This prototype measures whether a submitted software factory completes the local
orchestration contract. Its random scores deliberately measure no application
quality. The [V2 specification](spec/FactoryHorde-initial-prototype-specification-v2.md)
is authoritative; the [task list](spec/FactoryHorde-v2-sequential-implementation-tasks.md)
tracks implementation and evidence. This document selects defaults, not claims of
working components. The existing rendered scaffold must not be rendered again.

## Roles and placement

| Component | Responsibility | Implementation location |
|---|---|---|
| Miner submitter | Validate and commit a public digest using its own Pylon identity; exact read-back; exit | `miner/miner.py` and `miner/Dockerfile` |
| Baseline factory | Invoke pinned Pi help/version without inference, wait approximately 60 seconds, write fresh greeting project | `miner/factory/` |
| Judge | Check readable `main.py` and `README.md` without executing them; write one uniform random score report | `judge/` |
| Validator | Discover/freeze cohort, schedule, publish jobs/stops, accept scores, calculate weights | `validator/src/validator/` |
| Host executor | Poll files, pull/create/start/inspect/stop Docker jobs, reconcile after restart | `executor/executor.py` (one standard-library-only file) |
| Operator installation | Configure ownership, host paths, executor system unit, Compose and coherent updates | `installer/`, `envs/deployed/` |
| Protocol fixtures | Versioned valid/invalid JSON examples shared by component tests | `spec/fixtures/protocol-v1/` |
| Acceptance/fault tools | Local-chain bootstrap, approximately five miner identities, actual execution and independent read-back | `localnet/` |

These are placement decisions; absent paths are created by their implementation
tasks. Keep the two independent uv projects. Judge and executor add no general
platform package or root workspace. The validator operator owns the judge selection
and execution host; miners supply factory software, not persistent HTTP servers.
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
  -> WeightSetterNode + FactoryHorde softmax callback -> Pylon

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

The installer defaults the absolute host root to the operator home's
`factory-horde/data`, with a separately configured validator mount at
`/var/lib/factory-horde`. Localnet chooses an isolated absolute root. Requests carry
relative locations resolved only against the executor's host root; traversal,
absolute paths and symlink escape fail validation. Docker receives only per-job
input read-only/output writable, or judge specification/submission read-only and
report writable. A job-kind-specific fixed command/mount contract replaces arbitrary
shell commands. Operator configuration supplies target platform, resources and
UID/GID; Linux mount and ownership checks must prove the selected values in task 4.

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
separate unresolved state. Judge acceptance additionally requires confirmed clean
zero exit and a valid report linked to the exact judge/factory/round/miner. Forced,
nonzero, malformed, missing or unconfirmed judge outcomes receive no score.

Confirmed eligible jobs may yield a usable completed round result while another
job stays unresolved. Unresolved factories or judges still hold/skip every new
generation slot until reconciled. Safety comes from durable stop intent and Docker
evidence, not Nexus success branches or callback counts.

## Scores and chain weights

The judge draws once from uniform `[0,1]`; accept one finite score in that range,
including zero, immutably. Report/result-store recovery reads existing evidence;
it never reruns a job or redraws its score. Use the latest valid completed round,
not the previous chain epoch. Empty rounds preserve that history; absent usable
history suppresses submission at a gate before the setter. No fallback invented
scores, empty-map skip assumption or EMA.

Filter accepted scores to currently registered hotkeys; UID reuse must not transfer
scores. Compute `exp((score - max_score) / temperature)` and normalize over eligible
miners only, with finite positive temperature default `0.1`. Failures are excluded,
not assigned zero. Registration changes during calculation require revalidation.
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
from an immutable candidate on clean Linux. Record exact dependencies, digest
references, protocol records, outputs, scores and direct chain evidence outside
source commits. Executor updates serialize, verify checksum/protocol, replace
atomically and restart without rerunning detached jobs; no automatic rollback.

Generic HTTP, validator-only, inference and host-tmux recipes yield to V2. Mainnet,
subnet 12 / Compute Horde changes, public emissions, real generation/quality judging,
private/encrypted submissions, stronger isolation, TEE and scale systems are future
work. A random-score demonstration is neither economic nor security validation.
