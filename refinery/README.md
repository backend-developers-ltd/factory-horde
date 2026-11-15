# FactoryHorde on Refinery

FactoryHorde runs on the Refinery development chain (`ws://10.0.0.10:9944`, subnet 11, mechanism 0) with one validator server and one miner server. The miner periodically submits a published factory image; the validator runs it in every round, judges it and sets weights. Creating the servers is not covered: this assumes two existing Ubuntu 26.04 amd64 hosts attached to Refinery's private VLAN (10.0.0.0/24).

Both servers follow the operator layout of the Nexus subnet template: a non-root operator account, `ubuntu`, runs everything from one directory in its home, `~/factory-horde`. That directory is a git checkout of this repository, and on the validator it is also the installation that [`installer/install.sh`](../installer/README.md) manages. Each server has one settings file and one Compose file.

| | Validator server | Miner server |
| --- | --- | --- |
| Runs | Pylon (validator identity), validator, Prometheus, node-exporter, Grafana behind Caddy, systemd host executor, 15-minute updater | Pylon (miner identity), systemd timer running the one-shot submitter |
| Settings | `~/factory-horde/.env` | `~/factory-horde/refinery/miner/.env` |
| Compose file and project | [`envs/deployed/docker-compose.yml`](../envs/deployed/docker-compose.yml), `factory-horde` | [`miner/docker-compose.yml`](miner/docker-compose.yml), `factory-horde-miner` |
| Wallet | `~/.bittensor/wallets/factory-horde-validator` | `~/.bittensor/wallets/miner1` |
| Executes factory and judge containers | Yes; the executor pulls the submitted image from GHCR | No |

The servers never connect to each other. The miner publishes an image digest as an on-chain commitment through its Pylon; the validator reads commitments from the chain and pulls the image from GHCR. Both Pylons listen on loopback only. The validator's only public service is Grafana on ports 80 and 443.

## Set up a server

The `ubuntu` account needs passwordless sudo, as on Ubuntu cloud images. On a host that only has root, create it first:

```bash
adduser --disabled-password --gecos '' ubuntu
echo 'ubuntu ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/90-ubuntu && chmod 0440 /etc/sudoers.d/90-ubuntu
install -d -m 0700 -o ubuntu -g ubuntu /home/ubuntu/.ssh
install -m 0600 -o ubuntu -g ubuntu /root/.ssh/authorized_keys /home/ubuntu/.ssh/authorized_keys
```

Then, as `ubuntu` on each server:

```bash
git clone https://github.com/backend-developers-ltd/factory-horde.git ~/factory-horde
cd ~/factory-horde
refinery/install-prerequisites.sh   # then log out and in again for Docker access
refinery/validator/setup.sh         # on the validator server
refinery/miner/setup.sh             # on the miner server
```

Order does not matter: the validator admits a miner from the first round that starts after its commitment. Both setup scripts are idempotent.

`install-prerequisites.sh` installs Docker Engine with Compose from Docker's apt repository, Python 3.14, git, jq, curl, cron and openssl, gives the operator Docker access, and installs uv 0.13.7 and btcli 11.3.0 (as uv tool `bittensor`) in the operator's home. It keeps anything already installed.

### What the validator setup does

1. Creates `~/factory-horde/.env` from [`validator/.env.example`](validator/.env.example) on the first run and fills in the wallet directory, the public address for Grafana and a generated Grafana password.
2. `check-chain.sh` verifies the Refinery genesis hash. `wallet.sh validator` creates the wallet if missing (recovery words in `~/<wallet>-recovery.txt`), tops it up from development `//Alice` below 10 TAO, registers its hotkey and adds 1 TAO stake if it has none. Existing registration and stake are kept. The hotkey address goes into `.env` as `VALIDATOR_HOTKEY`.
3. Runs `installer/install.sh ~/factory-horde --ref snapshot --source file://$HOME/factory-horde`. The installer checks the checkout against `installer/release-manifest.json`, adds its defaults and generated Pylon tokens to `.env`, installs the `factory-horde-executor` systemd unit (running `executor/executor.py` from the checkout as `ubuntu`), starts the Compose application, and adds the 15-minute cron updater with its single restart grant. Later runs call `installer/update_compose.sh` instead.
4. Applies `GRAFANA_ADMIN_PASSWORD` to Grafana.

