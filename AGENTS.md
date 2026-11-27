# Context

FactoryHorde is a localnet-first orchestration prototype for software factories submitted as immutable
container images. A Nexus validator coordinates host Docker execution through shared files; a separate judge
produces persistent scores: an adaptive Claude controller that chooses isolated project tests and grades observed behavior against frozen criteria, with
random scoring available in explicit fixture mode. No public deployment is included.
Origin: the Copier-rendered Nexus subnet template.

Follow `spec/FactoryHorde-v2-sequential-implementation-tasks.md` in order and use `subnet_design.md` for the
selected implementation shape. V2 overrides generic HTTP-miner, validator-only and host-tmux recipes. Mark a
task `[DONE]` only when its required evidence exists, then commit it with a short title and no body. The
repository is already rendered: do not rerun Copier. The V2 components are implemented; use the retained
evidence and `docs/implementation-handoff.md` for the accepted scope and selected revisions.

## Repository layout

This is a monorepo with two **independent** uv projects plus shared local-development tooling:

- `validator/` — Nexus-based subnet validator (own `pyproject.toml`, `uv.lock`, `.venv`); also holds the
  production `Dockerfile`
- `miner/` — submission tooling and baseline factory assets (own `pyproject.toml`, `uv.lock`, `.venv`);
  `docker-compose.yml` runs a miner's Pylon and the published one-shot submitter;
  `miner` is a one-shot Pylon submitter; the `bootstrap` dependency group holds the local-chain SDK
- `localnet/` — Local subtensor + pylon + bootstrap + miner fixtures for end-to-end development
- `installer/` — rendered validator installer scripts (`install.sh`, `update_compose.sh`, `README.md`)
- `envs/deployed/` — rendered application `docker-compose.yml` (validator + pylon), with the opt-in `alloy` Compose profile and its `alloy/config.alloy`, which push metrics to the company monitoring stack; operator promotion uses `deploy-config-production`, which both the mainnet (subnet 12, mechanism 1) and the Refinery deployment follow, with this compose file and the installer scripts as the operator-critical files. As in the Nexus template, the validator image is selected only by this file's pin of `factory-horde-validator-${ENVIRONMENT}`; there is no `.env` image override outside localnet, whose overlay runs `VALIDATOR_IMAGE`
- `.github/workflows/` — rendered CI; `build-validator.yml` builds and pushes validator and submitter images to GHCR on push to `deploy-build-*` branches. `production` is the only environment (`deploy-build-production`, `deploy-config-production`, the `-production` image packages); Refinery is a production deployment on another chain. The template's `<env>` naming is kept so that another environment can be added later. `build-baselines.yml` publishes the factory and judge images when their sources change
- `refinery/` — only what is specific to the Refinery development chain (subnet 11): validator and miner `.env` examples, the `//Alice` funding script and a guide. Deployment itself is the generic path: the validator through `installer/` (on Refinery the `ubuntu` operator installs into `~/factory-horde` from `deploy-config-production`), a miner through `miner/docker-compose.yml`. Do not add Refinery-specific deployment or operation scripts
- `production/` — the same for mainnet subnet 12, where FactoryHorde is mechanism 1 beside ComputeHorde and
  reuses its hotkeys: `.env` examples and a guide, no deployment scripts
- `knowledge/` — Bittensor / Nexus / localnet domain knowledge
- `docs/` — additional documentation
- `spec/file-protocol.md`, `spec/fixtures/protocol-v1/` — implemented record contract and fixtures

There is **no** top-level Python project and **no** uv workspace. Run `uv sync` inside `validator/` or `miner/`
before working on it. There is no global `uv run` from the repo root.

