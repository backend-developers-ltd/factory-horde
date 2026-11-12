# FactoryHorde: server inspection and Refinery setup runbook

Inspected on 12 November 2026, approximately 05:45–05:53 UTC. Repository revision: `6ff66cd5442e79194770ff54383abcb1a34a63c9`. Target: `root@104.105.23.142` through `jumpuser@jb.reef.pl` (the server's actual hostname is `localhost`).

## Findings

**FactoryHorde is not running on this server. Validator identity preparation has happened on Refinery, but the application and miner setup are incomplete.** No changes were made to the remote filesystem, services, containers, wallets or chain during this inspection. SSH and RPC requests were read-only; Python chain reads used `-B` and an audit hook rejecting filesystem mutations, including client cache writes. Private wallet files and shell history were not opened.

| Item | Observed state |
| --- | --- |
| Host | Ubuntu 26.04.1, Linux amd64, 4 CPUs, approximately 7.7 GiB RAM and 72 GiB free disk |
| Prerequisites | Docker 29.9.3, Compose v5.7.0, Python 3.14.4, uv 0.13.7 and btcli/Bittensor 11.3.0 installed; uv/btcli are under `/root/.local/bin`, absent from noninteractive SSH's default PATH |
| Checkout | `/root/factory-horde`, clean `master`, same revision as the inspected local checkout |
| Docker | Default context; no images, containers, volumes or Compose projects |
| Services | No FactoryHorde systemd service or application cron entry found |
| Application configuration | No `.env`, `installation.json` or `registrations.json` found in the checkout search; no `localnet/.env`; `localnet/wallets` contains only `.gitignore` |
| Existing wallet | `/root/.bittensor/wallets/factory-horde-validator`, hotkey `default`; public and private key files exist, but private contents/unlockability were not tested |
| Existing setup scripts | `/root/test-refinery.sh`, `/root/fund-refinery-wallet.sh`, `/root/stake-refinery-validator.sh`; scripts target Refinery and subnet 11 |
| Chain connectivity | RPC `http://10.0.0.10:9944`, WebSocket `ws://10.0.0.10:9944`, reachable from this host; chain reports `Bittensor`, 3 peers, not syncing |
| Chain identity | Genesis `0xe185ddf8629910ec509d81f6568ccb227e6f2721747f7468d64261f8425ec0a9` |
| Subnet 2 | Exists; name `omron`; 3 registered nodes, existing commitment and weight rows. It is not a free FactoryHorde slot. |
| Subnet 11 | Exists; name `dippy`; 5 registered nodes, including the FactoryHorde validator. Other participants have weight rows; it is not empty. |
| FactoryHorde validator | Hotkey `5F1brCbve88ykBjP34jNXMmvvSczvWxvPNdUSzv2Vvy1U4WU`, coldkey `5GHsbRupA1kW7zgfTm2jX6xjWsTCQjagwFiNDiUPSoSGMWSE`; subnet 11 UID 4, permit `true`, active `false` at block 611593 |
| Funding/stake | Coldkey free balance 98.995687394 development TAO; subnet-11 alpha stake raw value 1521946264 (approximately 1.521946264 alpha, not TAO) |
| FactoryHorde weights | No current subnet-11 mechanism-0 weight row for UID 4; its `last_update` equals registration block 551331 |
| Miner submissions | No commitments on subnet 11 at inspection; no FactoryHorde miner wallet found in the inspected wallet locations |
| Chain settings | Subnets 2 and 11: tempo 360, one mechanism (ID 0), commit-reveal enabled. Subnet 11: minimum 1 weight, weight rate limit 100 blocks. Timestamp minimum period 6000 ms, consistent with 12-second blocks rather than the isolated test chain's 0.25 seconds. |

These observations show incomplete deployment and no current end-to-end run. They do not prove the host has never run containers that were subsequently removed, or that a previous chain state never contained submissions. Repository acceptance evidence documents an isolated chain on the development VM; it does not establish a run against this Refinery chain.

## What triggers each component?

| Action | Trigger today | Repeats automatically? |
| --- | --- | --- |
| Develop a factory | A person changes the factory source | No autonomous factory-improvement loop exists. |
| Build/publish a factory image | Local build command, or GitHub Actions `build-baselines.yml` after relevant changes on `deploy-build-*`, or manual workflow dispatch | CI automates a requested build. The validator never asks a miner to build an image. |
| Submit image reference | Run the `miner` CLI with a public GHCR digest | One-shot: reads, commits if changed, confirms and exits. Publishing an image alone does not submit it. |
| Discover submissions and create rounds | Long-running validator, with dispatch enabled | Yes. Each new round freezes the current valid registrations and image commitments. A miner can leave one digest registered across many rounds. |
| Start/stop factory containers | Host executor polls the validator's shared-file requests | Yes; default polling interval is two seconds. It enforces frozen deadlines and reconciles after restart. |
| Judge output | Validator's evaluation stage publishes a separate judge request | Yes. Early factory completion does not bypass the evaluation-stage gate. |
| Calculate/write weights | Nexus weight opportunities, with weights separately enabled and accepted scores available | Yes, subject to chain timing, permits and compatible weight configuration. |
| Pylon | Persistent Compose service | Serves chain requests continuously; does not build factories or generate applications. |
| Release updates | Full installer's cron | Every 15 minutes, following the selected release. The manual pilot below deliberately uses executor-only installation and explicit Compose commands. |

The baseline factory writes a fixed greeting project after about one minute; its judge checks files and assigns a random score. Real inference, useful quality judging and automated factory improvement remain outside this implementation. See [miner](../miner/README.md), [factory](../miner/factory/README.md), [validator](../validator/README.md) and the [Notion description](https://app.notion.com/p/Description-415b63b4ef1e8063ad96ed5813aa4e98).

## 1. Repeat the read-only checks

Run on your workstation:

```bash
ssh root@104.105.23.142 -J jumpuser@jb.reef.pl
```

Then run on the server as root. The existing `test-refinery.sh` was inspected: it only issues read-only HTTP RPC calls. It lists existing subnet IDs and registered-node counts at one finalized block.

```bash
export PATH=/root/.local/bin:$PATH
hostname
docker ps -a
docker image ls
docker compose ls -a
systemctl list-units --all --type=service 'factory-horde*'
bash /root/test-refinery.sh http://10.0.0.10:9944
curl --fail --silent --show-error --max-time 15 \
  -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"chain_getBlockHash","params":[0]}' \
  http://10.0.0.10:9944
```

These CLI reads submit no transactions, although btcli may refresh its local caches when you run them:

```bash
CHAIN_ARGS=(--network ws://10.0.0.10:9944 --fallback-endpoints none --archive-endpoints none)
btcli subnets metagraph 2 "${CHAIN_ARGS[@]}" --json
btcli subnets metagraph 11 "${CHAIN_ARGS[@]}" --json |
  jq --arg hk '5F1brCbve88ykBjP34jNXMmvvSczvWxvPNdUSzv2Vvy1U4WU' '
    (.hotkeys | index($hk)) as $uid |
    {block, name, owner_coldkey, tempo, num_uids, commit_reveal_weights_enabled,
     uid: $uid, registered: ($uid != null),
     permit: (if $uid != null then .validator_permit[$uid] else null end),
     active: (if $uid != null then .active[$uid] else null end)}'
```

A nonexistent subnet ID is not a reservation. Subnet creation auto-assigns an ID; an existing subnet with zero nodes still has an owner. Establish ownership and intended use with the chain operator, not just a node count.

## 2. Resolve subnet allocation before changing the chain

**Required decision:** obtain a dedicated FactoryHorde subnet, or explicit confirmation that subnet 11 and its existing participants/settings are intended for this experiment. Subnet 2 must not be reused merely because it is the repository default. Subnet 11 has an Alice-owned coldkey, but possession of the public development key does not establish that other projects have stopped using it.

The commands below prompt for the approved existing subnet. They do not create a subnet, change chain-wide settings or change somebody else's subnet parameters. If a new subnet is needed, the chain administrator must allocate/activate it first and return its actual ID. This is the only environment-specific allocation input that cannot be determined from the repository.

For the smallest pilot, use mechanism 0 on a dedicated subnet. Mechanism 1 does not currently exist on subnet 11. Reproducing the acceptance test's two-mechanism arrangement requires a separate administrator-controlled change; do not lower UID capacity or add mechanisms on a shared project subnet as a side effect of bootstrap.

Before enabling weights, the administrator must either configure the dedicated subnet to match the tested direct-weight path (commit-reveal disabled), or separately validate the application's complete commit-reveal path on this chain. Keep weights disabled until one of these is done. Current acceptance evidence does not validate the observed Refinery settings.

For the direct-weight option, the subnet administrator can run the following with the installed btcli 11.3.0, only after allocating the subnet to this experiment. Supply the actual owner wallet; the existing `factory-horde-validator` wallet is not the subnet owner. This changes one subnet's setting and requires the owner's signature. It does not create an owner key or assume permission to use Alice.

```bash
read -r -p 'Allocated subnet ID: ' FH_NETUID
read -r -p 'Owner wallet directory: ' FH_OWNER_PATH
read -r -p 'Owner wallet name: ' FH_OWNER_WALLET
btcli sudo set --network ws://10.0.0.10:9944 \
  --fallback-endpoints none --archive-endpoints none \
  --netuid "$FH_NETUID" --name commit_reveal_weights_enabled --value false \
  --wallet "$FH_OWNER_WALLET" --wallet-path "$FH_OWNER_PATH" \
  --proxy-for self --signer wallet --no-mev-shield
btcli subnets metagraph "$FH_NETUID" --network ws://10.0.0.10:9944 \
  --fallback-endpoints none --archive-endpoints none --json |
  jq '{netuid, owner_coldkey, tempo, commit_reveal_weights_enabled}'
```

Require the readback to show `false` before using the direct-weight procedure. Do not execute this on shared subnet 11 without its operator's explicit allocation/configuration decision.

**Do not run `localnet/bootstrap.py`, `localnet/compose.sh` or the localnet acceptance checks against Refinery.** They require a separate loopback chain, subnet 2 and isolated wallets. Bootstrap also changes chain-wide `AdminFreezeWindow`; simply removing its guards would affect other projects. `localnet/compose.yml` overrides Pylon's endpoint back to `ws://subtensor:9944` and hardcodes miner subnet 2.

## 3. Prepare a non-root operator and reuse the validator identity

Everything from this point is a proposed procedure for you to execute, not work performed during inspection. The steps write files, create services and eventually submit transactions. Use a maintenance session and run the sections in order. Do not paste all sections unattended across the allocation and weight gates.

Run as root. The installer rejects root execution, so create a dedicated operator with Docker access and use sudo only for installation. Preserve the existing wallet; do not regenerate it. Copy only the hotkey and public coldkey file into the runtime wallet tree—coldkey signing stays in the original root-owned wallet for administration.

```bash
set -euo pipefail
export PATH=/root/.local/bin:$PATH
id factoryhorde >/dev/null 2>&1 || adduser --disabled-password --gecos '' factoryhorde
usermod -aG docker factoryhorde
install -m 0755 /root/.local/bin/uv /usr/local/bin/uv
install -d -o factoryhorde -g factoryhorde -m 0750 /home/factoryhorde/run
install -d -o factoryhorde -g factoryhorde -m 0700 /home/factoryhorde/run/wallets/validator/hotkeys
install -o factoryhorde -g factoryhorde -m 0600 \
  /root/.bittensor/wallets/factory-horde-validator/coldkeypub.txt \
  /home/factoryhorde/run/wallets/validator/coldkeypub.txt
install -o factoryhorde -g factoryhorde -m 0600 \
  /root/.bittensor/wallets/factory-horde-validator/hotkeys/default \
  /home/factoryhorde/run/wallets/validator/hotkeys/default
git clone --no-hardlinks /root/factory-horde /home/factoryhorde/src
chown -R factoryhorde:factoryhorde /home/factoryhorde/src
sudo -u factoryhorde git -C /home/factoryhorde/src checkout --detach 6ff66cd5442e79194770ff54383abcb1a34a63c9
```

The clone is for deployment builds, not editing or pushing master. Future source changes belong on feature branches. A live build has not been tested on this host; retain build logs and stop if dependency/image retrieval fails.

## 4. Create and register one miner

Start with one miner to prove the loop; add more after it works. In the same root shell, record the chain operator's allocated subnet ID:

```bash
read -r -p 'Approved FactoryHorde subnet ID: ' FH_NETUID
[[ "$FH_NETUID" =~ ^[0-9]+$ ]] && (( FH_NETUID > 0 ))
export FH_NETUID
CHAIN_ARGS=(--network ws://10.0.0.10:9944 --fallback-endpoints none --archive-endpoints none)
btcli subnets metagraph "$FH_NETUID" "${CHAIN_ARGS[@]}" --json
```

Check the displayed owner and participant list against the allocation. If it differs, stop. The existing validator is registered on 11 only as far as this inspection establishes. If using another subnet, register that existing wallet there, then stake it and wait for a permit; use the same verified registration/staking commands below with the validator wallet. Do not register it again on 11 unnecessarily.

Create a miner wallet. This command prints recovery words: keep them out of logs and reports. `--no-password` is only for this private development wallet.

```bash
btcli wallet create --wallet miner1 --wallet-hotkey default \
  --wallet-path /root/.bittensor/wallets --no-password
FH_MINER_COLD=$(jq -r .ss58Address /root/.bittensor/wallets/miner1/coldkeypub.txt)
btcli wallet transfer "${CHAIN_ARGS[@]}" \
  --wallet factory-horde-validator --wallet-hotkey default \
  --wallet-path /root/.bittensor/wallets \
  --dest "$FH_MINER_COLD" --amount-tao 1 \
  --proxy-for self --signer wallet --no-mev-shield
btcli subnets register "${CHAIN_ARGS[@]}" --netuid "$FH_NETUID" \
  --wallet miner1 --wallet-hotkey default --wallet-path /root/.bittensor/wallets \
  --proxy-for self --signer wallet --no-mev-shield
```

Review the CLI's current cost before confirming. At inspection subnet 11's burn was 0.0005 development TAO; costs can change. One TAO is a pilot funding amount, not a guaranteed sufficient budget. Do not rerun creation/transfer blindly: creation should refuse an existing wallet, and transfer spends again.

Only if the selected subnet differs from 11 and the validator is not already registered/permitted there:

```bash
btcli subnets register "${CHAIN_ARGS[@]}" --netuid "$FH_NETUID" \
  --wallet factory-horde-validator --wallet-hotkey default --wallet-path /root/.bittensor/wallets \
  --proxy-for self --signer wallet --no-mev-shield
btcli stake add "${CHAIN_ARGS[@]}" --netuid "$FH_NETUID" --amount-tao 1 \
  --wallet factory-horde-validator --wallet-hotkey default --wallet-path /root/.bittensor/wallets \
  --hotkey 5F1brCbve88ykBjP34jNXMmvvSczvWxvPNdUSzv2Vvy1U4WU \
  --slippage-protection --rate-tolerance 0.05 \
  --proxy-for self --signer wallet --no-mev-shield
```

Stake does not guarantee a permit. Re-read the metagraph and wait for the chain to grant one before weight writes. Do not reuse `/root/stake-refinery-validator.sh` for another subnet: it hardcodes 11.

Copy the miner's runtime signing material, leaving its coldkey in the administrative wallet directory:

```bash
install -d -o factoryhorde -g factoryhorde -m 0700 /home/factoryhorde/run/wallets/miner1/hotkeys
install -o factoryhorde -g factoryhorde -m 0600 /root/.bittensor/wallets/miner1/coldkeypub.txt /home/factoryhorde/run/wallets/miner1/coldkeypub.txt
install -o factoryhorde -g factoryhorde -m 0600 /root/.bittensor/wallets/miner1/hotkeys/default /home/factoryhorde/run/wallets/miner1/hotkeys/default
printf '%s\n' "$FH_NETUID" > /home/factoryhorde/run/netuid
chown factoryhorde:factoryhorde /home/factoryhorde/run/netuid
```

## 5. Build application images and write a Refinery configuration

Use the current source for validator and submitter, and the existing published greeting factory/judge for the first loop. Do not use `build-validator.sh`: it calls localnet preparation. These direct builds avoid creating a second chain.

Enter an operator shell from the root session:

```bash
sudo -iu factoryhorde
set -euo pipefail
cd /home/factoryhorde/src
FH_RUN=/home/factoryhorde/run
FH_NETUID=$(cat "$FH_RUN/netuid")
docker build --platform linux/amd64 --iidfile "$FH_RUN/validator-image.id" validator
docker build --platform linux/amd64 --iidfile "$FH_RUN/submitter-image.id" miner
```

Create the configuration with dispatch and weights off. These values use the inspected chain's 12-second block timing, a three-minute demonstration round, one CPU/512 MiB per workload, and the existing validator hotkey. The factory still waits about 60 seconds, so do not shorten generation below that.

```bash
umask 077
cat > "$FH_RUN/.env" <<EOF
ENVIRONMENT=localnet
BITTENSOR_NETWORK=ws://10.0.0.10:9944
NETUID=$FH_NETUID
MECHANISM_ID=0
SUBNET_TEMPO=360
BLOCK_DURATION_SECONDS=12
BITTENSOR_WALLET_NAME=validator
BITTENSOR_WALLET_HOTKEY_NAME=default
HOST_WALLET_DIR=$FH_RUN/wallets
FACTORY_HORDE_DATA_ROOT=$FH_RUN/data
VALIDATOR_DATA_ROOT=/var/lib/factory-horde
CONTAINER_UID=$(id -u)
CONTAINER_GID=$(id -g)
EXECUTOR_USER=$(id -un)
EXECUTOR_GROUP=$(id -gn)
EXECUTOR_PYTHON=/usr/bin/python3.14
EXECUTOR_PLATFORM=linux/amd64
EXECUTOR_MEMORY=512m
EXECUTOR_CPUS=1
EXECUTOR_PIDS_LIMIT=128
EXECUTOR_POLL_SECONDS=2
EXECUTOR_WORKERS=32
VALIDATOR_IMAGE=$(cat "$FH_RUN/validator-image.id")
VALIDATOR_HOTKEY=5F1brCbve88ykBjP34jNXMmvvSczvWxvPNdUSzv2Vvy1U4WU
VALIDATOR_DISPATCH_ENABLED=false
VALIDATOR_WEIGHTS_ENABLED=false
GENERATION_SECONDS=100
CONFIRMATION_SECONDS=15
EVALUATION_SECONDS=65
JUDGE_STOP_RESERVE_SECONDS=10
STOP_GRACE_SECONDS=5
JUDGE_IMAGE=ghcr.io/backend-developers-ltd/factory-horde-judge@sha256:e46ef2808251ee3bd7df35e321b9e8383d82373f335da890920ad93724c289cf
PYLON_HOST_PORT=8000
PROMETHEUS_HOST_PORT=9090
VALIDATOR_PYLON_IDENTITY_TOKEN=$(openssl rand -hex 24)
VALIDATOR_PYLON_OPEN_ACCESS_TOKEN=$(openssl rand -hex 24)
PYLON_METRICS_TOKEN=$(openssl rand -hex 24)
MINER1_PYLON_TOKEN=$(openssl rand -hex 24)
EOF
```

Use this once for a fresh pilot. Re-running it replaces tokens and configuration. If the allocated subnet tempo differs, change `SUBNET_TEMPO` to its actual value. `ENVIRONMENT=localnet` labels a development environment; the explicit endpoint selects Refinery. Do not add the installer's `--localnet` option.

The common Compose file defines only the validator Pylon identity. Add one miner identity in an external overlay, without importing the repository's Subtensor service:

```bash
cat > "$FH_RUN/refinery.compose.yml" <<'YAML'
services:
  pylon:
    environment:
      PYLON_IDENTITIES: '["validator", "miner1"]'
      PYLON_EVM_RPC_URL: http://10.0.0.10:9944
      PYLON_EVM_ARCHIVE_RPC_URL: http://10.0.0.10:9944
      PYLON_ID_MINER1_WALLET_NAME: miner1
      PYLON_ID_MINER1_HOTKEY_NAME: default
      PYLON_ID_MINER1_NETUID: "${NETUID:?}"
      PYLON_ID_MINER1_TOKEN: "${MINER1_PYLON_TOKEN:?}"
YAML
cat > "$FH_RUN/compose" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
exec docker compose --project-name factory-horde-refinery \
  --env-file /home/factoryhorde/run/.env \
  -f /home/factoryhorde/src/envs/deployed/docker-compose.yml \
  -f /home/factoryhorde/run/refinery.compose.yml "$@"
SH
chmod 700 "$FH_RUN/compose"
"$FH_RUN/compose" config --quiet
"$FH_RUN/compose" config --services
```

Expect exactly `pylon`, `validator`, `node-exporter`, `prometheus`, and no `subtensor`. Pylon must reach `10.0.0.10` from Docker's network as well as from the host. If startup shows a connection failure, investigate routing/firewall rules; do not silently switch to a public endpoint.

## 6. Install the executor and start the application

First check the asset manifest without changing it:

```bash
/usr/bin/python3.14 -I installer/release.py manifest "$PWD" --check
```

The executor installer invokes sudo for its systemd unit. The operator has no password/sudo grant yet. For this one installation, return to the root shell, create a temporary restricted sudoers entry for `systemctl` and `install`, invoke the installer as the operator, and remove the grant even on failure:

```bash
exit
set -euo pipefail
FH_SUDOERS=/etc/sudoers.d/factory-horde-bootstrap
trap 'rm -f "$FH_SUDOERS"' EXIT
printf '%s\n' 'factoryhorde ALL=(root) NOPASSWD: /usr/bin/systemctl, /usr/bin/install' > "$FH_SUDOERS"
chmod 440 "$FH_SUDOERS"
visudo -cf "$FH_SUDOERS"
sudo -u factoryhorde /home/factoryhorde/src/installer/install-executor.sh \
  /home/factoryhorde/run/.env factory-horde-refinery-executor
rm -f "$FH_SUDOERS"
trap - EXIT
systemctl status factory-horde-refinery-executor --no-pager
sudo -iu factoryhorde
set -euo pipefail
FH_RUN=/home/factoryhorde/run
"$FH_RUN/compose" up -d --wait --wait-timeout 180 pylon validator node-exporter prometheus
"$FH_RUN/compose" ps
"$FH_RUN/compose" logs --tail 50 pylon validator
```

The temporary grant is privileged; remove it immediately as shown. Executor-only installation does not install the full application's cron updater. This avoids an updater starting Compose without the external miner overlay. Use the wrapper for every application operation. A durable managed Refinery release should package this overlay and extend the updater's asset selection before enabling automated updates.

Check readiness. `/readyz` checks the current runtime's chain connection, executor/Docker observations, record compatibility and writable storage:

```bash
"$FH_RUN/compose" exec -T validator /opt/venv/bin/python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9101/readyz').read().decode())"
cat "$FH_RUN/data/control/chain-observation.json"
cat "$FH_RUN/data/control/executor-health.json"
```

A healthy validator with dispatch disabled is connected but not generating work. If Pylon cannot open the copied hotkey, resolve the wallet format/encryption issue before continuing; this was not tested during the read-only inspection.

## 7. Submit the factory and enable recurring rounds

Run as `factoryhorde`. Load the generated trusted shell-compatible environment without printing tokens. The submitter uses the loopback Pylon port via Linux host networking and exits after exact readback:

```bash
(
set -a
. /home/factoryhorde/run/.env
set +a
export PYLON_IDENTITY_TOKEN="$MINER1_PYLON_TOKEN"
docker run --rm --read-only --tmpfs /tmp --cap-drop ALL \
  --security-opt no-new-privileges --network host \
  --env PYLON_IDENTITY_TOKEN \
  --env PYLON_ADDRESS=http://127.0.0.1:8000 \
  --env PYLON_IDENTITY=miner1 --env NETUID \
  "$(cat /home/factoryhorde/run/submitter-image.id)" \
  'ghcr.io/backend-developers-ltd/factory-horde-factory@sha256:8e38b2081f7628ca9dab545fb00ee756e23c1ed5d4d292db0e41c1012acbc8a3'
unset PYLON_IDENTITY_TOKEN
)
sed -i 's/^VALIDATOR_DISPATCH_ENABLED=false$/VALIDATOR_DISPATCH_ENABLED=true/' /home/factoryhorde/run/.env
/home/factoryhorde/run/compose up -d --no-deps --force-recreate --wait --wait-timeout 180 validator
/home/factoryhorde/run/compose logs --tail 100 validator
```

Expect submission JSON with `published`, `unchanged` or `recovered`, identifying the intended hotkey and subnet. Then expect a persisted round, factory execution, a separate judge during evaluation, and an accepted score. The factory/judge images are pulled by the executor; this can delay the first run. Subsequent rounds reuse the submitted digest automatically.

```bash
cat /home/factoryhorde/run/data/control/schedule.json
find /home/factoryhorde/run/data/rounds -type f \
  \( -name main.py -o -name README.md -o -name report.json -o -name result.json -o -name factory-result.json \) -print
/home/factoryhorde/run/compose logs --tail 100 validator
```

Inspect the paths returned by `find`; verify the generated greeting, linked report and accepted result. Do not delete retained execution evidence to force a new run. To diagnose the executor from a root session, use `journalctl -u factory-horde-refinery-executor -n 100 --no-pager`.

## 8. Enable and independently verify weights

**Gate:** confirm the allocated subnet is authorized for these writes, the validator has a permit there, mechanism 0 is selected, and the commit-reveal compatibility issue in section 2 is resolved. An application score is not evidence of an on-chain weight update.

After this gate, run as the operator:

```bash
sed -i 's/^VALIDATOR_WEIGHTS_ENABLED=false$/VALIDATOR_WEIGHTS_ENABLED=true/' /home/factoryhorde/run/.env
/home/factoryhorde/run/compose up -d --no-deps --force-recreate --wait --wait-timeout 180 validator
/home/factoryhorde/run/compose logs --tail 100 validator
cat /home/factoryhorde/run/data/control/weight-calculation.json
```

Allow a weight opportunity: tempo 360 at 12 seconds per block is about 72 minutes; a three-minute round does not imply three-minute chain writes. Pylon acknowledgement and `weight-calculation.json` are not inclusion proof.

From the root session, use the installed client's read API to inspect actual mechanism-0 weights. This is a chain read, though ordinary client cache refreshes may write local cache files:

```bash
FH_NETUID=$(cat /home/factoryhorde/run/netuid)
export FH_NETUID
/root/.local/share/uv/tools/bittensor/bin/python -B - <<'PY'
import asyncio
import json
import os
from bittensor import Client

async def main():
    netuid = int(os.environ['FH_NETUID'])
    async with Client(network='ws://10.0.0.10:9944', fallback_endpoints=[], archive_endpoints=[]) as client:
        graph = await client.read('metagraph', netuid=netuid)
        hotkey = '5F1brCbve88ykBjP34jNXMmvvSczvWxvPNdUSzv2Vvy1U4WU'
        uid = graph['hotkeys'].index(hotkey)
        weights = await client.read('weights', netuid=netuid, mechid=0)
        print(json.dumps({'block': graph['block'], 'uid': uid, 'permit': graph['validator_permit'][uid],
                          'last_update': graph['last_update'][uid], 'weights': weights.get(uid, {}),
                          'recipient_hotkeys': dict(enumerate(graph['hotkeys']))}, indent=2))

asyncio.run(main())
PY
```

Require a nonempty row for the validator UID targeting the registered miner hotkey, and a later update block. With one successful miner and no other valid submitted factories, the normalized row should give that miner weight 1.0. With more miners, compare the accepted scores' softmax to the chain vector with integer-encoding tolerance. Registration can change, so map UIDs to hotkeys when interpreting results. For durable acceptance, retain a block-aligned chain snapshot; the short diagnostic above performs consecutive reads and is not a replacement for such an acceptance harness.

## Completion checklist and operating notes

- Chain operator confirms the subnet allocation and compatible weight configuration; no other project's subnet/global parameters were changed incidentally.
- Validator and miner hotkeys are registered on that same subnet, and the validator has a permit.
- Pylon, validator, monitoring and host executor are healthy; readiness and chain observations remain fresh.
- Miner commitment points to the intended immutable public factory digest.
- At least one full round leaves generated files, judge report and immutable accepted score.
- A direct chain read confirms the validator's weight row, rather than only an API acknowledgement.
- Restart the same validator and executor after settled work and verify retained results are reused. Preserve data, wallets and Docker evidence.
- Keep this first experiment's claims narrow: fixed greeting generation and random scoring prove plumbing, not useful AI development.

The three-minute pilot is recurring once dispatch is enabled. Restore normal timing later by setting `GENERATION_SECONDS=3600`, `CONFIRMATION_SECONDS=300`, `EVALUATION_SECONDS=3300`, `JUDGE_STOP_RESERVE_SECONDS=300` and `STOP_GRACE_SECONDS=60`, then recreating the validator. Existing round plans retain their frozen deadlines.

This is a proposed shared-chain deployment procedure, not a claimed successful deployment. Read-only RPC/host checks and the installed CLI argument definitions were verified during inspection. Docker builds, new wallet creation, transfers, registration, service installation, Pylon compatibility and the end-to-end run were intentionally not executed. Stop at a failing gate and retain its logs; current source and Refinery have not undergone a new acceptance run together.

## References

- [Notion project description](https://app.notion.com/p/Description-415b63b4ef1e8063ad96ed5813aa4e98): intended Refinery target and product scope.
- [Implementation handoff](implementation-handoff.md): prior isolated acceptance and candidate/source differences.
- [Installer](../installer/README.md): ownership, systemd executor, full installer and updater contracts.
- [Localnet guide](../localnet/README.md): isolated test-chain workflow; not an existing shared-chain bootstrap.
- [Common Compose](../envs/deployed/docker-compose.yml): Pylon, validator and monitoring configuration.
- [Miner guide](../miner/README.md): one-shot digest submission.
