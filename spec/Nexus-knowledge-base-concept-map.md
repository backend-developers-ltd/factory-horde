# Nexus knowledge-base concept map for the FactoryHorde prototype

Date: 1 October 2026. Stage: breadth-first static review, before detailed implementation investigation.

## Result

The agreed prototype is already a strong match for the template's **container submission → validator-controlled execution → separate evaluation → weights** pattern. The most useful additional matches are its delayed-scoring lifecycle, configurable task/validation abstractions, commitment-based discovery, softmax weighting, and existing installation/promotion workflow. These are candidates for reuse; this pass does not establish that a complete filesystem executor, round barrier, or durable restart implementation already exists.

No architecture change is recommended on the strength of this first pass. The next useful investigation is narrow: establish how Nexus tasks and custom communicators represent long-running filesystem jobs, how round state and task results survive restarts, and how a stop-confirmation barrier interacts with task completion and retries. Those questions determine how much code FactoryHorde actually needs.

## Scope and evidence

The authoritative design is [FactoryHorde initial prototype specification](FactoryHorde-initial-prototype-specification.md). The review covers all **46 files** under `~/repo/factory-horde/knowledge`, with the inventory below. Core pattern/workflow documents were read in detail. Large external-subnet examples and peripheral integrations received a structural/concept scan of their headings, mechanisms, use cases, and summaries; this is not an exhaustive verification of every embedded code sample. The two index files were read directly. Existing repository instructions and limited already-available library/documentation evidence supplied context.

This is a map of what the local documentation discusses, not a claim about current external subnets, provider offerings, chain limits, or successfully running APIs. No web verification was needed for this local-document inventory. No environment, package installation, runtime import, test, container, chain write, or repository modification was performed. The prototype specification was not edited. This report is the only new deliverable.

Known Nexus/Pylon dependency and mechanism corrections belong to separate planned PRs. They remain integration prerequisites, not the next FactoryHorde design task.

**Evidence classes used below:**

- **Guidance:** concept, recipe, or example in the knowledge base; executable availability is not implied.
- **Existing integration surface:** a framework interface or template script identified in local sources/documentation, with behavior still to investigate or verify.
- **Project behavior:** agreed FactoryHorde semantics that must be preserved when choosing reusable parts.
- **Deferred:** useful future material, outside this prototype.

## Preserve the agreed scope

The prototype has no inference. Its baseline invokes a verified Pi help/version command, waits about a minute, and writes a dummy project. A separate judge container checks the expected files and produces a random score. Approximately five factories receive concurrent jobs. Submission is a public immutable registry digest commitment; a permanently running miner HTTP service is unnecessary.

The host executor remains one standard-library-only Python file under systemd, using the Docker CLI and filesystem requests, statuses, and stops. Nexus stays inside the validator. Two-hour rounds have generation, stop-confirmation, and judging stages; unresolved live work blocks the next round. Neither generic HTTP examples nor a remote execution recipe justifies replacing this architecture.

The specification explicitly labels several choices as **proposed defaults**, rather than user decisions: exact filenames, UTC storage convention, two-second polling, concurrent judge dispatch, the final five-minute judge stop reserve, uniform random score distribution, and the empty-round/previous-result fallback. The generation timing defaults are configurable, while their staged/no-overlap semantics are requirements. Softmax temperature **0.1** is explicitly an agreed project starting value, not a Nexus default. This review does not silently approve or alter any proposed default.

## Highest-value concept matches

