# FactoryHorde submitter

The `miner` command publishes a public GHCR manifest reference as this miner's FactoryHorde commitment envelope through its configured Pylon identity and exits after read-back. It runs no HTTP server, inference, factory job or wallet registration. Pylon owns signing credentials.

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
| `BITTENSOR_ARCHIVE_NETWORK` | Optional node for historical reads, default `BITTENSOR_NETWORK`; `archive` on mainnet |
| `BITTENSOR_WALLET_NAME`, `BITTENSOR_WALLET_HOTKEY_NAME`, `HOST_WALLET_DIR` | Registered miner wallet, mounted read-only into Pylon |
| `MINER_PYLON_TOKEN`, `PYLON_OPEN_ACCESS_TOKEN`, `PYLON_METRICS_TOKEN` | Three distinct secrets, e.g. `openssl rand -hex 24` |
| `FACTORY_IMAGE` | Default image to commit: any public `ghcr.io/OWNER/PACKAGE@sha256:…` reference |
| `COMMITMENT_MAX_BYTES` | Optional cap on the whole commitment, at most the chain's 512 bytes (default); use 200 on a hotkey shared with ComputeHorde |
| `PYLON_HOST_PORT` | Optional override (8000) |

For an operator installation, resolve the reviewed configuration branch once, download the Compose file and verify it against that release's manifest. Use `deploy-config-production`, for Refinery too. Repeat this sequence for upgrades; retain `.env` and the Pylon volume.

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

The chain has no compare-and-set for commitments, so another writer of the same hotkey's commitment (ComputeHorde's miner) can drop the envelope from a stale read after the submitter has confirmed it. Schedule the submitter, for example with cron; a run that finds its envelope in place reports `unchanged` and writes nothing:

```cron
*/5 * * * * cd $HOME/factory-horde/miner && docker compose run --rm -T submit >> submit.log 2>&1
```

Commitment writes of one hotkey share one quota per tempo across both mechanisms (see [the commitment format](#commitment-format)). A ComputeHorde miner on the same hotkey must therefore run a release that accepts 512-byte commitments ([ComputeHorde#880](https://github.com/backend-developers-ltd/ComputeHorde/pull/880) or newer) before the submitter commits a full reference, and the submitter there needs `COMMITMENT_MAX_BYTES=200`. An older ComputeHorde release either erases `<F:…>` every 5 minutes, so the repeated repairs exhaust the quota, or stops updating its manifest beside a commitment over 128 bytes. [Refinery](../refinery/README.md) supplies a filled-in example of these settings.

## Commitment format

A hotkey has one commitment per subnet, shared by all mechanisms. It is a sequence of tagged envelopes: ComputeHorde owns `<M:…>` (its manifest), legacy bt-ddos-shield values use `<D:…>`, and FactoryHorde owns `<F:ghcr.io/OWNER/PACKAGE@digest>`. The digest is the image's SHA-256 manifest digest in unpadded base64url (43 characters); the validator rebuilds `ghcr.io/OWNER/PACKAGE@sha256:<hex>` from the envelope. Any GHCR owner works, and `PACKAGE` may contain further `/` path segments. Envelopes from older submitters carry only `PACKAGE`; a validator reads them only while its transitional `SUBMISSION_NAMESPACE` is set, so resubmit with the current submitter.

FactoryHorde rewrites only `<F:…>` and keeps every other byte, including `<D:…>`, `<M:…>` and unrecognized data; a new envelope goes before the first `<M:…>`, else at the end. ComputeHorde rewrites only `<M:…>` and keeps the other envelopes. If the current commitment cannot be decoded, the submitter refuses to write.

The chain accepts commitments of up to 512 bytes. Pylon writes values of at most 128 bytes as `RawN` and longer ones as `BigRaw`, so a commitment over 128 bytes needs the Pylon release with `BigRaw` support ([bittensor-pylon#107](https://github.com/bittensor-church/bittensor-pylon/pull/107)). The submitter's effective limit is 512 bytes, or `COMMITMENT_MAX_BYTES` (`--commitment-max-bytes`) when set:

| Part | Bytes |
|---|---|
| ComputeHorde manifest reserve: `<M:` + six classes as `X999`, joined by `;` + `>` | 33 |
| FactoryHorde envelope: `<F:ghcr.io/` + OWNER + `/` + PACKAGE + `@` + 43-character base64url SHA-256 + `>` | 57 + OWNER + PACKAGE |
| FactoryHorde's own factory, `ghcr.io/backend-developers-ltd/factory-horde-factory` | 100 |
| Room for OWNER + PACKAGE under a 200-byte cap | 110 |

The submitter keeps ComputeHorde's 33-byte reserve within the effective limit: it refuses to write if every byte except `<M:…>` plus 33 would exceed the limit, or if the merged commitment itself would. A reference whose envelope can never fit beside the reserve is rejected before any request. The reserve assumes at most 999 executors per ComputeHorde class.

Each commitment write is charged its actual size, at least 100 bytes, against the hotkey's quota of 3,100 bytes per tempo (360 blocks on subnet 12, about 72 minutes), shared by every writer of that hotkey's commitment. ComputeHorde writes when its manifest changes (checked every 5 minutes) or every 5,000 blocks, at most about 14 times per tempo; a 200-byte commitment allows 15 writes per tempo, so the cap keeps both writers within the quota.

Current source selects Pylon client and service 2.4.0. The submitter uses the explicit v1 commitment API and writes bytes, not strings interpreted as hexadecimal. Only complete lowercase GHCR SHA-256 manifest references are accepted; tags, unsupported registries and overlong references fail. No value is truncated or replaced with a tag.

The submitter reads before writing. An unchanged envelope exits with `unchanged` and writes nothing. Writes have no automatic HTTP retries. After an ambiguous timeout or upstream error, it polls read-back without submitting again; it reports `recovered` only when its own envelope is confirmed for a registered hotkey at the returned block. Read-back compares only the `<F:…>` envelope, so a concurrent ComputeHorde manifest change does not prevent confirmation. Unconfirmed outcomes exit nonzero. A later invocation also reads first. JSON output contains the hotkey, image, commitment/observation blocks and elapsed time; the commitment block is the last write by either mechanism. Tokens never appear in successful output.

The validator reads all commitments, then membership at that exact block/hash. Its immutable snapshot includes miner hotkeys, UIDs, references and reserved job IDs. It excludes its own identity, commitments over 512 bytes or without exactly one well-formed `<F:…>` envelope, legacy bare references, envelopes without `ghcr.io/OWNER` while no transitional namespace is set, and unregistered hotkeys; an optional allowlist limits fixtures. Validator permits alone do not disqualify miners. Recovery reads the saved snapshot before making new chain queries.

The independent `bootstrap` dependency group pins the SDK used by localnet tools:
`uv run --project miner --group bootstrap python localnet/bootstrap.py` from the
repository root. Those dependencies are absent from the submitter image. The
[factory](factory/README.md) is built separately and contains no submission tooling.
