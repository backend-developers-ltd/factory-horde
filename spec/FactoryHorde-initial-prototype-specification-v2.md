# FactoryHorde — Initial Prototype Specification, Version 2

Date: 1 October 2026  
Status: Consolidated project specification for the initial orchestration prototype. Includes Nexus concept mapping and source-investigation findings; not an implementation or acceptance report.

This version supersedes the first prototype specification for design discussion. It incorporates the knowledge-base concept map, all seven deeper integration investigations, and subsequent clarifications. The first specification and review reports remain supporting records; this document contains the requirements, relevant findings, and integration recommendations needed to understand the prototype without reading those reports first.

## 1. Purpose

FactoryHorde evaluates software factories supplied by miners as container images. The initial prototype demonstrates the complete orchestration path: a miner publishes an immutable image reference on-chain, a validator discovers it, a host executor runs the factory, a separate judge container evaluates its output, and the validator converts the recorded scores into chain weights.

Generation and judging are deliberately stubs. No model inference occurs. The prototype demonstrates submission, execution, artifact exchange, lifecycle control, scoring plumbing, and weight submission; it does not demonstrate software-generation quality or meaningful economic performance.

The anticipated initial cohort is approximately five miners. Every eligible miner receives one factory job per round, and all factory jobs are dispatched concurrently without deliberate batching.

## 2. Scope and relationship to previous documents

This specification captures the simplified first version agreed after reviewing:

- `~/repo/factory-horde/spec/draft-specification-2.md`
- `~/repo/factory-horde/spec/agent-run-contract.md`
- `~/repo/factory-horde/spec/FactoryHorde-Nexus-audit.md`

The existing Copier-rendered repository is `~/repo/factory-horde`. It is the starting scaffold; Copier does not need to be rerun.

For this stage, the Nexus template's intended bootstrap, design, and implementation workflow is the starting point. Generic Nexus patterns should be reused where they fit. The concrete prototype behavior described here supplies the application requirements. The earlier requirement to approve a separate decision log and requirement-to-component-to-test matrix before implementation is not a prerequisite for this prototype.

The original full-product specification remains a future direction. Its encrypted submissions, credential-hiding inference proxy, real factory generation, real quality evaluation, and stronger isolation requirements are not acceptance prerequisites for this intentionally stubbed version. The original audit remains evidence about the snapshots it inspected, not proof of defects or capabilities in every later revision.

This is a project specification, not a coding-agent prompt. References to Nexus below identify relevant patterns and integration points without claiming that documentation examples are already implemented features.

The document distinguishes **agreed product behavior**, **existing framework capabilities**, and **recommended implementation choices**. A Nexus recipe is guidance; an exported interface is an extension point; neither implies that FactoryHorde's custom component is already supplied. Proposed defaults remain explicitly labelled. Consolidating the investigation does not silently turn every review suggestion into an additional product requirement.

### Included

- Containerized validator, baseline factory, judge, and short-lived miner submission tooling.
- Publicly retrievable factory images on GitHub Container Registry, identified by registry digest.
- On-chain image-reference commitments and discovery through Pylon.
- A single-file, standard-library-only Python executor running directly on a Linux host under systemd.
- File-based requests, cancellation, and status exchange between validator and executor.
- Persistent per-round input, output, evaluation, and result directories.
- Generation, stop-confirmation, and evaluation stages with deadlines.
- Random prototype scores and configurable softmax weight conversion.
- Extension of the template's installer and updater for the executor and shared directories.

### Excluded from this version

- LLM calls, inference costs, OpenRouter credentials, or a model proxy.
- Object storage, encrypted manifests, private registry credential distribution, or TEE integration.
- A continuously running miner HTTP server.
- Real specification adherence, application-quality judging, or reward-gaming resistance.
- Automatic executor-update rollback.
- A claim that ordinary Docker containers provide a sufficient boundary for arbitrary hostile public submissions.

## 3. Components and responsibilities

### 3.1 Miner tooling and baseline factory

The miner directory contains the baseline factory project and submission tooling. These have different responsibilities:

- The **factory image** is the submitted asset. The validator's executor runs it repeatedly on round tasks.
- The **submission tool** uses the miner's identity to commit the published image reference. It runs as a short-lived container and exits; it is not an HTTP service.

The existing generated `miner/miner.py` implements an HTTP callback example with localnet bootstrap helpers. That behavior is replaced or adapted for submission. The existing project-script entry can remain the command entry point while its implementation changes.

The factory image does not need the miner's wallet, chain credentials, or submission dependencies. Miners may leave a submitted image unchanged across many rounds. Updating a factory requires publishing the new image and committing its new digest reference.

### 3.2 Validator

The validator runs in Docker Compose with the supporting services supplied by the template. It owns round scheduling, miner discovery, specifications, job requests, stage transitions, score validation, score persistence, and weight calculation. Localnet and production use the same application services and startup arrangement; localnet adds a local Subtensor and isolated test configuration/bootstrap.

It mounts the shared data root read-write, creates the directory tree, and reads executor statuses and generated artifacts. It does not launch Docker containers directly and does not execute submitted applications inside its own container.

### 3.3 Host executor

The executor runs directly on the Linux host, outside Docker, as a systemd service. It consists of one Python file using only the Python standard library, invoking the installed Docker CLI. No third-party Python package, virtual environment, HTTP server, or separate executor database is required.

It polls file requests, pulls digest-pinned images when needed, creates and starts detached containers, handles stop requests, inspects Docker state, and publishes status files. It handles both factory and judge jobs through the same mechanism.

Docker owns running containers independently of the executor process. Restarting or replacing the executor does not require waiting for factory jobs to finish. On restart, the executor reconciles existing requests, statuses, and Docker containers.

### 3.4 Judge

The judge is an operator-selected container image, separate from the validator and factory. Each eligible factory output receives its own evaluation job and job ID.

The judge receives the specification and generated repository read-only, plus a writable report directory. It writes a structured report and exits. Future judges that build or execute an application use their own writable scratch copy rather than modifying the original submission.

The first judge only checks the presence and readability of the expected dummy-project files and emits a random prototype score. It makes no model calls.

### 3.5 Terminology and component boundaries

| Term | Meaning in this project |
|---|---|
| Round specification preparation | The validator supplies the common task description; fixed text is sufficient initially. |
| Factory execution task | Runs the submitted factory to produce a project. Sometimes called generation in Nexus examples; it does not mean generating the task specification. |
| Evaluation task | Runs the operator-selected judge against a stopped factory's output. |
| Weighing | Converts accepted miner scores into chain weights using softmax. It is not another judging stage. |
| Miner submission tool | Publishes a factory reference and exits. It is not a persistent miner server. |
| Validator operator | The person/team installing and running the validator stack. Initially the owner team; distinct from the miner role. |
| Nexus public interface | Supported exports from `nexus.v1` that subnet code can use or implement without copying private framework internals. |
| File communicator | A FactoryHorde component inside the validator implementing Nexus's communicator interface. Writes requests and observes statuses; does not operate Docker. |
| Host executor | The single-file systemd service outside containers that reads requests and operates Docker. It does not import Nexus. |
| Local/no-op router | An existing Nexus routing adapter that bypasses remote-neuron selection. “Local” does not mean localnet, and the router does not perform filesystem I/O. |