| Prototype concern | Knowledge-base match | Reuse opportunity | Boundary or next question |
|---|---|---|---|
| Reusable factory image, executed by validator infrastructure | `bittensor/container_execution.yaml`; `basilica.containers.yaml` | Adopt the submitted-software lifecycle and explicit cleanup/failure handling. | The recipes use Affinetes/Basilica method calls. They do not establish a ready-made file-volume executor. Keep the selected host executor. |
| Short-lived submission tool; no miner server | `miner.contract.yaml`, `miner.rules.yaml`, container pattern | Commit an artifact reference on change and let validators discover it. | Generic HTTP-miner rules are not the selected communication mode. |
| Digest-pinned factory references | `validator.deploy.md`, pinning rule at line 25 | Reuse the distinction between registry manifest digests and mutable tags, including hash-looking tags. | Apply it to submitted factories as already specified. A digest does not guarantee continued registry availability. |
| Collection now, judging later | `prediction_market.yaml:28–36`; `time_series_forecasting.yaml:27` | Separate dispatch/collection, pending durable records, and later scoring. | Delayed market truth is an analogy for lifecycle separation, not the judge's scoring rule. |
| Factory jobs and separate judge jobs | Container pattern plus validation/task concepts referenced by Nexus docs | Represent separate job identities and results; investigate reuse of task composition. | A judge runs in its own container, not an embedded in-process evaluator. |
| Cohort-wide concurrent dispatch | `basilica.containers.yaml:107` | The recipe demonstrates gathering independent asynchronous executions and handling errors. | Its semaphore/default concurrency is not a new FactoryHorde batching requirement. No durable round barrier is established by `gather`. |
| Stopping and waiting for actual exit | Container timeout/cleanup guidance | Treat cleanup and completion as explicit lifecycle work. | Timeout alone cannot mean stopped. The five-minute barrier, cancellation of pending starts, and restart reconciliation are project behavior requiring deeper investigation. |
| Miner identity and submission lookup | `bittensor.core.yaml`, `miner.contract.yaml:53`, `sdk.quick_reference.yaml` | Use chain identities/metagraph plus separate commitments rather than a new discovery service. | Verify Pylon encoding, read-back confirmation, and payload limits later. Do not copy SDK access into the validator merely because the recipe uses it. |
| Scores converted to weights | `validator.contract.yaml:98`; `incentive.primitives.yaml:110` | Softmax and temperature are already documented; use a project weighing function with Nexus's weight path. | Stable arithmetic, failed-result exclusion, finite score validation, and score lifetime remain project policy. |
| Independent application and chain clocks | `validator.contract.yaml:65`; Nexus epoch-driven weight-setting documentation | Reuse chain scheduling for weight opportunities while keeping application rounds separate. | Do not reinterpret two hours as one chain epoch or discard scores at epoch boundaries. |
| Existing host deployment | `tasks.project-bootstrap.md`; `validator.deploy.md` | Extend build/promotion and installer/update conventions for executor and shared directories. | systemd executor lifecycle/checksum updates are additions, not shipped guarantees. Automatic rollback remains excluded. |
| Local acceptance | `localnet/localnet.adapting-to-subnet.md:14–16` | Preserve direct independent subtensor checks; adapt fixture behavior to container submissions. | The real prototype path uses intentional factory/judge stubs, not mocked execution or invented chain success. |

## Framework concepts to investigate next, without assuming implementation fit

The template's knowledge base mostly describes subnet patterns. Detailed actor/task contracts live in the installed Nexus documentation and source. The local sibling library's `docs/nexus.md` identifies the following promising surfaces. That sibling's current checkout is not automatically the version selected by the generated validator's lockfile.

1. **Typed actors, sources/sinks, and pluggable task composition.** `docs/nexus.md:115–133` describes Nexus Task, routers, communicators, payload creators, result converters, timeout/retry, and result storage. Determine whether one task per factory/judge execution can remain outstanding across file polling without monopolizing an actor thread, and where completion should be emitted. Do not invent a second general-purpose queue before this investigation.
2. **Separate miner work and validation work.** `docs/nexus.md:165–194` explicitly distinguishes Miner Nexus Task, Validation Nexus Task, sampling, and batching. This is a strong candidate for representing generation and judging separately. The miner-specific convenience composition may assume neuron/HTTP routing; the generic task may fit committed images better. That distinction needs source tracing.
3. **Fan-out and fan-in.** Actor source/sink composition is a candidate for distributing a frozen cohort and consuming completions. The application's round-wide barrier still needs durable membership, deadlines, and terminal/unconfirmed states. A broadcast or a Python asynchronous gather is not automatically an idempotent fan-in implementation.
4. **Durable results and query scope.** Nexus documentation describes a task result store and persistence; the earlier static review identified in-memory defaults. Trace actual public storage injection, result keys, query filters, and restart behavior before selecting a backend. For this prototype, durable round/job/report records already live in the shared tree; avoid creating unrelated competing sources of truth.
5. **Retry identity versus re-execution.** Determine how a task retry interacts with the same job ID, pending stop intent, already-created/exited Docker containers, and a persisted random judge score. Transport/status-read retry may be safe; automatically launching a fresh execution or redrawing a score is a different action.
6. **Scheduling and weighing.** `docs/nexus.md:150–163` describes epoch-driven weighting and the developer-provided weighing function. Check where the latest valid completed round is selected and remapped to current registered identities, independently of the two-hour round clock. Known mechanism fixes remain external work.