Installation state (`data/`, `installation.json`, `applied-release.json`, `update.log`, `executor/executor.env`) lives in the checkout and is ignored by git.

### What the miner setup does

1. Creates `refinery/miner/.env` from [`miner/.env.example`](miner/.env.example) on the first run and fills in the wallet directory and generated Pylon tokens.
2. `wallet.sh miner` creates, funds and registers the miner wallet as above.
3. Starts Pylon from [`miner/docker-compose.yml`](miner/docker-compose.yml) (port 8000 on loopback).
4. Installs `factory-horde-submit.timer`, which runs [`miner/submit.sh`](miner/submit.sh) as `ubuntu` two minutes after boot and every `SUBMIT_INTERVAL`, and submits once immediately. The published submitter reads the current commitment first: an unchanged image reports `unchanged` and writes nothing.

## Configuration and updates

Each server's `.env` holds all of its settings and secrets and is never committed; the `.env.example` files hold the Refinery values. The installer rewrites the validator's `.env` once, sorted and without comments; the example keeps the explanations.

- **Validator settings:** edit `~/factory-horde/.env`. The updater applies the change within 15 minutes; `installer/update_compose.sh ~/factory-horde` applies it immediately. Changing the Grafana password also needs `refinery/validator/setup.sh`.
- **Validator code:** `git pull` in `~/factory-horde`. The updater verifies the checkout against its manifest and then restarts the executor or the Compose application if their files changed. A commit that changes a listed asset must therefore include the regenerated manifest (`/usr/bin/python3.14 -I installer/release.py manifest "$PWD"`). A changed systemd unit template needs `installer/install.sh ~/factory-horde --ref snapshot --source file://$HOME/factory-horde` once.
- **Miner:** edit `refinery/miner/.env` or `git pull`, then rerun `refinery/miner/setup.sh`. `FACTORY_IMAGE` must be a public GHCR reference pinned by digest (the validator accepts nothing else); `refinery/miner/submit.sh <image>` submits another image once. New commitments apply from the next round.
- **Rounds:** 10 minutes generation, 5 minutes stop confirmation and 15 minutes evaluation. The baseline factory needs about one minute. The judge must finish `JUDGE_STOP_RESERVE_SECONDS` before the round ends.

## Operate

```bash
refinery/validator/status.sh   # services, readiness, latest rounds/results, weight calculation, pending and revealed chain weights
refinery/miner/status.sh       # Pylon, submission timer, last submission, miner UID, incentive and emission
```

On the validator, plain `docker compose` commands work in `~/factory-horde` because `.env` names the Compose file and project. On the miner they work in `~/factory-horde/refinery/miner`.

| Task | Command |
| --- | --- |
| Validator logs | `docker compose logs -f validator` |
| Grafana and Caddy logs | `docker compose logs -f grafana caddy` |
| Executor logs | `journalctl -u factory-horde-executor -f` |
| Updater log | `~/factory-horde/update.log` |
| Round evidence | `~/factory-horde/data/rounds/<date>/round-*/<miner hotkey>/` (generated files, judge report, accepted result) |
| Submission log | `journalctl -u factory-horde-submit.service` |
| Submit now | `sudo systemctl start factory-horde-submit.service` |

Do not delete files under `data/` to force a rerun: recovery relies on them and never redraws an accepted score.

## Monitoring

Grafana and Caddy are the `grafana` profile of the common Compose file, enabled by `COMPOSE_PROFILES=grafana` in `.env`. Grafana reads the application's Prometheus, which scrapes the validator (including executor metrics it forwards), Pylon and node-exporter every 15 seconds. Caddy serves Grafana at `https://<GRAFANA_ADDRESS>/` and redirects HTTP to HTTPS. It obtains a Let's Encrypt certificate for the IP address (six-day `shortlived` profile, renewed automatically), so ports 80 and 443 must be reachable from the internet. Log in as `admin` with `GRAFANA_ADMIN_PASSWORD` from `.env`. Anonymous access and sign-up are disabled.

The provisioned dashboard **FactoryHorde validator** is Grafana's home page. Grafana rereads [`factory-horde-validator.json`](../envs/deployed/grafana/dashboards/factory-horde-validator.json) every minute, so a pulled revision shows up without a restart; changes saved in the UI are not kept. Its sections are:

