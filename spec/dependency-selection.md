# Selected prototype dependencies

Verified on Linux amd64, 1 October 2026, with CPython 3.14.6, uv 0.12.21
and Docker 29.8.2. This is task 2's dependency gate, not chain-weight acceptance.

## Versions and provenance

| Component | Selected source | Evidence |
|---|---|---|
| Nexus | [`mechanism-id`, commit `e7f4cc1a261dabd52f9cb66bcdfb351cfed53b7b`](https://github.com/bittensor-church/bittensor-nexus-library/commit/e7f4cc1a261dabd52f9cb66bcdfb351cfed53b7b) | Explicit `rev` in validator pyproject and lock; installed `0.0.1.dev179+ge7f4cc1a2`, with matching `direct_url.json` commit |
| Pylon client | PyPI `bittensor-pylon-client==2.3.0`; [`client-v2.3.0`](https://github.com/backend-developers-ltd/bittensor-pylon/commit/ec320a82e2a2c2d4f74df56027343d83fcb062df) | Explicit validator dependency, lock hashes and installed version; required exactly by selected Nexus |
| Pylon service | Docker Hub `backenddevelopersltd/bittensor-pylon:2.3.3`; [`service-v2.3.3`](https://github.com/backend-developers-ltd/bittensor-pylon/commit/d1e881c6784df369af7f24184eadf0722c7b33c8) | Public digest pull, image inspection and comparison of mechanism route/service/task source |
| Subtensor localnet | GHCR `opentensor/subtensor-localnet:devnet-ready` resolved below | Public digest pull; running node reports `4.0.0-dev-6a4206fac7b`, runtime `node-subtensor` spec version `424` |

The selected Nexus branch includes the public settings/flow compatibility changes
and mechanism-aware beat/setter changes. The sibling Nexus checkout stays on
`6e7b1a05c8c401aca9dbc63ac4181df856b899a3` (master); it is **not** the import source.
The installed public exports, grounding document and both weight actor files were
byte-compared with the selected branch. The old lock's `e1f0b682...` / Pylon 2.1.0
combination is replaced. The sibling Pylon checkout at `d1e881c...` is source
reference only; the application installs the published client wheel.

Both application deployment and localnet select this Pylon OCI index digest:

```text
docker.io/backenddevelopersltd/bittensor-pylon@sha256:162a2c56b1183b1420d1c86dad135334380aeadb78e609ded4f52fd6d169efc5
```

Its amd64 manifest digest is
`sha256:c88099ad340b4a614887b9627908db47e6d1a9ebb49b4dc7f96eb70a7ef96b20`.
The published image currently supplies only `linux/amd64`; that is the selected
prototype platform. Image environment reports `PYLON_OTEL_SERVICE_VERSION=2.3.3`.
Python distribution metadata inside that image reports fallback version `0.0.0`,
so it is not used as release identity. The installed image's `_unstable/services.py`,
`tasks.py` and `routers.py` match the tagged sibling sources by SHA-256:

```text
services.py cd2f0f86acd28128b4240b1d17e7990510d7f2e305b9c7628534dc087b3bfd15
tasks.py    9e9ff0555459e44706a9290d7b7f14c7d14f627feec1ec39c3799e5da97226c8
routers.py  6a8297b138ae3db0049f7d4e5926bea783f1d2a8c92221ffbb672e000819d7f6
```

Localnet selects this Subtensor OCI index digest:

```text
ghcr.io/opentensor/subtensor-localnet@sha256:592aa28d528ebadba5f83807d0d38e29fa954dd91ac3e180b48259d64a654e8f
```

Its amd64 manifest is
`sha256:81646b70cb60a23dcdd47b99bd5daf2e504e47b40d4004882a17711ba7c139be`.
An isolated disposable container produced blocks; direct RPC returned the node
version above. Decoded live metadata exposes
`SubtensorModule.set_mechanism_weights(netuid, mecid, dests, weights, version_key)`.
Metadata and source checks establish available interfaces; actual mechanism-1
weights and mechanism-0 non-interference still require task 12's independent proof.

## API checks

`validator/tests/test_dependency_contract.py` imports only public Nexus/Pylon APIs
and checks:

- Two generic tasks with distinct names, one-attempt policies, a shared injected
  result store, `NoopRouter`, explicit tap wiring and custom producer/communicator
  actor construction.
- A communicator returning promptly, then producing correlated completion after
  the input context scope closes.
- Real Pylon 2.3.0 HTTP serialization for mechanisms 0 and 1. Both status GET and
  weights PUT target `/api/_unstable/identity/validator/subnet/2/mechanism/<id>/...`;
  weighing receives the same injected result store. HTTP is intercepted, so these
  are not chain writes.
- Existing scaffold settings/graph import with mechanism 1. The ping graph remains
  until task 10; weight nodes are wired into the application in task 12.

Ruff, basedpyright (zero errors/warnings) and all four checks passed. Additionally,
all six tests in the selected upstream `tests/test_weight_mechanism.py` passed
against FactoryHorde's installed dependencies. These include cached mechanism-0
status not suppressing mechanism 1. Tests and their helper were extracted to a
temporary directory; no upstream source was vendored into FactoryHorde.

Run the maintained checks from the validator project:

```sh
cd validator
env -u UV_EXCLUDE_NEWER uv sync --locked
env -u UV_EXCLUDE_NEWER uv run ruff check --fix
env -u UV_EXCLUDE_NEWER uv run ruff format
env -u UV_EXCLUDE_NEWER uv run basedpyright
env -u UV_EXCLUDE_NEWER uv run pytest -q --tb=line -r f
```

Removing a shell override preserves the project's **11-day** dependency-age rule;
it does not disable that rule. Miner retains its independent one-week constraint
and lock. Both project-local environments synchronize successfully. The submitter
will adopt the selected Pylon client in task 6.

Commitment publication/discovery will use explicit `v1` APIs and exact byte/hex
conversion plus read-back. Mechanism status/writes use the selected `_unstable`
routes. In the selected service, commitments are awaited; weights are scheduled.
Read-back, chain payload constraints and identity authorization remain task 6.
The original validator placeholder is replaced by the [task-16 candidate](../envs/candidate/release.json);
no operator promotion or public-chain operation was performed by this gate.
