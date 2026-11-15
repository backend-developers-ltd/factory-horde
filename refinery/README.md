# FactoryHorde on Refinery

Refinery is a private development chain (`ws://10.0.0.10:9944`); FactoryHorde uses subnet 11, mechanism 0 on it. A deployment there is an ordinary deployment that happens to point at another chain: the validator is installed with the [operator installer](../installer/README.md) and a miner submits with the [miner's Compose file](../miner/README.md#submitting-from-a-server), exactly as on any other network. This folder only adds what is specific to Refinery:

| File | Purpose |
| --- | --- |
| [`validator.env.example`](validator.env.example) | Validator settings for the chain, subnet, round windows and Grafana |
| [`miner.env.example`](miner.env.example) | Miner settings for the chain, subnet and the committed factory image |
| [`fund-from-alice.sh`](fund-from-alice.sh) | Transfers development TAO from the chain's public `//Alice` key to an address |

The current deployment has one validator server and one miner server, both Ubuntu 26.04 amd64 hosts on Refinery's private VLAN (10.0.0.0/24). On each, the non-root operator account `ubuntu` (passwordless sudo, member of the `docker` group) runs everything from a git checkout in `~/factory-horde`. The servers never connect to each other: the miner commits an image digest on chain and the validator pulls that image from GHCR.

## Wallets

Refinery wallets are created on the server that uses them and funded from `//Alice`. This needs uv and btcli (`uv tool install --python /usr/bin/python3.14 'bittensor==11.3.0'`). For the validator wallet:

```bash
CHAIN=(--network ws://10.0.0.10:9944 --fallback-endpoints none --archive-endpoints none)
WALLET=(--wallet factory-horde-validator --wallet-hotkey default --wallet-path ~/.bittensor/wallets)
btcli wallet create "${WALLET[@]}" --no-password
refinery/fund-from-alice.sh "$(jq -r .ss58Address ~/.bittensor/wallets/factory-horde-validator/coldkeypub.txt)" 100
btcli subnets register "${CHAIN[@]}" --netuid 11 "${WALLET[@]}" --proxy-for self --signer wallet --no-prompt
HOTKEY=$(jq -r .ss58Address ~/.bittensor/wallets/factory-horde-validator/hotkeys/defaultpub.txt)
btcli stake add "${CHAIN[@]}" --netuid 11 --amount-tao 1 "${WALLET[@]}" --hotkey "$HOTKEY" \
  --slippage-protection --rate-tolerance 0.05 --proxy-for self --signer wallet --no-mev-shield --no-prompt
```

The miner wallet (`miner1`) takes the same steps without the stake. btcli 11.3.0 only submits burned registration MEV-shielded, so `register` must not get `--no-mev-shield`.

## Validator

Follow the [installer instructions](../installer/README.md) with the checkout as both source and installation directory:

```bash
cd ~/factory-horde
install -m 0600 refinery/validator.env.example .env
# Fill in HOST_WALLET_DIR, VALIDATOR_HOTKEY and GRAFANA_ADDRESS in .env.
installer/install.sh "$PWD" --ref snapshot --source "file://$PWD"
```

**Change the Grafana password straight away.** Grafana starts with the login `admin` / `admin` and is reachable from the internet: open `https://GRAFANA_ADDRESS/`, log in and set a new password when Grafana asks for one.

`.env` is the server's only settings file and `envs/deployed/docker-compose.yml` its only Compose file (project `factory-horde`, with the `grafana` profile enabled). Because `.env` names both, plain `docker compose` commands work in `~/factory-horde`. The updater applies `.env` edits and whatever `git pull` brings in within 15 minutes; `installer/update_compose.sh ~/factory-horde` does so immediately.

Rounds last 30 minutes: 10 minutes generation, 5 minutes stop confirmation and 15 minutes evaluation.

## Miner

Follow [the miner instructions](../miner/README.md#submitting-from-a-server):

```bash
cd ~/factory-horde/miner
install -m 0600 ../refinery/miner.env.example .env
# Fill in HOST_WALLET_DIR and the three tokens in .env.
docker compose up -d --wait
docker compose run --rm submit
```

The validator admits the miner from the first round that starts after its commitment.

## Subnet requirements

The deployment changes no subnet setting. It expects subnet 11 to allow registration and to permit a single miner to receive all weight (minimum one weight, maximum weight 1.0).

- **Commit-reveal.** When the subnet has commit-reveal enabled, Pylon detects it and commits and reveals the validator's weights itself. Committed weights appear in the chain's weights after `commit_reveal_period` tempos; The first weight opportunity needs a completed round with an accepted score.
- **Other UIDs.** The validator only evaluates registered hotkeys that commit a valid factory image; other UIDs on the subnet are ignored.

## Differences from a real deployment

- **Development chain and keys.** Refinery is a private development chain. Wallets are funded from the public `//Alice` key, and coldkeys are stored unencrypted in the wallet directory that Pylon mounts read-only. A real deployment uses funded, encrypted coldkeys kept off the servers, with only hotkeys on the hosts.
- **Release selection.** The validator installs from its own git checkout, so a release is whatever `git pull` brings in. Operators of a real deployment have no checkout: their installer downloads the same assets from a reviewed `deploy-config-*` branch.
- **Images.** The validator, submitter, factory and judge are the published prototype candidate images (`envs/candidate/release.json`). They predate later source fixes, and their acceptance tests ran on an isolated local chain with direct weights.
- **Mining.** The miner commits one fixed baseline image instead of building and publishing improved factories. The baseline factory writes a fixed greeting and the judge draws a random score, so scores and weights carry no quality signal.
- **Round length.** Rounds last 30 minutes instead of the default two hours (60/5/55 minutes).
- **Network access.** Both servers share the chain's private VLAN. Outside miners cannot reach the chain, which a real subnet would expose publicly.
- **Pylon EVM endpoints.** Both Pylons keep their default public mainnet EVM endpoints; FactoryHorde does not use EVM calls.
- **Monitoring.** Prometheus listens on the validator's loopback only, with no alerting or remote write. Grafana uses a single local admin account on a bare IP address with a short-lived certificate.