## 4. Image publication and chain commitments

### 4.1 Supported registries and immutable references

The supported factory registry is GitHub Container Registry (GHCR only, selected by the user on 1 October 2026). A factory submission contains a complete registry/repository reference with a SHA-256 registry manifest digest:

```text
ghcr.io/<owner>/<image>@sha256:<64 hexadecimal characters>
```

The initial prototype assumes images are publicly pullable; private-image authentication is deferred.

Tag-only references are invalid, including `latest`, version tags, and tags that happen to resemble hashes. The digest is the published registry digest, not a local Docker image ID or Git commit hash. The executor pulls and runs the digest-qualified reference rather than resolving a mutable tag.

A digest fixes content identity, not availability: an image may become unavailable, in which case the job fails to pull. It must not silently fall back to another tag or digest. The executor uses one configured target platform so multi-platform image references are executed consistently on the intended host.

### 4.2 Submission and retrieval

The miner publishes the image reference as its on-chain commitment for the configured subnet. The metagraph supplies miner identities and registration information; the corresponding commitment API supplies each miner's image-reference data. These are related chain reads, not a new field invented in the metagraph.

At the start of each round, the validator snapshots eligible miners and their committed references. Later commitment changes apply to subsequent rounds, not to jobs already frozen for the current round.

Pylon's inspected source implements commitment publication, individual reads, and subnet-wide reads. The image-reference text is encoded and decoded using the selected Pylon API's bytes/hex representation. It is not sent as a plain string where the API interprets strings as hexadecimal data.

Commitment write timing depends on the selected Pylon version. In the inspected Pylon checkout, the HTTP handler awaits the service, which awaits commitment submission and retries. Its client docstring instead describes immediate return after background scheduling; that wording is inconsistent with the inspected service implementation. This specification therefore does not assume scheduling-only behavior. Submission is reported as confirmed only after reading back the expected value from the chain-facing API. Following an ambiguous HTTP timeout, read before resubmitting: the underlying write may still complete.

The commitment collection response and miner membership must identify the frozen cohort consistently. Recommended implementation: use the returned commitment block reference to read miner membership at the same block when the selected API supports it. Separate “recent neurons” and “latest commitments” requests must not be described as an atomic snapshot without establishing that property.

The permitted commitment payload length, registration prerequisites, update rate limits, and exact client/service API versions must be verified during integration. An overlong or malformed reference is rejected explicitly; it is never truncated or replaced by a mutable tag.

## 5. Shared filesystem and artifact layout

There is one configured absolute data root on the host, by default under the validator operator's home directory. Its host path and validator-container mount path are installation configuration, not independently hardcoded guesses.

Illustrative layout:

```text
<data-root>/
  control/
    requests/<job-id>.json
    stops/<job-id>.json
    statuses/<job-id>.json
  rounds/
    <YYYY-MM-DD>/
      round-<sequence>-<HH-MM-SS>-<round-id>/
        round.json
        specification.md
        <miner-hotkey>/
          input/
            specification.md
            task.json
          output/
            main.py
            README.md
          evaluation/
            report.json
          result.json
```

The exact filenames are a proposed concrete convention. The agreed structural requirements are date → round and time → miner → input/output, plus durable control and evaluation records. A unique round suffix prevents collisions; timestamps alone are not identifiers. UTC is the proposed storage convention and must be documented consistently.

The validator writes input before publishing the corresponding request. The executor bind-mounts factory input read-only and output read-write. Factory containers receive only their own directories, not the data root or control directories. Judge containers receive the specification and submission read-only and their own report directory read-write.

Docker bind-mount source paths are host paths. Requests identify relative run locations or validated round/miner identifiers; the executor resolves them against its configured root. It rejects locations that escape that root, including through symlinks. Host directory ownership and container user IDs must permit the intended writes without granting access to unrelated directories.

The dummy output is a project directory. Git initialization and commit history are not required in this prototype; a genuine Git repository can be added when needed.

## 6. File-based executor protocol

### 6.1 Ownership and atomic publication

- The validator writes immutable job-request files and immutable stop-request files.
- The executor exclusively writes job-status files.
- Writers create a temporary file in the same directory and atomically replace or rename it into place. Readers ignore temporary files.
- Final statuses and requests are retained. A stopped container does not cause its request or status file to disappear.

There is no append-only event stream. Each job has one current status file, replaced when its state changes. The original request remains unchanged.

### 6.2 Job identity and request data

Every intended execution has a unique validator-generated job ID. Request filename, status filename, and Docker container name identify that same job. Factory and judge executions use separate IDs, linked to the same miner and round.

A request records at least:

- Protocol version, job ID, round ID, miner hotkey, and job kind (`factory` or `judge`).
- Immutable image reference and configured platform.
- Relative input, output, and report locations applicable to the job kind.
- Creation time, execution deadline, and applicable stop grace period.
- A fixed command/mount contract determined by the job kind.

The interface does not accept an arbitrary shell command or unrestricted Docker arguments. Docker CLI calls use argument arrays rather than shell interpolation. Execution resource settings are supplied by operator configuration.

### 6.3 Status data

Statuses distinguish pending/preparing, running, stopping, finished, failed, and cancelled outcomes. Each includes the job identity, last observation time, container identity when available, start/finish times, exit code when available, and a concise error or termination reason.

Process completion, forced termination, and application success are distinct facts. A stopped factory may still have a partial repository. A missing status, unavailable Docker daemon, or failed status query is not evidence that a container has stopped.

### 6.4 Polling and concurrency

The proposed default polling interval is two seconds. Each cycle discovers requests, processes stops, and checks unfinished jobs. Docker status inspection can be batched. Completed jobs are no longer polled during normal operation, and unchanged statuses need not be rewritten.

Slow image pulls and one-minute stop grace periods must not block the entire polling loop. Standard-library concurrency is sufficient to dispatch independent starts/stops while continuing status updates. All eligible factory jobs are dispatched without an artificial batching limit for the anticipated five-miner cohort. Actual image pulls and startup times mean simultaneous dispatch is not an exact simultaneous start.

### 6.5 Retry and restart behavior

Container names are deterministic from job IDs, and containers carry executor/job labels. Reprocessing an identical request locates the existing container instead of creating another execution. Conflicting parameters under an existing job ID are rejected.

If an executor stops between container creation and startup, reconciliation can complete startup only while the job remains authorized and before its deadline. If a container has already exited, reconciliation records its outcome rather than restarting it. Containers do not have an automatic restart policy that could rerun a completed factory.

A stop request permanently cancels further startup for that job. This rule also applies to queued jobs and image pulls that finish after cancellation. Stop intent is checked before creation/start and survives executor restarts.

