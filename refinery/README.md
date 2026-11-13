# FactoryHorde on Refinery

This folder deploys FactoryHorde to the Refinery development chain (`ws://10.0.0.10:9944`, subnet 11, mechanism 0) with one validator server and one miner server. The miner periodically submits a published factory image; the validator runs it in every round, judges it and sets weights. Creating the servers on Linode is not covered: this assumes two existing Ubuntu 26.04 amd64 hosts attached to Refinery's private VLAN (10.0.0.0/24) with root SSH access.

| | Validator server | Miner server |
| --- | --- | --- |
| Runs | Pylon (validator identity), validator, Prometheus, node-exporter, systemd host executor, 15-minute release updater | Pylon (miner identity), systemd timer running the one-shot submitter |
| Holds | Validator coldkey (root) and a runtime copy of its hotkey | Miner coldkey (root) and a runtime copy of its hotkey |
| Executes factory and judge containers | Yes; the executor pulls the submitted image from GHCR | No |
| Account | Non-root `factoryhorde` operator; the installer refuses root | root |

The servers never connect to each other. The miner publishes an image digest as an on-chain commitment through its Pylon; the validator reads commitments from the chain and pulls the image from GHCR. Both Pylons listen on loopback only.

## Set up a server

As root on each server, clone the repository and check out the deployment branch, then run the role's setup:

```bash
git clone https://github.com/backend-developers-ltd/factory-horde.git /root/factory-horde
cd /root/factory-horde
git checkout refinery-deployment
refinery/install-prerequisites.sh
refinery/check-chain.sh
refinery/miner/setup.sh        # on the miner server
refinery/validator/setup.sh    # on the validator server
```

Order does not matter: the validator admits a miner from the first round that starts after its commitment. Both setup scripts are idempotent; rerun them after changing [`refinery.env`](refinery.env) or pulling a new revision of this branch.

`install-prerequisites.sh` installs Docker Engine with Compose from Docker's apt repository, Python 3.14, git, jq, curl, cron, sudo, uv 0.13.7 and btcli 11.3.0 (as uv tool `bittensor`), keeping anything already installed. `check-chain.sh` is read-only: it verifies the Refinery genesis hash and lists subnets.

### What the validator setup does

1. `wallet.sh factory-horde-validator validator` creates the wallet if missing (recovery words in `/root/<wallet>-recovery.txt`), tops it up from development `//Alice` below `MIN_BALANCE_TAO`, registers its hotkey and adds `VALIDATOR_STAKE_TAO` stake if it has none. Existing registration and stake are kept.
2. Creates the `factoryhorde` operator with Docker access and copies only the hotkey and public coldkey file to `/home/factoryhorde/wallets`.
3. Writes the Refinery settings into `/home/factoryhorde/factory-horde/.env`, with dispatch and weights enabled.
4. Runs [`installer/install.sh`](../installer/README.md) as the operator with `--ref $RELEASE_REF`, granting sudo only for that run. The installer downloads the checksummed release assets from GitHub, installs the `factory-horde-refinery-executor` systemd unit, starts the common Compose application with the published validator image, and adds the cron updater and its single restart grant. Later runs use the installed `update_compose.sh`.

### What the miner setup does

1. `wallet.sh miner1 miner` creates, funds and registers the miner wallet as above.
2. Copies only the hotkey and public coldkey file to `/root/factory-horde-miner/wallets` and generates Pylon tokens in `/root/factory-horde-miner/.env`.
3. Starts Pylon from [`miner/docker-compose.yml`](miner/docker-compose.yml) (Compose project `factory-horde-miner`, port 8000 on loopback).
4. Installs `factory-horde-submit.timer`, which runs [`miner/submit.sh`](miner/submit.sh) two minutes after boot and every `SUBMIT_INTERVAL`, and submits once immediately. The published submitter reads the current commitment first: an unchanged image reports `unchanged` and writes nothing.

## Configuration

[`refinery.env`](refinery.env) holds every non-secret setting: chain endpoint and genesis, subnet, release commit, image digests, wallet names, funding thresholds, round windows and the submission interval. Tokens are generated on each server and never committed.

- **Factory image:** set `FACTORY_IMAGE` to a public GHCR reference pinned by digest (the validator accepts nothing else), then rerun `refinery/miner/setup.sh`, or run `refinery/miner/submit.sh <image>` once. New commitments apply from the next round.
- **Release:** `RELEASE_REF` must be a full commit SHA on GitHub whose `installer/release-manifest.json` matches its assets. After editing a listed asset, regenerate the manifest with `/usr/bin/python3.14 -I installer/release.py manifest "$PWD"` and check it with `--check`, push, then set `RELEASE_REF` to that commit and rerun the validator setup.
- **Rounds:** 10 minutes generation, 5 minutes stop confirmation and 15 minutes evaluation. The baseline factory needs about one minute. The judge must finish `JUDGE_STOP_RESERVE_SECONDS` before the round ends.