These are investigation targets, not claims that the library currently provides a filesystem communicator, timer/barrier actor, durable backend, or exactly-once execution.

## Coverage inventory: all template knowledge-base files

Paths in these tables are relative to `~/repo/factory-horde/knowledge`. Relevance is to the initial prototype, not the eventual full product.

### Workflow and local development — 7 files

| Path | Concepts reviewed | Prototype relevance |
|---|---|---|
| `template.bootstrap.md` | Detect unrendered, mixed, or rendered-but-unadapted states; fresh Copier rendering; post-render checks | Already satisfied structurally; continue adaptation without rerendering. |
| `tasks.project-bootstrap.md` | Bootstrap, design, implementation, localnet, build, release gates | Use the sequence; the current specification supplies project requirements. |
| `guidelines.coding-and-qa.md` | Python/uv, strict typing, lint/format/tests, ownership, documentation | Apply to validator/miner projects; standard-library-only executor is the explicit project exception to extra dependencies. |
| `validator.deploy.md` | Separate build/promotion, immutable digests, service-version promotion, branch conventions, tracing | Direct reuse for installer/deployment changes; distinguish documentation from current sidecar configuration. |
| `localnet/INDEX.md` | Components/start order, isolated wallets, chain bootstrap, fixture roles, cache/nonce gotchas | Direct operational guide later; numeric/environment details require integration verification. |
| `localnet/localnet.adapting-to-subnet.md` | Real subnet loop, adapted fixtures, independent chain evidence | Strong acceptance match. |
| `localnet/localnet.miner-fixtures.md` | Behavior profiles, multi-instance identity/funding, local-only fixture distinction | Adapt profiles to valid/broken/hanging image submissions; do not preserve HTTP fixture shape unnecessarily. |

### Core rules, contracts, and operational references — 15 files

| Path | Concepts reviewed | Prototype relevance |
|---|---|---|
| `bittensor/INDEX.yaml` | Category map, task routing, pattern taxonomy, doctrines | Navigation aid; generic doctrines do not override the agreed prototype. |
| `bittensor/subnet.invariants.yaml` | Commodity selection, commitments, chain rate limits, compute placement, container selection | Container/software and publish-on-change match; generic validator-only/remote-compute prescriptions conflict with explicit scope. |
| `bittensor/design_flow.yaml` | Commodity→verification→pattern decision tree; minimal validator responsibilities | Software branch matches; do not reopen agreed stub scoring because this is not an economic-quality evaluation. |
| `bittensor/bittensor.core.yaml` | Identities, hotkeys, UIDs, registration, metagraph, weights | Retain identity-aware records; UID reuse matters when converting stored results later. |
| `bittensor/subnet.lifecycle.yaml` | Registration/activation/configuration, epoch, emission and maintenance concepts | Local setup reference; numeric claims/API spellings are not validated current facts. |
| `bittensor/sdk.quick_reference.yaml` | Queries, transactions, commitments, wallets, response handling | Submission/local bootstrap reference; validator chain access remains through Pylon. |
| `bittensor/btcli.reference.yaml` | Operator commands, wallet/subnet/stake/configuration, diagnosis | Later manual integration support; no commands executed. |
| `bittensor/miner.contract.yaml` | Producer contract, discovery, image references, publish-on-change, non-server patterns | Direct fit for committed factories and one-shot submitter. |
| `bittensor/miner.rules.yaml` | Registration, validation, container submission versus HTTP | Use selected container mode; baseline prohibition is superseded by project requirements. |
| `bittensor/validator.contract.yaml` | Observe→score→aggregate→weights, Nexus mapping, softmax | Direct conceptual fit; no inference that all policy/round logic is supplied. |
| `bittensor/validator.rules.yaml` | Trust, public criteria, deterministic scoring, timeouts, no default EMA | Random score is an explicit plumbing stub; persist once rather than changing it on polls/restarts. |
| `bittensor/incentive.primitives.yaml` | Quality axes, verification categories, softmax, normalization, decay/credibility, simulations | Softmax directly relevant; quality/anti-gaming economics deferred. |
| `bittensor/trust.assumptions.yaml` | Trust boundaries, observable chain facts, copy attacks, identity limitations | Context for future evaluation; no new multi-validator or secrecy requirements. |
| `bittensor/sybil.realities.yaml` | Coldkey limitations, UID/registration economics, duplicate incentives | Deferred economic design; no coldkey deduplication added here. |
| `bittensor/ops.principles.yaml` | Deployment stages, monitoring, updates, recovery, wallet operations | Metrics categories useful; mutable-tag Watchtower recipe conflicts with actual template promotion/update path. |

