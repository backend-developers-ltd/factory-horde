# FactoryHorde on Refinery

Refinery is a private development chain (`ws://10.0.0.10:9944`); FactoryHorde uses subnet 11, mechanism 0 on it. A deployment there is an ordinary deployment that happens to point at another chain: the validator is installed with the [operator installer](../installer/README.md) and a miner submits with the [miner's Compose file](../miner/README.md#submitting-from-a-server), exactly as on any other network. This folder only adds what is specific to Refinery:

| File | Purpose |
| --- | --- |
| [`validator.env.example`](validator.env.example) | Validator settings for the chain, subnet, round windows, task, inference proxy and metrics push |
| [`miner.env.example`](miner.env.example) | Miner settings for the chain, subnet and the committed factory image |
| [`fund-from-alice.sh`](fund-from-alice.sh) | Transfers development TAO from the chain's public `//Alice` key to an address |

The current deployment has one validator server and one miner server, both Ubuntu 26.04 amd64 hosts on Refinery's private VLAN (10.0.0.0/24). On each, the non-root operator account `ubuntu` (passwordless sudo, member of the `docker` group) uses `~/factory-horde` as an installation directory, without a Git checkout. The servers never connect to each other: the miner commits an image digest on chain and the validator pulls that image from GHCR.

## Wallets

Refinery wallets are created on the server that uses them and funded from `//Alice` with `fund-from-alice.sh`, downloaded from the selected configuration release; the helper is not part of the runtime installation. This needs uv, jq and btcli (`uv tool install --python /usr/bin/python3.14 'bittensor==11.3.0'`). For the validator wallet:

```bash
RELEASE_REF=deploy-config-production
RELEASE_SHA=$(git ls-remote https://github.com/backend-developers-ltd/factory-horde.git "refs/heads/$RELEASE_REF" | cut -f1)
test "${#RELEASE_SHA}" -eq 40
RELEASE_URL="https://raw.githubusercontent.com/backend-developers-ltd/factory-horde/$RELEASE_SHA"
FUND=$(mktemp)
curl -fsSL "$RELEASE_URL/refinery/fund-from-alice.sh" -o "$FUND"

CHAIN=(--network ws://10.0.0.10:9944 --fallback-endpoints none --archive-endpoints none)
WALLET=(--wallet factory-horde-validator --wallet-hotkey default --wallet-path ~/.bittensor/wallets)
btcli wallet create "${WALLET[@]}" --no-password
bash "$FUND" "$(jq -r .ss58Address ~/.bittensor/wallets/factory-horde-validator/coldkeypub.txt)" 100
btcli subnets register "${CHAIN[@]}" --netuid 11 "${WALLET[@]}" --proxy-for self --signer wallet --no-prompt
HOTKEY=$(jq -r .ss58Address ~/.bittensor/wallets/factory-horde-validator/hotkeys/defaultpub.txt)
btcli stake add "${CHAIN[@]}" --netuid 11 --amount-tao 1 "${WALLET[@]}" --hotkey "$HOTKEY" \
  --slippage-protection --rate-tolerance 0.05 --proxy-for self --signer wallet --no-mev-shield --no-prompt
```

The miner wallet (`miner1`) takes the same steps without the stake. btcli 11.3.0 only submits burned registration MEV-shielded, so `register` must not get `--no-mev-shield`.

## Validator

Follow [published operator installation](../installer/README.md#published-operator-installation), selecting `RELEASE_REF=deploy-config-production`. After resolving `RELEASE_SHA` and downloading the bootstrap installer, prepare the Refinery environment:

```bash
umask 077
curl -fsSL "$RELEASE_URL/refinery/validator.env.example" -o "$HOME/operator.env"
# Set JUDGE_IMAGE to the published GHCR digest for the review judge in your release.
# Fill in HOST_WALLET_DIR, VALIDATOR_HOTKEY, CLAUDE_CODE_OAUTH_TOKEN, METRICS_UPSTREAM_USER and METRICS_UPSTREAM_PASSWORD; check SUBMISSION_NAMESPACE.
bash "$BOOTSTRAP/install.sh" "$HOME/factory-horde" --env-file "$HOME/operator.env" --ref "$RELEASE_REF"
```

**Upgrading to envelope commitments.** An existing installation must have `SUBMISSION_NAMESPACE=ghcr.io/backend-developers-ltd` in `~/factory-horde/.env` before it follows a release with envelope commitments: once dispatch is enabled, the validator refuses to start without it, and the updater does not roll back. The miners' `.env` needs the same line before they run the new submitter.

The metrics login comes from the company monitoring stack: see [monitoring](../installer/README.md#monitoring-and-recovery) for how it is created and registered. Metrics then appear in the central Grafana under the deployment's `client_server_group` and `host`.

`.env` is the server's only settings file and `envs/deployed/docker-compose.yml` its only Compose file (project `factory-horde`, with the `alloy` and `inference` profiles enabled). Because `.env` names both, plain `docker compose` commands work in `~/factory-horde`. The updater applies `.env` edits and releases promoted to `deploy-config-refinery` within 15 minutes; `installer/update_compose.sh ~/factory-horde` does so immediately.

Rounds last 60 minutes: 30 minutes generation, 5 minutes stop confirmation and 25 minutes evaluation. Every factory gets the task in `TASK_SPECIFICATION`, a small command-line to-do list manager in Python.

Factories call Claude through the validator's [inference proxy](../installer/README.md#task-specification-and-inference-proxy). `CLAUDE_CODE_OAUTH_TOKEN` is a Claude subscription token from `claude setup-token`; it stays in `.env` on the validator server and is never given to factories, which reach only the proxy. Each factory and judge gets a separate internal network connected only to the proxy. Application checks have no network. The proxy applies no per-factory quota.

## Miner

Follow [the miner instructions](../miner/README.md#submitting-from-a-server):

```bash
mkdir -p ~/factory-horde/miner
cd ~/factory-horde/miner
# Use the miner instructions to download the verified Compose file from deploy-config-production.
umask 077
curl -fsSL "$RELEASE_URL/refinery/miner.env.example" -o .env
# Fill in HOST_WALLET_DIR and the three tokens in .env; check SUBMISSION_NAMESPACE.
docker compose up -d --wait
docker compose run --rm submit
```

The validator admits the miner from the first round that starts after its commitment. Schedule the submitter as [the miner instructions](../miner/README.md#submitting-from-a-server) describe, so a commitment another writer dropped is restored.

The Refinery miner server runs three demo miners. Their factories are built and documented in [factory-horde-demo-miners](https://github.com/backend-developers-ltd/factory-horde-demo-miners).

## Subnet requirements

The deployment changes no subnet setting. It expects subnet 11 to allow registration and to permit a single miner to receive all weight (minimum one weight, maximum weight 1.0).

- **Commit-reveal.** When the subnet has commit-reveal enabled, Pylon detects it and commits and reveals the validator's weights itself. Committed weights appear in the chain's weights after `commit_reveal_period` tempos; The first weight opportunity needs a completed round with an accepted score.
- **Other UIDs.** The validator only evaluates registered hotkeys that commit a valid factory image; other UIDs on the subnet are ignored.

## Differences from a real deployment

- **Development chain and keys.** Refinery is a private development chain. Wallets are funded from the public `//Alice` key, and coldkeys are stored unencrypted in the wallet directory that Pylon mounts read-only. A real deployment uses funded, encrypted coldkeys kept off the servers, with only hotkeys on the hosts.
- **Release selection.** Refinery is a production deployment on another chain, so `ENVIRONMENT=production`: the validator follows `deploy-config-production` and the miner downloads its checksummed Compose file from that branch. Neither server deploys source checkouts.
- **Images.** Refinery runs the production validator and submitter images pinned by digest in the configuration release. The behavioral judge is pinned by digest, and the miners commit demo factories from [factory-horde-demo-miners](https://github.com/backend-developers-ltd/factory-horde-demo-miners) rather than the baseline factory; the earlier localnet candidate is historical acceptance evidence, not the active application release.
- **Mining.** Three demo miners on one server commit factories that write the requested program with Claude. The historical fixture judge draws random scores. Review scoring requires the updated validator, executor and matching judge digest. The example selects `JUDGE_MODE=review` and provides one list of criteria for all miners. Review weights start with the first eligible review round.
- **Inference.** All factories and judges use one Claude subscription through the validator's proxy, without a per-workload quota; peer workloads use separate isolated networks.
- **Round length.** Rounds last 60 minutes (30/5/25 minutes) instead of the default two hours (60/5/55 minutes).
- **Network access.** Both servers share the chain's private VLAN. Outside miners cannot reach the chain, which a real subnet would expose publicly.
- **Pylon EVM endpoints.** Both Pylons keep their default public mainnet EVM endpoints; FactoryHorde does not use EVM calls.
- **Monitoring.** Alloy pushes metrics to the company monitoring stack; only its generic host and silence alerts apply, with no FactoryHorde-specific rules. The local Prometheus listens on the validator's loopback only.
