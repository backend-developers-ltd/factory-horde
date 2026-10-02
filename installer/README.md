# FactoryHorde operator installation

The installer and updater use the common Compose application and one host systemd
executor. The accepted topology is localnet only; the [immutable candidate](../envs/candidate/README.md)
supplies published images and configuration. Clean-host acceptance remains task 17. No subnet-12 or production configuration is
promoted by these instructions.

## Requirements and ownership

Use Linux amd64, Python 3.14 at `/usr/bin/python3.14`, Docker with Compose, systemd,
cron, sudo, Git and Bash. Run installation as the non-root operator with Docker
access. `install.sh` takes an explicit environment file and release selection;
there is no default public network or subnet. The operator owns the installation,
shared data and workload output with matching numeric UID/GID. Default resources
are 512 MiB, one CPU, 128 PIDs, 32 executor workers and two-second polling.

The installation directory defaults its data root to `<installation>/data` and
mounts that root into the validator at `/var/lib/factory-horde`. Choosing
`$HOME/factory-horde` therefore gives `$HOME/factory-horde/data`. An explicit
`FACTORY_HORDE_DATA_ROOT` is supported outside localnet; localnet requires its
`data` and `wallets` below the isolated installation. Canonical absolute paths may
contain letters, numbers, slashes, dots, underscores and hyphens, with no symlinks.

The executor runs from `<installation>/executor/executor.py`, with only its resource
settings in `executor.env`. It has no package installation or virtual environment.
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
executor is not replaced/restarted. Compatible detached workloads keep running;
requests, permanent stops, Docker identities and accepted scores remain in place.

After replacement, the updater requires a fresh protocol-compatible health record
from the current systemd PID with a successful Docker probe. Health failure exits
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

## Monitoring and recovery

Common Compose uses pinned Prometheus and node-exporter images. Prometheus scrapes
host metrics, validator port 9101 and authenticated Pylon metrics. Its UI binds only
to host loopback. The validator owns `/metrics`, `/livez` and `/readyz` through Nexus;
the executor stays file-only. Readiness distinguishes missing/stale chain, executor
or Docker observations, unreconciled work, incompatible records and unwritable
control/result paths. Pylon acknowledgement is not independent chain proof.

Trace export, Alloy and remote-write remain disabled; no upstream credentials are
required. See [validator operations](../validator/README.md), the
[file protocol](../spec/file-protocol.md), and [localnet monitoring](../localnet/README.md#monitoring).
Keep unresolved requests, stops, ledgers, containers and results for diagnosis.
Restoring Docker access and restarting the same service resumes reconciliation;
missing expected executions are not permission to create replacements.
