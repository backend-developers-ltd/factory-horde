# FactoryHorde V2 — sequential implementation tasks

Date: 1 October 2026. Status: implementation plan; no implementation or runtime verification performed.

## Scope and provenance

Implement the [initial prototype specification V2](FactoryHorde-initial-prototype-specification-v2.md). V2 is authoritative for this stage and supersedes the [earlier prototype specification](FactoryHorde-initial-prototype-specification.md). The [original full-product specification](~/repo/factory-horde/spec/draft-specification-2.md) supplies future direction: its encrypted submissions, model proxy, real generation/evaluation, and public deployment are not initial-prototype acceptance requirements. All three specifications and both supporting reports were read completely when preparing this plan.

Supporting evidence: [Nexus concept map](Nexus-knowledge-base-concept-map.md) and [deeper integration investigation](Nexus-deeper-integration-investigation.md). Their source findings are static, version-scoped evidence, not passing runtime checks. In particular, the earlier prototype's unconditional claim that Pylon schedules commitment writes is superseded by V2's inspected awaited-write behavior and version-qualified read-back requirement.

Repository ownership:

| Repository | Role in this plan |
|---|---|
| [FactoryHorde](~/repo/factory-horde) | Own all project implementation: validator adapters, host executor, baseline factory, judge, submission tooling, shared deployment, localnet fixtures, installer and documentation. Start from the existing Copier-rendered scaffold; do not rerun Copier. |
| [Nexus library](~/repo/bittensor-nexus-library) | Reference public interfaces and examples; consume separately planned dependency/mechanism fixes. Do not duplicate those fixes in FactoryHorde or copy private framework code. |
| [Pylon](~/repo/bittensor-pylon) | Reference versioned client/service behavior and consume the compatible client/service releases. This is a separate repository and release surface. |

The scaffold was statically confirmed clean at `06795f7f0438b73f2695d4cae9cd365d7d4fc34c`. Its validator lock selects Nexus `e1f0b682968a09742e82215d9a512572ba33b7e5` and Pylon client `2.1.0`; the reviewed sibling Nexus revision was `6e7b1a05c8c401aca9dbc63ac4181df856b899a3`, and reviewed Pylon was `d1e881c6784df369af7f24184eadf0722c7b33c8`. Neither sibling checkout proves what an installed or deployed dependency supplies. The deployed Pylon digest, localnet Pylon reference, and placeholder validator digest also need deliberate reconciliation.

## Execution rules and ordering

Follow tasks 1–18 in order as reviewable changes. **The first delivery milestone is a complete localnet prototype**, reached in its core form at task 12 and accepted with reproducibility/failure evidence at task 17. No public-chain deployment or emissions change belongs to these tasks. Localnet uses the same application images, Compose services and host systemd executor startup as production, adding local Subtensor, isolated configuration and bootstrap only.

This plan is based on static source inspection. All dependency synchronization, imports, QA, builds, Docker execution, systemd operations, wallet/bootstrap operations and chain checks below are **future work on prepared Linux**. Use each Python project's own environment; there is no root uv workspace. The deployed executor itself stays one standard-library-only Python file, with no package installation or virtual environment. When a task changes code, run its focused tests and the applicable [template QA gates](~/repo/factory-horde/knowledge/guidelines.coding-and-qa.md) on Linux before considering it complete; keep documentation current with the change.

**Critical upstream gate:** task 2 consumes and verifies the separately planned compatibility/mechanism PRs. While those are pending, independent static preparation from tasks 1, 3–5 and executor protocol/fixture design may proceed. Do not claim integrated task completion, implement against unavailable exports, or recreate the upstream fixes to bypass that gate. A sibling checkout is not a dependency-selection mechanism.

Use the template's explore → design → build → verify process and its existing scripts to generate/adapt the project implementation. V2 already supplies the requested design scope; translating it into a compact repository design note does not require a new decision-log, traceability-matrix, or design-approval phase. The coverage checklist at the end is a review aid only. Generic HTTP-miner, validator-only, inference and host-tmux recipes yield to V2's explicit choices.

Recommended choices to concretize in task 1, rather than silently call fixed requirements: V2's illustrated filenames and UTC layout; two-second polling; concurrent judges; final five-minute judge stop reserve; uniform random scoring; latest valid completed-round fallback; `NoopRouter`; one Nexus task attempt; block-aligned membership lookup where supported; and simple updater serialization/coherent asset selection. Defaults remain configurable where appropriate. Required behavior includes two-hour rounds with 60/5/55-minute stages, up to 60 seconds of stop grace, temperature `0.1`, separate judge execution, stable identities and scores, and no overlap with unresolved workloads.

## Phase A — ground the adaptation and select compatible dependencies

### 1. Adapt the rendered scaffold and record the implementation shape

**Objective and scope.** Continue the already-rendered template workflow. Use V2 to write a concise `subnet_design.md` describing the submitted container commodity, intentionally meaningless random scoring, component ownership, actor graph, shared data contract and localnet-first acceptance. Record the chosen proposed defaults above and the concrete treatment of confirmed nonzero/forced-stop output eligibility without weakening V2's failure classifications. Keep this a working design note, not another full specification.

