# FactoryHorde localnet

This is the task-4 application foundation, with factory dispatch disabled. It runs
the common validator, Pylon and monitoring services plus a local Subtensor overlay.
Factory/judge source-build checks now run through `localnet/build-baselines.sh`.
Public baseline images are available on GHCR; see [image evidence](../spec/evidence/task5-published-images.json).
Containerized submissions and frozen discovery are implemented; the host systemd
executor and complete rounds remain incomplete; successful startup alone is not V2 acceptance.

## Prerequisites

Linux amd64, Docker with Compose, systemd, Python 3.14, uv, Bash, OpenSSL and GNU
coreutils. The operator account must have Docker access. Use a canonical checkout
path without shell metacharacters or spaces for the generated `.env` file.

Run these commands from the repository root:

```sh
env -u UV_EXCLUDE_NEWER uv sync --project miner --group bootstrap
env -u UV_EXCLUDE_NEWER uv sync --project validator
localnet/prepare.sh
localnet/build-validator.sh
localnet/compose.sh up -d --wait subtensor
env -u UV_EXCLUDE_NEWER uv run --project miner --group bootstrap python localnet/bootstrap.py
localnet/compose.sh up -d --wait pylon validator node-exporter prometheus
env -u UV_EXCLUDE_NEWER uv run --project validator python localnet/check.py
```

Allow one 15-second scrape interval before the final check. `prepare.sh` preserves
existing tokens and files. `build-validator.sh` records the local immutable Docker
image ID in `state/validator-image.id`; this is a source-build smoke check, not a
published registry digest or final release candidate.

`compose.sh` always combines `envs/deployed/docker-compose.yml` with the local
overlay. It rejects non-local endpoints, netuid other than 2, or wallets/data outside
the isolated paths. Bootstrap also enforces isolation before opening any wallet.
No application command uses tmux or runs the validator on the host.

## Identities and persistence

Bootstrap creates an owner, validator and five miners under `localnet/wallets/`,
funds them from local Alice and registers them sequentially. It activates subnet 2,
sets tempo 360, disables commit-reveal, and enables mechanism 1. The selected
runtime requires a smaller UID capacity for two mechanisms; local bootstrap uses
64, matching Pylon's integration fixture. Existing registrations, funding and stake
are retained on repeat runs.

| Pylon identity | Wallet / hotkey | Token variable |
|---|---|---|
| `validator` | `validator` / `default` | `VALIDATOR_PYLON_IDENTITY_TOKEN` |
| `miner1` … `miner5` | same name / `default` | `MINER1_PYLON_TOKEN` … `MINER5_PYLON_TOKEN` |

Open-access and metrics tokens are distinct. Owner/Alice are never Pylon identities.
Only Pylon receives the wallet mount, read-only. The local wallet tree contains
disposable funding keys; it is not a production wallet layout. Secrets and runtime
state are gitignored. Bootstrap logs omit generated recovery phrases.

`state/registrations.json` records UID/hotkey/coldkey mappings read directly from
Subtensor at one block, independently of Pylon. `check.py` compares Pylon's view,
checks that a miner token cannot access a different identity, verifies a fresh
validator observation and both monitoring scrape targets, then writes public
check results to `state/compose-check.json`.

Named volumes retain chain, Pylon database and Prometheus state. Subtensor starts
with `--no-purge`. These commands preserve chain identity and registrations:

```sh
localnet/compose.sh restart subtensor
localnet/compose.sh up -d --wait subtensor
env -u UV_EXCLUDE_NEWER uv run --project miner --group bootstrap python localnet/bootstrap.py
localnet/compose.sh logs --tail 20 validator
localnet/compose.sh down
```

Restart application services with the startup command above after `down`. Do not
delete volumes or wallets when resuming unfinished work; retained records belong
to their original chain and identities.

## Host executor installation contract

The host root is `localnet/state/data`, mounted read-write into the validator at
`/var/lib/factory-horde`. `installer/prepare-data-root.sh` creates canonical,
non-symlink directories owned by the operator's UID/primary GID with mode `0750`.
The generated `.env` selects that same numeric UID/GID for all workload containers
and names the host executor user/group. The operator controls the complete tree;
individual workloads will receive only their job's fixed protocol mounts.

Task 7 installs a system service running the single standard-library Python executor
as `EXECUTOR_USER`/`EXECUTOR_GROUP`, with `UMask=0027`, Docker-group access, automatic
service restart and this host root. Python defaults to `/usr/bin/python3.14`.
Docker access grants host-level control; it belongs only to the trusted executor.
The validator has a read-only container filesystem, no Docker socket, no wallet
mount and no execution responsibility. Factory/judge containers will have no
automatic restart policy and no wallet/control-root mounts.

The selected workload settings are `linux/amd64`, two-second polling, 512 MiB RAM,
one CPU and 128 PIDs; they are explicit operator configuration for task 7. Executor
installation is not yet claimed by this task-4 setup.

## Monitoring

Pylon listens on loopback port 8000, Subtensor on 9944 and Prometheus on 9090.
Prometheus scrapes Pylon with a distinct Bearer token and node-exporter for host
CPU/filesystem data. Node-exporter uses the host PID/root views but the Compose
network namespace. Both monitoring images are pinned. No remote-write, Alloy or
upstream credentials are required. Validator/executor metrics and full readiness
are added in task 14.

## Public image submissions and discovery

After the startup/check commands above and task-5 image verification:

```sh
localnet/build-submitter.sh
env -u UV_EXCLUDE_NEWER uv run --project validator python -m localnet.check_submissions
```

The check launches a short-lived submitter container for each of the five miner
identities. Each receives only its own Pylon token through the environment, with no
wallet or Docker socket mount. It confirms exact read-back, skips an unchanged
submission, rejects a token used for another identity and freezes block-aligned
validator discovery. It temporarily changes miner5's commitment to the published
judge reference to prove snapshot immutability, then restores the factory reference.
Factory dispatch remains disabled, so this update does not execute a workload.
Evidence is written under `state/`; public task evidence is in
[task6-submissions.json](../spec/evidence/task6-submissions.json).

The selected Pylon 2.3.3 writer supports at most 128 UTF-8 bytes through `RawN`;
the baseline factory reference is 124 bytes. The local runtime uses a 3,100-byte
per-epoch space allowance, with a minimum charge of 100 bytes per commitment.
Rapid updates seven blocks apart succeeded. Unregistered writes and an atomic
4,096-byte batch failed with the expected chain errors. These observations replace
the older generic 100-block interval guidance for this selected local runtime.
