# FactoryHorde localnet

Dispatch and weight writes default to disabled. Localnet runs
the common validator, Pylon and monitoring services plus a local Subtensor overlay.
Factory/judge source-build checks now run through `localnet/build-baselines.sh`.
Public baseline images are available on GHCR; see [image evidence](../spec/evidence/task5-published-images.json).
Containerized submissions and frozen discovery are implemented; the host systemd
executor runs concurrent factory/judge jobs. Automated rounds, recovery and independent
chain weights and packaged end-to-end acceptance are verified in
[task-17 evidence](../spec/evidence/task17-acceptance.json).

For the published image selection and full installer path, use the
[immutable candidate instructions](../envs/candidate/README.md). The commands below
remain the source-build development workflow. `check.py --env-file /absolute/installation/.env`
also verifies an installed candidate against its own bootstrap evidence and ports.

## Packaged candidate acceptance on this VM

The user selected the existing Linux VM for task 17 and prohibited preparing another
VM. Use a new isolated installation here; this proves reproducibility of application
installation on this host, without claiming a fresh operating-system installation.
The selected candidate assets are Git revision
`87353ed2e97cf435b6d8210dceb177affe2febfb`; all runtime images are registry digests.
Install and bootstrap from the repository root after satisfying the
[candidate prerequisites](../envs/candidate/README.md#install-the-selected-application):

```sh
TASK_INSTALL="$PWD/localnet/state/acceptance"
TASK_REVISION=87353ed2e97cf435b6d8210dceb177affe2febfb
env -u UV_EXCLUDE_NEWER uv sync --project validator
env -u UV_EXCLUDE_NEWER uv sync --project miner --group bootstrap
installer/install.sh "$TASK_INSTALL" --env-file "$PWD/envs/candidate/localnet.env" \
  --ref "$TASK_REVISION" --service factory-horde-acceptance-executor \
  --project factory-horde-acceptance --localnet --prepare-only
docker compose --project-name factory-horde-acceptance --env-file "$TASK_INSTALL/.env" \
  -f "$TASK_INSTALL/envs/deployed/docker-compose.yml" \
  -f "$TASK_INSTALL/localnet/compose.yml" up -d --wait subtensor
env -u UV_EXCLUDE_NEWER uv run --project miner --group bootstrap \
  python localnet/bootstrap.py --env-file "$TASK_INSTALL/.env"
"$TASK_INSTALL/installer/update_compose.sh" "$TASK_INSTALL"
```

After bootstrap/startup, run from the repository root:

```sh
TASK_ENV="$PWD/localnet/state/acceptance/.env"
env -u UV_EXCLUDE_NEWER uv run --project validator python localnet/check.py --env-file "$TASK_ENV"
env -u UV_EXCLUDE_NEWER uv run --project validator python -m localnet.check_submissions --env-file "$TASK_ENV"
env -u UV_EXCLUDE_NEWER uv run --project validator python -m localnet.check_rounds --env-file "$TASK_ENV"
env -u UV_EXCLUDE_NEWER uv run --project miner --group bootstrap python -m localnet.check_weights --env-file "$TASK_ENV"
env -u UV_EXCLUDE_NEWER uv run --project validator python -m localnet.check_monitoring --env-file "$TASK_ENV"
env -u UV_EXCLUDE_NEWER uv run --project validator python -m localnet.check_adversarial \
  localnet/state/task13-profile-images --env-file "$TASK_ENV"
env -u UV_EXCLUDE_NEWER uv run --project validator python -m localnet.check_installer \
  --validator-image ghcr.io/backend-developers-ltd/factory-horde-validator-prototype@sha256:71807ba7181bf544388d2b809d69777adefc5cda89124e2d918c4b5834112eb5
```

Download the published profile metadata as described under
[adversarial application acceptance](#adversarial-application-acceptance) before that
check. The selected environment determines Compose files/project, Pylon port,
executor service, candidate images and artifact paths. Checks reject public-network
settings and symlink escapes. Omit `--env-file` to retain the source-checkout workflow.
The independent chain checker runs as a module from the repository root using the
miner bootstrap group; no Bittensor SDK enters the validator runtime.

Checks run sequentially on the selected chain: submission checks temporarily change
commitments, round checks use 100/15/65-second stages, and weight checks independently
read two actual updates across a validator restart. Adversarial checks also change
commitments temporarily, retaining their deliberately unresolved workloads in a
separate data root. The installer check creates another isolated Compose/local-chain
installation on this same host and verifies an update during active detached work.
It uses revision-addressed HTTP fault fixtures, including a compatible older executor
variant, while selecting the published candidate validator image.

Public records and logs remain under the chosen installation's `state/` and `data/`;
installer checks retain their own `localnet/state/installer-*` directory. Keep wallets,
`.env` and token files out of evidence bundles. Do not delete an unresolved record to
make the next check pass.

After all checks pass, collect the public data and an inventory of hashes:

```sh
env -u UV_EXCLUDE_NEWER uv run --project validator python -m localnet.package_acceptance \
  localnet/state/task17-evidence.tar.gz --env-file "$TASK_ENV"
```

The packager includes the selected candidate, installed release metadata, registration
and commitment evidence, rounds, requests/stops/statuses, outputs, reports, accepted
scores, chain readback and adversarial/installer evidence. It also retains Docker logs
and verifies all five baseline Pi versions and minute-long factory executions. Keep
those finalized containers until packaging completes. It scans for the configured
token values and refuses a leaking artifact. Rejected workload symlinks are recorded
as inventory metadata and are never followed or archived as links. The tarball and
its JSON inventory remain outside source commits; a second collection requires a
new output filename. Raw evidence is retained on failure.

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
installer/install-executor.sh "$PWD/localnet/.env" factory-horde-localnet-executor
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
validator observation and all three monitoring scrape targets, then writes public
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

The source installer verifies `installer/release-manifest.json` and the standalone
executor checksum before installing. After editing listed assets, regenerate them
with `env -u UV_EXCLUDE_NEWER uv run --project validator python installer/release.py
manifest "$PWD"` and use `--check` before committing. The installer creates a system service running the single standard-library Python executor
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

## Full installer and update acceptance

The [operator installer](../installer/README.md) provides a checksummed application
installation, explicit revision selection and a fifteen-minute cron updater running
as the operator. It resolves a moving configuration branch once per run, validates
all files before replacement and grants only the exact executor service restart.
Use this path for candidate acceptance; `install-executor.sh` remains the source
checkout convenience interface for focused executor fixtures.

```sh
env -u UV_EXCLUDE_NEWER uv run --project validator python -m localnet.check_installer
```

This creates a separate local chain, isolated wallets/data, common application
stack and systemd service. It tests clean/repeated installs, no-change updates,
failed downloads/checksums/protocol, updater overlap, atomic replacement while
detached factories run, stable stops/accepted scores, visible unhealthy replacement
and ordinary repair. An actual cron run under a temporary restricted account proves
the update can restart its unit without general sudo access. The suite stops its
stack/executor and removes cron/sudoers rules, retaining raw artifacts under
`state/installer-*` and the summary in `state/task15-installer.json`.

`bootstrap.py --env-file /absolute/installation/.env` supports the full installer's
isolated root: wallets must be in `wallets/` beside that `.env`, outside
`~/.bittensor`. It still requires localnet subnet 2 and `ws://subtensor:9944`, connects
only through the configured loopback port and writes direct-chain registration
evidence to `state/registrations.json` beside the installation. The default
`localnet/.env` behavior is unchanged.

## Executor recovery acceptance

The lifecycle fixture is published on GHCR by `build-executor-fixture.yml`:

```sh
env -u UV_EXCLUDE_NEWER uv run --project validator python -m localnet.check_executor_recovery \
  ghcr.io/backend-developers-ltd/factory-horde-fixture@sha256:d3b79c5980ae248bc905e2782d022173ca50735e1b09ccc6361ac635c8b0ad17
```

The check installs the same executor/unit as a separate
`factory-horde-recovery-<root-hash>-executor` service, with data under
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
env -u UV_EXCLUDE_NEWER uv run --project miner --group bootstrap python -m localnet.check_weights
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
upstream credentials are required. Prometheus also scrapes the validator at
`validator:9101/metrics`. A Nexus actor owns this server; executor metrics come
from atomic files through the same scrape path. Host metrics remain host metrics.
The validator readiness health check requires the host executor to be installed
and running, even with dispatch disabled.

After the startup steps, inspect readiness without publishing a host HTTP port:

```sh
localnet/compose.sh exec -T validator /opt/venv/bin/python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9101/readyz').read().decode())"
env -u UV_EXCLUDE_NEWER uv run --project validator python -m localnet.check_monitoring
```

The check requires dispatch/weight writes disabled and every existing job confirmed
stopped. It temporarily stops the localnet executor, verifies that `/readyz` becomes
503 while `/livez` stays 200, and restarts it. It also removes/restores projection
directory write permission and verifies failure/recovery under the real container
UID. Service and permissions are restored in `finally` blocks. Evidence is written
to `state/task14-monitoring.json`; no requests, stops or results are removed.
Prometheus's `up` metric only proves scraping; `factory_horde_ready` reflects
application readiness. The [validator guide](../validator/README.md) explains
individual checks and metric names. Docker health failure, stale job observation,
blocked-next-round state, incompatible records and monitoring-thread cleanup also
have focused Linux tests in `validator/tests/test_monitoring.py` and
`executor/test_executor.py`.

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
