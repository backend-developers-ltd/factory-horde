# FactoryHorde — Initial Prototype Specification

Date: 1 October 2026  
Status: Project specification for the initial orchestration prototype; not an implementation or acceptance report.

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

### Included

- Containerized validator, baseline factory, judge, and short-lived miner submission tooling.
- Publicly retrievable factory images on Docker Hub or GitHub Container Registry, identified by registry digest.
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

The validator runs in Docker Compose with the supporting services supplied by the template. It owns round scheduling, miner discovery, specifications, job requests, stage transitions, score validation, score persistence, and weight calculation.

It mounts the shared data root read-write, creates the directory tree, and reads executor statuses and generated artifacts. It does not launch Docker containers directly and does not execute submitted applications inside its own container.

### 3.3 Host executor

The executor runs directly on the Linux host, outside Docker, as a systemd service. It consists of one Python file using only the Python standard library, invoking the installed Docker CLI. No third-party Python package, virtual environment, HTTP server, or separate executor database is required.

It polls file requests, pulls digest-pinned images when needed, creates and starts detached containers, handles stop requests, inspects Docker state, and publishes status files. It handles both factory and judge jobs through the same mechanism.

Docker owns running containers independently of the executor process. Restarting or replacing the executor does not require waiting for factory jobs to finish. On restart, the executor reconciles existing requests, statuses, and Docker containers.

### 3.4 Judge

The judge is an operator-selected container image, separate from the validator and factory. Each eligible factory output receives its own evaluation job and job ID.

The judge receives the specification and generated repository read-only, plus a writable report directory. It writes a structured report and exits. Future judges that build or execute an application use their own writable scratch copy rather than modifying the original submission.

The first judge only checks the presence and readability of the expected dummy-project files and emits a random prototype score. It makes no model calls.

## 4. Image publication and chain commitments

### 4.1 Supported registries and immutable references

The supported factory registries are Docker Hub and GitHub Container Registry. A factory submission contains a complete registry/repository reference with a SHA-256 registry manifest digest:

```text
docker.io/<owner>/<image>@sha256:<64 hexadecimal characters>
ghcr.io/<owner>/<image>@sha256:<64 hexadecimal characters>
```

The initial prototype assumes images are publicly pullable; private-image authentication is deferred.

Tag-only references are invalid, including `latest`, version tags, and tags that happen to resemble hashes. The digest is the published registry digest, not a local Docker image ID or Git commit hash. The executor pulls and runs the digest-qualified reference rather than resolving a mutable tag.

A digest fixes content identity, not availability: an image may become unavailable, in which case the job fails to pull. It must not silently fall back to another tag or digest. The executor uses one configured target platform so multi-platform image references are executed consistently on the intended host.

### 4.2 Submission and retrieval

The miner publishes the image reference as its on-chain commitment for the configured subnet. The metagraph supplies miner identities and registration information; the corresponding commitment API supplies each miner's image-reference data. These are related chain reads, not a new field invented in the metagraph.

At the start of each round, the validator snapshots eligible miners and their committed references. Later commitment changes apply to subsequent rounds, not to jobs already frozen for the current round.

Pylon's inspected source implements commitment publication, individual reads, and subnet-wide reads. The image-reference text is encoded and decoded using the selected Pylon API's bytes/hex representation. It is not sent as a plain string where the API interprets strings as hexadecimal data.

Pylon schedules commitment writes asynchronously. Submission is reported as confirmed only after reading back the expected value from the chain-facing API; an HTTP acknowledgement alone is not confirmation.

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

The intended eventual subnet placement remains subnet 12, mechanism 1, alongside Compute Horde. Local testing uses explicitly configured local chain identifiers. The known Nexus/Pylon dependency and mechanism-support corrections are acknowledged external work; this specification does not prescribe duplicating those fixes.

Submitted weights are not a guarantee of identical final emission percentages. Random prototype scores do not justify enabling public emissions. Mainnet deployment, emission changes, and modifications to existing Compute Horde services are separate from producing this prototype.

## 10. Nexus and Pylon integration references

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

The closest architectural fit is a round-coordination flow built from Nexus actors, individual factory/judge jobs represented through its task abstractions where appropriate, and a custom file-based communicator connecting those jobs to the host executor. Deadlines, cancellation, and the round-wide stop-confirmation gate are FactoryHorde behavior. A generic task timeout must not emit an evaluable result before the container is confirmed stopped.

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

## 11. Installation and updating

The Linux host provides Docker, Docker Compose, Python 3, systemd, and the prerequisites of the existing template installer. The validator, factory, judge, and submission tooling run as containers; the executor is the explicit host-process exception.

The template installer creates an operator working directory, writes configuration, downloads deployment files, starts the Compose stack, and installs a cron update check every fifteen minutes. The updater is a host script, not a container. The current generated validator image digest is a placeholder to be replaced by a built image.

The extension for this prototype:

1. Creates/configures the shared data root and appropriate permissions.
2. Installs the executor Python file and systemd unit.
3. Starts/enables the executor service.
4. Mounts the configured root into the validator through maintained Compose configuration.
5. Updates the executor file alongside compatible deployment configuration.

Executor updates download to a temporary file, verify the published checksum, atomically replace the installed file, and restart the service. There is no package installation or virtual-environment migration. Existing job files and Docker containers remain in place. Compatible request/status formats are required across updates.

Automatic health-based rollback, retaining/selecting previous versions, and avoiding repeated installation of a rejected release are explicitly deferred. Ordinary systemd crash restart is not a rollback mechanism.

Changes belong in the repository's installer/update/deployment sources, since local edits to downloaded Compose files may be overwritten by the updater.

## 12. Prototype verification and remaining integration checks

### First milestone: complete localnet run

**Localnet is the first implementation milestone.** Before any later deployment, demonstrate the complete prototype against a locally running Subtensor chain using the template's localnet setup and adapted miner fixtures.

The local environment includes:

- A local Subtensor blockchain and Pylon service.
- The containerized FactoryHorde validator.
- Registered test-miner identities and short-lived containerized submission tooling that publishes factory-image commitments; no persistent miner HTTP servers are required.
- The single-file executor running as a systemd service on the Linux host.
- Factory and judge containers launched by that executor, using the shared input, output, control, and report directories.

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
- Correct configured mechanism weight submission, with mechanism-0 non-interference where applicable.

Remaining implementation checks are commitment-size limits, exact compatible dependency revisions, Pi's non-inference invocation and version, Docker platform/user settings, and deployed filesystem permissions. These are not reasons to expand the prototype architecture.

All repository inspections informing this specification were static. No Python environment was created, no packages installed, no tests or containers run, and no chain writes or deployment performed during this discussion.

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