See `localnet/README.md` for the common Compose services and isolated bootstrap. Validator dispatch defaults
to disabled; enabling it requires explicit identity, judge and timing configuration. The host systemd executor is implemented;
`installer/install-executor.sh` installs its standalone file and unit. Concurrent dispatch, permanent
cancellation and Docker reconciliation have focused unit and real systemd fault checks. Development tests
live beside it but are not deployed. `installer/install.sh` and `update_compose.sh` use the standard-library
`installer/release.py` helper and a checksummed asset manifest. Updates resolve a configuration branch once, hand off to that release's own updater when it differs,
stage/validate all files, serialize on an installation lock and atomically replace the executor before
restarting its exact system unit. The operator's cron receives only that restart sudo grant. System-unit
changes require installation privileges; health failure is visible without rollback. Regenerate the manifest
and standalone checksum with `uv run --project validator python installer/release.py manifest "$PWD"`
after editing listed assets, and verify with `--check` before committing. `result_repository.py` owns immutable execution decisions
and accepted scores; `result_store.py` supplies public Nexus projections with rebuildable indexes.
`tasks.py` wires distinct factory/evaluation Nexus tasks with the shared provider;
`file_communicator.py` publishes frozen requests and observes outcomes on actor-owned poll ticks, keeping only the newest context per job; Nexus expires contexts idle for 15 minutes (`CONTEXT_TTL` in `main.py`).
`coordinator.py` and `round_actor.py` persist admission (verifying settled rounds once per process so poll cost does not grow with history), enforce stage/stop gates and recover the same
jobs/deadlines without relying on task callbacks. Unresolved old workloads hold new round slots.
Factory eligibility retains the minute-65 finish cutoff: late observation of a timely Docker exit
can qualify, but actual late completion cannot. Executor shutdown interrupts local Docker CLI calls
so held pulls do not delay restart for their full timeout; interrupted effects remain unresolved.
`weighing.py` selects the newest completed round when any miner was fully evaluated, filters current registration and revalidates
membership before returning 90/10 winner weights with stable round/hotkey SHA-256 ties. `weight_gate.py` suppresses empty opportunities;
both Nexus weight nodes receive `Settings.mechanism_id` and use the tasks' shared store provider.
Weight writes are independently opt-in. `localnet/check_weights.py` uses the miner bootstrap SDK to
verify the actual chain vector and mechanism-0 non-interference without relying on Pylon acknowledgements.
`localnet/check_adversarial.py` exercises published fault profiles with the production coordinator,
real chain and an isolated systemd executor. It restores miner commitments and retains unresolved
fixture evidence in its own root. Workload artifact rejection distinguishes invalid output from
transient host I/O failure; neither supplies a score.
Malformed report encodings and numeric overflow are persisted evaluation failures;
transient filesystem errors remain retryable. Judges must actually finish by the
frozen round end, including its stop-confirmation reserve. Definitive Docker startup
rejections become terminal never-started failures; ambiguous starts remain unresolved. A failed image pull is retried after 10 and 30 seconds before it becomes terminal.
Completed executor jobs leave normal polling after durable terminal publication; restart
rebuilds that knowledge through retained-record reconciliation. Application activation intent
is persisted before replacing Compose assets and cleared only after successful Compose startup.
Updater executor activation intent survives replacement/restart failures and requires health
from a process started during the activation attempt before it is cleared.