## Operate

```bash
refinery/validator/status.sh   # services, readiness, latest rounds/results, weight calculation, pending and revealed chain weights
refinery/miner/status.sh       # Pylon, submission timer, last submission, miner UID and incentive
```

| Task | Command |
| --- | --- |
| Validator logs | `sudo -u factoryhorde docker compose --project-name factory-horde-refinery --env-file /home/factoryhorde/factory-horde/.env -f /home/factoryhorde/factory-horde/envs/deployed/docker-compose.yml logs -f validator` |
| Executor logs | `journalctl -u factory-horde-refinery-executor -f` |
| Updater log | `/home/factoryhorde/factory-horde/update.log` |
| Round evidence | `/home/factoryhorde/factory-horde/data/rounds/<date>/round-*/<miner hotkey>/` (generated files, judge report, accepted result) |
| Submission log | `journalctl -u factory-horde-submit.service` |
| Submit now | `systemctl start factory-horde-submit.service` |

Do not delete files under `data/` to force a rerun: recovery relies on them and never redraws an accepted score.

## Subnet 11

This deployment changes no subnet setting; the current values are intended. Read on 13 November 2026:

- Registration is allowed, minimum one weight and maximum weight 1.0, so a single miner can receive all weight.
- **Commit-reveal is enabled** with a reveal period of one tempo (360 blocks, about 72 minutes). Pylon detects this and commits and reveals the validator's weights itself. Revealed weights appear about 72–144 minutes after a commit, and miner incentive changes at the epoch after that. The first weight opportunity needs a completed round with an accepted score.
- UIDs 0–3 belong to an earlier hello-world deployment and cannot be removed individually. Their keys are inactive, so Yuma consensus ignores their stake once they have not set weights for 5000 blocks (about 16.7 hours). Until the last of them goes inactive, the miner's incentive can stay at zero although the validator's weights are correct. FactoryHorde ignores them because they publish no factory image.
- Burned registration must be MEV-shielded with btcli 11.3.0; `wallet.sh` submits it that way.

## Files

| File | Purpose |
| --- | --- |
| `refinery.env` | Shared settings |
| `lib.sh` | Shared shell helpers |
| `install-prerequisites.sh` | Host software for both roles |
| `check-chain.sh` | Read-only chain identity, health and subnet listing |
| `fund-from-alice.sh` | Transfer development TAO from `//Alice` to an address |
| `wallet.sh` | Create, fund, register and (validator) stake a wallet, idempotently |
| `validator/setup.sh`, `validator/status.sh` | Validator installation/update and status |
| `miner/setup.sh`, `miner/status.sh` | Miner installation/update and status |
| `miner/docker-compose.yml` | Miner Pylon |
| `miner/submit.sh` | One submission with the published submitter image |

## Differences from a real deployment

- **Development chain and keys.** Refinery is a private development chain. Wallets are funded from the public `//Alice` key, coldkeys are stored unencrypted, and recovery words are written to `/root`. A real deployment uses funded, encrypted coldkeys kept off the servers, with only hotkeys on the hosts.
- **Release selection.** The validator follows a fixed commit on this feature branch instead of a reviewed `deploy-config-*` branch, so the cron updater never moves to a new release. The branch also regenerates master's stale release manifest, without which the installer rejects master.
- **Images.** The validator, submitter, factory and judge are the published prototype candidate images (`envs/candidate/release.json`). They predate later source fixes and passed acceptance only on an isolated local chain; this is their first run on a shared chain, and the first with commit-reveal weights.
- **Mining.** The miner resubmits one fixed baseline image instead of building and publishing improved factories. The baseline factory writes a fixed greeting and the judge draws a random score, so scores and weights carry no quality signal.
- **Round length.** Rounds last 30 minutes instead of the default two hours (60/5/55 minutes).
- **Network access.** Both servers share the chain's private VLAN. Outside miners cannot reach the chain, which a real subnet would expose publicly.
- **Subnet state.** Subnet 11 is shared with inactive hello-world UIDs that hold most of the stake, and the FactoryHorde validator has about 1.5 alpha. Incentive therefore reflects the validator's weights only once those UIDs are inactive.
- **Miner operations.** The miner runs as root with an ad hoc Compose file and no monitoring or update mechanism. A real miner would run its own tooling and publish to its own GHCR namespace.
- **Pylon EVM endpoints.** Both Pylons keep their default public mainnet EVM endpoints; FactoryHorde does not use EVM calls.
- **Monitoring.** Prometheus listens on the validator's loopback only, with no alerting or remote write.