Finished containers are retained until their final outcomes are recorded. Deleting their metadata must not allow an old request to execute again. Missing expected container state is treated as an explicit reconciliation failure, not permission to repeat completed work.

### 6.6 Nexus retries and the file protocol

The business job ID is assigned and persisted before the job enters a Nexus task. A new runtime context, repeated file observation, failed result-store write, or validator restart does not authorize another execution of that job.

In the inspected Nexus implementation, execution failures and some framework/storage failures can retry the entire task pipeline. Recommended initial configuration is one task attempt, with transient file-read/status-observation retries handled separately. Even with one attempt, executor idempotency is necessary for restart and create/start races. If framework retries are enabled later, they retain the original job identity, image, and directories and observe an existing result rather than repeat the Docker run.

Failure to save a result after execution does not justify regenerating the project or drawing a new random judge score. The existing terminal status and report are the recovery source. The result-store adapter itself is a proposed project implementation described in section 10, not a built-in Nexus disk backend.

## 7. Baseline factory behavior

The baseline image includes the Pi coding agent. Its exact upstream package and pinned version are selected during implementation; this specification refers to Pi, not a Python package inferred from the name.

The factory invokes a confirmed non-inference command, such as Pi's help or version command, and does not supply a generation prompt or model credentials. The chosen command must be verified to terminate without making inference calls.

It then waits approximately 60 seconds and writes a fixed minimal project to its output directory:

- `main.py`: a valid Python program that prints a simple FactoryHorde greeting.
- `README.md`: a short description and execution instructions.

The output does not depend on the input specification. Random source-file contents are unnecessary; randomness is confined to the judge's prototype score. The factory exits after producing the files. Output from a previous job must not be reused as evidence for a new run.

## 8. Validator rounds and stage gates

### 8.1 Schedule

The default round interval is two hours, independently configurable from chain tempo and weight-submission opportunities. There is at most one active evaluation round.

| Stage | Default window from round start | Behavior |
|---|---|---|
| Generation | 0–60 minutes | Freeze miner/image cohort, prepare common specification, dispatch factory jobs, monitor execution |
| Stop and confirmation | 60–65 minutes | Stop unfinished jobs and confirm that factory execution has ended |
| Evaluation | 65–120 minutes | Run judge jobs on eligible outputs and finalize scores |

All factories share the round deadline. Image retrieval/startup delays consume the generation window; there is no guaranteed full hour after each individual container starts. Deadline configuration must be shorten-able for integration demonstrations without changing stage semantics.

### 8.2 Generation

The validator snapshots miner identities and valid image references, creates a common specification, prepares per-miner input directories, and publishes requests. Each eligible miner gets one job.

The round record persists its identity, deadlines, cohort, frozen references, jobs, and stage. A validator restart resumes that record rather than creating duplicate requests. A round may record early factory completion, but scoring belongs to the evaluation stage after the stop-confirmation gate.

New dispatch waits for initial chain readiness. Nexus's task timestamper buffers outputs before its first block notification; the inspected implementation can drop older buffered outputs based on task-start age. This is a startup condition, not a five-minute maximum factory runtime. Executor terminal records and judge reports remain on disk independently of Nexus event delivery, allowing observation and result projection to be reconstructed.

### 8.3 Stop gate

At the generation deadline, the validator publishes stop requests for every job not already confirmed terminal. The executor requests graceful exit, allows up to 60 seconds, and then forces termination if necessary. Stops are dispatched concurrently within the five-minute overall confirmation window.

The grace period permits final output writes, so the effective contract is one hour of generation plus a bounded shutdown interval, not an exact one-hour filesystem freeze. Confirmation follows actual Docker inspection. Cancellation also prevents any still-pending job from starting later.

Only outputs whose factory has been confirmed stopped may proceed to judging. Unconfirmed jobs are excluded from that round's evaluation. Executor/host failures are classified separately from factory failures.

If a factory remains unconfirmed stopped, confirmed outputs may still be evaluated, but the next generation round is held or skipped until the unresolved workload is reconciled. No-overlap takes priority over keeping every scheduled slot.

### 8.4 Evaluation gate and completion

For each eligible output, the validator publishes a judge request with a new job ID. Proposed initial default: judge jobs are also dispatched concurrently for the small cohort.

The validator accepts a report only after the judge container is confirmed stopped. The report must identify the expected job, round, and miner and contain a finite score within the allowed range. Missing, malformed, mismatched, or out-of-range reports do not become successful scores.

Judges have a deadline within the evaluation window. Proposed default: reserve the final five minutes for stopping and confirming outstanding judges, using the same graceful-then-forceful mechanism. A remaining live or unresolved judge also prevents an overlapping new round.

The validator stores each accepted score once. Repeated status polls, process restarts, and weight-setting opportunities do not redraw random scores or count the same evaluation twice.

A round's usable result and permission to start the next round are separate facts. Confirmed jobs may yield scores even while another old job is unresolved, but unresolved execution still prevents new-round overlap. The coordinator uses durable executor evidence, not a count of Nexus callbacks, to enforce this distinction.

## 9. Judge scoring and chain weights

### 9.1 Prototype score

For a structurally valid dummy project, the judge emits one random score in `[0, 1]`. A uniform distribution is the proposed initial implementation default. This score is a plumbing fixture and has no claim to represent quality.

The report includes job/round/miner identity, checks performed, score, and failure information where appropriate. Ineligible or failed evaluations remain explicitly classified rather than masquerading as successful low-quality results.

### 9.2 Softmax conversion

Use the softmax recipe described by the Nexus knowledge base. The agreed starting temperature is `0.1`, configurable and strictly positive. That numeric value is a project choice, not a claimed Nexus default.

For eligible scores `s_i`, use a numerically stable calculation:

```text
m = max(s_i)
a_i = exp((s_i - m) / temperature)
w_i = a_i / sum(a_j)
```

Weights over eligible miners sum to one before chain encoding/processing. Lower temperatures favor higher-scoring miners more strongly. There is no fixed 80/20 allocation; that earlier proposal was replaced by softmax. Equal scores produce equal weights. A single eligible miner receives all eligible weight.

Failed/ineligible jobs are excluded before softmax, rather than being inserted with score zero, because softmax would otherwise give them positive weight. A valid successful score of zero is different from a failed evaluation. If all eligible scores are zero, equal weights are well-defined.

### 9.3 Empty rounds and score lifetime — proposed defaults

If a round has no eligible scores, it does not replace the last valid weight result with random or fabricated values. The proposed prototype fallback is to retain the latest completed valid round, restricted to still-registered identities and renormalized where necessary. If no usable prior result exists, the validator reports that no weight result is available and does not invent one. Exact chain constraints must be respected during integration.

The most recent valid completed round remains the source of weights until a newer valid round replaces it. Do not copy a previous-chain-epoch-only example that would discard scores simply because application rounds and chain epochs have different durations. Longer-term stale-score expiry is future policy work.

### 9.4 Chain integration