Commitments are tagged envelopes shared with ComputeHorde on the same hotkey (`spec/fixtures/commitment-v2/envelopes.json`). FactoryHorde owns `<F:ghcr.io/OWNER/PACKAGE@base64url-sha256>`; `discovery.py` rebuilds the full image reference and rejects commitments over the chain's 512 bytes or without exactly one well-formed envelope. Envelopes without `ghcr.io/OWNER`, written by older submitters, are rejected. The submitter rewrites only its own envelope, preserves all other bytes and keeps a 33-byte reserve for ComputeHorde's `<M:…>` manifest within its limit: 512 bytes, or `COMMITMENT_MAX_BYTES` (200 on hotkeys shared with ComputeHorde, whose writes share the hotkey's 3,100-byte quota per tempo). Pylon writes commitments over 128 bytes as `BigRaw`. The chain has no compare-and-set, so either writer can overwrite the other's newest change from a stale read; submitters are therefore scheduled and write only when their envelope is missing or different.

Workloads run without a network or environment by default. With `EXECUTOR_FACTORY_NETWORK` the executor attaches factories and judges to the internal network of the opt-in `inference` Compose profile and gives them `ANTHROPIC_BASE_URL` and a placeholder `CLAUDE_CODE_OAUTH_TOKEN`; review mode requires the proxy, and localnet explicitly selects fixture mode; that profile's `inference-proxy` (Caddy, configured inline in `envs/deployed/docker-compose.yml`) holds the real token and forwards only `/v1/messages*` to Anthropic. The configured network identifies the proxy; each workload gets its own internal isolated-gateway network connected only to that proxy. Application checks run in executor-owned networkless containers with a narrow judge-only file channel; parent terminal publication requires confirmed child termination. Inference shares one credential without a quota. `EXECUTOR_MAX_RUNNING_JOBS` (default 4) caps workload containers, counted from Docker so the limit survives restarts; waiting jobs never stretch round deadlines and are logged at error level; a factory cancelled before it ran is a validator-side gap (`factory_not_started`), not a rejected submission, and terminal jobs left in Docker's created state release their slot. Every hour the executor prunes stopped workload containers older than `EXECUTOR_CONTAINER_RETENTION_HOURS` (default 168), so their logs stay available that long. Per-workload networks need small Docker address pools (see `installer/README.md`). With `EXECUTOR_REGISTRY_NETWORK`, a judge's `install` action briefly connects its application sandbox to the `registry-proxy` (Squid, PyPI and npm only) for a fixed wheels-only pip or scripts-free npm install; it first kills every other sandbox process, runs pip isolated from the project with only plain index requirements, and the executor restores and verifies `--network none` before any other command. Tasks come only from `TASK_DIRECTORY`; without it new rounds use the built-in greeting task. Admission freezes each task's requirements, `JUDGE_MODE` and the optional `JUDGE_MODEL` with its text; judges record the model IDs actually used. Weights come only from the newest completed round: a miner without an accepted score for every task is excluded from that round, other miners are still weighted, older rounds never stand in, no weights are produced while a published judge of that round still lacks a retained result, and review rounds require the configured judge image digest. `TASK_DIRECTORY` enables discovery of all non-hidden JSON files in its read-only mount. Admission freezes the complete set before discovery; every miner runs every task, with separate executions and mean per-task scores. Judge sessions have no action-count limit or fixed command/session timeout; existing round stops remain authoritative. The judge's instructions and trusted guides, not controller code, require a later observation of the resulting state for each state change and tell the model to bound its own commands. Each judge has one sandbox time budget ending `EXECUTOR_JUDGE_RESERVE_SECONDS` before its deadline; once spent it finishes with the evidence it has. Undemonstrated or failing behaviour is unmet; a project that cannot run at all is rejected as an invalid submission; missing evidence for judge-side reasons makes that evaluation unavailable. Decisions, execution captures, assessments and final reasons are retained in each report directory.

Ruff and basedpyright config is duplicated between `validator/pyproject.toml` and `miner/pyproject.toml`. When
changing tooling config, keep both in sync.

## Adapting this repository to a new subnet

Refer to `knowledge/tasks.project-bootstrap.md` for template workflows. The rendered-file checklist has
passed, and V2 supplies the user-approved design scope. No new design-approval phase is required. The guide
contains workflows for:

- Bootstrapping the template
- Designing the subnet
- Implementing the validator
- Setting up localnet
- Adapting this repository to a new subnet
- Generally bootstrapping the project

Use those workflows within V2's sequential tasks. Build an immutable candidate and verify isolated localnet;
do not change emissions. Since 23 November 2026 the user has deployed FactoryHorde as mechanism 1 of subnet 12 (`production/`).
For task 17, the user explicitly selected this existing VM on 2 October 2026: do not create or prepare another
VM. Use a separate localnet installation here and report that environment scope accurately.

# Knowledge base

## Preparing for tasks

Start by discovering the information available in the knowledge base with `find knowledge -type f | sort`
Crucially: Never summarize index files. Never delegate reading indices to agents or exploration tools. During
your tasks and conversations, eagerly read additional files if they could be relevant. After compaction,
re-read indices directly and read relevant files again so as not to forget crucial details.

## Bittensor domain

Whenever Bittensor domain knowledge is required, focus on the Bittensor knowledge files and skip the rest. It
is important to first understand the specifics of the Bittensor ecosystem, work with high-level concepts, and
iterate on the subnet's design rather than jumping straight into implementation details. Designing a subnet is
a complex reasoning process and requires careful consideration on multiple levels.

Contains, among others:

- how to frame subnet ideas into the bittensor ecosystem
- requirements and invariants that must be satisfied by a good subnet design
- theory behind validation, mining, incentives, miner-validator contract
- suggested external integrations and tools in the ecosystem

Index: knowledge/bittensor/INDEX.yaml

Recommended subnet design location: ./subnet_design.md (create when needed)

## Nexus

Nexus is the framework for building Bittensor subnet validators. It replaces the bittensor SDK for validator
development. All validator code runs inside Nexus — it is the complete runtime. You must use Nexus for
implementing the validator.

Nexus provides a large set of reusable components that handle common validator concerns. Before writing any
code, making any decisions, or responding with recommendations — discover what Nexus offers. It will likely
already handle most of the requirements of the subnet you are working on.

The Nexus knowledge base ships with the Nexus package — find it in `validator/.venv` within the installed
Nexus package under `docs/`. Make sure Nexus is installed first by running `uv sync` in `validator/`. Read
`docs/nexus.md` in the Nexus package — it is the grounding document for all validator implementation work.

Whenever working on validator code, double-check compliance with Nexus's best practices, coding guidelines,
requirements, and correct and optimal usage of Nexus components.

Skip reading Nexus KB for higher level tasks that do not touch the code.

### Pylon

Collect or honor `default_mechanism_id` during generation/design (default 0). When implementing
weights, pass `Settings.mechanism_id` (`MECHANISM_ID`) to both `SetWeightsBeatNode`
and `WeightSetterNode`.

Sidecar subtensor communication proxy. Nexus uses Pylon for all subtensor (blockchain) communication. The pylon
client's source code can be found and inspected in `validator/.venv`.

Skip for higher level tasks that do not touch the code.

### Observability

`envs/deployed/docker-compose.yml` uses pinned official Prometheus and node-exporter images.
Prometheus scrapes node-exporter (host network namespace, bound only to the `docker0` address at `NODE_EXPORTER_HOST_PORT`, reached through `host.docker.internal`), the validator on port 9101, Pylon's `/metrics` with `PYLON_METRICS_TOKEN`
and `claude-usage-exporter` on port 9102. That standalone sidecar exports the Claude subscription's 5h/7d budget
use from the rate-limit response headers in `inference-proxy`'s access log; it is deliberately outside the
validator so it can be dropped when the subscription is replaced.
Local configuration generates separate identity/open-access/metrics tokens. `COMPOSE_PROFILES=alloy` adds
pinned Grafana Alloy, which scrapes the same targets and pushes OTLP to the company monitoring stack's
gateway (`METRICS_UPSTREAM_URL`, default `https://alloy.reef.pl`) with `METRICS_UPSTREAM_USER`/`_PASSWORD`;
dashboards live in the central Grafana. The gateway stamps identity labels from the inventory in
`prometheus-grafana-monitoring`, so the Alloy config sets none. Refinery enables the profile. No remote-write
or tracing sidecar runs.

`monitoring.py` owns `/metrics`, `/livez` and `/readyz` through a Nexus actor, including server shutdown.
Readiness requires fresh runtime chain/executor/Docker observations, compatible records, writable paths and
reconciliation. The same collector exports the latest round's number, cohort size and window, per-miner
accepted scores and calculated weights from records, and the validator's permit, last weight update and
per-miner incentive of the configured mechanism from a separate Nexus actor with a throttled, bounded Pylon neuron read that never blocks readiness. Miner hotkeys are
the only identity labels; job and round IDs stay in records. `control/executor-health.json` is atomic file-only health evidence; it never proves workload
termination. The same validator scrape path forwards executor counters/histograms. There is no textfile collector
or executor HTTP server. Record I/O counters and latency histograms live in
`validator/src/validator/record_files.py`. The selected Nexus revision has no
reusable actor metrics registry. When you extend the validator (new payload creators, scorers, nodes, weight setters),
treat metrics as first-class and follow Nexus's own conventions: inspect the
installed Nexus package (`validator/.venv` after `uv sync`, starting from
`docs/nexus.md` and the package sources) to see how Nexus exposes and registers
metrics for its components (actors, engine...), and mirror that
approach when adding observability to your validator. Every new subsystem
should ship with at least one event counter and one latency histogram, named
consistently with the Nexus patterns you find there. If you expose a validator
`/metrics` endpoint, add it into `envs/deployed/docker-compose.yml`
scrape targets and update `installer/README.md`.

#### Distributed tracing

The validator emits OpenTelemetry traces, configured in `validator/src/validator/otel.py`
(rendered to `otel.py`) and wired in from `main()` right after `configure_logging`. Resource
attributes **deliberately carry no operator hotkey** — the observability proxy adds it downstream;
the structlog processors in `logging_config.py` stamp the same attributes onto every log line so logs
and traces correlate.

Tracing export is explicitly disabled in common Compose. The Alloy config handles metrics only;
the updater downloads only the checksummed common application/executor assets.

#### Structured logging

The validator logs exclusively via `structlog`. Logging and structlog are configured in
`validator/src/validator/logging_config.py`, tunable via `VALIDATOR_LOGGING_`-prefixed
environment variables.

Skip for higher level tasks that do not touch the code.

## localnet

Local development environment that allows running a subnet locally, as opposed to testnet or mainnet. KB
contains everything needed to set it up and operate it: templates, recipes, requirements, operational
guidelines, best practices, gotchas, and much more.

Index: knowledge/localnet/INDEX.md Localnet resources: localnet/*

Read when working on or debugging issues during development on localnet. Skip for higher level tasks that do
not touch the code.

## Coding guidelines

Location: knowledge/guidelines.coding-and-qa.md

Conventions, tooling, best practices, QA gates, comments, documentation, and more.

Read when working with any kind of code, be it validator, localnet, or any other code in this repository. Skip
for higher level tasks that do not touch the code.

# General hints

- use `uv` instead of `python` for managing dependencies, running scripts, entrypoints, ad-hoc code
    - `uv add ...` / `uv remove ...` / `uv sync` (+ `--all-groups`, `--all-extras`)
    - `uv run --with foo,bar ...` (with temporary dependencies)
    - `uv run python -c '...'` / `uv run some/script.py` (code or script)

# Documentation rules

Keep README.md, AGENTS.md, tests, docstrings, and code up to date and in sync. If one changes, update the
others. Whenever updated, all information, claims, guides, commands, etc. in these files must be verified and
tested. Take great care to avoid drift between these files.


---

`AGENTS.md` is the repository's sole agent instruction file.

The adaptive judge uses trusted guides in `judge/skills/` and an observe/act loop. Model commands go through the executor to a separate networkless application container; never execute submitted code or model-selected commands on the host or in the judge controller. The application receives the full project in writable `/app`, a read-only `/submission`, and no controller mounts, credentials or Docker socket. Python, Node.js and POSIX shell are packaged; additional dependencies must be bundled or the judge reports unavailable evidence. Persist each decision before execution and each attributed observation afterward; recovery must not replay an ambiguous command. The output contract accepts arbitrary nonempty regular project trees, with no fixed entry-point filenames.