**Owner and references.** FactoryHorde documentation/configuration. Read [AGENTS.md](~/repo/factory-horde/AGENTS.md), [bootstrap tasks](~/repo/factory-horde/knowledge/tasks.project-bootstrap.md), [render-state guidance](~/repo/factory-horde/knowledge/template.bootstrap.md), [Bittensor index](~/repo/factory-horde/knowledge/bittensor/INDEX.yaml), and [design flow](~/repo/factory-horde/knowledge/bittensor/design_flow.yaml). Interpret conflicting generic recipes using V2. Nexus grounding is [docs/nexus.md](~/repo/bittensor-nexus-library/docs/nexus.md).

**Prerequisites.** V2 and existing rendered repository; no runtime setup required for this static task.

**Deliverables.** Compact repository design note; updated root README/AGENTS project identity; a placement decision for the single executor file, judge, factory image assets, protocol fixtures and localnet scripts. Preserve independent `validator/` and `miner/` projects; avoid a new general platform package.

**Completion.** Rendered-file checklist is satisfied without Copier. The design identifies two generic Nexus tasks, one file communicator implementation, one small persisted coordinator, and one shared-tree result adapter as project additions. No component is marked implemented merely because an example or interface exists. It distinguishes miner submitter, factory, judge and validator operator, and leaves no mainnet work in the prototype acceptance path.

### 2. Consume the upstream compatibility and mechanism fixes — integration gate

**Objective and scope.** Obtain the separately planned dependency/mechanism changes, select the compatible Nexus revision, Pylon client/API version, Pylon service digest and local Subtensor runtime/image, and update the FactoryHorde dependency/deployment selections. Verify that mechanism selection reaches both weight-status gating and actual writes. Preserve dependency-age constraints in the project configuration.

**Owner and references.** Upstream PR implementation belongs to Nexus/Pylon owners; FactoryHorde owns consumption and integration. Start from [validator pyproject](~/repo/factory-horde/validator/pyproject.toml), [validator lock](~/repo/factory-horde/validator/uv.lock), [public Nexus exports](~/repo/bittensor-nexus-library/src/nexus/v1/__init__.py), [weight beat implementation](~/repo/bittensor-nexus-library/src/nexus/_internal/actors/chain_beat/set_weights_beat.py), [weight setter implementation](~/repo/bittensor-nexus-library/src/nexus/_internal/actors/weight_setter.py), and [Pylon client documentation](~/repo/bittensor-pylon/docs/CLIENT.md). Private paths explain behavior; application imports use `nexus.v1` and public Pylon namespaces.

**Prerequisites.** Task 1; compatible upstream changes available. If unavailable, record exact missing PR/revision/capability and continue only independent preparation.

**Deliverables.** Reproducible version selection with upstream PR/commit references, project lock updates, pinned service/chain images, and a small selected-version API integration check. Explicitly compare lock, installed package, service image and sibling sources.

**Completion — Linux.** Project-local `uv sync` and public-import/wiring checks pass against the selected dependencies. `NexusTask`, communicator/actor/producer types, result-store injection, selected routing and flow wiring actually exist. Selected mechanism ID is accepted and forwarded consistently by beat/status and setter/write paths, with upstream regression evidence identified. A real mechanism-specific chain proof remains task 12; a signature check is not that proof. Do not repair missing support with private imports or a parallel weight setter.

## Phase B — establish contracts and the early submission slice

### 3. Define the versioned file protocol and canonical round/job records

**Objective and scope.** Implement the small typed validator-side record layer and fixtures that the executor can consume using only the standard library. Persist round/cohort/deadlines and durable unique factory/judge IDs before side effects. Define immutable requests/stops, executor-owned current statuses, untrusted judge reports, accepted results and stable result-projection identity. Choose serialization and thread-safe atomic writes for shared records; use same-directory temporary files, ignore incomplete temporary files, and specify crash-durability handling on the selected filesystem.

**Owner and references.** FactoryHorde validator records, executor contract and tests. V2 §§5–6; [current validator package](~/repo/factory-horde/validator/src/validator); [Nexus result-store contract](~/repo/bittensor-nexus-library/src/nexus/_internal/core/runtime/task_result_store.py).

**Prerequisites.** Task 1; task 2 before dependency-backed execution of tests.

**Deliverables.** Versioned request/status/stop/report/round contracts and representative valid/invalid JSON fixtures. Specify job kind, hotkey, round/job identity, digest/platform, relative paths, fixed mount/command contract, timestamps/deadlines/grace, Docker identity, exit/termination facts, and concise failure reasons. Establish one host data root and a separately configured validator mount path; paths sent to the executor are relative to its host root.