Nexus's weight-setting component invokes the project weighing function and submits through Pylon according to chain scheduling and configuration. The application round schedule is separate from this mechanism.

The inspected weighing-function interface returns a hotkey-to-weight mapping; it has no special “skip submission” result. Its setter can submit even an empty mapping. Recommended integration: a small usable-result gate before the setter suppresses a weight attempt when there is no valid current or permitted fallback result. A registration change during calculation must not fabricate replacement scores. Miner attribution comes from persisted round/job records, particularly if the routing adapter uses a synthetic local neuron.

The intended eventual subnet placement remains subnet 12, mechanism 1, alongside Compute Horde. Local testing uses explicitly configured local chain identifiers. The known Nexus/Pylon dependency and mechanism-support corrections are acknowledged external work; this specification does not prescribe duplicating those fixes.

Submitted weights are not a guarantee of identical final emission percentages. Random prototype scores do not justify enabling public emissions. Mainnet deployment, emission changes, and modifications to existing Compute Horde services are separate from producing this prototype.

## 10. Nexus concept mapping and implementation integration

### 10.1 Selected-source scope and evidence levels

The deeper review inspected these clean local checkouts:

| Repository | Inspected revision |
|---|---|
| FactoryHorde rendered scaffold | `06795f7f0438b73f2695d4cae9cd365d7d4fc34c` |
| Bittensor Nexus library | `6e7b1a05c8c401aca9dbc63ac4181df856b899a3` |
| Bittensor Pylon | `d1e881c6784df369af7f24184eadf0722c7b33c8` |

The scaffold's lockfile selects a different Nexus revision and Pylon client from the sibling sources. Detailed interface recommendations below apply to a deliberately selected compatible version, not automatically to the current lockfile. Dependency and mechanism corrections are separate planned PR work. Source paths below establish behavior and provenance; application imports use public namespaces, not the cited private implementation files.

Classification:

- **Existing capability:** implemented in the inspected source, with runtime behavior still untested here.
- **Extension point:** an existing public interface on which FactoryHorde supplies code.
- **Recipe/example:** useful guidance or sample composition, not an automatically installed feature.
- **Project behavior:** the rules, state, and filesystem protocol particular to FactoryHorde.
- **Future/out of scope:** useful concepts that do not belong in this initial prototype.

### 10.2 Recommended minimal composition

The investigation recommends the following composition. It is a concrete implementation proposal, not a claim that these FactoryHorde components already exist:

```text
Nexus validator runtime
  Wall-clock producer
    -> round coordinator
    -> file-communicator polling

  Round coordinator
    -> factory-execution NexusTask
       -> routing adapter
       -> custom file communicator
       -> typed outcome / shared result-store adapter
    -> persist factory observations and enforce stop-confirmation gate
    -> evaluation NexusTask
       -> routing adapter
       -> same file-communicator implementation
       -> typed judge outcome / shared result-store adapter
    -> accept and persist scores once

  Chain clock
    -> Nexus weight-opportunity gate
    -> usable-round-result gate
    -> Nexus weight setter with FactoryHorde softmax callback
    -> Pylon

Shared files <-> host executor <-> Docker factory/judge containers
```

Nexus provides the runtime, typed actor connections, generic task composition, communicator/store interfaces, and weighing callback. FactoryHorde provides the file communicator, round coordinator, disk-backed records, protocol, stub factory/judge, and application scoring policy. There is no additional queue service, HTTP executor, workflow engine, or general event-replay system.

### 10.3 Investigation 1: task and communicator lifecycle

**Existing capability:** the public `ExecutorCommunicator` and `CommunicatorActor` interfaces support correlated completion after the initial input handler has returned. A file communicator can publish a request, return without a result, and emit the outcome later. It need not occupy a thread for the duration of a factory run.

**FactoryHorde addition:** a communicator whose input handler publishes an immutable file request and whose polling handler checks unfinished statuses. A small periodic producer can deliver polling ticks. Approximately five outstanding jobs do not require one actor per miner. Slow Docker pulls and stopping are executor responsibilities, not work done inside validator actor handlers.

The same implementation serves separate factory and judge task instances. Explicitly convert malformed status/report data and file errors into classified outcomes or continued observation; do not rely on a raw exception merely being logged by the actor loop.

Two existing alternatives were inspected but are **not selected**:

- `EmbeddedExecutorCommunicator` calls a Python function synchronously inside the validator actor. It does not launch a Docker container and is unsuitable for blocking through an hour-long job.
- `AsyncHttpNeuronCommunicator` sends work to a miner HTTP server and receives callbacks. Its timeout stops waiting and removes pending state; it does not stop external execution. Our file design does not use that HTTP transport or timeout mechanism.

References in the Nexus library: `src/nexus/v1/__init__.py`; `src/nexus/_internal/actors/executor_communicator/base_communicator.py:32`; `src/nexus/_internal/core/runtime/actor.py:56`; `src/nexus/_internal/actors/executor_communicator/embedded_executor_communicator.py:59`; `src/nexus/_internal/actors/executor_communicator/timeout_sweep_runtime.py:96`.

### 10.4 Investigation 2: round scheduling, fan-out, and stop barrier

**Existing capability:** public producer and actor patterns, typed inputs/outputs, primary flows, independent taps, and context lineage. They can carry wall-clock ticks and individual job events. Chain block/epoch clocks already serve weight timing.

**Project behavior:** the two-hour application clock, frozen cohort, common deadlines, expected job membership, cancellation-before-start, stop-confirmation gate, and no-overlap policy. No ready-made persisted application-round scheduler or stop barrier was established in the source.

Recommended implementation: one small coordinator actor reconstructs its state from round files and reconciles on ticks and job observations. Each dispatched job has its own context and durable ID. A broadcast is not automatically one event per miner; a multi-parent context is lineage, not an implementation of “wait until every expected job is safely terminal.” There is no need for a general reusable barrier framework for the five-miner prototype.

The demo's immediate result sampler must not connect factory completion directly to judging, because that bypasses the selected round gate. Mandatory persistence and gate checks belong on the business path, not on an optional metrics branch.

References: Nexus `src/nexus/_internal/core/runtime/actor_patterns.py:49`; `src/nexus/_internal/core/runtime/event_bus.py:88`; `src/nexus/_internal/core/runtime/context_store.py:432`; `src/nexus/_internal/actors/task_result_sampler.py:50`; `docs/nexus.md` context and flow sections.

### 10.5 Investigation 3: result storage, retries, and recovery

**Existing capability:** a public result-store interface and provider injection into tasks and the weight setter. The built-in store is in memory. The context default is also in memory; descriptive persistence prose is not a supplied durable database implementation.

**Recommended extension:** implement the result-store interface over the same durable job/round records already required by this project. It exposes typed outcomes, accepted scores, identities, and artifact references. It does not copy whole generated repositories into another store or establish a second independently editable source of scores.

The public store queries by task name/result ID and completion epoch. It does not itself select the latest completed FactoryHorde round or deduplicate by external job ID. A project adapter can maintain a stable mapping from task category and job ID to the stored task-result ID. Identical replay returns the existing accepted record; conflicting replay is an error.

