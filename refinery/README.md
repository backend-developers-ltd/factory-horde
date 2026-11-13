# FactoryHorde on Refinery

FactoryHorde runs on the Refinery development chain (`ws://10.0.0.10:9944`, subnet 11, mechanism 0) with one validator server and one miner server. The miner periodically submits a published factory image; the validator runs it in every round, judges it and sets weights. Creating the servers on Linode is not covered: this assumes two existing Ubuntu 26.04 amd64 hosts attached to Refinery's private VLAN (10.0.0.0/24) with root SSH access.

| | Validator server | Miner server |
| --- | --- | --- |
| Deployment | Production environment [`envs/production`](../envs/production): one Compose project `factory-horde` plus the `factory-horde-executor` systemd unit | [`refinery/miner`](miner): Compose project `factory-horde-miner` plus a systemd timer |
| Runs | Pylon (validator identity), validator, Prometheus, node-exporter, Grafana behind Caddy, host executor | Pylon (miner identity), one-shot submitter |
| Settings | `.env` in the checkout (`envs/production/.env`) | `refinery/miner/.env` |
| Holds | Validator coldkey (root) and a runtime copy of its hotkey | Miner coldkey (root) and a runtime copy of its hotkey |
| Executes factory and judge containers | Yes, as the non-root `factoryhorde` service account; the executor pulls the submitted image from GHCR | No |

Each server has one checkout of this repository and one settings file. The servers never connect to each other. The miner publishes an image digest as an on-chain commitment through its Pylon; the validator reads commitments from the chain and pulls the image from GHCR. Both Pylons listen on loopback only. The validator's only public service is Grafana on ports 80 and 443.

## Validator server

The validator is a production-type deployment run from a git checkout, without a separate installation directory. As root:

```bash
git clone https://github.com/backend-developers-ltd/factory-horde.git /opt/factory-horde
cd /opt/factory-horde
bin/prepare-os.sh
./setup-prod.sh
refinery/check-chain.sh
refinery/wallet.sh validator
vim .env                       # VALIDATOR_HOTKEY (printed by wallet.sh) and GRAFANA_ADDRESS
./deploy.sh
```

Keep the checkout outside `/root`: the service account must be able to read `executor/executor.py`.

1. `bin/prepare-os.sh` installs Docker Engine with Compose from Docker's apt repository, Python 3.14, git, jq, curl, openssl, uv 0.13.7 and btcli 11.3.0 (as uv tool `bittensor`), keeping anything already installed.
2. `./setup-prod.sh` creates the `factoryhorde` system account, copies [`envs/production/.env.template`](../envs/production/.env.template) to `envs/production/.env` and links `.env` and `docker-compose.yml` into the repository root, so plain `docker compose` commands work there. It generates the Pylon tokens and the Grafana password and fills the account's UID/GID and the checkout's `wallets` and `data` paths. An existing `.env` is never overwritten.
3. `refinery/check-chain.sh` is read-only: it verifies the Refinery genesis hash and lists subnets.
4. `refinery/wallet.sh validator` creates the wallet `BITTENSOR_WALLET_NAME` under `/root/.bittensor/wallets` if missing (recovery words in `/root/<wallet>-recovery.txt`), tops it up from development `//Alice` below 10 TAO, registers its hotkey and adds 1 TAO stake if it has none. It then copies only the hotkey and public coldkey file to `wallets/` for Pylon and prints the hotkey address. Existing registration and stake are kept.
5. `./deploy.sh` prepares `data/` for the service account, installs the `factory-horde-executor` systemd unit running `executor/executor.py` from the checkout, pulls the pinned images and starts the Compose stack, waiting until the validator reports ready.

### Configuration and updates

`.env` is the only configuration file. It holds the chain endpoint, subnet, validator identity, tokens, executor limits, judge image, round windows, weight switch and Grafana settings; the template documents each value. After changing it, or to deploy a new revision, run:

```bash
git pull
./deploy.sh
```

`deploy.sh` is safe to rerun. It restarts the executor only when `executor/executor.py` or an executor setting changed, and recreates only containers whose configuration changed. Running factory and judge containers survive both; the executor reconciles them after its restart. Nothing updates the server automatically.