**Completion — Linux tests.** Round IDs cannot collide solely because timestamps coincide. Identical immutable publication is idempotent; conflicting reuse fails. Readers never treat temporary/partial writes as committed records. Tests reject path traversal, absolute-path escape, symlink escape, unsupported versions and mismatched identities. Terminal/failed/cancelled status, confirmed stop, application success, and unresolved observation remain distinct. Input files become complete before a request becomes visible; final evidence is retained.

### 4. Establish the shared application deployment and isolated Linux localnet

**Objective and scope.** Adapt one maintained application Compose/configuration arrangement for both deployment modes. Add a localnet overlay for Subtensor and test settings, plus isolated bootstrap. Reuse wallet/funding/subnet registration helpers and configure approximately five distinct test-miner identities for Pylon-based submission. Keep bootstrap out of the production validator. Prepare the data root/ownership and the systemd service installation contract used by task 7.

**Owner and references.** FactoryHorde deployment/localnet/installer. [Application Compose](~/repo/factory-horde/envs/deployed/docker-compose.yml), [localnet Compose](~/repo/factory-horde/localnet/compose.yml), [bootstrap](~/repo/factory-horde/localnet/bootstrap.py), [fixture template](~/repo/factory-horde/localnet/miners/miner.template.py), [localnet index](~/repo/factory-horde/knowledge/localnet/INDEX.md), [old tmux startup](~/repo/factory-horde/localnet/run-in-tmux.sh).

**Prerequisites.** Tasks 1–3. Linux host with Docker/Compose, compatible Python, systemd, uv for application development/bootstrap, and installer prerequisites.

**Deliverables.** Common service configuration, local override/environment example, idempotent local bootstrap command and explicit identity-to-wallet/token configuration. Provide host/container root, target platform, container UID/GID and resource settings. Validator is containerized and receives the shared root read-write; the executor remains a host systemd service. No Docker socket/CLI execution responsibility enters the validator. Factory and judge containers receive no wallets or control root.

**Completion — Linux.** Render/validate Compose, build/start the selected application container shape with dispatch disabled until implemented, and bring up healthy Subtensor/Pylon. Independently confirm local registrations and identities against Subtensor. Local wallets stay under isolated test paths and production defaults cannot accidentally select finney/subnet 12. Repeated bootstrap is safe. The documented application startup uses Compose/systemd, not host validator/miner commands from tmux. Choose a minimal common monitoring configuration that runs without unconfigured remote credentials; retain the same application shape in both modes.

### 5. Build the baseline factory and separate judge images

**Objective and scope.** Create the two intentionally stubbed executables and their container builds. Select the actual Pi coding-agent upstream package and pin its version. Factory invokes only verified help/version, waits approximately 60 seconds, writes fresh `main.py` and `README.md`, and exits. Judge checks required file presence/readability and writes one structured report with job/round/miner and factory-job linkage; a successful report has one random finite score in `[0,1]`. Use the chosen uniform default; missing/broken inputs remain classified failures.

**Owner and references.** FactoryHorde factory assets under `miner/` and separate judge assets, at the locations chosen in task 1. [Miner project](~/repo/factory-horde/miner/pyproject.toml); [container-submission recipe](~/repo/factory-horde/knowledge/bittensor/container_execution.yaml); [digest/build guidance](~/repo/factory-horde/knowledge/validator.deploy.md).

**Prerequisites.** Tasks 1–4 for Linux builds and mount checks; fixed protocol and target platform.

**Deliverables.** Factory and judge image sources, pinned build inputs, contract tests and published public baseline references for Docker Hub and GHCR. Record registry manifest digests, not local image IDs or hash-looking tags. Keep submission dependencies and credentials out of the factory image.

**Completion — Linux.** Invoke Pi help/version with no model credentials or generation prompt and demonstrate it terminates without inference; an offline execution check can strengthen this evidence. Run both images using the intended mount contract. Factory produces a valid greeting program after the intended wait in an initially empty output directory; judge cannot modify the specification/submission and can write its report. No generated project is executed by the judge. Pull published references anonymously by digest for the chosen platform. These are isolated image checks; full executor execution follows in task 7.

### 6. Implement the containerized submitter and frozen commitment discovery

**Objective and scope.** Replace/adapt the HTTP example miner entry point into a one-shot submission CLI: validate a complete supported digest reference, encode UTF-8 bytes through the selected Pylon public API, write using the miner's own configured identity, read back the exact value, then exit. Add the validator's small commitment-discovery adapter and role/eligibility policy. Prefer commitment-block-aligned membership reads where supported; persist the block and mapping and document any weaker snapshot semantics honestly.

**Owner and references.** FactoryHorde `miner/`, validator discovery and localnet identity fixtures. [Existing miner](~/repo/factory-horde/miner/miner.py), [miner contract](~/repo/factory-horde/knowledge/bittensor/miner.contract.yaml), [Pylon public client exports](~/repo/bittensor-pylon/pylon_client/pylon_client/artanis/__init__.py), [versioned client implementation](~/repo/bittensor-pylon/pylon_client/pylon_client/_internal/api/v1/sync/api.py), and [commitment service behavior](~/repo/bittensor-pylon/pylon_service/pylon_service/api/_unstable/services.py).

