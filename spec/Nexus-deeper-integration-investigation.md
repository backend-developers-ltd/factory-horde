# FactoryHorde: deeper Nexus integration investigation

Date: 1 October 2026. Static source review.

## Conclusions

FactoryHorde can reuse Nexus for the validator runtime, typed flows, task composition, actor lifecycle, result-store injection, and chain-driven weighing. The smallest fitting extension is **two NexusTask compositions sharing one file-communicator implementation, a small persisted round coordinator, and a result-store adapter over the existing shared directory tree**. The Linux host executor remains one standard-library-only Python file. No new queue service, general workflow engine, HTTP executor, object store, or durable event-log framework is justified by this prototype.

The seven investigations establish these boundaries:

1. **Long-running file jobs fit the public communicator interface.** A handler can publish a request, return without a result, and emit a correlated result later. Do not put an hour-long polling loop inside the synchronous embedded communicator.
2. **Nexus supplies flow/context primitives, not the FactoryHorde stop barrier.** Persist the cohort and job IDs before dispatch. Factory exit confirmation and cancellation-before-start remain executor/protocol responsibilities; the coordinator enforces the generation/evaluation/no-overlap rules.
3. **Persistence is an extension point, not an enabled default.** Both shipped context and result defaults are in-memory. The public result API queries by task name/result ID or completion epoch; it has no latest-completed-round query and no execution-id deduplication guarantee. Use the shared tree as the durable authority and make Nexus task records a view of it.
4. **“Miner Nexus Task” and “Validation Nexus Task” are recipes, not separate specialized public classes.** The actual generic task still requires a neuron-shaped route. Public NoopRouter bypasses network routing, which is adequate here if true miner identity, image, round and job IDs live in the payload.
5. **Reuse weighing callbacks and Pylon commitment APIs, with small project adapters.** Do not copy the demo’s prior-epoch score policy. A newly found documentation discrepancy matters: the inspected Pylon service awaits commitment submission/retries, although its client docstring says submission is merely scheduled. Keep explicit read-back and version-specific timeout handling.
6. **Extend the existing installer/updater and scrape configuration.** They do not already install a host executor, mount a shared root, or atomically replace the executor. No automatic rollback is needed.
7. **The current localnet is useful infrastructure, not the finished prototype topology.** Preserve chain bootstrap, isolated wallets, fixture identities and independent Subtensor verification. Replace persistent HTTP fixtures with one-shot image submissions and actual factory/judge execution.

The implementation blockers are concrete: select compatible Nexus/Pylon revisions and consume the separately planned mechanism fixes; implement the file executor/protocol, persisted round state, and result projection; then verify the complete path on Linux localnet. A generic durable context backend, arbitrary-target framework refactor, universal barrier node, or new scheduler service is optional upstream work, not a prerequisite for this prototype.

## Evidence scope and notation

The full initial prototype specification and knowledge-base concept map were read before investigation. Applicable repository instructions were read. This report does not edit either document or any repository file. There were no Python imports, environments, package installs, tests, containers, services, network research, or chain operations. Test references below describe assertions in test source, **not passing test runs**. Source behavior is distinguished from documentation and recommendations.

All three working trees returned empty `git status --short` at inspection. Git initially rejected repository ownership; per-command `safe.directory` arguments enabled read-only metadata inspection without changing global Git configuration.

| Prefix used below | Exact local root | Inspected HEAD |
|---|---|---|
| F | `~/repo/factory-horde` | `06795f7f0438b73f2695d4cae9cd365d7d4fc34c` |
| N | `~/repo/bittensor-nexus-library` | `6e7b1a05c8c401aca9dbc63ac4181df856b899a3` |
| P | `~/repo/bittensor-pylon` | `d1e881c6784df369af7f24184eadf0722c7b33c8` |

An evidence locator such as `N/src/nexus/_internal/core/runtime/nexus_task.py:67` means the exact file beneath the N root at the HEAD above, with a one-based line number. Private paths are cited to establish behavior; **application imports must use `nexus.v1`**, not these implementation paths.

**Version boundary:** `F/validator/uv.lock:64–81` locks Nexus to `e1f0b682968a09742e82215d9a512572ba33b7e5` and Pylon client `2.1.0`; it does not select the sibling checkouts above. The locked Nexus Git object is available locally and its public export file was inspected read-only. Core extension points such as NexusTask, ExecutorCommunicator, CommunicatorActor, NoopRouter, TaskResultStore/Provider, Producer/ProducerActor and SubnetBuilder exist there too. However, its export file lacks current `Targets`, `ParentContextSnapshot` and `PylonClientSettingsMixin`. A static diff from that commit to N HEAD changes 24 Nexus source files, including flow, context, routing and provider code. Therefore current primary/tap wiring and all detailed behavior below are recommendations for a **compatible selected revision incorporating these interfaces**, not a claim that the generated lockfile already works.

The deployed Pylon image is separately pinned by digest in `F/envs/deployed/docker-compose.yml:3–4`, with a comment identifying 2.0.0; localnet names `backenddevelopersltd/bittensor-pylon:2.1.0` at `F/localnet/compose.yml:14`. Neither identifies P HEAD. Reconcile this matrix through the planned dependency work; do not repair it by importing private modules or silently using sibling source.

Classification used throughout: **reusable now** means implemented in the inspected source, subject to the version boundary; **extend existing** means implement against a public hook; **project-specific** means required FactoryHorde policy/protocol; **upstream prerequisite** refers to the separately assigned compatibility/mechanism work; **unverified** means runtime, external or deployed-version behavior not established here.