- **Images:** the validator, Pylon, Prometheus, node-exporter, Grafana and Caddy images are pinned by digest in [`envs/deployed/docker-compose.yml`](../envs/deployed/docker-compose.yml) and [`envs/production/docker-compose.yml`](../envs/production/docker-compose.yml). `JUDGE_IMAGE` is pinned in `.env`.
- **Rounds:** 10 minutes generation, 5 minutes stop confirmation and 15 minutes evaluation. The baseline factory needs about one minute. The judge must finish `JUDGE_STOP_RESERVE_SECONDS` before the round ends.

### Operate

Run these as root in the checkout:

| Task | Command |
| --- | --- |
| Status: services, readiness, latest rounds and results, weight calculation, pending and revealed chain weights | `bin/status.sh` |
| Service list and logs | `docker compose ps`, `docker compose logs -f validator` (or `pylon`, `prometheus`, `grafana`, `caddy`) |
| Executor logs | `journalctl -u factory-horde-executor -f` |
| Round evidence | `data/rounds/<date>/round-*/<miner hotkey>/` (generated files, judge report, accepted result) |

Do not delete files under `data/` to force a rerun: recovery relies on them and never redraws an accepted score.

### Monitoring

Grafana and Caddy are services of the same Compose project. Grafana reads the project's Prometheus, which scrapes the validator (including executor metrics it forwards), Pylon and node-exporter every 15 seconds. Caddy serves Grafana at `https://<GRAFANA_ADDRESS>/` and redirects HTTP to HTTPS. For an IP address it obtains a Let's Encrypt certificate (six-day `shortlived` profile, renewed automatically), so ports 80 and 443 must be reachable from the internet.

Log in as `admin` with `GRAFANA_ADMIN_PASSWORD` from `.env`; `deploy.sh` applies that password on every run. Anonymous access and sign-up are disabled.

The provisioned dashboard **FactoryHorde validator** is Grafana's home page. Grafana rereads [`factory-horde-validator.json`](../envs/production/grafana/dashboards/factory-horde-validator.json) every minute, so `git pull` alone updates it; changes saved in the UI are not kept. Its sections are:

- **Status:** readiness, scrape targets, round phase, active and unresolved jobs, usable scores, age of the latest usable round and validator uptime.
- **Liveness:** when each pipeline step last happened (chain beat, commitment discovery, round admission, image pull, container start, job completion, accepted judge score, weight calculation, Pylon acknowledgement and on-chain weight commit), a timeline of these events, observation ages and individual readiness checks.
- **Rounds and weights:** round phase over time, discovery, coordinator and result events, job gauges and the weight pipeline.
- **Host executor:** Docker operations, errors and latency.
- **Pylon and chain:** subtensor operations and Pylon API requests with latency.
- **Validator internals:** shared-record I/O, handler latency, process memory and CPU, readiness samples.
- **Server:** CPU, memory, load and disk from node-exporter.

The validator does not export per-miner scores, weights or evaluated image digests as metrics, so the dashboard shows pipeline activity but not individual miners. Event counts come from Prometheus `increase` and are approximate, and the Last activity panel looks back seven days. Prometheus keeps up to 1 GB of samples. node-exporter runs in the project's bridge network, so the Server section shows no host network traffic.

## Miner server

As root:

```bash
git clone https://github.com/backend-developers-ltd/factory-horde.git /root/factory-horde
cd /root/factory-horde
bin/prepare-os.sh
refinery/miner/setup.sh
```

`refinery/miner/setup.sh` is idempotent and does everything:

1. Copies [`miner/.env.template`](miner/.env.template) to `refinery/miner/.env` on the first run, generating the Pylon tokens. This file is the miner's only configuration.
2. Checks the chain and runs `refinery/wallet.sh miner`, which creates, funds and registers the wallet as above and copies the hotkey and public coldkey file to `refinery/miner/wallets`.
3. Starts Pylon from [`miner/docker-compose.yml`](miner/docker-compose.yml) (port 8000 on loopback).
4. Installs `factory-horde-submit.timer`, which runs [`miner/submit.sh`](miner/submit.sh) two minutes after boot and every `SUBMIT_INTERVAL`, and submits once immediately. The published submitter reads the current commitment first: an unchanged image reports `unchanged` and writes nothing.