### Mechanism patterns — 8 files

| Path | Concepts reviewed | Prototype relevance |
|---|---|---|
| `bittensor/container_execution.yaml` | Image is submission; chain discovery; controlled execution, score, cleanup | Primary architectural match; transport details remain project-specific. |
| `bittensor/prediction_market.yaml` | Broadcast, store pending outputs, await later event, score, aggregate | Strong lifecycle analogy for separate generation/judging and persistent pending state. |
| `bittensor/time_series_forecasting.yaml` | Immediate structural checks versus delayed scoring; baseline-relative quality | Useful lifecycle separation; scoring formula not relevant. |
| `bittensor/adversarial_red_blue.yaml` | Sequential generator/discriminator stages and separate roles | Structural analogy only; operator judge is not a competing miner team. |
| `bittensor/data_indexing.yaml` | Committed location, index/content fetch, sampling, deep verification, provenance | Useful distinction between submission discovery and artifact evaluation; storage/scorable-bytes machinery deferred. |
| `bittensor/external_activity_verification.yaml` | No miner server, identity-linked artifacts, eligibility before scoring | Supports non-server mining; GitHub-specific scoring/identity mechanics not adopted. |
| `bittensor/compute_auction.yaml` | Hardware/price bids, verification, auction clearing | Different commodity; do not turn the factory executor into a compute marketplace. |
| `bittensor/capacity_market.yaml` | Orchestrator, usage records, uptime and billing, non-public miners | Responsibility separation is an analogy; socket service, GPU proofs and usage economics not needed. |

### Service integration recipes — 5 files

| Path | Concepts reviewed | Prototype relevance |
|---|---|---|
| `bittensor/basilica.containers.yaml` | Container build/load/evaluate/cleanup; local/remote modes; parallel execution | Worth comparing later for lifecycle semantics; no reason to replace the agreed host executor now. |
| `bittensor/chutes.integration.yaml` | Inference hosting, model endpoints, access control, TEE concepts | Excluded: prototype performs no inference. |
| `bittensor/hippius.integration.yaml` | S3/IPFS, provenance, immutable content, encryption, erasure coding | Artifact identity/provenance concepts useful later; shared filesystem is sufficient for this scope. |
| `bittensor/desearch.integration.yaml` | Live web/social search, retrieval, current-context/ground-truth integrations | No initial-prototype use; possible future task research only. |
| `bittensor/dataverse.integration.yaml` | On-demand social queries, bulk collection, asynchronous export, Parquet | No initial-prototype dependency; long-running collection is only a loose lifecycle analogy. |

### External-subnet examples — 11 files

These summarize what the local examples say. Their upstream implementations, current economics, security, and performance were not independently verified.

| Path | Conceptual lesson | Relevance and limits |
|---|---|---|
| `bittensor/example.affine_sn120.yaml` | Discover submitted models, evaluate across environments, aggregate comparative performance | Repeated evaluation of reusable submissions fits; Pareto/winner rules and inference do not. |
| `bittensor/example.numinous_sn6.yaml` | Submitted agent code runs repeatedly; collect predictions, await outcome, score later | Strong lifecycle analogy; gateway, real forecasting, deterministic consensus and rolling-window policy are deferred. |
| `bittensor/example.gradients_sn56.yaml` | Submit training code rather than final model; execution precedes separate evaluation | Strong artifact→execution→evaluation analogy; tournament/hidden-test/ML training scope not adopted. |
| `bittensor/example.kinitro_sn26.yaml` | Packaged agents evaluated against explicit tasks in simulation | Supports reusable asset and separate evaluator; no simulation dependency needed. |
| `bittensor/example.swarm_sn124.yaml` | Packaged policy, generated task, simulation, result measurement | Useful execution/evaluation shape; hidden tasks and winner-take-all are not prototype requirements. |
| `bittensor/example.404gen_sn17.yaml` | Dispatch common prompts, collect artifacts, render and evaluate separately | Useful artifact-producing generation followed by evaluation; visual metrics/inference deferred. |
| `bittensor/example.bitmind_sn34.yaml` | Generator outputs feed later discriminator evaluation; distinct scores/roles | Stage composition analogy; no adversarial marketplace or new miner role. |
| `bittensor/example.nova_sn68.yaml` | Round challenge, submissions, fixed evaluator/oracle, rank and aggregate | Useful common-round/evaluator separation; deterministic scientific oracle does not validate random stub scores. |
| `bittensor/example.zeus_sn18.yaml` | Collect forecasts, obtain later truth, evaluate accuracy and latency | Useful separate clocks/pending results; no forecast or speed bonus introduced. |
| `bittensor/example.lium_sn51.yaml` | Executor hosts, control/verification, workload lifecycle | Host responsibility analogy; hardware rental, collateral and slashing are different product requirements. |
| `bittensor/example.hippius_sn75.yaml` | Artifact storage, off-chain workers, health/challenge records, multidimensional scoring | Persistent artifact and observation concepts useful; distributed storage/compute market deferred. |