| Concept | Reusable now | Extend existing / project-specific | Prerequisite or unverified boundary |
|---|---|---|---|
| 1. Task lifecycle | NexusTask and communicator/actor event contracts | File communicator and permanent stop protocol | File transport and Docker lifecycle need Linux verification. |
| 2. Round barrier | ProducerActor, flows, contexts and lineage | Persisted cohort, wall-clock ticks and confirmed-stop gates | Current primary/tap surface requires compatible selected Nexus. |
| 3. Durability/retries | TaskResultStore/Provider and RetryStrategy hooks | Canonical file-backed projection, stable IDs and startup reconstruction | No shipped durable default or automatic exactly-once replay established. |
| 4. Generation/judging | Two generic tasks, NoopRouter and payload conversion | Typed factory/judge payloads and stage linkage | Neuron-shaped targets remain an API constraint; generic target refactor is optional. |
| 5. Weights/commitments | Weighing callback and actual Pylon read/write APIs | Round query, softmax, empty-result gate and encoding/read-back adapter | Planned dependency/mechanism fixes; runtime payload limits and chain evidence. |
| 6. Deployment/observability | Installer/updater layout, host/service scrape stack and logging | Executor/unit/root installation, atomic update and project metrics | Linux permissions, service readiness and update behavior need demonstration. |
| 7. Localnet | Subtensor/Pylon bootstrap, isolated wallets and fixture setup | Container submitters, factory/judge profiles and independent acceptance capture | Selected runtime mechanism support and full end-to-end execution remain unverified. |

## 1. Nexus task and communicator lifecycle

### Source findings

| Evidence | What the implementation establishes |
|---|---|
| `N/src/nexus/v1/__init__.py:8–139,141–229` | Public exports include NexusTask, ExecutorCommunicator, CommunicatorActor, Actor/ActorBuilder, Context, SendEvent, ReceiveEvent, NodeSinks/NodeSources, Producer and stores. Custom transport does not require private imports. |
| `N/src/nexus/_internal/actors/executor_communicator/base_communicator.py:32–77` | The node accepts `Routed[Input]`; its processed source carries `ProcessedInput[Routed[Input], Output]`. Processed output includes success or executor failure; a separate error source represents framework failures. It is a logical transport contract, not HTTP-specific code. |
| Same file `:98–150,152–171,200–201` | CommunicatorActor saves original input in context, calls the subclass handler, constructs correlated completion/failure events by context ID, and supports later emission through the bus. |
| `N/src/nexus/_internal/core/runtime/actor.py:56–95` | Each actor handles one incoming event at a time. Handlers return a SendEvent or tuple of events; an empty tuple produces no downstream event. Unexpected handler exceptions are logged by the actor loop rather than automatically converted into a task outcome. |
| `N/src/nexus/_internal/actors/executor_communicator/embedded_executor_communicator.py:59–69` | The executor callable is invoked synchronously inside `handle_input`. Blocking there blocks subsequent input on that actor. Exceptions become executor failures. |
| `N/src/nexus/_internal/actors/executor_communicator/async_http_neuron_communicator.py:205–290,324–347` | The HTTP implementation owns background sender, callback and timeout lifecycles and completes work later. This demonstrates the supported asynchronous pattern; it does not implement the selected file protocol. |
| `N/src/nexus/_internal/actors/executor_communicator/pending_requests.py:14–58,61–87` | Public pending HTTP records contain request ID, context ID and expiry. The interface provides put/pop/pop-expired, not enumerate/recover-all. The default is an in-memory locked dictionary. |
| `N/src/nexus/_internal/actors/executor_communicator/timeout_sweep_runtime.py:96–107` | Expiration pops pending records and emits RemoteResponseTimeoutException. There is no external cancellation or stop-confirmation operation. |
| `N/src/nexus/_internal/core/runtime/nexus_task.py:67–81,161–192` | NexusTask wires payload creation, routing, timestamping, communicator, conversion, result persistence and retries. It has no independent task timeout constructor parameter: timeout behavior comes from components such as the communicator. |

`N/tests/test_executor_communicator.py:131–211` contains HTTP completion/failure/no-callback-timeout cases; `:260–308` covers embedded success/failure. These establish intended interfaces and error distinctions only. They contain no Docker stop barrier or file transport acceptance test.

### Minimal file adapter

**Extend existing:** implement a small `FileExecutorCommunicator` node using public ExecutorCommunicator and ActorBuilder, with a CommunicatorActor subclass. Use the same implementation for generation and judges, with distinct node IDs and typed payloads. Keep Docker operations entirely on the host.

A practical shape avoids an additional thread pool inside the validator:

1. The input handler validates the already-persisted job identity, checks for an identical existing request/final outcome, publishes the immutable request atomically if needed, remembers the current runtime context association, and returns `()`.
2. Add a typed poll sink to the node; include it in NodeSinks and the actor’s handler map. A small ProducerActor supplies periodic ticks. Poll handlers inspect a bounded set of job-status files and return completion events for the corresponding job contexts. Five outstanding jobs need neither one actor per miner nor one blocked thread per execution.
3. Keep only reconstructible `(job ID, runtime context ID)` associations in memory. After validator restart, the coordinator rebuilds subscriptions by resubmitting the same persisted jobs to fresh contexts. The adapter reads or resumes observation of existing requests; it never invents a new execution ID.
4. Persist authoritative file outcome/report acceptance before considering downstream completion handled. Use the public superclass completion helpers or construct the exported event/data types; do not import private HTTP sender/sweep classes.
5. Catch/report polling and file-validation failures explicitly. A raw exception merely logged by Actor is insufficient for round accounting.

An actor-owned polling thread is also supported by the lifecycle pattern, but is unnecessary for approximately five file observations per cycle. File I/O must stay short; registry pulls, Docker waits and grace periods remain in the executor’s standard-library concurrency.

**Project-specific:** deadline and stop handling. At the generation deadline, persist stops and continue observing. An expired task, missing status, failed Docker query or stale executor heartbeat is an **unconfirmed** workload, not a confirmed failure that can be judged. Confirmed nonzero exit, confirmed cancellation and protocol/framework error should remain distinct in the durable records. Whether a confirmed terminal failure is carried as a typed outcome or an executor exception is a small adapter choice; scoring eligibility must never be inferred merely from the Nexus success branch.

**Unverified:** atomic publication/permissions on the selected Linux filesystem, actor polling fairness under load, late observation deduplication and shutdown behavior. None requires a new generic queue.

