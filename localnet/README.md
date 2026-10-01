# FactoryHorde localnet

This is the task-4 application foundation, with factory dispatch disabled. It runs
the common validator, Pylon and monitoring services plus a local Subtensor overlay.
Factory/judge source-build checks now run through `localnet/build-baselines.sh`.
Public baseline images are available on GHCR; see [image evidence](../spec/evidence/task5-published-images.json).
Containerized submissions and frozen discovery are implemented; the host systemd
executor now runs a real factory/judge pair. Automated complete rounds remain incomplete; successful startup alone is not V2 acceptance.

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
with `--no-purge`. The overlay limits it to four CPUs and sets Tokio/Rayon worker
counts to four. Without these bounds the three-node image reached this host's
990-thread cgroup limit and its health checks could not fork. Recreation with the
same chain volume reduced it to 186 threads and preserved healthy application reads. These commands preserve chain identity and registrations:

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
individual workloads receive only their job's fixed protocol mounts.

The installer creates a system service running the single standard-library Python executor
as `EXECUTOR_USER`/`EXECUTOR_GROUP`, with `UMask=0027`, Docker-group access, automatic
service restart and this host root. Python defaults to `/usr/bin/python3.14`.
Docker access grants host-level control; it belongs only to the trusted executor.
The validator has a read-only container filesystem, no Docker socket, no wallet
mount and no execution responsibility. Factory/judge containers have no
automatic restart policy and no wallet/control-root mounts.

The selected workload settings are `linux/amd64`, two-second polling, 512 MiB RAM,
one CPU and 128 PIDs; they are explicit operator configuration.

```sh
installer/install-executor.sh "$PWD/localnet/.env" factory-horde-localnet-executor
env -u UV_EXCLUDE_NEWER uv run --project validator python -m localnet.check_executor
```

This approximately 140-second check publishes a factory request and a separate
judge request, verifies real Docker identities/mounts/resource limits, then restarts
the executor and verifies unchanged terminal statuses and report. It writes
`state/task7-executor-pair.json`. This manually driven fixture does not enable the
validator round coordinator. Stop the executor separately from Compose when needed:

```sh
sudo systemctl stop factory-horde-localnet-executor
```

Stopping the service leaves detached workloads running; normal job cancellation
uses permanent protocol stop records. Preserve its data root to resume observation.

## Executor recovery acceptance

The lifecycle fixture is published on GHCR by `build-executor-fixture.yml`:

```sh
env -u UV_EXCLUDE_NEWER uv run --project validator python -m localnet.check_executor_recovery \
  ghcr.io/backend-developers-ltd/factory-horde-fixture@sha256:d3b79c5980ae248bc905e2782d022173ca50735e1b09ccc6361ac635c8b0ad17
```

The check installs the same executor/unit as a separate
`factory-horde-recovery-executor` service, with data under
`state/executor-acceptance/data`. A local test-only Docker CLI wrapper holds real
create/start/pull responses or simulates unavailable observation. The production
executor contains no fault hooks. All workload containers are real; statuses are
written by the service, never injected by the test.

It runs five TERM-resistant factories concurrently, restarts during stop grace,
crashes after create/start, replays finalized work, deletes finalized containers,
cancels a delayed pull while another job finishes, and deletes an expected
container before final observation. The last scenario deliberately retains an
unresolved record in the isolated acceptance root; it never clears evidence to
force a rerun. The check removes its wrapper override and stops/disables only the
acceptance service. Normal localnet services keep running. Public results are in
[task8-recovery.json](../spec/evidence/task8-recovery.json).

## Recover accepted results from the real pair

After `localnet.check_executor` has retained a successful factory/judge pair:

```sh
env -u UV_EXCLUDE_NEWER uv run --project validator python -m localnet.check_results
```

This reads the existing Docker statuses/report and a current Pylon block, accepts
the original score, saves both Nexus projections and rebuilds them using a fresh
store. It checks unchanged requests, report, stable IDs and original execution
times across fresh contexts. It creates no execution requests or containers.
Evidence is written to `state/task9-results.json`.

## Exercise both Nexus tasks

With the current validator image built, the executor active, five baseline
commitments and the retained task-7 failed-pull fixture:

```sh
env -u UV_EXCLUDE_NEWER uv run --project validator python -m localnet.check_tasks
```

This approximately three-minute check freezes a new five-miner plan and runs the
application's actual Nexus tasks in a container created by the common Compose
service. It restarts that validator while all five factories run, verifies unchanged
Docker identities, waits for evaluation before launching separate judges, and
checks five accepted scores plus the retained failed-pull result. The probe also
verifies automatic discovery of both task block clocks. Fixture actors supply the
fixed plan; use the coordinator check below to exercise automatic scheduling. Runtime artifacts stay
under `state/`, with a public summary in
[task10-tasks.json](../spec/evidence/task10-tasks.json).

## Exercise the persisted round coordinator