**Prerequisites.** Tasks 2–5, registered test identities, public baseline digests and explicit Pylon client/service API choice.

**Deliverables.** Submission container/CLI, version-specific encoder/decoder, discovery adapter and tests, and an early real-chain commitment/read-back evidence record. Support publish-on-change without a persistent miner server, callback port or axon requirement. Do not use absence of validator permit alone to determine miner identity; local fixtures can acquire permits.

**Completion — Linux; first meaningful vertical slice.** Each selected miner can publish only through its configured identity and read back its exact Docker Hub/GHCR digest reference; validator discovery recovers the correct identities/references. Test byte-length limits, registration requirements and update-rate behavior on the selected runtime before proceeding deeply into orchestration. Reject malformed hex/UTF-8, unsupported registries, tag-only/malformed/overlong references explicitly, never truncating or substituting tags. After ambiguous HTTP timeout, read before resubmitting because the inspected service awaits shielded writes/retries that may still finish. Current service behavior wins over stale scheduling-only client prose. If no useful full reference fits the verified chain limit, report that concrete integration blocker; do not invent an object-store workaround. Freeze discovery output so later updates do not rewrite an active cohort.

## Phase C — execute safely once and expose durable outcomes through Nexus

### 7. Implement the single-file executor and prove a real factory-to-judge run

**Objective and scope.** Implement basic polling, request validation, digest pull, deterministic named/labeled Docker create/start, detached execution, observation and final status publication for both job kinds. Use Python standard-library code and Docker argument arrays, with operator-controlled platform/resource/user configuration and fixed commands/mounts. Add the initial installer/systemd wiring now so all execution checks use the intended service arrangement.

**Owner and references.** FactoryHorde executor file, minimal systemd unit and installer extension. [Installer](~/repo/factory-horde/installer/install.sh), [application Compose](~/repo/factory-horde/envs/deployed/docker-compose.yml); V2 §§3,5–7,11. Executor imports neither Nexus nor project/third-party packages.

**Prerequisites.** Tasks 3–6; data-root permissions and factory/judge digests.

**Deliverables.** One deployable Python executor file, service unit and installation command, a minimal test driver using the actual file protocol, and focused Docker integration checks. File requests are sufficient; no executor HTTP server, queue or database.

**Completion — Linux.** The systemd executor consumes a real request, pulls/runs its exact digest, and records actual Docker exit evidence. It binds only that job's input read-only/output writable; a separate judge job binds specification/submission read-only/report writable. A real baseline-to-judge pair completes through files with distinct linked job IDs. Confirmed terminal records survive process restart; absent status or failed Docker inspection never means stopped. Containers have no automatic restart policy and are not removed before outcome recording. Failed pull has an explicit outcome and never falls back to another reference.

### 8. Add concurrent dispatch, permanent cancellation and executor reconciliation

**Objective and scope.** Complete the executor lifecycle before validator automation relies on it. Use standard-library concurrency so pulls/stops never block polling other jobs. Make duplicate create/start idempotent and reject conflicting parameters under the same ID. Reconcile existing Docker names/labels and durable records after restart. Check permanent stop intent and deadline again before create/start, including after a slow pull.

**Owner and references.** FactoryHorde single executor file and protocol fixtures; V2 §6 and §8. The [deeper investigation](Nexus-deeper-integration-investigation.md) describes the cancellation/create-start races this must cover.

**Prerequisites.** Task 7.

**Deliverables.** Concurrent lifecycle implementation, graceful-then-force stop, durable terminal/cancellation acknowledgement and restart tests. Stop grace is at most 60 seconds; overall factory stop confirmation is five minutes by default. A cancelled pending job cannot later start, even after executor replacement.

**Completion — Linux.** Dispatch approximately five jobs without artificial batching; slow pull and hanging stop do not starve others. Tests cover duplicate request; conflict; crash after create/before start; restart with running, exited, finalized and missing-expected containers; stop during pull; stop before create/start; expired deadline; and container deletion after final status. Exited/finalized work never reruns. Missing expected state is a reconciliation failure, not authorization for a replacement run. Concurrent stops force a signal-ignoring fixture after grace and record inspected termination. Cancellation acknowledgement proves outstanding startup work cannot race into a later start. Stale/unavailable Docker evidence remains unresolved.

### 9. Implement accepted-result persistence and the Nexus result-store adapter

**Objective and scope.** Extend task 3's shared-tree repository into the public `TaskResultStore`/provider adapter. Validate reports only after confirmed judge termination, accept a score once, and expose task outcomes without a second score database. Persist stable `(task category, business job ID) → task-result ID` mapping and original accepted metadata. Fresh framework timestamps/contexts are observations, not new executions.

**Owner and references.** FactoryHorde validator repository/store/report validation. [Public store interface and implementations](~/repo/bittensor-nexus-library/src/nexus/_internal/core/runtime/task_result_store.py), [provider hook](~/repo/bittensor-nexus-library/src/nexus/_internal/actors/task_result_store_provider.py), [task storer behavior](~/repo/bittensor-nexus-library/src/nexus/_internal/actors/task_result_storer.py).

