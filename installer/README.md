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
then use that same SHA. `installer/release-manifest.json` lists the complete fixed
asset set and its SHA-256 hashes, protocol 1, Linux amd64 and Python 3.14 minimum.
`executor/executor.sha256` publishes the standalone checksum too.

The updater locks the installation before downloads. A concurrent invocation exits
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
succeeds. With the Grafana profile enabled, activation also recreates Grafana and Caddy to load their mounted provisioning and Caddyfile; a failure leaves activation pending for the next update. An ordinary update retries activation even when asset bytes already match.

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

Workloads run without a network by default. Factories that call Claude need the inference proxy, which three settings enable together:

- `COMPOSE_PROFILES=inference` (or `grafana,inference`) starts the `inference-proxy` service and creates the internal Docker network `factory-horde-inference`. This profile requires Docker Engine 28 or newer.
- `CLAUDE_CODE_OAUTH_TOKEN` is a Claude subscription token created with `claude setup-token`. It stays in `.env` on the validator host, readable only by the operator, and reaches only the proxy container. It is never written to `executor.env` or given to a factory.
- `EXECUTOR_FACTORY_NETWORK=factory-horde-inference` makes the executor attach factory containers to that network and give them `ANTHROPIC_BASE_URL` (`EXECUTOR_INFERENCE_URL`, default `http://inference-proxy:8080`) and the placeholder `CLAUDE_CODE_OAUTH_TOKEN=inference-proxy`. Judges stay without a network. The same variable names the network in Compose, so one value keeps both sides in step.

The network has no route to the internet or to the host. The proxy forwards only `/v1/messages*` to `https://api.anthropic.com`, replaces the `Authorization` header with the real token and answers 404 for any other path. Factories that run at the same time share the network and can reach each other, and the proxy applies no per-factory quota, so every factory in a round draws on the same subscription.

Real factories usually also need more than the default resources: `EXECUTOR_MEMORY`, `EXECUTOR_PIDS_LIMIT` and `EXECUTOR_TMP_SIZE` (a size such as `64m` or `1g`) apply to factories and judges alike. Enable the profile and the token before or together with `EXECUTOR_FACTORY_NETWORK`: while the network does not exist, Docker refuses to start factory containers and those factories fail without a score.

## Monitoring and recovery

Common Compose uses pinned Prometheus and node-exporter images. Prometheus scrapes
host metrics, validator port 9101 and authenticated Pylon metrics. Its UI binds only
to host loopback. The validator owns `/metrics`, `/livez` and `/readyz` through Nexus;
the executor stays file-only. Readiness distinguishes missing/stale chain, executor
or Docker observations, unreconciled work, incompatible records and unwritable
control/result paths. Pylon acknowledgement is not independent chain proof.

`COMPOSE_PROFILES=grafana` in `.env` adds Grafana with the provisioned validator
dashboard and Caddy, which serves it over HTTPS on ports 80 and 443 at
`GRAFANA_ADDRESS`. Without the profile neither service starts. The Caddyfile, provisioning and dashboard are release assets
under `envs/deployed/`. Caddy obtains a Let's Encrypt certificate for `GRAFANA_ADDRESS`
(for a bare IP address the six-day `shortlived` profile, renewed automatically), so
ports 80 and 443 must be reachable from the internet. Anonymous access and sign-up are
disabled.

**Grafana's first login is `admin` / `admin`, and it is reachable from the internet.**
Log in right after the first start and set a new password when Grafana asks for one.
The password lives in Grafana's database, not in `.env`; change it later under the
profile menu, or reset a lost one with
`docker compose exec grafana grafana cli admin reset-admin-password <new password>`.

The provisioned dashboard **FactoryHorde validator** is Grafana's home page. Grafana rereads [`factory-horde-validator.json`](../envs/deployed/grafana/dashboards/factory-horde-validator.json) every minute, so an updated release shows up without a restart; changes saved in the UI are not kept. Its sections are:

- **Status:** readiness, scrape targets, round phase, active and unresolved jobs, usable scores, age of the latest usable round and validator uptime.
- **Liveness:** when each pipeline step last happened (chain beat, commitment discovery, round admission, image pull, container start, job completion, accepted judge score, weight calculation, Pylon acknowledgement and on-chain weight commit), a timeline of these events, observation ages and individual readiness checks.
- **Rounds and weights:** round phase over time, the current round's number, cohort size and end, each miner's accepted score, calculated weight and on-chain incentive, the validator's permit and last weight update, discovery, coordinator and result events, job gauges and the weight pipeline.
- **Host executor:** Docker operations, errors and latency.
- **Pylon and chain:** subtensor operations and Pylon API requests with latency.
- **Validator internals:** shared-record I/O, handler latency, process memory and CPU, readiness samples.
- **Server:** CPU, memory, load and disk from node-exporter.

Per-miner series are labelled with the miner hotkey. The permit, last weight update and incentive come from one Pylon neuron read a minute; Pylon does not expose the weights stored on chain or pending commit-reveal commits, so the dashboard shows calculated weights and the resulting incentive instead. Evaluated image digests stay in the records. Event counts come from Prometheus `increase` and are approximate, and the Last activity panel looks back seven days. Prometheus keeps up to 1 GB of samples. node-exporter runs in the application's bridge network, so the Server section shows no host network traffic.

Trace export, Alloy and remote-write remain disabled; no upstream credentials are
required. See [validator operations](../validator/README.md), the
[file protocol](../spec/file-protocol.md), and [localnet monitoring](../localnet/README.md#monitoring).
Keep unresolved requests, stops, ledgers, containers and results for diagnosis.
Restoring Docker access and restarting the same service resumes reconciliation;
missing expected executions are not permission to create replacements.