## 2. Round coordinator, fan-out/fan-in and stop barrier

### Source findings

`N/src/nexus/_internal/core/runtime/actor_patterns.py:49–105` provides ProducerActor: a lifecycle-bound producer thread yields events, with a new context per product. Its documentation explicitly permits an event-based polling sleep with stop signaling. There is no exported wall-clock interval scheduler or persisted application-round timer in the inspected public index/source. BlockBeatNode and EpochBeatNode are chain clocks; they do not substitute for the independent two-hour round.

`N/src/nexus/_internal/core/runtime/event_bus.py:88–118` implements one primary target plus isolated tap contexts. This is useful for ticking independent consumers and observability. It does not enumerate a miner cohort or gather an expected number of job completions. A tap broadcasts the same payload to a configured sink, not one dynamically chosen miner per event.

`N/src/nexus/_internal/core/runtime/context_store.py:432–500` permits child contexts with one or several parents. Parent snapshots are available through `Context.copy_parent_context_snapshots` at `:198–207`; multi-parent contexts do not merge user data. Test source `N/tests/test_context_store.py:260–285` explicitly asserts empty child payload/user data and separate parent snapshots. `N/tests/test_runtime.py:339–486` checks tap isolation and snapshot timing. This is lineage support, not a durable membership/barrier implementation.

`N/src/nexus/_internal/actors/task_result_sampler.py:50–99` makes EveryTaskResultSampler emit singleton batches immediately. It is not a wait-for-all coordinator and would violate the agreed stage gate if connected directly from early factory success to judging. `ForkActor` at `N/src/nexus/_internal/core/runtime/actor_patterns.py:128–144` selects one of two outcomes; it is not dynamic fan-out.

### Recommended round state machine

**Extend existing:** a Producer/ProducerActor for wall-clock ticks, and an Actor/ActorBuilder node with tick and task-outcome sinks. The actor owns round policy. It loads a small round repository from the shared tree on startup and uses normal Nexus events to submit factory/judge tasks. No standalone scheduler outside the actor runtime.

**Project-specific durable membership:** persist round ID, frozen cohort and digest references, absolute deadlines, factory/judge IDs, stage, accepted score references and unresolved jobs before corresponding side effects. The expected membership is this frozen list, not the current metagraph or a count of callbacks. Create a distinct child context for each dispatched job when useful; correlate and deduplicate by durable job ID, not context ancestry or callback count.

| Persisted phase | Required behavior |
|---|---|
| Generation | Concurrently publish every eligible factory request. Early terminal results are recorded, but do not launch judges before the evaluation stage. |
| Stop confirmation | Publish stop intent for every not-yet-confirmed-terminal factory. A pending/pulling job is permanently forbidden to start after its stop request. Graceful stop can last up to 60 seconds, then force; all stops proceed concurrently. |
| Evaluation | At the evaluation gate, judge only confirmed-stopped eligible outputs. Unknown factory jobs remain recorded as unresolved even if their outputs are excluded. |
| Judge finalization | Accept a judge report only after its container is confirmed stopped. Apply the configured judge deadline/stop policy and persist accepted scores once. |
| Completed / unresolved | Persist results separately from permission to start the next round. A round can have usable scores from confirmed jobs while unresolved workload still blocks the next generation round. |

A timeout changes a monitoring/eligibility decision; **stop confirmation changes whether execution can still modify output or overlap the next round**. Keep those facts separate. A cancelled-before-start job needs executor acknowledgement that creation/start can no longer occur, including a pull worker finishing late. Do not equate an absent container at one instant with durable cancellation while startup work is still authorized.

One coordinator repeatedly reconciling five jobs is simpler than a reusable multi-parent gather subsystem. Multi-parent contexts are optional tracing lineage. The full round membership and gates belong in round.json (or its chosen equivalent), not in an assumed persistent Context.

## 3. Durable records, queries, retries and restart

### What is actually durable

| Evidence | Consequence |
|---|---|
| `N/docs/nexus.md:96–112,135–143` | Documentation describes persistent contexts and results. This is conceptual guidance, not proof of an installed disk backend. |
| `N/src/nexus/_internal/actors/task_result_store_provider.py:6–22` | Default provider returns a module-global InMemoryTaskResultStore. |
| `N/src/nexus/_internal/core/runtime/subnet_runtime.py:107–123` | SubnetBuilder accepts `context_store=`, but otherwise creates an in-memory persistence instance. |
| `N/src/nexus/_internal/nexus_validator.py:48–56,112–125` | NexusValidator constructs SubnetBuilder without injecting a context store. Its constructor accepts settings, not a persistence backend. |
| `N/src/nexus/_internal/core/runtime/context_store.py:44–66,325–425` | ContextStorePersistence is a public storage seam; recover_from replays entries and returns context_store plus last_messages. It is not a bundled disk implementation. |
| `N/src/nexus/_internal/core/runtime/subnet_runtime.py:39–46,152–193` | Runtime startup starts actor/bus threads; builder construction does not automatically consume RecoveredContextStore.last_messages. |
| `N/src/nexus/_internal/core/runtime/event_bus.py:88–92` | pass_message_downstream documents a recovery replay use, but caller-level orchestration is still needed. |
| `N/tests/test_context_store.py:288–334,377–385` | Recovery tests reuse in-memory persistence objects; the last-message test includes an unresolved replay FIXME. This does not demonstrate process-restart exactly-once delivery. |

The source inventory contains interfaces and in-memory implementations, not a supplied SQLite/file/Postgres result or context backend. Building one for arbitrary Nexus objects would add serialization, schema evolution, replay and atomicity responsibilities beyond this prototype.

### Result identity and query scope

`N/src/nexus/_internal/core/runtime/task_result_store.py:71–169` defines thread-safe hooks:

- `add_successful_task_result(ctx, task_name, result)` and `add_executor_failure(...)`;
- `get_task_result(task_name, task_result_id)`;
- successful/failure queries for a task name and **completion epoch**;
- convenience counts grouped by `result.target.hotkey`.