**Prerequisites.** Tasks 2–3 and 7–8; real terminal/report fixtures.

**Deliverables.** Typed thread-safe adapter with supported task-name/result-ID/completion-epoch queries, rebuildable indexes, accepted-score records and latest-usable-round query. Raw report, executor observation and validator acceptance have distinct owners.

**Completion — Linux tests.** Reject mismatched job/round/miner/factory linkage, missing or malformed reports, non-finite/out-of-range values and reports from unconfirmed live judges. Valid score zero remains successful. Identical replay returns the same result ID and accepted score; conflicting immutable result content fails. Simulated result-save failure and process restart reconstruct from existing terminal/report evidence without rerunning factory/judge or redrawing a random score. Lost final report is a failed evaluation, not permission to rerun that judge. Accepted records persist atomically; epoch query metadata does not control application score lifetime.

### 10. Connect two public Nexus tasks through a nonblocking file communicator

**Objective and scope.** Replace the ping/HTTP wiring with factory and evaluation `NexusTask` compositions using one custom `ExecutorCommunicator`/`CommunicatorActor` implementation and the task 9 provider. Publish or observe existing requests in the input handler, return promptly, and emit correlated completion on poll ticks. Use a small public producer/poll sink; Docker work stays on the host.

**Owner and references.** FactoryHorde validator actors/wiring. [Current main](~/repo/factory-horde/validator/src/validator/main.py), [public Nexus index](~/repo/bittensor-nexus-library/src/nexus/v1/__init__.py), [communicator contract](~/repo/bittensor-nexus-library/src/nexus/_internal/actors/executor_communicator/base_communicator.py), [actor patterns](~/repo/bittensor-nexus-library/src/nexus/_internal/core/runtime/actor_patterns.py), [two-task demo](~/repo/bittensor-nexus-library/demos/cat-images/cat_images/validator/validator.py).

**Prerequisites.** Tasks 2–3 and 8–9.

**Deliverables.** Public-only imports, typed payload/outcome conversion, distinct stable task/node IDs, configured one-attempt task policy, bounded observation retries and error-source logging/outcome wiring. Recommended `NoopRouter` is only a routing adapter: retain real hotkey/digest/round/job identity in payload and durable records. Supply the same store provider to both tasks and later the setter.

**Completion — Linux.** Multiple actual executor jobs remain outstanding without blocking an actor for their duration; poll/file errors become explicit outcomes or continued observation. Fresh runtime contexts resubscribe to existing IDs rather than regenerate work. Graph wiring uses one primary for mandatory business transitions and independent taps for clocks/metrics, with a separate context per dispatched job. Task endpoints are discoverable so Nexus installs their block-beat/timestamp wiring. Neither the demo's immediate sampler nor `EmbeddedExecutorCommunicator` is used to bypass the round gate or separate judge container. Synthetic router identity cannot become the miner's weight identity.

## Phase D — complete the round and independently prove chain effects

### 11. Implement the persisted round coordinator and real staged localnet flow

**Objective and scope.** Add one small actor-owned wall-clock coordinator, independent of chain tempo. It freezes discovery, persists all intended jobs/deadlines, prepares common/per-miner inputs, dispatches all factory jobs, enforces stop confirmation, launches eligible judges in evaluation, and finalizes results. Persist scheduling state so restart resumes the same round. Keep usable result completion separate from permission to start another round.

**Owner and references.** FactoryHorde validator coordinator and localnet tests. V2 §8; [Nexus task composition](~/repo/bittensor-nexus-library/src/nexus/_internal/core/runtime/nexus_task.py), [runtime wiring](~/repo/bittensor-nexus-library/src/nexus/_internal/nexus_validator.py), [timestamper behavior](~/repo/bittensor-nexus-library/src/nexus/_internal/actors/timestamper.py).

**Prerequisites.** Tasks 4,6,8–10.

**Deliverables.** Default two-hour schedule: generation 0–60 minutes, stop/confirmation 60–65, evaluation 65–120; shortened demo configuration preserving the same semantics. Initial chain-readiness gate, persisted cohort/stage and unresolved-job tracking, judge deadline/stop reserve, and restart reconciliation. Stop intent and Docker evidence, not callback counts, decide execution safety.

**Completion — Linux.** Run the real published-baseline cohort through commitment → files → systemd executor → factory → stop gate → separate judges → persisted accepted scores. Early factory completion cannot start judging before evaluation. Pull/start delays consume the common generation window. Generation deadline publishes stops for every unconfirmed job; only confirmed-stopped eligible outputs reach judging. Confirmed outputs may score while another workload remains unresolved, but any unresolved factory or judge holds/skips the next generation slot. Restart at request publication and at stage boundaries retains IDs and deadlines. Initial missing chain beat prevents new dispatch; delayed/dropped Nexus result delivery cannot lose durable stop evidence or accepted scores. Reconcile old unresolved workloads before admitting a new round.