| Durable record | Authority |
|---|---|
| Round plan, requests, stop intent | Validator; intended membership and authorization to execute |
| Status and container observations | Executor, reconciled with Docker |
| Judge report | Untrusted judge output until validated |
| Accepted score and task-result projection | Validator/store adapter; recorded once for that job |
| Latest usable round | Derived from completed round records, not file modification time |

Recommended restart approach: fresh Nexus runtime contexts reconstruct their observations from existing files. Unique business state must not reside only in a runtime context, timer, callback, or in-memory dictionary. A complete generic context backend and graph replay system are unnecessary for this prototype.

The public `SubnetBuilder` has context-store injection, but the inspected convenience `NexusValidator` constructor does not expose it. This does not prevent file-based recovery; it limits assumptions about automatic framework replay. The task timestamper's initial-block buffering is likewise a reason to keep stop evidence and score acceptance independent of event delivery.

References: Nexus `src/nexus/_internal/actors/task_result_store_provider.py:6`; `src/nexus/_internal/core/runtime/task_result_store.py:71`; `src/nexus/_internal/core/runtime/subnet_runtime.py:107`; `src/nexus/_internal/nexus_validator.py:48`; `src/nexus/_internal/core/runtime/nexus_task.py:185`; `src/nexus/_internal/actors/task_result_storer.py:180`; `src/nexus/_internal/actors/timestamper.py:158`.

### 10.6 Investigation 4: separate factory and evaluation tasks

**Existing composition example:** the cat-images demo constructs two instances of the generic `NexusTask`, one for miner work and another for validation. “Miner Nexus Task” and “Validation Nexus Task” in the documentation are recipes, not separate public task classes. Their linked results demonstrate attribution from evaluation back to miner work.

**Recommended reuse:** two task categories with distinct job IDs, one for factory execution and one for judging, connected through our persisted round coordinator. The judge report records which factory job and miner it evaluated. Weighing uses that association to allocate the accepted score to the correct hotkey.

The generic task is still neuron-shaped in the inspected implementation, despite broader “arbitrary target” wording in the documentation. A router is required by this abstraction. Public `NoopRouter` bypasses remote selection and attaches a synthetic local neuron, which is sufficient if real miner identity, image digest, round, and job ID remain in the application payload.

This no-op router neither launches containers nor accesses files, and it is not a localnet requirement. It is a proposed adapter for this specific Nexus task composition. Weighing must not mistake its synthetic target hotkey for the actual miner. An alternative small router carrying the frozen real neuron is possible if target-based analytics become valuable; there is no need to introduce it merely to satisfy task wiring.

References: Nexus `docs/nexus.md:165`; `demos/cat-images/cat_images/validator/validator.py:73`; `src/nexus/_internal/core/runtime/nexus_task.py:54`; `src/nexus/_internal/actors/neuron_router.py:228`.

### 10.7 Investigation 5: commitments and score-to-weight conversion

**Existing capabilities:** Pylon client/service commitment reads and writes, Nexus chain timing and weight submission, and a developer-supplied weighing callback. **Knowledge-base recipe:** normalization and softmax with temperature. **Project policy:** valid-score selection, latest usable round, temperature `0.1`, failures excluded before softmax, and proposed empty-round fallback.

The factory commitment is an image reference encoded as data bytes, not a separate object-store document. Membership and reference are frozen per round. Pylon's public API provides collection, individual-hotkey, and own-identity reads; submission requires the correct configured miner identity. Validator read access does not grant permission to write on behalf of miners.

Use explicit selected API versions. In the inspected service, plain commitment writes are awaited with retries; weight application follows a different background-task path. Do not infer the timing of one from the other. Read-back verifies commitment publication, while the localnet milestone independently verifies actual weights against Subtensor.

Nexus's weighing callback accepts the calculation bundle and returns a hotkey-to-weight mapping. It may read the project round repository instead of reproducing the demo's previous-chain-epoch selection. A small gate suppresses attempts when no usable scores exist. The known mechanism fixes must align status polling and writes before mechanism-1 verification.

References: template `knowledge/bittensor/miner.contract.yaml:41`, `knowledge/bittensor/validator.contract.yaml:92`, `knowledge/bittensor/incentive.primitives.yaml:105`; Nexus `src/nexus/_internal/actors/weight_setter.py:27`, `src/nexus/_internal/actors/chain_beat/set_weights_beat.py:127`; Pylon `docs/CLIENT.md`, `pylon_service/pylon_service/api/_unstable/services.py:168`, `pylon_service/pylon_service/api/_unstable/tasks.py:93`.

### 10.8 Investigation 6: installation, promotion, and observability

**Existing scripts and recipes:** validator-image build and separate promotion, digest-pinned deployment, operator installer, periodic updater, and host/service monitoring configuration. The operators in this deployment guide are validator operators, not miners publishing factories.

The current updater fetches deployment files before applying them, but applies them with ordinary writes. It does not already install systemd executor units or atomically update the executor. These are focused project extensions; section 11 defines their required behavior.

Existing monitoring includes host/container and Pylon scraping. The scrape job named validator is not proof of application-level round/job metrics. The inspected Nexus source did not establish a ready-made actor metrics surface, and the template's trace sidecar is disabled in the generated configuration.

Recommended minimal monitoring is structured round/job/error logs plus visible current phase, unresolved-job count, latest usable-round age, and executor observation freshness. It may reuse the existing monitoring stack. A heartbeat file or metrics endpoint is an implementation option, not a requirement for a new executor HTTP server. Keep exact job IDs in records/logs rather than high-cardinality metric labels.

References: template `knowledge/validator.deploy.md:25`, `installer/install.sh:66`, `installer/update_compose.sh:34`, `envs/deployed/docker-compose.yml:72`; library metrics availability remains source-scoped, not a promise about every release.

### 10.9 Investigation 7: localnet and verification fixtures

**Existing infrastructure:** local Subtensor/Pylon Compose services, isolated wallets, test funding and registration, fixture profiles, and guidance requiring independent chain-state checks. **Required adaptation:** use the same containerized application deployment shape as production and replace HTTP-server miners with submission tooling.

In the inspected template, `localnet/compose.yml` starts only Subtensor and Pylon, while `localnet/run-in-tmux.sh` launches validator/miner processes on the host. That is a different development convenience, not our selected topology. It must not be copied as the final prototype startup path.

Reuse one application Compose/configuration arrangement with local overrides for chain endpoint, images, isolated wallets, and test values. Localnet adds Subtensor and bootstrap/seeding; it does not maintain a separate implementation of the validator, executor, factory, or judge. The host executor remains a systemd service in both environments. Local setup helpers may differ from production but must stay outside the production validator's normal logic.

Pylon-based submission tooling needs separately configured miner identities and wallet access, or an explicitly chosen miner-owned Pylon setup. The current localnet configuration's validator identity alone cannot publish all miner commitments. Factory containers never receive those wallets.