Order between the servers does not matter: the validator admits a miner from the first round that starts after its commitment.

To submit another factory, set `FACTORY_IMAGE` in `refinery/miner/.env` to a public GHCR reference pinned by digest (the validator accepts nothing else) and rerun `refinery/miner/setup.sh`, or run `refinery/miner/submit.sh <image>` once. New commitments apply from the next round.

| Task | Command |
| --- | --- |
| Status: Pylon, submission timer, last submission, miner UID, incentive and emission | `refinery/miner/status.sh` |
| Submission log | `journalctl -u factory-horde-submit.service` |
| Submit now | `systemctl start factory-horde-submit.service` |

## Subnet requirements

The deployment changes no subnet setting. It expects subnet 11 to allow registration and to permit a single miner to receive all weight (minimum one weight, maximum weight 1.0).

- **Commit-reveal.** When the subnet has commit-reveal enabled, Pylon detects it and commits and reveals the validator's weights itself. Committed weights appear in the chain's weights after `commit_reveal_period` tempos; `bin/status.sh` lists commits still waiting for their reveal. The first weight opportunity needs a completed round with an accepted score.
- **Registration.** btcli 11.3.0 only submits burned registration MEV-shielded; `wallet.sh` does so.
- **Other UIDs.** The validator only evaluates registered hotkeys that commit a valid factory image; other UIDs on the subnet are ignored.

## Files

| File | Purpose |
| --- | --- |
| `bin/prepare-os.sh` | Host software for both roles |
| `setup-prod.sh` | Validator: service account, `.env` from the template, root links |
| `deploy.sh` | Validator: executor unit and Compose stack, initial and every later deployment |
| `bin/status.sh` | Validator status |
| `envs/production/` | Validator settings template, Compose file (includes the common application in `envs/deployed/`), Caddyfile, Grafana provisioning and dashboard |
| `refinery/lib.sh` | Shared helpers of the scripts below; reads the server's settings file |
| `refinery/check-chain.sh` | Read-only chain identity, health and subnet listing |
| `refinery/fund-from-alice.sh` | Transfer development TAO from `//Alice` to an address |
| `refinery/wallet.sh` | Create, fund, register and (validator) stake the server's wallet, idempotently |
| `refinery/miner/` | Miner settings template, Pylon Compose file, `setup.sh`, `status.sh` and `submit.sh` |

## Differences from a real deployment

- **Development chain and keys.** Refinery is a private development chain. Wallets are funded from the public `//Alice` key, coldkeys are stored unencrypted, and recovery words are written to `/root`. A real deployment uses funded, encrypted coldkeys kept off the servers, with only hotkeys on the hosts.
- **Owner-operated release.** The validator runs from a checkout that its owner updates by hand. It uses neither the checksummed [operator installer](../installer/README.md) with its cron updater nor a reviewed `deploy-config-*` branch, which remain the path for distributing releases to other operators.
- **Images.** The validator, submitter, factory and judge are the published prototype candidate images (`envs/candidate/release.json`). They predate later source fixes, and their acceptance tests ran on an isolated local chain with direct weights. The host executor is the checkout's current source.
- **Mining.** The miner resubmits one fixed baseline image instead of building and publishing improved factories. The baseline factory writes a fixed greeting and the judge draws a random score, so scores and weights carry no quality signal.
- **Round length.** Rounds last 30 minutes instead of the default two hours (60/5/55 minutes).
- **Network access.** Both servers share the chain's private VLAN. Outside miners cannot reach the chain, which a real subnet would expose publicly.
- **Miner operations.** The miner runs as root with an ad hoc Compose file and no monitoring or update mechanism. A real miner would run its own tooling and publish to its own GHCR namespace.
- **Pylon EVM endpoints.** Both Pylons keep their default public mainnet EVM endpoints; FactoryHorde does not use EVM calls.
- **Monitoring.** Prometheus listens on the validator's loopback only, with no alerting or remote write. Grafana uses a single local admin account on a bare IP address with a short-lived certificate.