### 12. Add softmax weighing, the usable-result gate and independent local-chain proof

**Objective and scope.** Wire Nexus chain weight opportunities through a small usable-result gate into `WeightSetterNode`. The project callback reads the latest completed valid round from the same repository, filters to still-registered hotkeys and uses stable softmax with positive configurable temperature, starting at `0.1`. Use the chosen empty-round fallback; do not select only the prior chain epoch or return an empty mapping as an assumed skip signal.

**Owner and references.** FactoryHorde weighing/gate and localnet verifier; consume task 2's upstream mechanism support. [Weight setter](~/repo/bittensor-nexus-library/src/nexus/_internal/actors/weight_setter.py), [weight beat](~/repo/bittensor-nexus-library/src/nexus/_internal/actors/chain_beat/set_weights_beat.py), [softmax recipe](~/repo/factory-horde/knowledge/bittensor/incentive.primitives.yaml), [independent verification requirement](~/repo/factory-horde/knowledge/localnet/localnet.adapting-to-subnet.md).

**Prerequisites.** Tasks 2,6,9–11; local runtime capable of the selected mechanism and appropriate test-chain constraints.

**Deliverables.** Stable softmax callback, result/registration readiness gate, mechanism configuration in both status and writes, pure weighting tests, and an independent Subtensor readback tool/evidence file outside validator/Pylon success logging.

**Completion — Linux; first complete localnet milestone.** Tests cover equal scores, one eligible miner, all successful zeros, invalid/nonpositive temperature, failure exclusion, no scores/no history, previous valid-round fallback, vanished hotkeys/UID reuse, and registration changes during calculation. Compute `exp((score-max_score)/temperature)` and normalize over eligible miners only. Empty rounds preserve permitted usable history; absent usable history suppresses submission. Repeated weight opportunities reuse accepted scores. On localnet, submit actual derived weights, then directly read Subtensor to establish subnet, validator, block, mechanism, UID/hotkey mapping and weight vector, accounting for chain encoding/constraints. Verify mechanism-0 non-interference when testing mechanism 1. If the selected local chain cannot demonstrate this, record the limitation and obtain a capable local runtime before marking mechanism acceptance complete. Pylon acknowledgement is not independent chain proof, and weights do not prove final emission percentages.

### 13. Complete adversarial fixtures and cross-component recovery checks

**Objective and scope.** Extend the focused checks already beside each component into a small repeatable localnet failure suite. Use the same application deployment, actual Docker containers and real local chain around the intentionally stubbed factory/judge. Keep fault injection and malformed-chain publication helpers localnet-only.

**Owner and references.** FactoryHorde localnet fixtures and component tests. [Fixture guidance](~/repo/factory-horde/knowledge/localnet/localnet.miner-fixtures.md), [bootstrap](~/repo/factory-horde/localnet/bootstrap.py), V2 §12 and the [deeper fixture matrix](Nexus-deeper-integration-investigation.md).

**Prerequisites.** Task 12 and component-level passing checks from tasks 3–11.

**Deliverables.** Named fixture images/profiles and scripted interventions with expected durable/chain outcomes. Capture evidence per scenario instead of relying only on logs saying success.

**Completion — Linux.** Exercise failed pull, malformed/tag-only/overlong commitment, missing output, confirmed nonzero exit, hanging factory, late pull after cancellation, malformed/mismatched/NaN/infinite/out-of-range judge report, hanging/nonzero judge and Docker unavailability. Kill executor after create/before start and while jobs run; kill validator after publication/acceptance; inject result-store write failure and delayed initial block/result delivery. Verify no duplicate execution, late startup, premature judging, score redraw or overlapping unresolved round. Change a commitment mid-round and observe it only in the next cohort. Combine successful and failed miners to prove unaffected jobs proceed and failures never receive positive softmax weight. Evidence must show terminal Docker state or explicit unresolved status; test-only injected statuses alone cannot satisfy execution acceptance.

## Phase E — make the accepted topology observable and reproducible

### 14. Add minimal operational visibility and readiness

**Objective and scope.** Extend existing structured logging and monitoring with the state operators need to diagnose blocked rounds. Provide event counters and latency histograms for new subsystems, plus visible phase, active/unresolved jobs, latest usable-round age and executor observation freshness. Preserve the executor's file-only interface.

**Owner and references.** FactoryHorde validator/executor instrumentation and maintained scrape configuration. [Logging setup](~/repo/factory-horde/validator/src/validator/logging_config.py), [Compose monitoring](~/repo/factory-horde/envs/deployed/docker-compose.yml), [repository observability requirements](~/repo/factory-horde/AGENTS.md). Verify selected-version Nexus facilities before adopting any claimed metrics imports.

**Prerequisites.** Tasks 8–13. Basic structured failure reporting should already exist in earlier tasks.