Useful fixture variants are valid baseline, failed pull, malformed reference, missing output, nonzero exit, hanging factory, late image pull after cancellation, malformed judge report, and hanging judge. The accepted prototype uses actual Docker execution and actual chain writes around intentionally stubbed factory/judge logic.

References: template `localnet/compose.yml:1`, `localnet/run-in-tmux.sh:22`, `localnet/bootstrap.py`, `localnet/miners/miner.template.py:169`, `knowledge/localnet/localnet.miner-fixtures.md`, `knowledge/localnet/localnet.adapting-to-subnet.md:14`.

### 10.10 Broader knowledge-base concepts and their relevance

The catalogue contains alternatives for many subnet types. Relevant analogies do not introduce extra dependencies or turn unrelated business models into prototype requirements.

| Concept | What maps to FactoryHorde | Source / boundary |
|---|---|---|
| Submitted software versus a remote service | The image is the reusable product; miner publishes and need not keep a server running. | `bittensor/container_execution.yaml`, `miner.contract.yaml`, `miner.rules.yaml` |
| Publish-on-change discovery | Commit an image reference when it changes; read it each round rather than rewriting for every job. | `miner.contract.yaml`, `sdk.quick_reference.yaml`, `subnet.invariants.yaml` |
| Generation then separate evaluation | Produce an artifact, associate a later evaluation with it, then derive miner weights. | Nexus cat-images demo; `example.404gen_sn17.yaml`, `example.gradients_sn56.yaml` |
| Delayed scoring | Store pending work and separate observation/collection time from scoring time. | `prediction_market.yaml`, `time_series_forecasting.yaml`, `example.numinous_sn6.yaml`, `example.zeus_sn18.yaml`; lifecycle analogy only |
| Concurrent container evaluation | Start independent jobs, collect failures, clean up. | `basilica.containers.yaml`; its remote service/transport and concurrency defaults are not adopted |
| Repeated testing of a reusable submission | Evaluate the same image across later rounds without miner intervention. | `example.affine_sn120.yaml`, `example.numinous_sn6.yaml`, `example.gradients_sn56.yaml` |
| Packaged agent plus task/evaluator | Distinguish agent artifact, task, and measured outcome. | `example.kinitro_sn26.yaml`, `example.swarm_sn124.yaml`, `example.nova_sn68.yaml`; simulations/tournaments are not required |
| Linked multistage evaluation | Separate producer and evaluator roles with explicit result identity. | `adversarial_red_blue.yaml`, `example.bitmind_sn34.yaml`; no adversarial miner marketplace is introduced |
| Eligibility before scoring | Discover a submission, establish it is eligible, then score it. | `external_activity_verification.yaml`, `data_indexing.yaml`; GitHub activity and storage economics are not adopted |
| Score normalization and softmax | Use a supported weighing hook with a documented weighting recipe. | `validator.contract.yaml`, `incentive.primitives.yaml`; no exact winner-share promise |
| Identity and registration | Persist miner hotkeys, distinguish them from changing UIDs and runtime job IDs. | `bittensor.core.yaml`, `subnet.lifecycle.yaml`, `trust.assumptions.yaml` |
| Build versus promotion | Publishing a new validator image does not automatically select it for operator deployment. | `validator.deploy.md`, `tasks.project-bootstrap.md` |
| Immutable image identity | Manifest digests, including for third-party images; hash-looking tags remain mutable. | `validator.deploy.md`; explicitly extended to factory submissions here |
| Failure profiles and independent evidence | Exercise controlled failures and verify actual chain state independently of application logs. | `localnet/INDEX.md`, `localnet.miner-fixtures.md`, `localnet.adapting-to-subnet.md` |
| Coding and integration workflow | Follow the template's project environments, typing/QA practices, and design-to-localnet progression. | `guidelines.coding-and-qa.md`, `template.bootstrap.md`, `tasks.project-bootstrap.md`; executor remains standard-library-only |
| Artifact storage and provenance | Keep clear artifact identity and custody; shared host files suffice initially. | `hippius.integration.yaml`, `example.hippius_sn75.yaml`; object storage and distributed persistence are deferred |
| Hardware executor/marketplace concepts | Host lifecycle is a loose analogy, but FactoryHorde rewards factory software rather than rented hardware. | `compute_auction.yaml`, `capacity_market.yaml`, `example.lium_sn51.yaml`; no auctions, collateral, or slashing |
| Inference and external data services | Potential future model access or task research. | `chutes.integration.yaml`, `desearch.integration.yaml`, `dataverse.integration.yaml`; no initial dependency |
| Sybil resistance and production economics | Relevant when scores represent real quality and public rewards matter. | `sybil.realities.yaml`, `incentive.primitives.yaml`; not validated by random-score softmax |

Conflicting generic recipes are interpreted by their selected use case. HTTP-only or validator-only guidance does not remove the agreed baseline factory and submission tooling. Mutable-tag/Watchtower advice in generic operations material does not replace the actual digest-promotion and cron updater. Cached image layers are allowed; caching a previous round's project or random score as evidence of a new execution is not. Hidden-test, inference, remote-executor, and anti-gaming examples remain contextual material, not automatic scope additions.

### 10.11 Reference index

Paths below are relative to the indicated inspected repository. They are references for implementation design, not assertions that the currently locked dependency already supplies every needed behavior.

### Nexus template: `~/repo/factory-horde`

| Reference | Relevance |
|---|---|
| `AGENTS.md` | Declares the separate miner and validator projects and directs the template workflow. |
| `knowledge/tasks.project-bootstrap.md` | Bootstrap/design/implementation sequence; the current repository is already rendered. |
| `knowledge/bittensor/container_execution.yaml` | Describes miners building/publishing images and committing their locations, with validators executing and scoring containers. Its method-call/Basilica examples are guidance, not our implemented file-volume executor. |
| `knowledge/bittensor/basilica.containers.yaml` | Additional container-execution examples; Basilica itself is not required for this host-executor prototype. |
| `knowledge/bittensor/miner.contract.yaml` | Lists Docker image references as commitment data and describes publish-on-change/read-by-validator behavior. |
| `knowledge/bittensor/sdk.quick_reference.yaml` | Documents commitment write and subnet-wide read APIs in the Bittensor SDK. |
| `knowledge/bittensor/subnet.invariants.yaml` | Discusses commitment rate limits and Pylon background submission. Numeric chain limits require verification for the chosen environment. |
| `knowledge/bittensor/validator.contract.yaml` | Describes scoring/weight-setting responsibilities, normalization, softmax, and temperature. |
| `knowledge/bittensor/incentive.primitives.yaml` | Contains the score-to-weight softmax recipe and low-temperature winner-heavy behavior. |
| `knowledge/validator.deploy.md` | Requires registry manifest digests for deployed image references and explains why hash-looking tags remain mutable. Apply the same convention explicitly to submitted factories. |
| `validator/src/validator/main.py` | Current ping/router/HTTP-communicator scaffold; the reference point for adapting validator wiring. |
| `miner/miner.py`, `miner/pyproject.toml` | Current server example and command entry point to adapt for factory publication/submission. |
| `envs/deployed/docker-compose.yml` | Validator/supporting-service deployment and proposed shared-root mount configuration. |
| `installer/install.sh`, `installer/update_compose.sh`, `installer/README.md` | Existing host installation and periodic deployment-update mechanism to extend. |