## Important mismatches to handle consciously

- **Patterns are alternatives, not cumulative requirements.** The container chapter says the image itself is the submission and the validator runs code. Generic endpoint/HTTP and validator-only rules describe other patterns and conflict even within this bundle. Preserve the explicitly chosen factory and one-shot submission tool.
- **Some examples contradict generic doctrines.** Gradients and Swarm discuss hidden evaluations although generic rules prohibit secret sets. This is evidence to interpret the KB by context, not to import either policy into the dummy judge.
- **Deployment guidance has competing recipes.** `ops.principles.yaml` suggests Watchtower and mutable tags, while the project-specific `validator.deploy.md` describes separate build/promotion and immutable digests. The actual installer has a fifteen-minute cron updater (`installer/install.sh:84`), and updater downloads Compose/Alloy configuration (`installer/update_compose.sh:68–78`). Extend that path; do not add Watchtower or automatic rollback from the generic recipe.
- **Caching an image differs from caching an evaluation.** Basilica guidance recommends caching results for unchanged versions. FactoryHorde can reuse pulled image layers, but a new round still has a new factory job/output/judge result. The same digest is not permission to reuse a previous dummy project as current execution evidence.
- **A failed job is not a successful zero score.** The Basilica recipe suggests score zero on failure; generic normalization mentions uniform zero handling. The prototype explicitly excludes failures before softmax. All valid scores being zero and having no valid scores are different cases.
- **A task timeout is not a container stop.** Preserve actual exit confirmation before judging and before overlapping rounds. Generic timeout/cleanup prose does not establish these guarantees.
- **Historical numeric values are reference material.** Block times, commitment frequency, activation APIs, subnet counts, and current provider offerings can drift. This pass neither relies on them as verified limits nor broadens the task into live network research.
- **Documentation is not capability proof.** Nexus persistence prose, Basilica isolation claims, and operational health descriptions require selected-version source/runtime checks before acceptance. The initial prototype explicitly avoids claiming hostile-public-workload security.

## Prioritized deeper investigations

| Order | Focus | Concrete question to answer | Desired result |
|---|---|---|---|
| 1 | Nexus task and communicator lifecycle | Can filesystem requests/status polling fit the public task/communicator interface while keeping concurrent jobs outstanding? Where are pending callbacks/timeouts handled? | Minimal adapter boundary and exact reusable public interfaces. |
| 2 | Round coordinator and stop barrier | What existing producer/timer, fan-out/fan-in and context mechanisms fit a persisted round with a confirmed-stop gate? | Clear split between reusable nodes and FactoryHorde state machine. |
| 3 | Durable records, result queries and retries | How do public storage hooks, result identity, filtering and restart work? Can persisted report acceptance prevent score redraw and duplicate executions? | One coherent source of truth and explicit retry/reconciliation semantics. |
| 4 | Generation versus validation task composition | Do generic tasks or specialized miner/validation compositions best represent digest jobs and separate judges? | Avoid unnecessary neuron/HTTP assumptions or duplicated framework work. |
| 5 | Weighing input and commitment discovery | How do Pylon discovery/encoding/read-back and Nexus weighing hooks connect to frozen cohorts and latest completed rounds? | Small project-specific adapters; consume external dependency/mechanism fixes. |
| 6 | Installer, updater and observability | Which existing configuration/download/health conventions extend cleanly to shared directories and systemd executor? | Focused installer extension and useful round/job/stop/score/weight metrics. |
| 7 | Local verification fixtures | How should existing fixture profiles publish valid, malformed, failing and hanging containers, while chain evidence stays independent? | Reproducible demonstration plan matching the accepted stub scope. |

The first three investigations are the best return on effort: they answer whether the apparent custom orchestration can be mostly expressed through existing Nexus abstractions. No specification changes are made or implied until those concrete findings are reviewed.