**Deliverables.** Atomic executor heartbeat/health evidence and one selected metrics path: an actor-owned validator endpoint or explicitly configured node-exporter textfile collection. Do not add both or introduce an executor HTTP server. Use round/job identities in logs/records, not high-cardinality metric labels.

**Completion — Linux.** Operators can distinguish pull/factory failure, executor/host uncertainty, report rejection, score availability, Pylon submission and independently verified chain effects. Tests demonstrate stale executor/Docker observation, blocked-next-round state and result-store failure are visible. Readiness reflects compatible/readable records, writable paths, reconciliation and initial chain connection; liveness alone is insufficient. Current template host/cAdvisor scraping is not mislabeled as new application metrics. Keep disabled tracing explicit unless deliberately configured with valid settings.

### 15. Finish coherent installer and atomic executor updates

**Objective and scope.** Extend the minimal installation from tasks 4/7 through the existing installer/updater. Install/configure data-root permissions, executor/unit, common Compose settings and documented service privileges. Fetch executor/checksum/configuration from one coherent release selection; stage on the destination filesystem, verify checksum and protocol compatibility, atomically replace the executor, then restart its service. Serialize concurrent updater runs.

**Owner and references.** FactoryHorde [install.sh](~/repo/factory-horde/installer/install.sh), [update_compose.sh](~/repo/factory-horde/installer/update_compose.sh), [installer README](~/repo/factory-horde/installer/README.md), and deployment sources. Avoid operator-local edits that the updater would overwrite.

**Prerequisites.** Tasks 8,13–14; known compatible request/status formats and Linux ownership/platform settings.

**Deliverables.** Reproducible installer/update path, checksum publication, coherent version/config selection and explicit narrowly required systemd/Docker privilege arrangement for installation and the fifteen-minute host cron updater. Executor remains a single file; no dependency migration, rollback manager or parallel old/new executor.

**Completion — Linux.** Test clean install, repeated install, no-change update, failed download, bad checksum, incompatible protocol and overlapping updater invocations. Failed validation leaves the installed executor untouched. Demonstrate atomic replacement/restart while detached factory jobs continue; reconciliation preserves requests, stops, Docker identities and accepted scores without waiting for jobs to finish or rerunning them. Verify permissions as the actual service/cron users, not only as root. An ordinary user's cron job must not be assumed able to restart a system unit. Post-replacement health failure is visible and requires ordinary repair; automatic rollback/rejected-release tracking remains excluded.

### 16. Package an immutable candidate through the template build/promotion workflow

**Objective and scope.** Produce a reproducible tested candidate using the existing validator Dockerfile/build workflow and maintained deployment sources. Separate building/publishing images from selecting their digests for deployment. Pin compatible validator, Pylon, judge, submission/factory and enabled supporting-service images; resolve any template placeholders or mutable runtime references in the candidate.

**Owner and references.** FactoryHorde [validator Dockerfile](~/repo/factory-horde/validator/Dockerfile), [build workflow](~/repo/factory-horde/.github/workflows/build-validator.yml), [deployment procedures](~/repo/factory-horde/knowledge/validator.deploy.md), installer and Compose sources.

**Prerequisites.** Tasks 12–15 and passing applicable QA. Public factory references were already exercised in task 6; this task packages the complete release candidate.

**Deliverables.** Built/published candidate images, exact digests and source revisions, executor checksum and compatible protocol/release metadata, and a concrete candidate deployment configuration usable on localnet. Reuse template build versus promotion mechanics; do not introduce a second release platform.

**Completion — Linux.** Frozen-dependency builds and container smoke checks pass for the selected platform. Candidate configuration contains the tested images and coherent executor assets; localnet uses this same application selection. The former zero validator digest is gone from the candidate. Build success alone does not select a release for operators. Prepare the promotion change and document its separate execution; do not push production deployment configuration to active operators, deploy to subnet 12, change emissions or modify Compute Horde as part of this prototype task.

### 17. Run the clean Linux end-to-end acceptance gate

**Objective and scope.** Reproduce the prototype from the candidate and documented setup on a clean Linux host/VM with systemd, using the same application installation/startup as production plus the localnet overlay/bootstrap. This is the final acceptance gate for the first localnet milestone, not a new deployment milestone.

**Owner and references.** FactoryHorde localnet acceptance harness, candidate deployment and installer. V2 §12; [localnet adaptation checklist](~/repo/factory-horde/knowledge/localnet/localnet.adapting-to-subnet.md).

**Prerequisites.** Tasks 1–16; exact candidate assets and actual published factory digests; resolved required mechanism/runtime capability.

**Deliverables.** Reproducible evidence bundle with command/configuration versions, image digests, submitted commitments and blocks, frozen cohort, round/job/request/stop/status records, generated files, judge reports, immutable accepted scores, calculated weights and independent Subtensor readback. Redact secrets; keep run artifacts outside source commits as the template workflow requests.