### Nexus library: `~/repo/bittensor-nexus-library`

| Reference | Relevance |
|---|---|
| `docs/nexus.md` — Node and Actor; Source, Sink, Pipe; Nexus Task | Describes typed actor pipelines and pluggable routers, communicators, payload creators, and result converters. |
| `docs/nexus.md` — Epoch-driven weight setting | Describes chain timing signals and weighing-function integration separately from task execution. |
| `src/nexus/_internal/actors/weight_setter.py` | Existing weighing-function hook and submission flow; use public library interfaces rather than copying private implementation. |
| `src/nexus/_internal/actors/chain_beat/set_weights_beat.py` | Chain scheduling/status gate, subject to the acknowledged mechanism-support work. |
| `src/nexus/_internal/actors/pylon_client_provider.py` | Existing Pylon client connection point. Its actor-facing protocol is a subset of Pylon, not proof of missing Pylon capabilities. |
| `demos/cat-images/cat_images/validator/weighing_algorithm.py` | Concrete example of aggregating task scores into per-hotkey weights. Its task-specific formula and epoch selection are not the FactoryHorde policy. |

The recommended composition in sections 10.2–10.9 uses round coordination built from Nexus actors, two generic tasks, and a custom file communicator. Deadlines, cancellation, and the round-wide stop-confirmation gate remain FactoryHorde behavior. A generic task timeout must not emit an evaluable result before the container is confirmed stopped.

The host executor does not depend on Nexus. Nexus remains inside the validator and manages the application workflow. Default Nexus persistence must not be assumed durable merely from descriptive documentation; the selected storage path must actually preserve the round and score records needed here.

### Pylon: `~/repo/bittensor-pylon`

| Reference | Relevance |
|---|---|
| `docs/CLIENT.md` | Public client API documentation, including commitment reads/writes and API-version distinctions. |
| `pylon_client/pylon_client/_internal/api/abstract_sync.py` | Implements client calls for reading all commitments, reading one hotkey, reading own commitment, and scheduling a write. |
| `pylon_client/pylon_client/_internal/api/abstract_async.py` | Corresponding asynchronous client surface. |
| `pylon_client/pylon_client/_internal/api/v1/sync/api.py` | Version-specific commitment response handling. |
| `pylon_service/pylon_service/api/v1/api.py` and `api/v1/services.py` | Stable commitment read endpoints and response adaptation. |
| `pylon_service/pylon_service/api/_unstable/api.py` and `api/_unstable/services.py` | Commitment endpoint/service implementations used by the versioned API structure. |
| `pylon_service/pylon_service/bittensor/contact.py` | Actual chain-facing commitment fetch/set implementation through turbobt. |

Static inspection confirmed read and write implementations. It did not execute them, establish deployed-version compatibility, or determine the final allowed commitment payload length.

### 10.12 Knowledge-base coverage inventory

The concept survey covered all 46 files under the rendered repository's `knowledge` directory. Core workflows/patterns were read in detail; large external examples and peripheral integrations received a concept/mechanism survey. The seven deeper investigations then traced the relevant library/service implementations and test source without executing them. The inventory is included so this consolidated document identifies both selected guidance and material intentionally left outside scope.

| Group | Files relative to `knowledge/` |
|---|---|
| Workflow and localnet (7) | `template.bootstrap.md`; `tasks.project-bootstrap.md`; `guidelines.coding-and-qa.md`; `validator.deploy.md`; `localnet/INDEX.md`; `localnet/localnet.adapting-to-subnet.md`; `localnet/localnet.miner-fixtures.md` |
| Core/operational material (15; under `bittensor/`) | `INDEX.yaml`; `subnet.invariants.yaml`; `design_flow.yaml`; `bittensor.core.yaml`; `subnet.lifecycle.yaml`; `sdk.quick_reference.yaml`; `btcli.reference.yaml`; `miner.contract.yaml`; `miner.rules.yaml`; `validator.contract.yaml`; `validator.rules.yaml`; `incentive.primitives.yaml`; `trust.assumptions.yaml`; `sybil.realities.yaml`; `ops.principles.yaml` |
| Mechanism patterns (8; under `bittensor/`) | `container_execution.yaml`; `prediction_market.yaml`; `time_series_forecasting.yaml`; `adversarial_red_blue.yaml`; `data_indexing.yaml`; `external_activity_verification.yaml`; `compute_auction.yaml`; `capacity_market.yaml` |
| Integrations (5; under `bittensor/`) | `basilica.containers.yaml`; `chutes.integration.yaml`; `hippius.integration.yaml`; `desearch.integration.yaml`; `dataverse.integration.yaml` |
| External examples (11; under `bittensor/`) | `example.affine_sn120.yaml`; `example.numinous_sn6.yaml`; `example.gradients_sn56.yaml`; `example.kinitro_sn26.yaml`; `example.swarm_sn124.yaml`; `example.404gen_sn17.yaml`; `example.bitmind_sn34.yaml`; `example.nova_sn68.yaml`; `example.zeus_sn18.yaml`; `example.lium_sn51.yaml`; `example.hippius_sn75.yaml` |

External examples describe the local knowledge bundle, not independently verified current behavior of those subnets. Provider availability, numeric chain limits, and historical release assumptions are not adopted as verified facts.

## 11. Installation and updating

The Linux host provides Docker, Docker Compose, Python 3, systemd, and the prerequisites of the existing template installer. The validator, factory, judge, and submission tooling run as containers; the executor is the explicit host-process exception.

The template installer creates an operator working directory, writes configuration, downloads deployment files, starts the Compose stack, and installs a cron update check every fifteen minutes. The updater is a host script, not a container. The current generated validator image digest is a placeholder to be replaced by a built image.

Deployment has two distinct steps: build/publish a validator image, then promote an explicitly selected digest through the maintained deployment configuration. Validator operators consume the promoted configuration; miners independently publish factory references through commitments. Pylon and judge versions must be selected compatibly with the validator release. This is the existing guide's structure, not evidence that a release has already been promoted.

The extension for this prototype:

1. Creates/configures the shared data root and appropriate permissions.
2. Installs the executor Python file and systemd unit.
3. Starts/enables the executor service.
4. Mounts the configured root into the validator through maintained Compose configuration.
5. Updates the executor file alongside compatible deployment configuration.

Executor updates download to a temporary file, verify the published checksum, atomically replace the installed file, and restart the service. There is no package installation or virtual-environment migration. Existing job files and Docker containers remain in place. Compatible request/status formats are required across updates.

