# Context

FactoryHorde is a localnet-first orchestration prototype for software factories submitted as immutable
container images. A Nexus validator coordinates host Docker execution through shared files; a separate judge
produces persistent random fixture scores. No inference, quality validation or public deployment is included.
Origin: the Copier-rendered Nexus subnet template.

Follow `spec/FactoryHorde-v2-sequential-implementation-tasks.md` in order and use `subnet_design.md` for the
selected implementation shape. V2 overrides generic HTTP-miner, validator-only and host-tmux recipes. Mark a
task `[DONE]` only when its required evidence exists, then commit it with a short title and no body. The
repository is already rendered: do not rerun Copier. New component locations in the design are planned until
their implementation tasks complete.

## Repository layout

This is a monorepo with two **independent** uv projects plus shared local-development tooling:

- `validator/` — Nexus-based subnet validator (own `pyproject.toml`, `uv.lock`, `.venv`); also holds the
  production `Dockerfile`
- `miner/` — submission tooling and baseline factory assets (own `pyproject.toml`, `uv.lock`, `.venv`);
  `miner` is a one-shot Pylon submitter; the `bootstrap` dependency group holds the local-chain SDK
- `localnet/` — Local subtensor + pylon + bootstrap + miner fixtures for end-to-end development
- `installer/` — rendered validator installer scripts (`install.sh`, `update_compose.sh`, `README.md`)
- `envs/deployed/` — rendered application `docker-compose.yml` (validator + pylon);
  the rendered repo is promoted on the `deploy-config-production` branch, with this compose file and the
  installer scripts as the operator-critical files
- `.github/workflows/` — rendered CI; `build-validator.yml` builds and pushes the validator
  image to GHCR on push to `deploy-build-*` branches; `build-baselines.yml` publishes
  the factory and judge images when their sources change
- `knowledge/` — Bittensor / Nexus / localnet domain knowledge
- `docs/` — additional documentation
- `spec/file-protocol.md`, `spec/fixtures/protocol-v1/` — implemented record contract and fixtures

There is **no** top-level Python project and **no** uv workspace. Run `uv sync` inside `validator/` or `miner/`
before working on it. There is no global `uv run` from the repo root.

See `localnet/README.md` for the common Compose services and isolated bootstrap. Validator dispatch is
disabled until the application task graph exists. The host systemd executor is implemented;
`installer/install-executor.sh` installs its standalone file and unit. Concurrent dispatch, permanent
cancellation and Docker reconciliation have focused unit and real systemd fault checks. Development tests
live beside it but are not deployed. `result_repository.py` owns immutable execution decisions
and accepted scores; `result_store.py` supplies public Nexus projections with rebuildable indexes.
The task graph is wired in task 10.

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
do not promote configuration to active operators, deploy to subnet 12 or change emissions for this prototype.

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
Prometheus scrapes node-exporter and Pylon's `/metrics` with `PYLON_METRICS_TOKEN`.
Local configuration generates separate identity/open-access/metrics tokens. No remote-write
or tracing sidecar runs, and no upstream credentials are needed.

The validator has no `/metrics` endpoint yet. Record I/O counters and latency histograms live in
`validator/src/validator/record_files.py`; scrape exposure is task 14. The selected Nexus revision has no
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

Tracing export is explicitly disabled in common Compose. The inherited Alloy config and updater are
not the active prototype topology; coherent installer/update behavior is implemented in task 15.

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
