# FactoryHorde operator installation

The verified prototype setup is currently the [localnet procedure](../localnet/README.md).
It uses the common `envs/deployed/docker-compose.yml` services plus the isolated
local-chain overlay. Public installation is not yet ready: the common validator
image reference is a placeholder, and the inherited `install.sh`/`update_compose.sh`
are adapted for coherent application/executor installation in tasks 7 and 15.
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
3.14, `UMask=0027`, Docker-group access and automatic service restart. Task 7 supplies
the single-file executor and actual systemd installation. Operator ownership and
fixed job mounts let factory/judge containers write only their assigned output or
report locations. They receive no wallets or control root.

Docker access grants host-level control. The executor is trusted; the validator has
no Docker socket or wallet mount. Pylon alone mounts wallets, read-only. Localnet
keys are disposable funding keys under `localnet/wallets`, isolated from production
wallet paths. Production installation must supply only the required hotkeys/public
coldkeys and explicit network/subnet configuration.

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