The temporary executor file must be staged on the destination filesystem for atomic replacement. Installation must specify how the updater obtains narrowly required systemd privileges and Docker access; an ordinary user cron job cannot be assumed to restart a system service. Recommended implementation safeguards are serialization of overlapping updater runs and a coherent release selection for script/checksum/configuration, avoiding assets fetched from different moving-branch revisions. These do not introduce automatic rollback.

Localnet uses this same application startup arrangement and application images, with local configuration and Subtensor/bootstrap additions. The current template's host-process tmux startup is an adaptation source, not an alternative supported mode for the FactoryHorde validator. Test funding/registration helpers are kept separate from production runtime behavior.

Automatic health-based rollback, retaining/selecting previous versions, and avoiding repeated installation of a rejected release are explicitly deferred. Ordinary systemd crash restart is not a rollback mechanism.

Changes belong in the repository's installer/update/deployment sources, since local edits to downloaded Compose files may be overwritten by the updater.

## 12. Prototype verification and remaining integration checks

### First milestone: complete localnet run

**Localnet is the first implementation milestone.** Before any later deployment, demonstrate the complete prototype against a locally running Subtensor chain using the template's localnet setup and adapted miner fixtures. Its application services and startup arrangement match production; the additions are local Subtensor, isolated test configuration, and bootstrap/seeding. It is not a separate host-process implementation of the validator or miners.

The local environment includes:

- A local Subtensor blockchain and Pylon service.
- The containerized FactoryHorde validator.
- Registered test-miner identities and short-lived containerized submission tooling that publishes factory-image commitments; no persistent miner HTTP servers are required.
- The single-file executor running as a systemd service on the Linux host.
- Factory and judge containers launched by that executor, using the shared input, output, control, and report directories.

The template currently starts only Subtensor/Pylon in localnet Compose and launches validator/miner examples from a host tmux script. The milestone changes that wiring to the required containerized application setup. Miner commitment publication through Pylon requires test-miner identities/wallets explicitly configured for submission; the validator's identity is not reused to impersonate all test miners.

The demonstration exercises the full path from commitment publication and discovery through factory execution, stop confirmation, separate judging, recorded scores, and actual local-chain weight submission. Verify weights directly against Subtensor, independently of validator or Pylon success logs, and verify the configured mechanism and mechanism-0 non-interference where supported by the local chain. Record any local-chain limitation explicitly rather than claiming deployment parity.

This milestone runs on a Linux environment with the required runtime prerequisites. Mainnet deployment remains a separate later milestone.

### Verification scope

The prototype is demonstrated when an actual published baseline image is committed, discovered, executed through the filesystem protocol, stopped/confirmed, judged in a separate container, scored once, and used for a real local-chain weight submission with chain evidence.

The generation and judging stubs are intentional accepted components of this version. Their successful execution does not count as real generation or evaluator-quality evidence.

Essential behavior to exercise includes:

- Accepted digest references from both supported registries and rejection of tag-only references.
- Commitment publication/read-back and frozen round discovery.
- Input read-only/output writable mounts and correct per-miner artifact collection.
- Concurrent job dispatch for the small cohort.
- Executor restart without duplicate runs, including interruption between create and start.
- Stop requests overriding pending starts, and confirmed termination before judging.
- A slow or hanging factory being stopped within the configured policy.
- Judge completion/report validation, score persistence, and no random redraw on retry.
- Softmax calculation, failed-job exclusion, equal scores, one eligible miner, and no-eligible-result behavior.
- Validator restart resuming an existing round.
- Initial chain readiness before dispatch and recovery of on-disk terminal outcomes even if Nexus result delivery is delayed or missed.
- Result-store failure/recovery without re-executing a completed job or redrawing its accepted random score.
- Same application startup topology in localnet and production, with local-only chain/bootstrap differences explicit.
- Correct configured mechanism weight submission, with mechanism-0 non-interference where applicable.

Remaining implementation checks are commitment-size limits, exact compatible dependency revisions, Pi's non-inference invocation and version, Docker platform/user settings, and deployed filesystem permissions. These are not reasons to expand the prototype architecture.

All repository inspections informing this specification were static. No Python environment was created, no packages installed, no tests or containers run, and no chain writes or deployment performed during this discussion. Test-source citations in the investigation establish intended checks, not passing execution results.

### Suggested implementation sequence within the localnet milestone

1. Select compatible Nexus/Pylon revisions incorporating the separately planned fixes; record the selected local-chain image and runtime.
2. Adapt the shared application startup configuration for Linux localnet. Exercise registration and an actual digest-reference commitment round trip early, including payload length.
3. Implement the single-file executor and file protocol, including unique IDs, mounts, restart reconciliation, and cancellation that defeats late starts.
4. Connect factory and judge tasks through the public Nexus interfaces, file communicator, persisted round coordinator, and shared result-store adapter.
5. Connect accepted round scores to the softmax callback and weight gate, then independently verify chain effects.
6. Exercise targeted failures, startup/restart cases, stop gates, and score persistence.
7. Complete reproducible installer/updater and minimal operational visibility, including executor replacement while detached jobs continue.

This sequence reuses Nexus where the inspected implementation supports the requirement and keeps application-specific behavior local to FactoryHorde. It does not require building a general database backend, workflow service, or arbitrary-target Nexus refactor before the prototype.

## 13. Future versions — not initial-prototype requirements

1. **Real generation:** pass the task specification to Pi or other factory agents, make model calls, and produce meaningful applications instead of a fixed dummy project.
2. **Model access and credentials:** introduce approved-provider access, spending limits, per-run accounting, and the credential-hiding proxy required by the full-product direction.
3. **Private submissions:** encrypted per-validator metadata and credential references, image-access controls, and object storage where needed.
4. **Real judging:** agent-assisted evaluation, requirements-based checks, application execution, partial credit, reproducible evidence, and calibration against known-good and broken controls.
5. **Stronger isolation:** review and implement an adequate boundary for untrusted public factory and application execution; include filesystem, process, network, and resource controls. Docker-only prototype execution is not a security certification.
6. **Executor update rollback:** freshness/health checks, restoration of a known-good version, and rejection tracking for failed releases.
7. **Operational scaling:** admission control, configurable concurrency, preparation/pull scheduling, resource quotas, disk retention, artifact cleanup, improved monitoring, and richer failure recovery.
8. **Economic behavior:** quality thresholds, duplicate/tie policy, stale-score expiry, anti-replication incentives, and an explicit production all-failed/burn policy. Random-score softmax is not an economic design validation.
9. **Repository lifecycle:** actual Git history where useful, successive feature changes, bug fixes, regressions, and long-horizon software maintenance.
10. **Deployment and coexistence:** separately authorized subnet-12 mechanism-1 deployment, immutable tested releases, allocation verification, and preservation of existing Compute Horde operation.
11. **Additional trust models:** additional authorized validators, coordination of stochastic scores, and eventual confidential/TEE execution.

These extensions preserve the basic submission → factory job → confirmed-stop gate → judge job → score → weight flow while replacing the prototype stubs and strengthening its operational guarantees.
