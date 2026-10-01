# FactoryHorde operator installation

The verified prototype setup is currently the [localnet procedure](../localnet/README.md).
It uses the common `envs/deployed/docker-compose.yml` services plus the isolated
local-chain overlay. Public installation is not yet ready: the common validator
image reference is a placeholder, and the inherited `install.sh`/`update_compose.sh`
will be adapted for coherent application/executor installation in task 15.
Do not use those inherited scripts as the prototype startup procedure.

## Implemented host preparation

Run `prepare-data-root.sh /absolute/data/root` as the operator account selected for
the executor. The localnet preparation command already invokes it for
`localnet/state/data`. It creates the protocol directories with owner/primary group
matching that account, mode `0750`, and rejects symlinks or differently owned existing
directories. It preserves all existing evidence files.

The validator uses the same numeric `CONTAINER_UID`/`CONTAINER_GID` and mounts the
host root read-write at `VALIDATOR_DATA_ROOT` (default `/var/lib/factory-horde`).
The host executor service contract selects `EXECUTOR_USER`/`EXECUTOR_GROUP`, Python
3.14, `UMask=0027`, Docker-group access and automatic service restart. Operator ownership and
fixed job mounts let factory/judge containers write only their assigned output or
report locations. They receive no wallets or control root.

Docker access grants host-level control. The executor is trusted; the validator has
no Docker socket or wallet mount. Pylon alone mounts wallets, read-only. Localnet
keys are disposable funding keys under `localnet/wallets`, isolated from production
wallet paths. Production installation must supply only the required hotkeys/public
coldkeys and explicit network/subnet configuration.

## Install the host executor

After preparing localnet, run from the repository root:

```sh
installer/install-executor.sh "$PWD/localnet/.env" factory-horde-localnet-executor
systemctl status factory-horde-localnet-executor --no-pager
journalctl -u factory-horde-localnet-executor --no-pager -n 20
```

Run as the configured non-root operator; the installer uses sudo only for systemd
unit installation/reload/restart. It copies one Python file and a restricted
executor-only environment next to the data root. No uv environment, third-party
packages, Pylon tokens or wallet keys are installed for the service. The unit
allows writes only under its data root. Docker workloads run detached, with no
restart policy, network access, added capabilities or writable root filesystem.

The executor pulls public GHCR digests using its own empty Docker configuration.
Its journal records Docker operation outcomes and durations. Current observations,
terminal evidence and metrics snapshots are under `control/`; failed observation
is unresolved, never proof that a container has stopped. Keep these records and
containers when diagnosing a failure. Concurrent cancellation/recovery acceptance
is task 8; coherent verified updates remain task 15.

## Current monitoring

Common Compose uses pinned official Prometheus and node-exporter images. Prometheus
scrapes node-exporter and Pylon's `/metrics` with `PYLON_METRICS_TOKEN`. Its UI is
bound to host loopback on port 9090. Pylon's identity, open-access and metrics tokens
are distinct. The validator currently records I/O/chain counters and histograms but
does not expose a scrape endpoint; task 14 adds metrics exposure and full readiness.

Tracing export and remote-write are disabled. No Alloy/proxy service or upstream
credentials are required for this prototype topology. The inherited Alloy asset is
unused. See the [sequential plan](../spec/FactoryHorde-v2-sequential-implementation-tasks.md)
for remaining installer, candidate-image and clean-host acceptance work.