There is no round ID filter, latest-completed-round selector, global chronological query, stable external job key or upsert contract. The in-memory implementation assigns `uuid.uuid7()` on each add (`:216–236,251–271`), so replaying an add produces another result ID. Task name identifies a task category; it is not a unique execution ID. Completion metadata records the timestamping actor’s observation, not Docker’s true start/finish (`TaskResultBase`, `:45–61`).

**Extend existing:** supply the same project TaskResultStoreProvider to both NexusTask instances and WeightSetterNode. Implement the public TaskResultStore methods over canonical per-job records in the shared tree. Persist a stable TaskResultId mapping for each `(task name, job ID)` and return the existing record for an identical replay. Treat conflicting content as an error. The adapter may build rebuildable in-memory indexes for epoch queries, but disk records remain authoritative.

Replay equivalence compares immutable job parameters and accepted outcome/report identity. A fresh observation context or new framework timestamp is not a new execution; return the previously stored record and preserve its original metadata instead of overwriting it or treating those observation-only changes as a conflicting job.

A small project method such as `latest_usable_completed_round()` belongs to that same round repository; it need not become a new generic Nexus query API. The weighing callback can close over the typed repository. Keeping a canonical Nexus result projection beside its job record is acceptable; maintaining a separate database with independently editable scores is unnecessary.

Suggested authority boundaries:

| Record | Writer and authority |
|---|---|
| Round plan/stage and immutable requests/stops | Validator; authoritative intended membership, identity and permission to execute. |
| Status and Docker evidence | Executor; authoritative observed lifecycle, with Docker reconciliation after restart. |
| Raw judge report | Judge output, untrusted until validator validates identity, structure, finiteness, range and stopped state. |
| Accepted score / typed task record | Validator/store adapter; accepted once and thereafter immutable for that judge job. |
| Latest usable round selection | Derived from completed round records; an optional cached pointer must be rebuildable. |

### Retry is not re-execution permission

`N/src/nexus/_internal/actors/retry_strategy.py:103–166` re-emits original input after failure, with delay timers. `N/src/nexus/_internal/core/runtime/nexus_task.py:185–192` wires payload, routing, communicator and storage errors back through that retry path. `ExecutorFailureTaskResultStorer` at `N/src/nexus/_internal/actors/task_result_storer.py:180–221` first stores a failure and then emits RetryTaskAfterExecutorFailureException. The failure branch can therefore coexist with a later success; an intermediate failure is not an aggregate round completion signal.

`N/tests/test_nexus_task.py:712–775` explicitly expects a stored failure, a subsequent success and two communicator attempts. Converter failure is different: `:409–536` tests error without retry. Storage failure behavior is tested in `N/tests/test_task_result_storer.py:187` onward. These sources do not claim deduplicated Docker execution.

**Recommended first-version policy:** configure task `RetryStrategy(max_attempts=1, delay=...)`; implement transient file-read/poll retries and restart reconciliation using the same job ID. If task retries are enabled later, allocate job IDs before entering NexusTask and preserve the frozen image/paths/identity on every retry. A retry after storage failure must read the existing final execution/report, not run Docker again.

Executor idempotency remains necessary even with one task attempt: validator restart reissues observations; the process can crash between publication and acknowledgement; Docker create/start can be interrupted. Deterministic names/labels, immutable requests, durable stop precedence and retained final statuses enforce the existing specification. A report lost after a judge exits is a failed evaluation, not permission to redraw by rerunning that judge. A subsequent round gets genuinely new IDs and output directories even if its digest is unchanged.

### Restart without a general context backend

Use fresh ephemeral Nexus contexts after restart; reconstruct their work from round/job files. No unique business state may exist only in contexts, queued events, timers, pending dictionaries or cached block beats. Reconcile in this order: unfinished round and unresolved old jobs; permanent stop intents and executor observations; accepted scores and task projections; missing publications/subscriptions using existing IDs; then eligibility for a new round.

If full Nexus graph replay later becomes required, public ContextStorePersistence plus SubnetBuilder(context_store=...) is the starting point. It requires explicit replay handling and serialization tests; subclassing private NexusValidator._build_runtime is not a clean public injection hook. This is optional framework investment.

### Chain-clock coupling to preserve as a limitation

`N/src/nexus/_internal/actors/timestamper.py:32–35,158–192,194–242` buffers results until the first BlockBeat. The warning/error thresholds are one/five minutes; the age is measured from **processing start**, and old entries can be dropped when handling another output before any beat. It is not a one-hour job timeout. After a beat has been seen, the latest cached beat is used (`:244–261`), without proving its freshness.

`N/tests/test_timestamper.py:47,154` tests waiting and the five-minute drop behavior. Gate new dispatch on initial chain readiness, retain durable terminal outcomes independently, and reconcile task projections after temporary loss. **Do not make Nexus result emission the sole authority for the stop gate or accepted-score survival.** This design contains the limitation without making another framework fix a prerequisite.

## 4. Generation and validation task composition

**Source versus recipe:** `N/docs/nexus.md:165–194` describes miner/validation recipes. The public index exports one NexusTask class, not MinerNexusTask or ValidationNexusTask. The cat-images demo constructs both stages as NexusTask (`N/demos/cat-images/cat_images/validator/validator.py:73–105,111–135`). Its separate stages and shared weighing integration are reusable composition patterns; its S3 URLs, HTTP callbacks, OpenRouter credentials and immediate validation are irrelevant here.

The implementation is less generic about targets than its prose:

- `N/src/nexus/_internal/core/runtime/nexus_task.py:54,73` requires NeuronRouter.
- `N/src/nexus/_internal/actors/neuron_router.py:54–57` makes Routed.target a Neuron.
- `N/src/nexus/_internal/core/runtime/task_result_store.py:53` stores that Neuron as target.
- `NeuronRouterActor._transform` fetches recent neurons and selects afresh (`N/src/nexus/_internal/actors/neuron_router.py:143–171`); ordinary round-robin retry can change the target.
- NoopRouter and its actor bypass discovery and attach a generated `local-neuron` (`same file :228–265`).

