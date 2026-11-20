# FactoryHorde operator installation

The installer and updater use the common Compose application and one host systemd
executor. Historical localnet acceptance used the [immutable candidate](../envs/candidate/README.md). Refinery uses the same operator release path with its own chain settings and current published images. [End-to-end acceptance](../spec/evidence/task17-acceptance.json)
passed in a separate installation on the existing user-selected VM. Follow the
[packaged reproduction sequence](../localnet/README.md#packaged-candidate-acceptance-on-this-vm).
No subnet-12 or production configuration is promoted by these instructions.

## Published operator installation

Build and promote images through [the deployment procedures](../knowledge/validator.deploy.md). On the operator host, select the configuration branch explicitly and resolve it once before downloading the installer. The installation directory is runtime state, not a Git checkout.

```sh
RELEASE_REF=deploy-config-production # use deploy-config-refinery for Refinery
RELEASE_SHA=$(git ls-remote https://github.com/backend-developers-ltd/factory-horde.git "refs/heads/$RELEASE_REF" | cut -f1)
test "${#RELEASE_SHA}" -eq 40
RELEASE_URL="https://raw.githubusercontent.com/backend-developers-ltd/factory-horde/$RELEASE_SHA"
BOOTSTRAP=$(mktemp -d)
curl -fsSL "$RELEASE_URL/installer/install.sh" -o "$BOOTSTRAP/install.sh"
curl -fsSL "$RELEASE_URL/installer/release.py" -o "$BOOTSTRAP/release.py"
# Prepare a literal operator environment file before running this command.
bash "$BOOTSTRAP/install.sh" "$HOME/factory-horde" --env-file "$HOME/operator.env" --ref "$RELEASE_REF"
```

The first installation validates and applies a complete release; subsequent cron runs follow the chosen configuration branch. If a release changes the manifest's asset set, bootstrap the new installer as above and rerun installation against the existing directory; its `.env`, wallet paths and data are preserved.

## Requirements and ownership

Use Linux amd64, Python 3.14 at `/usr/bin/python3.14`, Docker with Compose, systemd,
cron, sudo, Git and Bash. Run installation as the non-root operator with Docker
access. `install.sh` takes an explicit environment file and release selection;
there is no default public network or subnet. The operator owns the installation,
shared data and workload output with matching numeric UID/GID. Default resources
are 512 MiB, one CPU, 128 PIDs, a 64 MiB `/tmp`, 32 executor workers and two-second polling.

The installation directory defaults its data root to `<installation>/data` and
mounts that root into the validator at `/var/lib/factory-horde`. Choosing
`$HOME/factory-horde` therefore gives `$HOME/factory-horde/data`. An explicit
`FACTORY_HORDE_DATA_ROOT` is supported outside localnet; localnet requires its
`data` and `wallets` below the isolated installation. Canonical absolute paths may
contain letters, numbers, slashes, dots, underscores and hyphens, with no symlinks.

The executor runs from `<installation>/executor/executor.py`, with only its resource
and factory network settings in `executor.env`. It has no package installation or virtual environment.
Its system unit uses the configured operator/primary group, Docker supplementary
group, `UMask=0027`, a read-only system/home view and writes only under its data
root. Docker access grants host-level control and belongs to the trusted executor.
The validator has no Docker socket or wallet mount. Pylon alone mounts wallets
read-only; workload containers receive only their assigned input/output/report paths.

Initial installation uses administrator privileges to install/enable the system
unit, a sudoers rule and `/etc/cron.d/<service>`. The fifteen-minute cron entry runs
as the non-root operator and writes `<installation>/update.log`. Its only added
sudo privilege is exactly:

```text
<operator> ALL=(root) NOPASSWD: /usr/bin/systemctl restart <service>.service
```

The updater uses `sudo -n`; it cannot depend on an interactive password or cached
sudo session. A system-unit or service identity/path change requires rerunning the
installer with administrator privileges. Runtime resource changes in `.env` update
`executor.env` and restart that same unit. There is no broad systemctl grant.

## Release assets and update behavior

`installer/release.py` is the standard-library helper behind the existing shell
entrypoints. A remote selection is either a full Git commit SHA or an explicit
`deploy-config-*` branch. A branch resolves once with `git ls-remote`; all downloads
then use that same SHA. When that release ships a different `installer/release.py`, the installed updater checks it against the release manifest and lets it apply the same SHA, so a release's own update logic, such as new executor settings, takes effect in one run. `installer/release-manifest.json` lists the complete fixed
asset set and its SHA-256 hashes, protocol 1, Linux amd64 and Python 3.14 minimum.
`executor/executor.sha256` publishes the standalone checksum too.

The updater locks the installation before downloading release assets; only the updater comparison happens earlier. A concurrent invocation exits
75 without changing installed assets. All downloads, checksums, executor protocol,
existing request/stop/status headers and Compose syntax are checked first. The
verified executor is copied to a temporary file on its destination filesystem,
fsynced, renamed atomically and directory-fsynced before the service restarts.
Configuration and updater assets come from the same verified selection. An unchanged
executor is not replaced/restarted unless activation is pending or the service is
inactive. Before replacing executor inputs, `executor-activation.json` durably
records pending activation. Retrying an interrupted or failed update completes the
restart even if installed bytes already match. Compatible detached workloads keep running;
requests, permanent stops, Docker identities and accepted scores remain in place.
Application updates similarly persist `application-activation.json` before replacing
Compose assets. Pending activation survives interrupted replacement, failed Compose
startup and preparation-only runs; it clears only after `docker compose up --wait`
succeeds. With the `alloy` profile enabled, activation also recreates Alloy to load its mounted configuration; a failure leaves activation pending for the next update. An ordinary update retries activation even when asset bytes already match.

Executor shutdown interrupts local Docker CLI process groups, including a pull
still waiting on the registry, and cancels queued workers. Interrupted calls remain
unresolved; detached containers keep running and the next executor reconciles the
same durable create/start/stop intent. The pull's normal timeout remains 600 seconds,
but shutdown checks it every 0.2 seconds. The system unit allows 30 seconds before
forced termination, within the default five-minute confirmation reserve. The unit
uses `KillMode=mixed`: TERM goes to the executor, which interrupts its clients;
systemd's forced termination still covers the whole service group. Installing
the changed unit requires installation privileges; ordinary updates do not replace
system units. The interruptible shutdown also works with an already installed unit.

After replacement, the updater requires a fresh protocol-compatible health record
from the current systemd PID with a successful Docker probe. After restart, its
process start time must also follow the activation attempt; a fresh heartbeat from
the old process cannot clear pending activation. Health failure exits
nonzero, leaves the selected file installed and records the failure in
`applied-release.json`. Repair the configuration or select a working release using
ordinary installation/update commands. There is no automatic rollback, rejected
release tracking or parallel old/new executor. Never delete execution evidence to
force progress.

`.env` remains operator-owned and is preserved on repeated installation. New
installations generate distinct Pylon identity, open-access and metrics tokens;
localnet also generates five miner tokens. Configuration is literal `KEY=value`,
optionally shell-quoted, without duplicate keys or variable/command expansions.
`installation.json` records the selected source/ref, service, Compose project and
fixed service identity; `applied-release.json` records the applied manifest/revision
and health outcome. Do not edit downloaded Compose files: change repository sources
and regenerate their manifest before selecting a release.

For a developer source change, run from the repository root before committing:

```sh
env -u UV_EXCLUDE_NEWER uv run --project validator python installer/release.py manifest "$PWD"
env -u UV_EXCLUDE_NEWER uv run --project validator python installer/release.py manifest "$PWD" --check
```

A `file://` source with `--ref snapshot` is available for source-checkout development.
It has the same checksum validation but is not an immutable published candidate.
For remote operation, `--ref <full-commit-sha>` selects a fixed release; a
`deploy-config-*` ref opts into following that configuration branch on subsequent
cron checks. Build/publish and configuration promotion stay separate.

## Isolated installation from a source checkout

The repeatable real installer check builds a separate local chain, wallets, service
and common application stack, using the existing local validator image:

```sh
localnet/build-validator.sh
env -u UV_EXCLUDE_NEWER uv sync --project miner --group bootstrap
env -u UV_EXCLUDE_NEWER uv run --project validator python -m localnet.check_installer
```

It verifies clean/repeated installation, unchanged updates, failed HTTP downloads,
bad checksums, incompatible protocol, concurrent updater exclusion, replacement
while actual factories run, unchanged stops and accepted scores, visible health
failure and ordinary repair. It also runs an actual cron update as a fresh account
with Docker access and only the exact restart grant, checking that a different
system unit cannot be restarted. The check stops its application/executor and
removes its cron/sudoers entries; raw data, logs and chain volumes remain under
`localnet/state/installer-*`, with a public summary in `state/task15-installer.json`.
The temporary permission-check account is removed while its evidence home remains.

For manual use, the startup order is the same installer plus local-chain bootstrap:

1. Prepare a literal operator environment with `ENVIRONMENT=localnet`, `NETUID=2`,
   `BITTENSOR_NETWORK=ws://subtensor:9944`, `MECHANISM_ID=1`,
   `BLOCK_DURATION_SECONDS=0.25`, unused `SUBTENSOR_HOST_PORT`, `PYLON_HOST_PORT` and
   `PROMETHEUS_HOST_PORT`, and an explicit `VALIDATOR_IMAGE`. Other identity/path/token
   defaults are generated. Dispatch and weight writes default to false.
2. Run `installer/install.sh /absolute/installation --env-file /absolute/operator.env
   --ref snapshot --source file:///absolute/source-checkout --service
   factory-horde-demo-executor --project factory-horde-demo --localnet --prepare-only`.
   This installs and starts the executor and prepares the application files/cron.
3. Start only Subtensor with `docker compose --project-name factory-horde-demo
   --env-file /absolute/installation/.env -f /absolute/installation/envs/deployed/docker-compose.yml
   -f /absolute/installation/localnet/compose.yml up -d --wait subtensor`.
4. Bootstrap with `env -u UV_EXCLUDE_NEWER uv run --project miner --group bootstrap
   python localnet/bootstrap.py --env-file /absolute/installation/.env`.
5. Run `/absolute/installation/installer/update_compose.sh /absolute/installation`
   to start the common application. Later invocations use the same command, optionally
   adding `--ref <new-revision>` to select a compatible update.

Prepare-only leaves the application stopped for bootstrap. The normal updater starts
it after bootstrap, including when no release bytes changed. The fifteen-minute cron
entry also invokes that normal updater, so complete bootstrap before its next run.
For an intentionally longer preparation, temporarily disable that installation's
cron entry as the administrator and restore it once bootstrap completes.

For executor-only development/fault fixtures, the existing interface remains:

```sh
installer/install-executor.sh "$PWD/localnet/.env" factory-horde-localnet-executor
systemctl status factory-horde-localnet-executor --no-pager
journalctl -u factory-horde-localnet-executor --no-pager -n 20
```

It validates the source manifest and installs next to the configured data root,
without installing an application cron job. Use the full installer for candidate
acceptance. `prepare-data-root.sh /absolute/data/root` remains the independent
ownership/permissions preparation command.

## Task specification and inference proxy

`TASK_SPECIFICATION` in `.env` is the task text given to every factory in new rounds. Write it on one line in single quotes, without `$` or backticks. Unset or empty keeps the built-in greeting task, and a round that is already admitted keeps the text it started with.

Set `TASK_REQUIREMENTS` to a single-quoted JSON array of distinct criteria, for example `'["Add a task.","Persist tasks across invocations."]'`. An empty list uses the whole specification as a single criterion. The validator freezes the list, `JUDGE_MODE` and the optional `JUDGE_MODEL` (a Claude Code alias or full model ID; empty uses the judge image's default) at admission, so changing the model affects only new rounds. Review is the default. Use `JUDGE_MODE=fixture` explicitly for offline localnet checks.

Workloads run without a network by default. Factories that call Claude need the inference proxy, which three settings enable together:

- `COMPOSE_PROFILES=inference` (or `alloy,inference`) starts the `inference-proxy` service and creates the internal Docker network `factory-horde-inference`. This profile requires Docker Engine 28 or newer.
- `CLAUDE_CODE_OAUTH_TOKEN` is a Claude subscription token created with `claude setup-token`. It stays in `.env` on the validator host, readable only by the operator, and reaches only the proxy container. It is never written to `executor.env` or given to a factory.
- `EXECUTOR_FACTORY_NETWORK=factory-horde-inference` makes the executor attach each factory and judge to its own internal network connected only to that configured network's inference proxy and give them `ANTHROPIC_BASE_URL` (`EXECUTOR_INFERENCE_URL`, default `http://inference-proxy:8080`) and the placeholder `CLAUDE_CODE_OAUTH_TOKEN=inference-proxy`. Review mode uses this proxy. A missing proxy setting makes the round ineligible for review weights. The same variable names the network in Compose, so one value keeps both sides in step.

`EXECUTOR_REGISTRY_NETWORK=factory-horde-registry` lets judges install a project's declared dependencies. The same profile starts `registry-proxy`, a Squid forward proxy that tunnels only to `pypi.org`, `files.pythonhosted.org` and `registry.npmjs.org`. For one fixed install command, the executor moves the judge's application sandbox from `--network none` onto a job-owned internal network whose only peer is that proxy. Every other sandbox process is killed first, pip runs isolated from the project and installs prebuilt wheels for plain index requirements only, and npm runs without lifecycle scripts or a configurable Git executable, so no project code runs while the network is attached. The executor verifies that the sandbox is back on `none` before any other command. Without this setting, install requests are answered as unavailable.

The network has no route to the internet or to the host. The proxy forwards only `/v1/messages*` to `https://api.anthropic.com`, replaces the `Authorization` header with the real token and answers 404 for any other path. Peer workloads are isolated on separate networks. The proxy applies no per-workload quota, so every factory and judge in a round draws on the same subscription. Application checks run in separate networkless containers with 256 MiB each for `/app` and `/tmp`; the executor must confirm their termination before accepting a judge exit.

Real factories usually also need more than the default resources: `EXECUTOR_MEMORY`, `EXECUTOR_PIDS_LIMIT` and `EXECUTOR_TMP_SIZE` (a size such as `64m` or `1g`) apply to factories and judges alike. `EXECUTOR_JUDGE_RESERVE_SECONDS` (default 180) ends each judge's shared sandbox time budget that long before the judge deadline, leaving time for its final assessment.

`EXECUTOR_MAX_RUNNING_JOBS` (default 4) caps how many factory or judge containers exist at once; a judge's application sandbox shares its slot. Round deadlines never stretch, so set it high enough for every job of a stage (miners × tasks) to run together, within the host's memory and CPU. A job waiting for a slot reports `Waiting for a workload slot` and logs `workload_slot_wait` at error level; one whose window ends before it starts logs `job_never_created`, and its miner is excluded from the round as a validator-side gap (`miner_excluded` at error level), not as a rejected submission. Terminal jobs whose containers Docker left in the created state do not hold a slot.

Stopped factory, judge and judge-application containers are kept for a week so their `docker logs` remain available when a job needs investigating. The executor prunes older ones hourly (`containers_pruned`, or `container_prune_failed` at error level).

Each networked workload gets its own small internal network. Docker's default address pools allow only about 31 networks per host, so give it a dedicated pool before raising the limit, for example in `/etc/docker/daemon.json`:

```json
{
  "bip": "172.17.0.1/16",
  "default-address-pools": [{"base": "10.200.0.0/16", "size": 24}],
  "live-restore": true
}
```

That allows 256 new networks of 254 hosts each; existing networks keep their subnets and `bip` pins the default bridge. Choose a base that overlaps no existing network or host route (`ip -4 route`). `live-restore` keeps containers running across later daemon restarts, but the restart that applies this file still stops all containers unless it was already enabled, so apply it when no factory or judge is running (`docker ps --filter label=factory_horde.job_id` is empty). Validate first with `sudo dockerd --validate --config-file /etc/docker/daemon.json`, then `sudo systemctl restart docker`. Enable the profile and the token before or together with `EXECUTOR_FACTORY_NETWORK`: while the network does not exist, Docker refuses to start factory and judge containers and those jobs fail without a score.

## Monitoring and recovery

Common Compose uses pinned Prometheus and node-exporter images. Prometheus scrapes
host metrics, validator port 9101 and authenticated Pylon metrics. Its UI binds only
to host loopback. The validator owns `/metrics`, `/livez` and `/readyz` through Nexus;
the executor stays file-only. Readiness distinguishes missing/stale chain, executor
or Docker observations, unreconciled work, incompatible records and unwritable
control/result paths. Pylon acknowledgement is not independent chain proof.

`COMPOSE_PROFILES=alloy` in `.env` adds Grafana Alloy, which scrapes the same three targets and pushes them as OTLP to the company monitoring stack, where they are read in the central Grafana. Without the profile Alloy does not start and nothing leaves the host. The profile needs these `.env` values:

- `METRICS_UPSTREAM_USER` and `METRICS_UPSTREAM_PASSWORD`: the deployment's login on the monitoring server, created there with `./scripts/add_loki_target.sh <server_group> <env>` from `prometheus-grafana-monitoring`.
- `METRICS_UPSTREAM_URL`: the OTLP gateway, `https://alloy.reef.pl` by default.

The login also needs an entry in that repository's `scripts/otel_inventory.yml`; the gateway refuses logins without one. The gateway stamps `client_server_group`, `environment`, `host` and the other identity labels from the inventory, so this repository sets none of them. Series arrive in Mimir as `job="factory-horde/<job>"` (`node-exporter`, `pylon`, `validator`) with the scrape address as `instance`; filter on `host` to select the machine. Alloy's scrape converter carries no HELP or TYPE, so every family arrives as a gauge; values, `rate()` and `histogram_quantile()` are unaffected. The Alloy configuration is the release asset [`envs/deployed/alloy/config.alloy`](../envs/deployed/alloy/config.alloy).

Per-miner validator series are labelled with the miner hotkey. The permit, last weight update and incentive come from one Pylon neuron read a minute; Pylon does not expose the weights stored on chain or pending commit-reveal commits, so the validator exports calculated weights and the resulting incentive instead. Evaluated image digests stay in the records. The local Prometheus keeps up to 1 GB of samples. node-exporter runs in the application's bridge network, so it reports no host network traffic.

Trace export and remote-write remain disabled; only the `alloy` profile needs upstream
credentials. See [validator operations](../validator/README.md), the
[file protocol](../spec/file-protocol.md), and [localnet monitoring](../localnet/README.md#monitoring).
Keep unresolved requests, stops, ledgers, containers and results for diagnosis.
Restoring Docker access and restarting the same service resumes reconciliation;
missing expected executions are not permission to create replacements.

### Server task files

The installer creates its default `tasks/` directory as the operator without replacing its contents. Set `TASK_DIRECTORY` to an absolute operator-owned directory. The validator discovers every non-hidden `*.json` file directly in that directory at each new admission; no `TASK_FILE` selector is needed. Filenames are descriptive operator choices, such as `todo.json` or `calculator.json`; `current.json` has no special meaning. Each file contains one JSON object with `specification` and an optional ordered `requirements` list. The directory is mounted read-only at `/tasks` and passed to the validator as `VALIDATOR_TASK_DIRECTORY`.

All discovered tasks run for every miner in every round, with separate factory and judge executions and isolated input/output directories per task. Admission freezes the complete task set before discovery and persists every execution identity before publishing work. Recovery uses that snapshot, including when source files are edited, added or removed. The round score is the equal-weight mean of a miner’s task scores. A miner must have an accepted score for every task to participate; any missing score, whether from a submission failure or unavailable judging, excludes only that miner, and the other miners are still weighted. Existing 90/10 allocation and tie handling apply to the mean scores.

Write a complete replacement to a hidden or non-JSON temporary file, then rename it atomically to its final `.json` name. An empty configured directory or any invalid task blocks new admissions while existing work continues to reconcile. Omitted or empty requirements use the whole specification. Without `TASK_DIRECTORY`, the existing `TASK_SPECIFICATION` and `TASK_REQUIREMENTS` settings apply. Task files are operator data and are not release assets. When upgrading an installation that used `TASK_FILE`, retain `TASK_DIRECTORY` and remove the obsolete `TASK_FILE` setting.