**Completion — Linux.** A roughly five-miner run demonstrates concurrent factory dispatch, Pi help/version plus its approximately 60-second stub, staged confirmed-stop gating, separate judges, single accepted random scores and actual mechanism-specific chain weights. A subsequent round or restart demonstrates correct score lifetime, discovery changes and no overlap. Run shortened stage durations for repeatability and verify the 60/5/55-minute defaults/configuration explicitly. Re-run the relevant recovery/failure cases against the packaged candidate and demonstrate an executor update during active detached work. Confirm mechanism-0 non-interference with direct chain evidence and all application services use the common topology. Document actual chain encoding/constraints and any remaining limitations. A missing required proof is an incomplete gate, not a passing mock substitute.

### 18. Finish the implementation handoff and verified operating instructions

**Objective and scope.** Consolidate documentation around the tested implementation and deliver the code/candidate/evidence to the next operator or implementation agent. Keep the root README about the subnet, validator/installer READMEs about operation, and localnet README about isolated setup and fixtures. Remove stale claims about HTTP miners, host validator startup, automatic durability and unimplemented production capabilities.

**Owner and references.** FactoryHorde [root README](~/repo/factory-horde/README.md), [AGENTS.md](~/repo/factory-horde/AGENTS.md), [validator README](~/repo/factory-horde/validator/README.md), [installer README](~/repo/factory-horde/installer/README.md), [localnet README](~/repo/factory-horde/localnet/README.md) and design/contract documentation.

**Prerequisites.** Task 17's evidence and final selected versions.

**Deliverables.** Verified commands for build, isolated bootstrap, miner publication/read-back, install/start, observing a round, fixture execution, restart/reconciliation and checksum update; settings/defaults and data ownership; concise release/handoff notes linking source changes and acceptance evidence. State the canonical data root, host/container path mapping, supported executor Python/platform/user settings and observed commitment constraints. Document recovery of unresolved jobs without deleting evidence to force progress.

**Completion.** A reader can reproduce task 17 without unrecorded shell steps or operator-local patches. Commands and claims match executed evidence; remaining limitations are explicit. Future real inference, quality judging, stronger isolation, encrypted/private submissions, automatic rollback, scale/storage systems, TEE and public-chain deployment remain outside the completed prototype. No prototype random-score demonstration is described as economic, security or application-quality validation.

## V2 coverage checklist

Use this checklist to review the implementation after the corresponding tasks. It adds no prerequisite approval matrix.

| V2 section / major requirement | Tasks |
|---|---|
| §§1–2 purpose, simplified scope, existing rendered scaffold, future-product separation | 1, 18 |
| §3 component boundaries; no persistent miner server; containerized application and host executor | 4–7, 10–11 |
| §4 both public registries, immutable digests, correct Pylon identities/encoding/read-back, frozen cohort | 2, 5–6, 11, 13 |
| §5 configured shared root, layout, per-job permissions, host path resolution | 3–5, 7, 15 |
| §§6.1–6.4 atomic publication, protocol/status/identity, concurrency | 3, 7–8, 10 |
| §§6.5–6.6 restart/create-start races, permanent stop, retries without re-execution/redraw | 8–10, 13, 15 |
| §7 pinned Pi non-inference invocation, 60-second wait, fixed fresh project | 5, 7, 17 |
| §§8.1–8.2 application clock, frozen concurrent jobs, persisted round, chain readiness | 6, 10–11, 13 |
| §§8.3–8.4 confirmed factory/judge termination, stage gates, unresolved-work no-overlap | 8–9, 11, 13, 17 |
| §§9.1–9.3 valid scores once, stable softmax 0.1, failures excluded, empty-round/history policy | 5, 9, 12–13 |
| §9.4 Nexus/Pylon weights, mechanism consistency, independent chain proof, separate public launch | 2, 12, 16–18 |
| §§10.1–10.7 selected-version public APIs, two tasks, routing, file communicator, coordinator/store | 1–2, 6, 9–12 |
| §§10.8–10.9 installer/visibility and production-shaped localnet/fixtures | 4, 7, 13–17 |
| §§10.10–10.12 knowledge-base reuse without treating recipes as implemented features | 1–2 and referenced tasks throughout |
| §11 systemd installation, coherent checksum/atomic update, privileges, no rollback | 4, 7, 15–17 |
| §12 real localnet first; observable behavior, failures and reproducible evidence | 6–13, 17–18 |
| §13 future work excluded from this implementation | 1, 16, 18 |

## What remains unperformed and what can block execution

Only this plan was written. The specifications and repositories were not modified. No environments were created, dependencies installed, runtime imports/tests/builds executed, containers/services started, registries written, wallets accessed or chain operations performed for this planning work.

The genuine integration dependencies are the exact compatible upstream fix releases/revisions (task 2), a capable prepared Linux/local-chain runtime (tasks 4/12), usable full-reference commitment limits and correct identity access (task 6), the verified Pi package/version/non-inference command (task 5), and actual filesystem/platform/systemd privileges (tasks 4/7/15). These are assigned verification work, not newly invented architecture or approval gates. No blocker prevents delivering this plan; none of these runtime prerequisites is claimed satisfied by static source inspection.
