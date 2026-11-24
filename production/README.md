# FactoryHorde on mainnet subnet 12

FactoryHorde runs as mechanism 1 of mainnet subnet 12. Mechanism 0 is ComputeHorde, and FactoryHorde reuses its validator and miner hotkeys, so both mechanisms share one commitment per hotkey (see [the commitment format](../miner/README.md#commitment-format) and [second mechanism beside ComputeHorde](../installer/README.md#second-mechanism-beside-computehorde)). Like [Refinery](../refinery/README.md), it is an ordinary deployment that points at another chain; this folder only holds its settings:

| File | Purpose |
| --- | --- |
| [`validator.env.example`](validator.env.example) | Validator settings for the chain, subnet, mechanism, round windows, task, inference proxy and metrics push |
| [`miner.env.example`](miner.env.example) | Settings for a miner using [the miner's Compose file](../miner/README.md#submitting-from-a-server) |

The deployment has one validator server (`ubuntu@172.104.155.7`) and one miner server (`ubuntu@139.162.142.199`), both reached through `jumpuser@jb.reef.pl`. They are set up like the Refinery servers: the non-root operator `ubuntu` (passwordless sudo, `docker` group) installs into `~/factory-horde`, without a Git checkout.

## Wallets

Nothing is registered or funded for this deployment. Each server gets only the existing hotkeys (and `coldkeypub.txt`) of the ComputeHorde nodes whose hotkeys it reuses, in `~/.bittensor/wallets`; coldkey secrets stay off the servers. The validator hotkey needs ComputeHorde's validator permit, which is subnet-wide.

## Validator

Follow [published operator installation](../installer/README.md#published-operator-installation) with `RELEASE_REF=deploy-config-production`, using [`validator.env.example`](validator.env.example) as the operator environment file. Fill in the wallet names, `VALIDATOR_HOTKEY`, `JUDGE_IMAGE`, `CLAUDE_CODE_OAUTH_TOKEN` and the metrics login. The validator image is the one the release pins. Rounds, task, inference proxy and monitoring are the same as on Refinery.

## Miners

The miner server runs the `oneshot` and `openhands` demo miners from [factory-horde-demo-miners](https://github.com/backend-developers-ltd/factory-horde-demo-miners) (its `server/` folder, with `MINERS='["oneshot", "openhands"]'`). Another miner uses [`miner.env.example`](miner.env.example) with `miner/docker-compose.yml` and schedules `docker compose run --rm -T submit`.

## Before the first commitment

- Every ComputeHorde miner whose hotkey commits a FactoryHorde envelope must run a ComputeHorde release with 512-byte commitments ([ComputeHorde#880](https://github.com/backend-developers-ltd/ComputeHorde/pull/880)) or newer. Releases before `ec106a6e` (ComputeHorde #878) remove `<F:…>` within 5 minutes, and the two writers then use up the hotkey's commitment quota; later releases without 512-byte commitments stop updating their manifest beside a full image reference.
- The submitter keeps `COMMITMENT_MAX_BYTES=200` from [`miner.env.example`](miner.env.example), so FactoryHorde and ComputeHorde together stay within the hotkey's 3,100-byte commitment quota per tempo.
- The validator and miners run the Pylon release with `BigRaw` support ([bittensor-pylon#107](https://github.com/bittensor-church/bittensor-pylon/pull/107)), since full image references make commitments longer than 128 bytes.
- The subnet already has two mechanisms. Mechanism 1 receives emission only after the subnet owner changes the emission split; until then its weights have no effect on rewards.
- Commit-reveal is enabled on the subnet; Pylon commits and reveals the validator's weights.