**Recommended minimal choice:** use NoopRouter for both file-job tasks and put `miner_hotkey`, `round_id`, `job_id`, immutable digest, deadlines and paths in their typed payloads. The target is the local execution facility; the payload names the miner whose output is being evaluated. The file communicator ignores the synthetic axon. Weighing must use the persisted application identity, never `result.target.hotkey` or the generic count-by-target helper.

An alternative is a small router that attaches the frozen real Neuron from the round snapshot for factory tasks, while still executing locally. It preserves compatibility with target-based analytics but adds a project router and two notions of target (miner attribution versus execution facility). It is optional, not needed to launch committed-image jobs. Do not use RoundRobinNeuronRouter for already-assigned round jobs.

Use two stable task names, for example `factory-generation` and `factory-evaluation`, with separate job IDs and typed outcome conversion. Link them through the persisted coordinator gate, not direct success-to-judge chaining. Keep the judge in its operator-selected container. EmbeddedExecutorCommunicator is suitable for short pure transformations, not replacement of the required judge container.

The demo’s `EveryTaskResultSampler` wiring (`validator.py:155–157`) deliberately validates each mining result immediately. FactoryHorde needs no sampling or batching for the small frozen cohort. Reusing that sampler would add no value and would obscure the stage barrier.

## 5. Weighing, commitment discovery and submission

### Weighing API and latest completed round

`N/src/nexus/_internal/actors/weight_setter.py:27–42` defines `WeighingFunc = Callable[[WeightsCalculationBundle], Mapping[Hotkey, Weight]]`; the bundle contains epoch and tasks_result_store. WeightSetterNode accepts weighing_func, Pylon provider and result-store provider (`:60–71`). Its actor calls the callback, submits the returned mapping and emits success (`:86–109`). The callback is ordinary actor-owned code and may use the same project repository and a Pylon client for current registration checks.

**Project-specific callback:** choose the newest completed round containing usable accepted scores; apply the specified proposed empty-round fallback; filter against current registered hotkeys; use stable softmax with agreed temperature 0.1; exclude failed/ineligible evaluations and renormalize. Use round order/identity/completion status, not file modification time or an assumed previous chain epoch. Current UID lookup is Pylon’s job: `P/pylon_service/pylon_service/api/_unstable/tasks.py:348–364` maps hotkeys to current UIDs and skips missing hotkeys. Preserve hotkeys in durable records so a UID reassignment cannot transfer an old score to a different miner.

The demo weighing algorithm (`N/demos/cat-images/cat_images/validator/weighing_algorithm.py:60–75,107–131`) selects the previous mining epoch and current/previous validation epochs, then uses target-hotkey counts and averages. Its formula and score lifetime do not implement FactoryHorde’s policy.

There is **no skip sentinel** in the inspected WeighingFunc type. WeightSetterActor submits even an empty mapping at `weight_setter.py:93–99`. Add a small readiness gate on SetWeightsBeat before the setter when there is no usable result. Recheck defensively in the callback; a concurrent registration change should fail closed instead of fabricating weights. This can be a narrow coordinator/gate handler returning `()`, not a replacement weight-setting framework.

SetWeightsBeatNode already gates by epoch offset, block cooldown and Pylon status (`N/src/nexus/_internal/actors/chain_beat/set_weights_beat.py:127–169`). Keep this chain clock separate from wall-clock round ticks. WeightSettingSuccess currently follows the Pylon call, not independent chain read-back; Pylon weight services schedule ApplyWeights (`P/pylon_service/pylon_service/api/v1/services.py:47–48`, `_unstable/services.py:231–232`). Acceptance must verify weights on Subtensor directly.

**Upstream prerequisite already assigned:** the inspected Nexus constructors do not accept mechanism_id, the beat queries status without passing one, and WeightSetter uses the v1/default identity put_weights path. Pylon v1 weights explicitly schedule mechanism 0 (`P/pylon_service/pylon_service/api/v1/services.py:48`). Consume the planned fixes rather than copying internals. Afterward configure the same selected mechanism in both status gating and submission. Source-level support in a sibling repository is not proof of the locked/deployed path.

### Actual commitment APIs

Public client exports are in `P/pylon_client/pylon_client/artanis/__init__.py:1–4,30–31`; use PylonClient, Config, CommitmentDataBytes and CommitmentDataHex from this public namespace. The client explicitly builds v1 and unstable namespaces (`P/pylon_client/pylon_client/_internal/client/sync/client.py:49–68`). Prefer explicit `client.v1...` over deprecated `client.identity/open_access` aliases (`:131–147`).

| Operation | Inspected public call and evidence |
|---|---|
| All public hex commitments | `client.v1.open_access.get_commitments(netuid)`; v1 implementation `P/pylon_client/pylon_client/_internal/api/v1/sync/api.py:23–31`. Identity-scoped `get_commitments()` is at `:76–84`. |
| One miner commitment | `client.v1.open_access.get_commitment(netuid, hotkey)` at `:33–47`, or identity `get_commitment(hotkey)` at `:86–99`. |
| Submitter read-back | `client.v1.identity.get_own_commitment()` at `:101–111`. |
| Write | `client.v1.identity.set_commitment(CommitmentDataBytes(image_ref.encode("utf-8")))`, inherited method `P/pylon_client/pylon_client/_internal/api/abstract_sync.py:649–662`. Async equivalents exist in abstract_async.py. |

v1 bulk results map hotkeys directly to hex values (`P/pylon_commons/pylon_commons/v1/responses.py:42–48`). Individual v1 results include commitment fields. Unstable results carry variant objects. Do not write one decoder that guesses both layouts.