After building the current validator image, while ordinary dispatch is disabled:

```sh
env -u UV_EXCLUDE_NEWER uv run --project validator python -m localnet.check_rounds
```

This bounded check runs the production entrypoint and common Compose service with
five existing baseline commitments. It verifies initial chain unavailability,
then uses 100/15/65-second stages, a ten-second judge reserve and five-second stop
grace. It restarts after request publication and at generation/evaluation boundaries,
checks early factory completion does not start judges, and reconstructs all ten
task outcomes and five scores. The same installed systemd executor handles every
Docker job. It stops the probe after settled results, before the next slot, and
restores the ordinary validator. Raw artifacts remain in `state/task11-rounds.json`
and `state/task11-coordinator.log`.

For continuous scheduling, set `VALIDATOR_DISPATCH_ENABLED=true`, `VALIDATOR_HOTKEY`
to the registered validator's public hotkey and `JUDGE_IMAGE` to the published judge
digest in `localnet/.env`, then recreate the validator. The example environment lists
the default two-hour timing. Only one coordinator may write a data root. Keep
existing files across restart; unresolved old jobs hold future slots. Disabling
dispatch pauses the coordinator; existing executor requests retain their deadlines.

## Independently verify chain weights

After a completed five-miner coordinator run and a current validator image build:

```sh
env -u UV_EXCLUDE_NEWER uv run --project miner --group bootstrap python localnet/check_weights.py
```

The checker runs the production validator with weight writes enabled and round
admission disabled, using the existing accepted scores. The Nexus opportunity node,
result gate and setter handle submission through Pylon. The checker itself uses
the bootstrap SDK to read Subtensor directly: subnet 2, both mechanisms, the
validator's UID/hotkey, current recipient mapping, constraints and integer weight
vector at the same block. It independently recomputes softmax and accounts for
u16 quantization, restarts the validator between two actual updates, verifies
identical score-derived weights and unchanged mechanism 0, then restores the
ordinary validator. Raw evidence is in `state/task12-weights.json` and
`state/task12-validator.log`; public evidence is
[task12-weights.json](../spec/evidence/task12-weights.json).

The verified chain uses tempo 360, minimum one weight, maximum weight limit 1.0
and a 100-block write rate limit. Two updates appeared at blocks 25,895 and 25,996;
their normalized integer vectors differ from the requested softmax by at most
`0.00000672`. Weight vectors are not final emission percentages.

For continuous writes, set `VALIDATOR_WEIGHTS_ENABLED=true` in `localnet/.env`
and recreate the validator. It is independent of `VALIDATOR_DISPATCH_ENABLED`.
`WEIGHT_TEMPERATURE` defaults to `0.1`; `SUBNET_TEMPO` and `WEIGHT_EPOCH_OFFSET`
configure Nexus weight opportunities. No usable registered scores means no write.

## Adversarial application acceptance

Keep ordinary round admission disabled while this suite temporarily changes the
five local miner commitments. It uses the common validator image and production
entrypoint, a separate data root, a separately installed systemd executor, and the
existing local Pylon/Subtensor services. It restores the original commitments and
stops its executor afterward. No weights are submitted by this suite; it verifies
failure exclusion in calculated weights. Task 12 supplies independent chain-write
evidence.

Download the sixteen immutable profile artifacts from the published fixture build
once, then run the suite from the repository root:

```sh
gh run download 36934317769 --pattern 'fixture-*' --dir localnet/state/task13-profile-images
localnet/build-validator.sh
env -u UV_EXCLUDE_NEWER uv run --project validator python -m localnet.check_adversarial \
  localnet/state/task13-profile-images
```

The checker anonymously pulls every digest. Scenarios cover mixed successful,
missing-output, nonzero, hanging and symlink factories; rejected commitments and
failed image pulls; delayed pull cancellation with blocked admission; denied Nexus
projection writes and validator kills after publication/acceptance; every judge
report profile; and executor kills around Docker create/start. Docker unavailability
and deletion of an expected execution deliberately leave unresolved evidence.
The suite checks actual Docker identities, exit states and job-local startup counts.

Raw records, outputs, reports, logs and incremental `evidence.json` remain under
`state/factory-horde-adversarial-<uuid>/`; `state/task13-latest.txt` identifies the
latest retained run. The isolated executor is stopped and disabled on completion.
Do not point ordinary admission at the deliberately unresolved fixture root or
delete its evidence to force a new round.

The selected Pylon 2.3.3 writer has a 128-byte commitment bound. A direct 129-byte
request during development timed out instead of returning a rejection. The suite
tests the production submitter's rejection before network access, the validator's
independent length check and unchanged chain commitment. Malformed and tag-only
values within the bound are actually published and excluded by discovery.
The completed run and its real Docker observations are summarized in
[task13-adversarial.json](../spec/evidence/task13-adversarial.json). All ten judge
profiles passed, alongside the mixed-cohort, cancellation, storage and crash checks.

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