- **Status:** readiness, scrape targets, round phase, active and unresolved jobs, usable scores, age of the latest usable round and validator uptime.
- **Liveness:** when each pipeline step last happened (chain beat, commitment discovery, round admission, image pull, container start, job completion, accepted judge score, weight calculation, Pylon acknowledgement and on-chain weight commit), a timeline of these events, observation ages and individual readiness checks.
- **Rounds and weights:** round phase over time, discovery, coordinator and result events, job gauges and the weight pipeline.
- **Host executor:** Docker operations, errors and latency.
- **Pylon and chain:** subtensor operations and Pylon API requests with latency.
- **Validator internals:** shared-record I/O, handler latency, process memory and CPU, readiness samples.
- **Server:** CPU, memory, load and disk from node-exporter.

The validator does not export per-miner scores, weights or evaluated image digests as metrics, so the dashboard shows pipeline activity but not individual miners. Event counts come from Prometheus `increase` and are approximate, and the Last activity panel looks back seven days. Prometheus keeps up to 1 GB of samples. node-exporter runs in the application's bridge network, so the Server section shows no host network traffic.

## Subnet requirements

The deployment changes no subnet setting. It expects subnet 11 to allow registration and to permit a single miner to receive all weight (minimum one weight, maximum weight 1.0).

- **Commit-reveal.** When the subnet has commit-reveal enabled, Pylon detects it and commits and reveals the validator's weights itself. Committed weights appear in the chain's weights after `commit_reveal_period` tempos; `validator/status.sh` lists commits still waiting for their reveal. The first weight opportunity needs a completed round with an accepted score.
- **Registration.** btcli 11.3.0 only submits burned registration MEV-shielded; `wallet.sh` does so.
- **Other UIDs.** The validator only evaluates registered hotkeys that commit a valid factory image; other UIDs on the subnet are ignored.

## Files

| File | Purpose |
| --- | --- |
| `lib.sh` | Shared shell helpers |
| `install-prerequisites.sh` | Host software for both roles |
| `check-chain.sh` | Read-only chain identity, health and subnet listing |
| `fund-from-alice.sh` | Transfer development TAO from `//Alice` to an address |
| `wallet.sh` | Create, fund, register and (validator) stake this server's wallet, idempotently |
| `validator/.env.example` | Template of the validator's `.env` |
| `validator/setup.sh`, `validator/status.sh` | Validator installation/update and status |
| `miner/.env.example` | Template of the miner's `.env` |
| `miner/setup.sh`, `miner/status.sh` | Miner installation/update and status |
| `miner/docker-compose.yml` | Miner Pylon |
| `miner/submit.sh` | One submission with the published submitter image |

## Differences from a real deployment

- **Development chain and keys.** Refinery is a private development chain. Wallets are funded from the public `//Alice` key, coldkeys are stored unencrypted in the wallet directory that Pylon mounts read-only, and recovery words are written to the operator's home. A real deployment uses funded, encrypted coldkeys kept off the servers, with only hotkeys on the hosts.
- **Release selection.** The validator installs from its own git checkout, so a release is whatever `git pull` brings in. Operators of a real deployment have no checkout: their installer downloads the same assets from a reviewed `deploy-config-*` branch.
- **Images.** The validator, submitter, factory and judge are the published prototype candidate images (`envs/candidate/release.json`). They predate later source fixes, and their acceptance tests ran on an isolated local chain with direct weights.
- **Mining.** The miner resubmits one fixed baseline image instead of building and publishing improved factories. The baseline factory writes a fixed greeting and the judge draws a random score, so scores and weights carry no quality signal.
- **Round length.** Rounds last 30 minutes instead of the default two hours (60/5/55 minutes).
- **Network access.** Both servers share the chain's private VLAN. Outside miners cannot reach the chain, which a real subnet would expose publicly.
- **Miner operations.** The miner runs an ad hoc Compose file and no monitoring or update mechanism. A real miner would run its own tooling and publish to its own GHCR namespace.
- **Pylon EVM endpoints.** Both Pylons keep their default public mainnet EVM endpoints; FactoryHorde does not use EVM calls.
- **Monitoring.** Prometheus listens on the validator's loopback only, with no alerting or remote write. Grafana uses a single local admin account on a bare IP address with a short-lived certificate.