Encode UTF-8 bytes once; decode using `CommitmentDataBytes.fromhex(value).decode("utf-8")`. `P/pylon_commons/pylon_commons/types/bittensor.py:56–83` handles optional 0x and enforces nonempty byte values; `_unstable/bodies.py:38–60` treats strings as hex and serializes bytes to hex. A raw `ghcr.io/...` string is not the correct write argument. Client test source `P/pylon_client/tests/unit/synchronous/identity/test_set_commitment.py:26–37` asserts the wire hex representation; its invalid-value cases cover bad/odd/empty hex.

Bulk commitment service reads commitments and registration state at a chosen block and removes unregistered hotkeys (`P/pylon_service/pylon_service/api/_unstable/services.py:171–184`); v1 additionally removes timelock variants (`api/v1/services.py:18–27`). This prototype needs ordinary public hex data, not revealed/encrypted commitments.

For a more coherent frozen cohort, read bulk commitments first, then use their returned block number for `client.v1.open_access.get_neurons(netuid, block_number)` (`abstract_sync.py:130–141`). Persist the block and frozen mapping. This avoids pretending two unrelated latest/recent responses form an atomic snapshot. Runtime support/history availability remains to verify. A small commitment-discovery adapter is needed because Nexus’s Pylon protocols deliberately expose only the methods its stock actors use (`N/src/nexus/_internal/actors/pylon_client_provider.py:28–68`). The narrow protocol does not mean Pylon lacks commitments. Use a typed project provider/client wrapper, not duplicated REST/chain code.

Also avoid blindly copying `miners_only`: it filters solely on absence of validator_permit (`N/src/nexus/_internal/actors/neuron_router.py:45–46`). The template localnet documentation warns miner fixtures can acquire permits. Prototype eligibility needs an explicit identity/role policy appropriate to the small registered cohort, not axon reachability or a permanent miner server requirement.

### Correction: commitment write timing

The client’s set_commitment docstring says the service schedules and immediately returns. **P HEAD implements something different:**

- v1 reuses the unstable write handler (`P/pylon_service/pylon_service/api/v1/api.py:102`).
- The handler awaits the service and returns 201 after it returns (`api/_unstable/api.py:203–210`).
- The service directly awaits `SetCommitment(...)()` (`api/_unstable/services.py:168–169`).
- `BackgroundTask.__call__` awaits its retry loop; only `.schedule()` creates an asyncio background task (`api/_unstable/tasks.py:93–104,135–166`).
- SetCommitment’s attempt awaits a shielded contact write with a 120-second timeout (`:417–422`); the contact sends bytes through turbobt (`bittensor/contact.py:735–736`).
- Service test source checks eventual HTTP success after three mocked attempts and 502 on exhausted failure (`P/pylon_service/tests/unit/identity_endpoints/test_set_commitment_endpoint.py:34–91`).

Thus the specification’s unconditional “Pylon schedules commitment writes asynchronously” statement should become version-qualified. Continue to require read-back of the exact bytes before the submitter reports confirmed publication. An HTTP timeout is ambiguous: the shielded operation may continue, and retries may already be occurring. Read first before resubmitting the same value. Do not assume a background-job status endpoint or HTTP acknowledgement proves finality.

### Payload constraints

The inspected Pylon value/body types require nonempty data and valid hex, but impose no discovered maximum byte length. The contact passes data to turbobt; this review did not inspect the deployed chain runtime/turbobt dependency implementation for commitment field bounds or registration/rate rules. **No numerical chain payload limit is established by this investigation.** Measure UTF-8 byte length, test representative full Docker Hub/GHCR digest references against the selected local runtime, and expose an explicit validated limit once established. Never truncate, hash away the retrievable location, or substitute a tag. This is an early localnet check, not permission to add object storage.

## 6. Installer, updater and observability

### Existing extension points

`F/installer/install.sh:9–16,31–57` creates an operator working directory and .env. `:66–84` downloads/runs the updater and installs a 15-minute cron job. The generated file already defaults NETUID=12 and MECHANISM_ID=1 (`:34–35`); localnet configuration must be explicit and isolated, not accidentally inherit those production defaults.

`F/installer/update_compose.sh:34–69` separates fetching deployment assets from applying them. `:45–60` applies with `cat > destination`, not atomic rename; `:71–80` applies Compose/Alloy and runs Compose up. The updater currently does not install an executor, verify an executor checksum, replace a single Python file, manage a systemd unit, or reconcile protocol compatibility. Its existing download/apply shape is useful but does not already meet the new requirements.

`F/envs/deployed/docker-compose.yml:21–37` starts the validator without a shared-root volume; the image digest is a placeholder. Add the maintained shared-root bind mount here and in localnet. Docker mount source resolution belongs to the host executor; do not send the container-side root as though it were a host path.

**Extend existing, focused changes:**

1. Add host root/container root settings and create control/round directories with agreed ownership. The validator sees the root RW; factory and judge mount permissions remain per job.
2. Install one executor.py plus a unit with an explicit user, configured root, Python path and restart-on-crash policy. Ensure Docker access and Python/Docker/systemd prerequisites. Avoid a venv/package installer for the executor.
3. Fetch the executor, checksum and compatible config from a coherent release selection; verify before application. Put the staged executor file on the destination filesystem and atomically replace it, then restart the service. Existing Docker containers and job files survive the process restart.
4. Serialize overlapping updater invocations and avoid a moving-branch race among assets by resolving a release/commit or using a small manifest. These are small installer mechanics, not a release-management service.
5. Arrange the narrowly required systemd install/restart privilege explicitly: an ordinary user cron process cannot be assumed to control a system service. Document system versus user unit choice and Docker permissions in installer instructions.
6. Keep schema compatibility checks simple and explicit. An incompatible update must stop before replacing files; automatic restoration of old releases remains deferred.

No health-based rollback, rejected-release database, or parallel old/new executor is required. A service restart is not rollback and must not stop/restart every Docker job.

### What monitoring exists

`F/envs/deployed/docker-compose.yml:72–127` supplies cAdvisor, node-exporter, Prometheus and Pylon scraping. The job named “validator” actually scrapes cAdvisor and host node-exporter (`:113–115`), not the validator process. The trace sidecar is commented out and the SDK disabled (`:28–55`); documentation language about an Alloy deployment must not be mistaken for active traces.

