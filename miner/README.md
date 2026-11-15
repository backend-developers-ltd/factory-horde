# FactoryHorde submitter

The `miner` command publishes a public GHCR manifest reference through its configured
Pylon identity and exits after exact read-back. It runs no HTTP server, inference,
factory job or wallet registration. Pylon owns signing credentials.

```sh
localnet/build-submitter.sh
# Configure PYLON_IDENTITY_TOKEN in your shell without placing it in command history.
docker run --rm --read-only --tmpfs /tmp --cap-drop ALL \
  --security-opt no-new-privileges --network factory-horde-localnet_default \
  --env PYLON_IDENTITY_TOKEN \
  --env PYLON_ADDRESS=http://pylon:8000 --env PYLON_IDENTITY=miner1 --env NETUID=2 \
  "$(cat localnet/state/submitter-image.id)" \
  'ghcr.io/backend-developers-ltd/factory-horde-factory@sha256:8e38b2081f7628ca9dab545fb00ee756e23c1ed5d4d292db0e41c1012acbc8a3'
```

Run this example from the repository root after localnet startup, using miner1's
configured token. The submitter needs network access to Pylon, but no mounted wallet,
Docker socket, callback endpoint or registry credential. See
[localnet's automated five-identity check](../localnet/README.md#public-image-submissions-and-discovery)
for a complete token/configuration fixture. Python development uses `uv sync` inside
`miner/`; `uv run miner --help` describes configuration.

## Submitting from a server

[`docker-compose.yml`](docker-compose.yml) runs a Pylon with one `miner` identity on
loopback and offers the published submitter as a one-shot service. It reads `.env`
beside it (never committed):

| Setting | Meaning |
| --- | --- |
| `ENVIRONMENT`, `BITTENSOR_NETWORK`, `NETUID`, `BLOCK_DURATION_SECONDS` | Chain and subnet |
| `BITTENSOR_WALLET_NAME`, `BITTENSOR_WALLET_HOTKEY_NAME`, `HOST_WALLET_DIR` | Registered miner wallet, mounted read-only into Pylon |
| `MINER_PYLON_TOKEN`, `PYLON_OPEN_ACCESS_TOKEN`, `PYLON_METRICS_TOKEN` | Three distinct secrets, e.g. `openssl rand -hex 24` |
| `FACTORY_IMAGE` | Default image to commit |
| `PYLON_HOST_PORT`, `SUBMITTER_IMAGE` | Optional overrides (8000, the published submitter) |

For an operator installation, resolve the reviewed configuration branch once, download the Compose file and verify it against that release's manifest. Use `deploy-config-refinery` for the internal chain. Repeat this sequence for upgrades; retain `.env` and the Pylon volume.

```sh
mkdir -p ~/factory-horde/miner
cd ~/factory-horde/miner
RELEASE_REF=deploy-config-production
RELEASE_SHA=$(git ls-remote https://github.com/backend-developers-ltd/factory-horde.git "refs/heads/$RELEASE_REF" | cut -f1)
test "${#RELEASE_SHA}" -eq 40
RELEASE_URL="https://raw.githubusercontent.com/backend-developers-ltd/factory-horde/$RELEASE_SHA"
curl -fsSL "$RELEASE_URL/installer/release-manifest.json" -o release-manifest.json
curl -fsSL "$RELEASE_URL/miner/docker-compose.yml" -o docker-compose.yml.next
printf '%s  %s\n' "$(jq -r '.files["miner/docker-compose.yml"]' release-manifest.json)" docker-compose.yml.next | sha256sum --check -
# Prepare .env on first installation before validating the configuration.
docker compose -f docker-compose.yml.next config --quiet
mv docker-compose.yml.next docker-compose.yml
```

```sh
docker compose up -d --wait             # Pylon
docker compose run --rm submit          # commit FACTORY_IMAGE
docker compose run --rm submit IMAGE    # commit another image once
```

The commitment stays on chain, so nothing needs to keep submitting; Pylon is only
needed while a submission runs. [Refinery](../refinery/README.md) supplies a filled-in
example of these settings.

Current source selects Pylon client 2.3.1 and service 2.3.4. The submitter uses the
explicit v1 commitment API. References are encoded as UTF-8 bytes, not supplied as
strings interpreted as hexadecimal.
Only complete lowercase GHCR SHA-256 manifest references are accepted. The selected
writer sends `RawN`, so references must fit 128 bytes; the baseline is 124 bytes.
Tags, malformed hex/UTF-8, unsupported registries and overlong references fail.
The chain also supports larger data variants, but this selected writer does not
use them. No value is truncated or replaced with a tag.

The submitter reads before writing. An unchanged reference exits with `unchanged`
and retains its original commitment block. Writes have no automatic HTTP retries.
After an ambiguous timeout or upstream error, it polls read-back without submitting
again; it reports `recovered` only when the exact value is confirmed for a registered
hotkey at the returned block. Unconfirmed outcomes exit nonzero. A later invocation
also reads first. JSON output contains the hotkey, image, commitment/observation
blocks and elapsed time; tokens never appear in successful output.

The validator reads all commitments, then membership at that exact block/hash.
Its immutable snapshot includes miner hotkeys, UIDs, references and reserved job
IDs. It excludes its own identity, malformed references and unregistered hotkeys;
an optional allowlist limits fixtures. Validator permits alone do not disqualify
miners. Recovery reads the saved snapshot before making new chain queries.

The independent `bootstrap` dependency group pins the SDK used by localnet tools:
`uv run --project miner --group bootstrap python localnet/bootstrap.py` from the
repository root. Those dependencies are absent from the submitter image. The
[factory](factory/README.md) is built separately and contains no submission tooling.