The Nexus source search for Prometheus/Counter/Histogram/Gauge/metrics found no actor/engine metric registration implementation; the package depends on `litestar[standard,prometheus]` (`N/pyproject.toml:25`), which alone is not a configured metrics endpoint. Generic repository instructions asking to mirror Nexus metric conventions exceed what is discoverable in this snapshot. Reuse structured logs and available service/host scraping, then add a small project metrics surface rather than inventing nonexistent imports.

**Proposed minimal observability:** event counters and latency histograms for round progression, job terminal outcomes, stop confirmation, report acceptance and weight attempts; gauges for active/unresolved jobs, current phase, age of latest usable round and executor heartbeat age. Keep job IDs, round IDs and miner identities in structured logs/records rather than high-cardinality metric labels. Distinguish “submitted to Pylon” from “verified on chain.”

A simple option preserves the no-HTTP executor: executor publishes an atomic health/heartbeat file; validator reads it and exposes project metrics from an actor-owned endpoint, or publishes Prometheus textfiles for an explicitly configured node-exporter textfile collector. The collector is not configured in current Compose and would need its directory/flag. Choose one path; do not add both for the prototype.

Readiness should require readable compatible records, successful reconciliation, writable shared paths, a recent executor heartbeat/Docker observation, and initial chain connectivity. A stale heartbeat is not proof that Docker stopped. Pausing new rounds because of unresolved work should remain a visible operational state, not be hidden by a green process-liveness check.

## 7. Local verification and fixtures

### What can be reused

`F/localnet/compose.yml:1–34` starts Subtensor and Pylon and mounts isolated localnet wallets into Pylon. It does not contain the validator, submitter or executor. `F/localnet/run-in-tmux.sh:22–39` syncs host projects, runs bootstrap, and starts host validator/miner commands. Therefore it needs adaptation to the agreed containerized validator/submitter topology; its present invocation is not the required milestone.

`F/localnet/bootstrap.py:41,83–110,122–186,188–273,276–321,324–361` supplies isolated wallets, funding, subnet creation/activation, tempo/commit-reveal configuration, registration and validator stake. Keep these local-chain setup responsibilities outside production validator logic. Its inspected code does not configure or prove multiple-mechanism support.

`F/localnet/miners/miner.template.py:169–219` supplies named-instance wallet/funding/registration behavior; `:222` onward serves an axon. Adapt the former and replace the latter with commit/read-back/exit. The current production miner likewise serves HTTP (`F/miner/miner.py:139–217`). No open callback port or serve_axon is needed merely to publish an image commitment. Localnet Pylon currently configures only the validator identity; miner submissions through Pylon require additional identity/wallet configuration or a separate submitter-owned Pylon arrangement. A containerized submitter does not magically inherit permission to write as five distinct miners.

`F/knowledge/localnet/localnet.miner-fixtures.md:24–40` provides useful profile naming and idempotent setup guidance. Its persistent-server/multiprocessing assumptions are template choices to replace. `F/knowledge/localnet/localnet.adapting-to-subnet.md:14–16` requires actual weights and independent Subtensor verification. This is documentation acceptance intent, not evidence that the current scaffold already meets it.

### First milestone topology and fixture matrix

Run on a prepared **Linux** host: Subtensor + selected Pylon service + containerized validator; approximately five registered miner identities using short-lived submission containers; the single-file host executor under systemd; digest-pinned baseline/failure factories and operator judge containers. No inference provider or model credentials. The baseline includes the chosen Pi package/version, invokes verified help/version only, waits roughly 60 seconds and writes main.py/README.md. The judge inspects files and emits one random score in [0,1].

Shorten round durations for demonstrations, but preserve generation, stop confirmation and evaluation gates. Demonstrate concurrent dispatch from persisted membership and capture real Docker/chain evidence, not merely injected successful statuses. Publish the baseline and selected fixture variants under real public Docker Hub/GHCR digests; local image tags alone do not satisfy the commitment path.

| Fixture / intervention | Required observable result |
|---|---|
| Valid baseline digest, both registries | UTF-8/hex commitment round trip, pull exact digest, unique output, Pi non-inference command, approximately 60-second stub and valid judge report. |
| Tag-only, malformed digest, invalid UTF-8/hex, overlong reference | Explicit rejection in the appropriate submit/discovery layer; no truncation, fallback tag or factory dispatch. A malformed-chain fixture may need test-only publication that bypasses normal submitter validation. |
| Missing public digest / failed pull | Explicit infrastructure/pull result, no successful score; other miners proceed. |
| Immediate nonzero exit / missing files | Confirmed stop and classified failure; no invented zero-success score. |
| Factory that ignores graceful stop | Grace then force, recorded Docker terminal state before any judge can read the output. |
| Slow pull with stop arriving before start | Persisted cancellation wins even after pull finishes; no delayed container startup. |
| Judge hangs, exits badly, emits wrong identity, NaN, infinity or out-of-range score | Stop/confirm as required; reject report; no score redraw under the same job ID. |
| Executor killed after create and before start | Restart reconciles authorization/deadline/stop intent; never reruns an exited or finalized job. |
| Validator killed after request publication or report acceptance | Resume same round/job IDs, return existing task projection, preserve accepted random score. |
| Docker temporarily unreachable / executor heartbeat stale | Unknown remains unknown; judging and subsequent round rules remain enforced. |
| Empty round / disappeared registered hotkey / equal successful scores | Exercise the proposed fallback, exclusion/remapping and softmax edge cases separately from random fixture scoring. |
| Commitment changes mid-round | Active round keeps its frozen digest; next round discovers the change. |

Direct chain evidence should include the subnet/validator identity, committed bytes/hotkeys, sampled blocks, mechanism-specific submitted weight vector and current UID mapping. Compare to persisted accepted scores and softmax output, allowing for chain encoding/constraints rather than expecting byte-identical floating-point values. Verify mechanism-0 non-interference when running mechanism 1. If the selected local runtime lacks the required mechanism support, state that limitation and use a capable local runtime before claiming that acceptance item. Neither a successful Pylon status call nor a green WeightSettingSuccess event establishes the independent chain result.

## Minimal composition using supported interfaces

The following is a design outline, not executable code or a claim of passing integration:

```text
NexusValidator / public Nexus runtime
  WallClockProducer (Producer + ProducerActor)
    ticks -> RoundCoordinator (Node + ActorBuilder + Actor)
          -> generation FileExecutorCommunicator.poll
          -> evaluation FileExecutorCommunicator.poll

  RoundCoordinator.factory_job -> NexusTask("factory-generation")
    RetryStrategy(max_attempts=1)
    NoopPayloadCreator / typed project payload creator
    NoopRouter
    FileExecutorCommunicator
    typed outcome converter
    shared file-backed TaskResultStoreProvider
  task outcomes -> RoundCoordinator (record; do not start judge immediately)

  RoundCoordinator.judge_job, only after evaluation gate
    -> NexusTask("factory-evaluation") with same component pattern
  task outcomes -> RoundCoordinator -> immutable accepted score/round completion

  subnet_clock -> SetWeightsBeatNode -> usable-result gate -> WeightSetterNode
    weighing callback reads the same round repository
    selected mechanism support supplied by the planned upstream fixes

  Every error source -> logger / project outcome handler

Shared tree <-> single-file Linux host executor <-> Docker factories/judges
```

On the current N interface use taps for wall/chain clock broadcasts and a primary for mandatory business transitions. Give every job its own runtime context; never emit five concurrent jobs using one mutable linear context. Wire actual task endpoints so NexusValidator discovers the composition and adds its block-beat connection (`N/src/nexus/_internal/nexus_validator.py:103–125`). Keep mandatory persistence/stop gating on the primary business path; a metrics tap cannot enforce cleanup ordering.

The coordinator reads durable file lifecycle independently of task result delivery. This is not a second competing source of truth: both it and the store adapter interpret the same job/round records. Nexus events are delivery and observation mechanisms, while executor status owns stop evidence and accepted-score records own scoring.

## Concrete specification edits to consider

These are suggested amendments; **the specification was not changed**.

1. In section 10, replace the tentative task/communicator paragraph with the public composition above: two generic NexusTasks, NoopRouter with payload identity, a shared file communicator and a persisted project coordinator. Make clear that miner/validation task names describe recipes.
2. In sections 6 and 8, state explicitly that business job IDs are assigned/persisted before entering the task pipeline and survive transport retry and fresh runtime contexts. Adopt one task attempt initially; re-observation does not authorize re-execution.
3. In section 10, replace any implication of automatic persistence with the actual default/injection boundary. Choose the shared round tree as authority; the TaskResultStore adapter exposes those records and the coordinator rebuilds work on startup.
4. In section 8, distinguish round result completion from permission for another generation round when unresolved old jobs remain. Tie the barrier to executor terminal/cancelled acknowledgement, independently of Nexus timeout or block-stamped result emission.
5. In section 9, explicitly add an empty-result gate before WeightSetterNode; its current callback return type has no skip value. Keep latest completed round selection independent of chain epochs and obtain miner hotkeys from application records when NoopRouter is used.
6. In section 4.2, replace the unconditional asynchronous-scheduling claim with selected-version behavior and the inspected service/client-doc mismatch. Retain exact read-back and ambiguous-timeout handling.
7. In section 4, optionally freeze the cohort using commitment response block plus neurons read at that block; document fallback/availability if the selected client does not support this consistently. Do not assume separate recent/latest calls are atomic.
8. In section 11, identify systemd privilege, atomic same-filesystem executor replacement, coherent update assets and updater serialization as installer additions. Keep rollback deferred.
9. In section 12, explicitly state that the template tmux topology must be adapted, Pylon needs miner identities for Pylon-based submitters, and independent mechanism/chain checks are acceptance evidence. Preserve localnet as the first implementation milestone.
10. Preserve all currently proposed defaults as proposals: filenames, UTC layout, two-second polling, concurrent judges, final judge stop reserve, uniform random distribution and fallback policy. This report does not approve them by describing their implementation fit.

## Prioritized implementation and verification order

| Priority | Deliverable / check | Why it comes here |
|---|---|---|
| 1 | Consume compatible dependency/mechanism work; record Nexus commit, Pylon client version, service digest and local chain image/runtime. | Current lock, sibling source and service versions differ. Avoid implementing against an accidentally unavailable public interface. |
| 2 | Bring up the Linux localnet scaffold and one-shot registered submit/read-back path; check commitment payload size with actual full digest references. | Validates a possible chain constraint before spending effort on orchestration. This is the first slice of the localnet milestone, not deployment. |
| 3 | Implement the single-file executor and canonical protocol/durable IDs; demonstrate factory and judge mounts and termination. | Execution idempotency and cancellation are the essential new behavior. |
| 4 | Wire two NexusTasks, file communicator, shared result adapter and small round coordinator; recover from the shared tree. | Reuses the framework while keeping project policy explicit and small. |
| 5 | Add callback/gate softmax weighing and verify real local-chain weights for the selected mechanism. | Completes commitment-to-execution-to-scoring-to-chain acceptance. |
| 6 | Exercise targeted crash/late-start/hang/no-score cases and the first-BlockBeat/result recovery edge. | Validates the places where source abstractions do not imply operational guarantees. |
| 7 | Finish installer/update/root configuration and minimal health/metrics, then demonstrate executor replacement during running jobs. | Makes the accepted Linux topology reproducible without adding rollback. |

Remaining checks are bounded: selected-version API compatibility; exact commitment byte/rate/registration constraints; verified Pi package/version/help command; Linux Docker platform/user/mount permissions; file crash durability and reconciliation; chain readiness/epoch metadata behavior; report acceptance and stable random-score replay; mechanism-specific local chain evidence; and updater/systemd privileges. These runtime checks remain unperformed.
