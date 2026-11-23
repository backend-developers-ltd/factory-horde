# FactoryHorde as a Second Mechanism beside ComputeHorde — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let FactoryHorde run as mechanism 1 on ComputeHorde's subnet with the same miner and validator hotkeys, so both mechanisms share one commitment per hotkey and each only ever rewrites its own part.

**Architecture:** The commitment becomes a sequence of tagged envelopes, extending the format ComputeHorde already uses (`<M:…>` manifest, legacy `<D:…>` shield). FactoryHorde owns `<F:package@digest>`, where the digest is base64url and the registry/owner namespace (for example `ghcr.io/backend-developers-ltd`) comes from configuration. FactoryHorde's submitter replaces only its own envelope and keeps every other byte. ComputeHorde's miner keeps every envelope it does not own (merged in ComputeHorde #878), so its rebuild also keeps `<F:…>`. ComputeHorde's validator already ignores anything outside `<M:…>`. The chain has no compare-and-set for commitments, so either writer can overwrite the other's newest change from a stale read; both writers therefore run periodically, and each run repairs its own part.

Pylon's neuron reads also become mechanism-aware. Subtensor stores `Incentive` and `LastUpdate` per mechanism, but Pylon builds neurons from the `get_neurons_lite` runtime call, which only reads mechanism 0. An optional `mechanism_id` query parameter (default 0) overlays the requested mechanism's values. FactoryHorde's monitoring then requests `MECHANISM_ID`, so its incentive and last-update gauges stop showing ComputeHorde's values.

**Tech Stack:** Python 3.14, uv, pytest, Pylon client 2.3.x → next minor, Litestar (Pylon service), Nexus (FactoryHorde validator); Django/Celery, nox (ComputeHorde miner and Pylon).

**Execution order:** The Pylon change is independent of everything else and comes first (Tasks 1–2). Task 3 is a stop: the person running the plan merges the Pylon pull request and publishes the service and client releases. Only then do the ComputeHorde change (Task 4) and the FactoryHorde changes (Tasks 5–10, one branch and one pull request) begin, so FactoryHorde pins the released Pylon from the start.

---

## Background and decisions

**Why the envelope format.** Commitments are stored per (netuid, hotkey). Mechanisms have no separate slot. ComputeHorde's miner (`miner/app/src/compute_horde_miner/miner/manifest_commitment.py:43`) rebuilds the commitment every 5 minutes, keeps every other single-capital-letter envelope (`<[A-LN-Z]:…>`, since #878; before it only `<D:…>`) and appends a fresh `<M:…>`. ComputeHorde's validator (`compute_horde/compute_horde/manifest_utils.py`, `extract_manifest_payload`) searches for the first `<M:…>` anywhere and ignores the rest.

**Size budget.** Pylon writes a single `Raw` field, so the whole commitment is limited to 128 bytes. ComputeHorde's writer enforces the same limit.

| Part | Bytes |
|---|---|
| ComputeHorde manifest reserve: `<M:` + six classes as `X999`, joined by `;` + `>` | 33 |
| FactoryHorde envelope: `<F:` + package + `@` + 43-character base64url SHA-256 + `>` | 48 + package |
| `factory-horde-deceptive` (longest current baseline package, 23 bytes) | 71 |
| Remaining room for a legacy `<D:…>` shield envelope (worst current package) | 24 |

A hex digest would cost 21 more bytes, leaving almost no room for anything else, so FactoryHorde commits base64url. The current bt-ddos-shield no longer writes commitments (its manifest moved to `http://{axon}/shield_manifest.json`), but old `<D:…>` values may still exist on chain, and ComputeHorde keeps them.

The FactoryHorde writer guarantees ComputeHorde's room before writing. It refuses to write if every byte except `<M:…>` plus the 33-byte reserve would exceed 128, or if the merged value itself would (an existing manifest larger than the reserve). An envelope that can never fit beside the reserve (a package name over 47 bytes) is rejected before any request. ComputeHorde never writes more than 128 bytes and refuses its own commit if it would. The reserve assumes at most 999 executors per class; that limitation is documented in the miner README.

**Ownership rules.**

- FactoryHorde rewrites only `<F:…>` envelopes. It keeps every other byte unchanged, including `<D:…>`, `<M:…>` and unrecognized data. A new envelope goes immediately before the first `<M:…>` (else at the end). That matches ComputeHorde's canonical order (preserved envelopes, then the manifest), so ComputeHorde sees no change and does no extra rewrite. If FactoryHorde cannot decode the current commitment, it refuses to write rather than risk destroying the other mechanism's data.
- ComputeHorde rewrites only `<M:…>`. It keeps every other tagged envelope, including `<D:…>` and `<F:…>`, and still discards non-envelope garbage, which is its existing cleanup behaviour.

**Concurrent writes.** Both writers read, modify, then write, and the chain offers no compare-and-set. The commitments pallet on current subtensor (`pallets/commitments/src/lib.rs`, `set_commitment`, checked on `main`) has no per-hotkey block interval. It charges each write `max(100, length)` bytes against a quota of 3100 bytes per hotkey per tempo (`MaxSpace`), about 31 writes, shared by both mechanisms. Two writes seconds apart therefore both land, and the later one wins. A write computed from a stale read can undo the other writer's newest change:

- A ComputeHorde write from a stale read drops a just-written `<F:…>`. Nothing restores it until the submitter runs again, and the submitter has already reported success.
- A FactoryHorde write from a stale read restores an outdated `<M:…>`. ComputeHorde's next 5-minute run sees the difference and rewrites it.

The window is the few blocks between one writer's read and the inclusion of its write, and ComputeHorde writes only when its manifest changes or every 5000 blocks, so collisions are rare. The repair is the same in both directions: each writer runs periodically, reads first and writes only when its own part is missing or different. ComputeHorde's miner already does this every 5 minutes. FactoryHorde's submitter is one-shot, so miners must schedule it (cron). A run that finds its envelope in place reports `unchanged` and writes nothing, so scheduling it costs no quota. Task 9 documents this for miners and changes the demo miners' `sync.sh`, which today skips the submitter unless the image changed, to call it on every run. The design does not depend on the interval being absent: on a runtime that still rate-limits commitment writes, the later write fails instead and the next run retries it.

The quota is a second reason for the rollout order below. An unpatched ComputeHorde miner erases `<F:…>` every 5 minutes and a scheduled submitter restores it. That is two writes per cycle, about 29 per 72-minute tempo, which is at the quota and can block that hotkey's manifest updates.

**Namespace configuration.** All submissions must live under one configured namespace: `VALIDATOR_SUBMISSION_NAMESPACE` on the validator (Compose variable `SUBMISSION_NAMESPACE`) and `SUBMISSION_NAMESPACE` on the submitter. Images outside it are rejected. This restricts miners to packages the namespace owner publishes, which the user accepted.

**Rollout order.**

1. Pylon is merged and released first (Task 3). The change is additive and affects nobody until a client asks for a non-zero mechanism.
2. ComputeHorde's miner change is released before any FactoryHorde envelope is written; otherwise it erases `<F:…>` within 5 minutes.
3. `SUBMISSION_NAMESPACE` is added to the `.env` of every validator installation and miner before the FactoryHorde release reaches them. A validator with dispatch enabled refuses to start without it, and an installation that follows a configuration branch through the cron updater (Refinery) applies a release without rollback.
4. The FactoryHorde validator and submitter follow together. Legacy bare-reference commitments are not accepted, so a miner is left out of rounds until its submitter has run once in the new format. On Refinery that is the demo miners' next 5-minute `sync.sh` run after the miner host has the new `sync.sh` and submitter image (Task 9). With the old `sync.sh` they would never re-commit, because it skips the submitter while the image is unchanged.
5. Every FactoryHorde miner schedules the submitter (see "Concurrent writes").

**Per-mechanism neuron reads in Pylon.** Verified in subtensor (`pallets/subtensor/src/lib.rs`, commit `541a50d`):
- `Incentive` and `LastUpdate` are keyed by `NetUidStorageIndex = mechanism_id * 4096 + netuid`.
- `MechanismCountCurrent(netuid)` holds the number of mechanisms.
- `NeuronInfoLite` (`rpc_info/neuron_info.rs:90-94`) reads both values at `NetUidStorageIndex::from(netuid)`, which is mechanism 0.
- The chain's getters default a missing UID entry to 0.

`Consensus`, `Dividends`, `Emission` and the other neuron fields remain subnet-wide, so the response keeps them unchanged. `ConsensusByMechanism` exists, but nobody needs it yet, so it is left out (YAGNI).

Backward compatibility:
- **Requests:** without `mechanism_id`, every request takes exactly today's code path. A new client sends the query parameter only when it is non-zero.
- **Responses:** they gain a `mechanism_id` field. Pydantic ignores unknown fields by default, so old clients keep working.
- **Guard against old services:** a new client checks that the field matches the request. An old service ignores the unknown query parameter and would otherwise silently return mechanism 0 values.

Pylon reads the two storage maps through turbobt's generic `client.subtensor.state.getStorage`, which is the same call turbobt's own storage accessors use, so no turbobt release is needed. Recent (cached) neurons and validators stay mechanism 0; this is documented.

**Pylon client version for FactoryHorde.** Nexus (`bittensor-nexus-library` at `d54e268`) pins `bittensor-pylon-client==2.3.1` exactly. The FactoryHorde validator therefore adopts the new client through a `[tool.uv] override-dependencies` entry until Nexus raises its pin. This is safe because the change is additive.

**Out of scope.** Actual deployment to the user's subnet, setting the mechanism count or emission split, any turbobt change and the Nexus pin bump. Pylon releases are published by the person running the plan in Task 3, never by an agent. Per `AGENTS.md`, no active operator configuration is promoted by this plan.

## File structure

Listed in execution order.

bittensor-pylon (branch `mechanism-neuron-reads`, from `master`; paths relative to `/home/jan/Projects/reef/bittensor-pylon`):

- Modify `pylon_commons/pylon_commons/models.py`: `SubnetNeurons.mechanism_id`.
- Modify `pylon_commons/pylon_commons/exceptions.py`: `PylonMechanismMismatch`; export it from `pylon_client/pylon_client/artanis/__init__.py` and `pylon_client/pylon_client/v1/__init__.py`.
- Modify `pylon_commons/pylon_commons/_unstable/requests.py`: `mechanism_id` on `GetNeuronsRequest` and `GetLatestNeuronsRequest`.
- Modify `pylon_service/pylon_service/bittensor/contact.py`: `MechanismMetrics`, `get_mechanism_metrics` on the port, abstract client and turbobt contact.
- Modify `pylon_service/pylon_service/bittensor/contact_router.py`, `pylon_service/pylon_service/bittensor/mock_contact.py`.
- Modify `pylon_service/pylon_service/api/services.py` (`MechanismNotFoundError`), `pylon_service/pylon_service/api/utils.py` (404 mapping), `pylon_service/pylon_service/api/_unstable/services.py` (`NeuronService`), `pylon_service/pylon_service/api/_unstable/api.py` (query parameter).
- Modify `pylon_client/pylon_client/_internal/api/_unstable/translators.py`, `abstract_sync.py`, `abstract_async.py`, `_unstable/sync/api.py`, `_unstable/asynchronous/api.py`.
- Tests: `pylon_service/tests/unit/_unstable_identity_endpoints/test_get_neurons_mechanism.py` (new), `pylon_service/tests/unit/open_access_endpoints/test_get_neurons_mechanism.py` (new), `pylon_service/tests/integration/contact/test_reads.py`, `pylon_service/tests/pact/state_handlers.py`, `pylon_client/tests/unit/{synchronous,asynchronous}/identity/test_get_latest_neurons.py`, `pylon_client/tests/pact/{synchronous,asynchronous}/identity/test_get_latest_neurons.py`, `pylon_client/tests/pact/matchers.py`, the regenerated pact JSON files and snapshot files.
- Docs: `CLAUDE.md` (Neuron Data endpoints), `docs/SERVICE.md`, `docs/CLIENT.md`.

ComputeHorde (branch `preserve-factory-horde-commitment`, merged as #878):

- Modify `miner/app/src/compute_horde_miner/miner/manifest_commitment.py:42-44`.
- Modify `miner/app/src/compute_horde_miner/miner/tests/test_manifest_commitment.py`.
- Modify `validator/app/src/compute_horde_validator/validator/allowance/tests/test_manifests.py` (test only).

FactoryHorde (branch `second-mechanism-commitments`, from `origin/master`, in the worktree `../factory-horde-second-mechanism`):

- Create `spec/fixtures/commitment-v2/envelopes.json`: shared cross-mechanism examples.
- Modify `miner/miner.py`: envelope encoding, byte-preserving merge, namespace option.
- Modify `miner/tests/test_submission.py`.
- Modify `validator/src/validator/discovery.py`: envelope decoding with the namespace.
- Modify `validator/src/validator/main.py`: `submission_namespace` setting, required for dispatch; `settings.mechanism_id` passed to the chain view.
- Modify `validator/tests/test_discovery.py`, `validator/tests/test_dependency_contract.py`, `validator/tests/test_chain_observer.py` (the `Settings` dispatch test).
- Modify `validator/pyproject.toml`, `validator/uv.lock`, `validator/src/validator/monitoring.py:74-96`, `validator/tests/test_monitoring.py`: chain view for the configured mechanism on the released Pylon client.
- Modify `envs/deployed/docker-compose.yml` and `miner/docker-compose.yml` (namespace variable, Pylon service digest); regenerate `installer/release-manifest.json`.
- Modify `refinery/validator.env.example`, `refinery/miner.env.example`, `localnet/.env.example`, `envs/candidate/localnet.env`.
- Modify docs: `miner/README.md`, `validator/README.md`, `installer/README.md`, `refinery/README.md`, `localnet/README.md`, `AGENTS.md`, `subnet_design.md`, `docs/implementation-handoff.md`.
- Modify `localnet/check_submissions.py`, `localnet/check_tasks.py`, `localnet/check_rounds.py`, `localnet/check_adversarial.py`, `localnet/check_behavioral.py`.

factory-horde-demo-miners (branch `submission-namespace`, from `origin/master`):

- Modify `server/docker-compose.yml`: pass `SUBMISSION_NAMESPACE`, derived from the existing `IMAGE_NAMESPACE`.
- Modify `server/sync.sh`: call the submitter on every run.
- Modify `server/README.md`.

---

### Task 1: Pylon service returns per-mechanism incentive and last update

**Goal:** Pylon's neuron endpoints (open access and identity, latest and at-block, `_unstable` and `v1`) accept an optional `mechanism_id` query parameter, default 0, and return that mechanism's `incentive` and `last_update`.

**Files:** (all paths in Tasks 1–2 are relative to `/home/jan/Projects/reef/bittensor-pylon`)
- Modify: `pylon_commons/pylon_commons/models.py:135` (`SubnetNeurons`)
- Modify: `pylon_service/pylon_service/bittensor/contact.py` (port protocol near line 113, abstract client near line 211, `TurboBtContact` after `get_neurons` at line 467)
- Modify: `pylon_service/pylon_service/bittensor/contact_router.py:256`, `pylon_service/pylon_service/bittensor/mock_contact.py:222`
- Modify: `pylon_service/pylon_service/api/services.py`, `pylon_service/pylon_service/api/utils.py:26`
- Modify: `pylon_service/pylon_service/api/_unstable/services.py:98-108`, `pylon_service/pylon_service/api/_unstable/api.py:115-125`
- Test: `pylon_service/tests/unit/_unstable_identity_endpoints/test_get_neurons_mechanism.py`, `pylon_service/tests/unit/open_access_endpoints/test_get_neurons_mechanism.py`, `pylon_service/tests/integration/contact/test_reads.py`
- Docs: `CLAUDE.md` (Neuron Data section), `docs/SERVICE.md`

**Acceptance Criteria:**
- [ ] `GET …/block/latest/neurons` without `mechanism_id` calls only `get_latest_block` and `get_neurons` on the contact (no `get_mechanism_metrics` call) and returns today's body plus `"mechanism_id": 0`.
- [ ] `GET …/block/latest/neurons?mechanism_id=1` calls `get_mechanism_metrics(netuid, 1, block)` once and returns each neuron with `incentive = parts / 65535` and `last_update` from mechanism 1, other fields unchanged, and `"mechanism_id": 1`.
- [ ] A mechanism the subnet doesn't have (contact returns `None`) gives HTTP 404 `{"status_code": 404, "detail": "Mechanism 5 not found on subnet 1."}`.
- [ ] The at-block endpoint (`/block/123/neurons?mechanism_id=1`) behaves the same, for both `_unstable` and `v1` paths.
- [ ] On the integration local chain, after `set_weights` on mechanism 1, `get_mechanism_metrics(netuid, 1, block).last_update(alice_uid)` is greater than the block number read before the write, and `get_mechanism_metrics(netuid, 0, block)` matches `get_neurons` for every UID. For mechanism 2 on a two-mechanism subnet it returns `None`.
- [ ] Existing service unit tests pass after updating snapshots only for the added `mechanism_id` field.

**Verify:** `cd /home/jan/Projects/reef/bittensor-pylon/pylon_service && nox -s test` → all pass. Then `nox -s test-integration-contact -- -k mechanism_metrics` → the new integration test passes (needs Docker for the local chain). Both are required; the integration test is the only check of the real storage read.

**Steps:**

- [ ] **Step 1: Branch.** `cd /home/jan/Projects/reef/bittensor-pylon && git status --short` (it has unrelated untracked files; leave them). Then `git checkout master && git pull && git checkout -b mechanism-neuron-reads`.

- [ ] **Step 2: Write the failing endpoint tests.** Create `pylon_service/tests/unit/_unstable_identity_endpoints/test_get_neurons_mechanism.py`:

```python
"""
Tests for the optional mechanism_id query parameter of the identity neuron endpoints.
"""

import pytest
from litestar.status_codes import HTTP_200_OK, HTTP_404_NOT_FOUND
from pylon_commons.models import SubnetNeurons
from pylon_commons.types import Incentive, Timestamp

from pylon_service.bittensor.contact import MechanismMetrics
from tests.factories import BlockFactory, NeuronFactory


def _neurons(block):
    neuron = NeuronFactory.build(uid=0, incentive=Incentive(0.1), last_update=Timestamp(10))
    return neuron, SubnetNeurons(block=block, neurons={neuron.hotkey: neuron})


@pytest.mark.asyncio
@pytest.mark.parametrize("api", ["_unstable", "v1"])
async def test_latest_neurons_default_mechanism_is_unchanged(api, mock_bt_client_factory, identity_test_client_factory):
    block = BlockFactory.build()
    neuron, subnet_neurons = _neurons(block)
    async with mock_bt_client_factory("sn1") as mock_client:
        async with mock_client.mock_behavior(get_latest_block=[block], get_neurons=[subnet_neurons]):
            async with identity_test_client_factory("sn1") as client:
                response = await client.get(f"/api/{api}/identity/sn1/subnet/1/block/latest/neurons")
        assert "get_mechanism_metrics" not in mock_client.calls

    assert response.status_code == HTTP_200_OK, response.content
    assert response.json() == subnet_neurons.model_dump(mode="json")


@pytest.mark.asyncio
@pytest.mark.parametrize("api", ["_unstable", "v1"])
async def test_latest_neurons_for_mechanism_overlays_mechanism_values(
    api, mock_bt_client_factory, identity_test_client_factory
):
    block = BlockFactory.build()
    neuron, subnet_neurons = _neurons(block)
    metrics = MechanismMetrics(incentives=[32768], last_updates=[42])
    async with mock_bt_client_factory("sn1") as mock_client:
        async with mock_client.mock_behavior(
            get_latest_block=[block], get_neurons=[subnet_neurons], get_mechanism_metrics=[metrics]
        ):
            async with identity_test_client_factory("sn1") as client:
                response = await client.get(
                    f"/api/{api}/identity/sn1/subnet/1/block/latest/neurons", params={"mechanism_id": 1}
                )
        assert mock_client.calls["get_mechanism_metrics"] == [(1, 1, block)]

    expected = SubnetNeurons(
        block=block,
        mechanism_id=1,
        neurons={
            neuron.hotkey: neuron.model_copy(
                update={"incentive": Incentive(32768 / 65535), "last_update": Timestamp(42)}
            )
        },
    )
    assert response.status_code == HTTP_200_OK, response.content
    assert response.json() == expected.model_dump(mode="json")


@pytest.mark.asyncio
async def test_neurons_at_block_for_mechanism(mock_bt_client_factory, identity_test_client_factory):
    block = BlockFactory.build(number=123)
    _, subnet_neurons = _neurons(block)
    metrics = MechanismMetrics(incentives=[], last_updates=[])
    async with mock_bt_client_factory("sn1") as mock_client:
        async with mock_client.mock_behavior(
            get_block=[block], get_neurons=[subnet_neurons], get_mechanism_metrics=[metrics]
        ):
            async with identity_test_client_factory("sn1") as client:
                response = await client.get(
                    "/api/_unstable/identity/sn1/subnet/1/block/123/neurons", params={"mechanism_id": 1}
                )
        assert mock_client.calls["get_mechanism_metrics"] == [(1, 1, block)]

    assert response.status_code == HTTP_200_OK, response.content
    neuron = next(iter(response.json()["neurons"].values()))
    assert (neuron["incentive"], neuron["last_update"], response.json()["mechanism_id"]) == (0.0, 0, 1)


@pytest.mark.asyncio
async def test_unknown_mechanism_returns_404(mock_bt_client_factory, identity_test_client_factory):
    block = BlockFactory.build()
    _, subnet_neurons = _neurons(block)
    async with mock_bt_client_factory("sn1") as mock_client:
        async with mock_client.mock_behavior(
            get_latest_block=[block], get_neurons=[subnet_neurons], get_mechanism_metrics=[None]
        ):
            async with identity_test_client_factory("sn1") as client:
                response = await client.get(
                    "/api/_unstable/identity/sn1/subnet/1/block/latest/neurons", params={"mechanism_id": 5}
                )

    assert response.status_code == HTTP_404_NOT_FOUND, response.content
    assert response.json() == {"status_code": 404, "detail": "Mechanism 5 not found on subnet 1."}
```

Create the open-access twin `pylon_service/tests/unit/open_access_endpoints/test_get_neurons_mechanism.py`. Open-access tests use the `open_access_test_client` fixture and `mock_bt_client_factory()` without an identity:

```python
"""
Tests for the optional mechanism_id query parameter of the open-access neuron endpoints.
"""

import pytest
from litestar.status_codes import HTTP_200_OK
from litestar.testing import AsyncTestClient
from pylon_commons.models import SubnetNeurons
from pylon_commons.types import Incentive, Timestamp

from pylon_service.bittensor.contact import MechanismMetrics
from tests.factories import BlockFactory, NeuronFactory


@pytest.mark.asyncio
async def test_open_access_latest_neurons_default_mechanism_is_unchanged(
    open_access_test_client: AsyncTestClient, mock_bt_client_factory
):
    block = BlockFactory.build()
    neuron = NeuronFactory.build(uid=0)
    subnet_neurons = SubnetNeurons(block=block, neurons={neuron.hotkey: neuron})
    async with mock_bt_client_factory() as mock_client:
        async with mock_client.mock_behavior(get_latest_block=[block], get_neurons=[subnet_neurons]):
            response = await open_access_test_client.get("/api/v1/subnet/1/block/latest/neurons")
        assert "get_mechanism_metrics" not in mock_client.calls

    assert response.status_code == HTTP_200_OK, response.content
    assert response.json() == subnet_neurons.model_dump(mode="json")


@pytest.mark.asyncio
async def test_open_access_latest_neurons_for_mechanism_overlays_mechanism_values(
    open_access_test_client: AsyncTestClient, mock_bt_client_factory
):
    block = BlockFactory.build()
    neuron = NeuronFactory.build(uid=0, incentive=Incentive(0.1), last_update=Timestamp(10))
    subnet_neurons = SubnetNeurons(block=block, neurons={neuron.hotkey: neuron})
    metrics = MechanismMetrics(incentives=[32768], last_updates=[42])
    async with mock_bt_client_factory() as mock_client:
        async with mock_client.mock_behavior(
            get_latest_block=[block], get_neurons=[subnet_neurons], get_mechanism_metrics=[metrics]
        ):
            response = await open_access_test_client.get(
                "/api/v1/subnet/1/block/latest/neurons", params={"mechanism_id": 1}
            )
        assert mock_client.calls["get_mechanism_metrics"] == [(1, 1, block)]

    expected = SubnetNeurons(
        block=block,
        mechanism_id=1,
        neurons={
            neuron.hotkey: neuron.model_copy(
                update={"incentive": Incentive(32768 / 65535), "last_update": Timestamp(42)}
            )
        },
    )
    assert response.status_code == HTTP_200_OK, response.content
    assert response.json() == expected.model_dump(mode="json")
```

- [ ] **Step 3: Run to see them fail.** `cd pylon_service && nox -s test -- tests/unit/_unstable_identity_endpoints/test_get_neurons_mechanism.py`. Expected: ImportError for `MechanismMetrics`.

- [ ] **Step 4: Model field.** In `pylon_commons/pylon_commons/models.py`, `class SubnetNeurons(BittensorModel)`, add after `block`:

```python
    mechanism_id: MechanismId = MechanismId(0)
```

Import `MechanismId` from `pylon_commons.types` if it is not already imported there.

- [ ] **Step 5: Contact.** In `pylon_service/pylon_service/bittensor/contact.py`, add near the other module-level definitions:

```python
# Subtensor keys per-mechanism storage by mechanism_id * GLOBAL_MAX_SUBNET_COUNT + netuid
# (pallets/subtensor/src/subnets/mechanism.rs, get_mechanism_storage_index).
GLOBAL_MAX_SUBNET_COUNT = 4096
U16_MAX = 65535


@dataclass(frozen=True)
class MechanismMetrics:
    """Raw per-UID mechanism storage; missing UIDs read as 0, like Subtensor's getters."""

    incentives: list[int]
    last_updates: list[int]

    def incentive(self, uid: int) -> Incentive:
        return Incentive(self.incentives[uid] / U16_MAX if uid < len(self.incentives) else 0.0)

    def last_update(self, uid: int) -> Timestamp:
        return Timestamp(self.last_updates[uid] if uid < len(self.last_updates) else 0)
```

Add `async def get_mechanism_metrics(self, netuid: NetUid, mechanism_id: MechanismId, block: Block) -> MechanismMetrics | None: ...` to the port protocol and the abstract client next to `get_neurons`. Implement it in `TurboBtContact` after `get_neurons`:

```python
    @track_operation(
        bittensor_operation_duration,
        labels={"uri": Attr("uri"), "netuid": Param("netuid"), "hotkey": Attr("hotkey")},
    )
    async def get_mechanism_metrics(
        self, netuid: NetUid, mechanism_id: MechanismId, block: Block
    ) -> MechanismMetrics | None:
        async def read(client: Bittensor) -> MechanismMetrics | None:
            state = client.subtensor.state
            count = await state.getStorage("SubtensorModule.MechanismCountCurrent", netuid, block_hash=block.hash)
            if not 0 <= mechanism_id < (count or 1):
                return None
            index = mechanism_id * GLOBAL_MAX_SUBNET_COUNT + netuid
            incentives = await state.getStorage("SubtensorModule.Incentive", index, block_hash=block.hash)
            last_updates = await state.getStorage("SubtensorModule.LastUpdate", index, block_hash=block.hash)
            return MechanismMetrics(
                incentives=[int(value) for value in incentives or []],
                last_updates=[int(value) for value in last_updates or []],
            )

        return await self._protect_turbobt("get_mechanism_metrics", read)
```

On chain, `Incentive` is `Vec<PerU16>` and the storage key is the `NetUidStorageIndex` newtype. The unit tests mock this method, so only the integration test in Step 9 proves that turbobt encodes the key and decodes each element to an integer. That test is mandatory for this task, not optional.

In `contact_router.py`, add a method mirroring `get_neurons` (line 256) with `main_call`/`archive_call` lambdas calling `get_mechanism_metrics(netuid, mechanism_id, block)`. In `mock_contact.py`, add:

```python
    async def get_mechanism_metrics(
        self, netuid: NetUid, mechanism_id: MechanismId, block: Block
    ) -> MechanismMetrics | None:
        return await self._execute_behavior("get_mechanism_metrics", netuid, mechanism_id, block)
```

- [ ] **Step 6: Service and error.**
  - In `pylon_service/pylon_service/api/services.py`, add `class MechanismNotFoundError(ServiceError): pass`.
  - In `api/utils.py:26`, add it to the 404 tuple.
  - In `api/_unstable/services.py`, change `NeuronService`:

```python
class NeuronService(BaseService):
    async def get_neurons(
        self, netuid: NetUid, block_number: BlockNumber, mechanism_id: MechanismId = MechanismId(0)
    ) -> SubnetNeurons:
        block = await self.contact_router.get_block(block_number)
        if block is None:
            raise BlockNotFoundError(f"Block {block_number} not found.")
        neurons = await self.contact_router.get_neurons(netuid, block)
        return await self._for_mechanism(neurons, netuid, mechanism_id)

    async def get_latest_neurons(self, netuid: NetUid, mechanism_id: MechanismId = MechanismId(0)) -> SubnetNeurons:
        block = await self.contact_router.get_latest_block()
        neurons = await self.contact_router.get_neurons(netuid, block)
        return await self._for_mechanism(neurons, netuid, mechanism_id)

    async def _for_mechanism(self, neurons: SubnetNeurons, netuid: NetUid, mechanism_id: MechanismId) -> SubnetNeurons:
        """Overlay the mechanism's incentive and last update; mechanism 0 is what neuron info already holds."""
        if mechanism_id == 0:
            return neurons
        metrics = await self.contact_router.get_mechanism_metrics(netuid, mechanism_id, neurons.block)
        if metrics is None:
            raise MechanismNotFoundError(f"Mechanism {mechanism_id} not found on subnet {netuid}.")
        return SubnetNeurons(
            block=neurons.block,
            mechanism_id=mechanism_id,
            neurons={
                hotkey: neuron.model_copy(
                    update={"incentive": metrics.incentive(neuron.uid), "last_update": metrics.last_update(neuron.uid)}
                )
                for hotkey, neuron in neurons.neurons.items()
            },
        )
```

- [ ] **Step 7: Handlers.** In `api/_unstable/api.py`, give both handlers a query parameter. The `v1` aliases (`v1/api.py:73-95`) reuse these handlers, so they pick it up automatically.

```python
    @handler(Endpoint.NEURONS)
    async def get_neurons(
        self,
        unstable_neuron_service: NeuronService,
        block_number: BlockNumber,
        netuid: NetUid,
        mechanism_id: MechanismId = MechanismId(0),
    ) -> GetNeuronsResponse:
        neurons = await unstable_neuron_service.get_neurons(netuid, block_number, mechanism_id)
        return GetNeuronsResponse.model_validate(neurons, from_attributes=True)

    @handler(Endpoint.LATEST_NEURONS)
    async def get_latest_neurons(
        self, unstable_neuron_service: NeuronService, netuid: NetUid, mechanism_id: MechanismId = MechanismId(0)
    ) -> GetNeuronsResponse:
        neurons = await unstable_neuron_service.get_latest_neurons(netuid, mechanism_id)
        return GetNeuronsResponse.model_validate(neurons, from_attributes=True)
```

- [ ] **Step 8: Run the unit suite.** `cd pylon_service && nox -s test`. The neuron snapshot tests fail only because of the new `"mechanism_id": 0` key. Inspect the diff and confirm it is only that key, then update with `nox -s test -- --snapshot-update` and rerun.

- [ ] **Step 9: Integration test.** Append to `pylon_service/tests/integration/contact/test_reads.py` (import `MechanismId`, `Weight`, `DevAccount` and the `participant_uids_factory`/`write_contact` fixtures as `test_writes.py` does):

```python
@pytest.mark.asyncio
async def test_get_mechanism_metrics_reads_mechanism_storage(
    participant_uids_factory, write_contact, open_contact, mechanism_direct_netuid
):
    uids = await participant_uids_factory(write_contact, mechanism_direct_netuid)
    alice = uids[DevAccount.ALICE]
    before = await open_contact.get_latest_block()

    await write_contact.set_weights(mechanism_direct_netuid, MechanismId(1), {uids[DevAccount.CHARLIE]: Weight(1.0)})

    block = await open_contact.get_latest_block()
    mechanism_one = await open_contact.get_mechanism_metrics(mechanism_direct_netuid, MechanismId(1), block)
    mechanism_zero = await open_contact.get_mechanism_metrics(mechanism_direct_netuid, MechanismId(0), block)
    neurons = await open_contact.get_neurons(mechanism_direct_netuid, block)

    assert mechanism_one is not None and mechanism_zero is not None
    assert mechanism_one.last_update(alice) > before.number
    assert {n.uid: (n.incentive, n.last_update) for n in neurons.neurons.values()} == {
        n.uid: (mechanism_zero.incentive(n.uid), mechanism_zero.last_update(n.uid)) for n in neurons.neurons.values()
    }
    assert await open_contact.get_mechanism_metrics(mechanism_direct_netuid, MechanismId(2), block) is None
```

Run `cd pylon_service && nox -s test-integration-contact -- -k mechanism_metrics`. If the mechanism-0 equality fails on `incentive` by float rounding, compare `round(x * 65535)` on both sides, and keep the comparison on raw parts.

- [ ] **Step 10: Docs.** In `CLAUDE.md`'s "Neuron Data" endpoint list and in `docs/SERVICE.md`, document `?mechanism_id=<n>` (default 0) for the at-block and latest neuron endpoints: only `incentive` and `last_update` are per mechanism, `recent` neurons and validators remain mechanism 0, and an unknown mechanism gives 404.

- [ ] **Step 11: Lint and commit.** `nox -s format`, then:

```bash
git add pylon_commons pylon_service CLAUDE.md docs/SERVICE.md
git commit -m "feat: read neuron incentive and last update per mechanism" -m "Impacts: service, commons"
```

---

### Task 2: Pylon client requests a mechanism's neurons

**Goal:** `get_neurons` and `get_latest_neurons` (open access and identity, sync and async, `unstable` and `v1`) take a keyword-only `mechanism_id` (default 0). The client sends it only when non-zero and raises `PylonMechanismMismatch` when the service answers for a different mechanism.

**Files:**
- Modify: `pylon_commons/pylon_commons/_unstable/requests.py:88-104`, `pylon_commons/pylon_commons/exceptions.py`
- Modify: `pylon_client/pylon_client/_internal/api/_unstable/translators.py:75-87`
- Modify: `pylon_client/pylon_client/_internal/api/abstract_sync.py`, `abstract_async.py` (open-access `get_neurons`/`get_latest_neurons` near lines 130-155, identity ones near lines 506-528, abstract builders near line 380)
- Modify: `pylon_client/pylon_client/_internal/api/_unstable/sync/api.py:54-61,135-146`, `_unstable/asynchronous/api.py` (same builders)
- Test: `pylon_client/tests/unit/{synchronous,asynchronous}/identity/test_get_latest_neurons.py`, `pylon_client/tests/pact/{synchronous,asynchronous}/identity/test_get_latest_neurons.py`, `pylon_service/tests/pact/state_handlers.py`, regenerated `pylon_client/tests/pact/pacts/*.json`
- Docs: `docs/CLIENT.md`

**Acceptance Criteria:**
- [ ] `client.unstable.identity.get_latest_neurons()` sends no query string (the requests of the existing pact interactions are unchanged; their response bodies gain `"mechanism_id": 0`).
- [ ] `client.unstable.identity.get_latest_neurons(mechanism_id=MechanismId(1))` sends `?mechanism_id=1` and returns the response.
- [ ] A response with `mechanism_id` 0 to a mechanism-1 request raises `PylonMechanismMismatch` (an old service ignored the parameter).
- [ ] A file-backed `neurons_file` serves only mechanism 0; other mechanisms go to the service.
- [ ] Client pact tests generate the new interaction and the service pact verification (`nox -s test-pact` in `pylon_service`) passes.

**Verify:** `cd /home/jan/Projects/reef/bittensor-pylon/pylon_client && nox -s test-3.14` → pass. Then `cd .. && nox -s test-pact` → pass.

**Steps:**

- [ ] **Step 1: Failing unit tests.** The client unit tests stub HTTP with respx (`service_mock`); a route declared with `params=` matches only requests that carry that query. In `pylon_client/tests/unit/synchronous/identity/test_get_latest_neurons.py`, add `from httpx import Response, codes`, add `MechanismId` and `PylonMechanismMismatch` to the `pylon_client.artanis` import, and add these methods to `TestSyncIdentityGetLatestNeurons`:

```python
    def test_mechanism_sends_query_and_checks_answer(self, service_mock, pylon_client, endpoint_url, success_response):
        self._setup_login_mock(service_mock)
        body = success_response.model_copy(update={"mechanism_id": MechanismId(1)}).model_dump(mode="json")
        route = service_mock.get(endpoint_url, params={"mechanism_id": "1"}).mock(
            return_value=Response(status_code=codes.OK, json=body)
        )

        with pylon_client:
            response = pylon_client.unstable.identity.get_latest_neurons(mechanism_id=MechanismId(1))

        assert route.called
        assert response.mechanism_id == 1

    def test_mechanism_rejects_service_without_support(self, service_mock, pylon_client, endpoint_url, success_response):
        self._setup_login_mock(service_mock)
        # A service without mechanism support ignores the query and answers without a mechanism_id field.
        body = success_response.model_dump(mode="json", exclude={"mechanism_id"})
        service_mock.get(endpoint_url, params={"mechanism_id": "1"}).mock(
            return_value=Response(status_code=codes.OK, json=body)
        )

        with pylon_client:
            with pytest.raises(PylonMechanismMismatch):
                pylon_client.unstable.identity.get_latest_neurons(mechanism_id=MechanismId(1))

    def test_default_mechanism_sends_no_query(self, service_mock, pylon_client, route_mock, success_response):
        self._setup_login_mock(service_mock)
        route_mock.mock(return_value=Response(status_code=codes.OK, json=success_response.model_dump(mode="json")))

        with pylon_client:
            pylon_client.unstable.identity.get_latest_neurons()

        assert route_mock.calls.last.request.url.query == b""
```

Add this module-level test below `test_neurons_file_returns_file_backed_neurons` in the same file:

```python
def test_neurons_file_is_bypassed_for_other_mechanisms(tmp_path, test_url, service_mock):
    """
    Test that a configured neurons_file answers only mechanism 0; other mechanisms are read from the service.
    """
    neurons_file = tmp_path / "neurons.yaml"
    file_backed = write_neurons_file(
        neurons_file, {"5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY": ("127.0.0.1", 9090)}
    )
    service_mock.get(EndpointUnstable.IDENTITIES.absolute_url(is_public_=True)).mock(
        return_value=Response(status_code=codes.OK, json={"identities": {"sn1": 1}})
    )
    body = file_backed.model_copy(update={"mechanism_id": MechanismId(1)}).model_dump(mode="json")
    route = service_mock.get(
        EndpointUnstable.LATEST_NEURONS.absolute_url(netuid_=1, identity_name_="sn1"), params={"mechanism_id": "1"}
    ).mock(return_value=Response(status_code=codes.OK, json=body))
    client = PylonClient(
        Config(
            address=test_url,
            identity_name=IdentityName("sn1"),
            identity_token=PylonAuthToken("sn1_token"),
            neurons_file=str(neurons_file),
            retry=DEFAULT_RETRIES.copy(wait=wait_none()),
        )
    )

    with client:
        response = client.unstable.identity.get_latest_neurons(mechanism_id=MechanismId(1))

    assert route.called
    assert response.mechanism_id == 1
```

In `pylon_client/tests/unit/asynchronous/identity/test_get_latest_neurons.py`, add the same imports (`MechanismId`, `PylonMechanismMismatch`; `Response` and `codes` are already imported) and the asynchronous form of the three class methods in `TestIdentityGetLatestNeurons`:

```python
    @pytest.mark.asyncio
    async def test_mechanism_sends_query_and_checks_answer(
        self, service_mock, pylon_client, endpoint_url, success_response
    ):
        self._setup_login_mock(service_mock)
        body = success_response.model_copy(update={"mechanism_id": MechanismId(1)}).model_dump(mode="json")
        route = service_mock.get(endpoint_url, params={"mechanism_id": "1"}).mock(
            return_value=Response(status_code=codes.OK, json=body)
        )

        async with pylon_client:
            response = await pylon_client.unstable.identity.get_latest_neurons(mechanism_id=MechanismId(1))

        assert route.called
        assert response.mechanism_id == 1

    @pytest.mark.asyncio
    async def test_mechanism_rejects_service_without_support(
        self, service_mock, pylon_client, endpoint_url, success_response
    ):
        self._setup_login_mock(service_mock)
        body = success_response.model_dump(mode="json", exclude={"mechanism_id"})
        service_mock.get(endpoint_url, params={"mechanism_id": "1"}).mock(
            return_value=Response(status_code=codes.OK, json=body)
        )

        async with pylon_client:
            with pytest.raises(PylonMechanismMismatch):
                await pylon_client.unstable.identity.get_latest_neurons(mechanism_id=MechanismId(1))

    @pytest.mark.asyncio
    async def test_default_mechanism_sends_no_query(self, service_mock, pylon_client, route_mock, success_response):
        self._setup_login_mock(service_mock)
        route_mock.mock(return_value=Response(status_code=codes.OK, json=success_response.model_dump(mode="json")))

        async with pylon_client:
            await pylon_client.unstable.identity.get_latest_neurons()

        assert route_mock.calls.last.request.url.query == b""
```

Run `cd /home/jan/Projects/reef/bittensor-pylon/pylon_client && nox -s test-3.14 -- tests/unit -k "mechanism or neurons_file"`. Expected: the new tests fail (ImportError for `PylonMechanismMismatch`, then unexpected keyword `mechanism_id`).

- [ ] **Step 2: Requests and exception.** In `pylon_commons/pylon_commons/_unstable/requests.py`, add `mechanism_id: MechanismId = MechanismId(0)` to `GetNeuronsRequest` and `GetLatestNeuronsRequest`. In `pylon_commons/pylon_commons/exceptions.py`:

```python
class PylonMechanismMismatch(BasePylonException):
    """
    Error raised when the service answers a neuron request for a different mechanism, e.g. a service without mechanism support.
    """
```

Export it next to `PylonMisconfigured` in `pylon_client/pylon_client/artanis/__init__.py` and `pylon_client/pylon_client/v1/__init__.py`.

- [ ] **Step 3: Translators.** In `_unstable/translators.py`, pass the parameter only when non-zero:

```python
    def _translate_get_neurons(self, request: GetNeuronsRequest, communicator: HttpCommunicatorT) -> Request:
        url = self._build_url(self._endpoint_cls.NEURONS, request)
        headers = self._get_auth_headers(request, communicator)
        return communicator.raw_client.build_request(
            method=self._endpoint_cls.NEURONS.method, url=url, headers=headers, params=_mechanism_params(request)
        )
```

Do the same in `_translate_get_latest_neurons`, and add at module level:

```python
def _mechanism_params(request: GetNeuronsRequest | GetLatestNeuronsRequest) -> dict[str, int] | None:
    """Omit the default so requests stay identical for services without mechanism support."""
    return {"mechanism_id": request.mechanism_id} if request.mechanism_id else None
```

- [ ] **Step 4: Builders and public methods.** In both `_unstable/sync/api.py` and `_unstable/asynchronous/api.py`, add `mechanism_id: MechanismId = MechanismId(0)` to `_get_neurons_request` and `_get_latest_neurons_request` (open access and identity), passing it into the request. In `abstract_sync.py` (and the same in `abstract_async.py` with `await`):
  - Update the abstract builder signatures.
  - Add the helper to `AbstractApi`:

```python
    @staticmethod
    def _require_mechanism(response: GetNeuronsResponse, mechanism_id: MechanismId) -> GetNeuronsResponse:
        if response.mechanism_id != mechanism_id:
            raise PylonMechanismMismatch(
                f"Requested mechanism {mechanism_id} but the service answered for mechanism {response.mechanism_id}; "
                "upgrade the Pylon service"
            )
        return response
```

  - Change the four public methods. Identity latest:

```python
    def get_latest_neurons(self, *, mechanism_id: MechanismId = MechanismId(0)) -> GetNeuronsResponse:
        """
        Retrieves neurons for the authenticated identity's subnet at the latest available block.

        Args:
            mechanism_id: Mechanism whose incentive and last update to return; other fields are subnet-wide.

        Returns:
            GetNeuronsResponse containing the latest block information and a dictionary mapping hotkeys to
            Neuron objects.

        Raises:
            PylonMechanismMismatch: The service answered for a different mechanism.
        """
        if mechanism_id == 0 and (file_backed := self._file_backed_neurons()) is not None:
            return file_backed
        response = self._send_identity_request(partial(self._get_latest_neurons_request, mechanism_id=mechanism_id))
        return self._require_mechanism(response, mechanism_id)
```

  - Apply the same pattern to identity `get_neurons(block_number, *, mechanism_id=...)` and open-access `get_neurons(netuid, block_number, *, mechanism_id=...)` / `get_latest_neurons(netuid, *, mechanism_id=...)`.

The `v1` translator and API classes inherit these. Confirm with `grep -n "class .*Translator\|class .*Api" pylon_client/pylon_client/_internal/api/v1/*.py pylon_client/pylon_client/_internal/api/v1/*/*.py`; if one overrides a neuron builder, apply the same change there.

- [ ] **Step 5: Pact.**
  - In `pylon_service/tests/pact/state_handlers.py`, add below `NeuronsExistHandler` (import `MechanismMetrics` from `pylon_service.bittensor.contact`):

```python
class NeuronsExistForMechanismHandler(StateHandler):
    name = "neurons exist for mechanism"

    def setup(self, parameters: dict[str, Any]) -> None:
        block = BlockFactory.build()
        neurons = NeuronFactory.batch(parameters.get("neuron_count", 1))
        subnet_neurons = SubnetNeurons(block=block, neurons={n.hotkey: n for n in neurons})

        client = self._get_client(parameters)
        self._set_default_latest_block(client, block)
        client.add_behavior("get_neurons", subnet_neurons)
        client.add_behavior("get_mechanism_metrics", MechanismMetrics(incentives=[], last_updates=[]))
```

  - In `pylon_client/tests/pact/matchers.py`, give `neurons_response_matcher` the mechanism. A plain value in a pact body is matched exactly, so the contract pins which mechanism the service answers for; the neuron fields stay type matchers, so the mechanism's zero incentive and last update satisfy them:

```python
def neurons_response_matcher(hotkey_1: str, hotkey_2: str, mechanism_id: int = 0) -> dict:
    return {
        "block": block_matcher(),
        "mechanism_id": mechanism_id,
        "neurons": match.each_value_matches(
            match.each_key_matches(  # type: ignore[reportArgumentType]
                {
                    hotkey_1: neuron_matcher(hotkey_1, uid=1),
                    hotkey_2: neuron_matcher(hotkey_2, uid=2),
                },
                rules=match.str(hotkey_1),
            ),
            rules=match.like(neuron_matcher(hotkey_1, uid=1)),
        ),
    }
```

  - In `pylon_client/tests/pact/synchronous/identity/test_get_latest_neurons.py`, import `MechanismId` from `pylon_client.artanis` and `neurons_response_matcher` from `tests.pact.matchers`, and add:

```python
def test_get_latest_neurons_for_mechanism(pact: Pact, pylon_client_factory):
    (
        pact.upon_receiving("an identity request for latest neurons of mechanism 1")
        .given("neurons exist for mechanism", identity_name=IDENTITY_NAME, netuid=NETUID, neuron_count=2)
        .with_request("GET", f"/api/_unstable/identity/{IDENTITY_NAME}/subnet/{NETUID}/block/latest/neurons")
        .with_query_parameters({"mechanism_id": "1"})
        .with_header("Authorization", f"Bearer {IDENTITY_TOKEN}")
        .will_respond_with(codes.OK)
        .with_body(neurons_response_matcher(HOTKEY_1, HOTKEY_2, mechanism_id=1), content_type="application/json")
    )

    with pact.serve() as srv:
        client = pylon_client_factory(str(srv.url), logged_in=True)
        with client:
            response = client.unstable.identity.get_latest_neurons(mechanism_id=MechanismId(1))

    assert response == GetNeuronsResponse(
        block=build_block(),
        mechanism_id=MechanismId(1),
        neurons={
            Hotkey(HOTKEY_1): build_neuron(HOTKEY_1, uid=1),
            Hotkey(HOTKEY_2): build_neuron(HOTKEY_2, uid=2),
        },
    )
```

  - In `pylon_client/tests/pact/asynchronous/identity/test_get_latest_neurons.py`, add the same imports and the same test as `@pytest.mark.asyncio async def test_get_latest_neurons_for_mechanism(...)`, with `async with client:` and `response = await client.unstable.identity.get_latest_neurons(mechanism_id=MechanismId(1))`. The interaction chain and the final assertion are identical to the synchronous test.
  - Regenerate the pacts by running the client pact tests (`cd pylon_client && nox -s test-pact`). Every neuron interaction gains `"mechanism_id": 0` in its response body and the new interaction appears; the existing requests are unchanged. Then run `nox -s test-pact` from the repository root to verify the service against them.

- [ ] **Step 6: Docs.** In `docs/CLIENT.md`, document `mechanism_id` on the neuron methods, the version requirement (a service release that contains Task 1) and `PylonMechanismMismatch`.

- [ ] **Step 7: Format, run Verify, commit.**

```bash
nox -s format
git add pylon_commons pylon_client pylon_service/tests/pact docs/CLIENT.md
git commit -m "feat: request neuron incentive and last update per mechanism" -m "Impacts: client, commons"
```

- [ ] **Step 8: Pull request.** `git branch --show-current` (must be `mechanism-neuron-reads`), `git push -u origin mechanism-neuron-reads`, `gh pr create --base master`. The description should say what the PR adds, the default-0 compatibility guarantee and the old-service guard. Do not merge it and do not run `rt release`; Task 3 hands both to the person running the plan.

---

### Task 3: The person running the plan publishes the Pylon release

**Goal:** The Pylon pull request from Task 2 is merged and both products are released by the person running the plan, and the published client and service are confirmed to contain the change before any ComputeHorde or FactoryHorde work starts.

> **USER-ORDERED GATE — NON-SKIPPABLE.** This task was requested by the user in the current conversation. It MUST NOT be closed by walking around it, by declaring it "verified inline", or by substituting a cheaper check. Close only after every item in `acceptanceCriteria` has been re-validated independently, with output captured.

**Files:**
- None. This task changes no repository.

**Acceptance Criteria:**
- [ ] `gh pr view mechanism-neuron-reads --repo bittensor-church/bittensor-pylon --json state -q .state` prints `MERGED`.
- [ ] The newest `bittensor-pylon-client` on PyPI imports `PylonMechanismMismatch` from `pylon_client.artanis`.
- [ ] The released service image `docker.io/backenddevelopersltd/bittensor-pylon:<service version>` imports `MechanismMetrics` from `pylon_service.bittensor.contact`.
- [ ] The client version, the service version and the service image digest are stated in the task's completion note for Task 8.

**Verify:** `uv run --no-project --refresh --with bittensor-pylon-client python -c "from pylon_client.artanis import PylonMechanismMismatch; from importlib.metadata import version; print(version('bittensor-pylon-client'))"` → prints a version above 2.3.1 without an ImportError.

**Steps:**

- [ ] **Step 1: Stop and hand over.** Tasks 1 and 2 must be complete and their pull request open. Tell the person running the plan, in these words or equivalent, and then wait. Do not start Task 4 or any later task, and do not merge or release yourself: `rt release` pushes a bump commit and a tag to `master`, which agents never do.

  > The Pylon change is ready in pull request `<URL from Task 2>`. Before I continue with ComputeHorde and FactoryHorde, please:
  > 1. Review and merge the pull request.
  > 2. On an up-to-date `master` of bittensor-pylon, release the service: `cd pylon_service && rt release`.
  > 3. Release the client: `cd pylon_client && rt release`.
  > 4. Wait for both release workflows to finish (Docker Hub image for the service, PyPI for the client), then tell me the two version numbers.

- [ ] **Step 2: Confirm the merge.** After the person reports back, run `gh pr view mechanism-neuron-reads --repo bittensor-church/bittensor-pylon --json state -q .state`. Expected: `MERGED`. If the repository slug differs, read it from `git -C /home/jan/Projects/reef/bittensor-pylon remote get-url origin`.

- [ ] **Step 3: Confirm the client release.** Run the Verify command. It installs the newest published client into a throwaway environment. Expected: the version the person reported, and no ImportError. If PyPI still serves the old version, wait for the release workflow and repeat; do not continue on the old client.

- [ ] **Step 4: Confirm the service release and resolve its digest.** With `<service version>` as reported:

```bash
docker buildx imagetools inspect docker.io/backenddevelopersltd/bittensor-pylon:<service version> --format '{{json .Manifest}}' | jq -r .digest
docker run --rm --platform linux/amd64 --entrypoint /app/pylon_service/.venv/bin/python \
  docker.io/backenddevelopersltd/bittensor-pylon:<service version> \
  -c "from pylon_service.bittensor.contact import MechanismMetrics; print('ok')"
```

  Expected: a `sha256:…` digest, then `ok`.

- [ ] **Step 5: Record.** State the client version, the service version and the service digest in the completion note. Task 8 pins exactly these three values. If a later session lacks the note, the values can be read again with the commands of Steps 3 and 4.

---

### Task 4: ComputeHorde keeps other mechanisms' envelopes

**Status:** Done. Merged as ComputeHorde pull request #878 (`ec106a6e`, "feat: preserve commitment envelopes written by others").

**Goal:** ComputeHorde's miner preserves every tagged envelope it does not own when it rewrites its manifest, without naming FactoryHorde or its tag, and tests show its validator still parses manifests next to such envelopes.

**Files:**
- Modify: `/home/jan/Projects/reef/ComputeHorde/miner/app/src/compute_horde_miner/miner/manifest_commitment.py`
- Test: `/home/jan/Projects/reef/ComputeHorde/miner/app/src/compute_horde_miner/miner/tests/test_manifest_commitment.py`
- Test: `/home/jan/Projects/reef/ComputeHorde/validator/app/src/compute_horde_validator/validator/allowance/tests/test_manifests.py`

**Acceptance Criteria:**
- [x] With `<D:…><X:…><M:A2>` on chain and manifest `{A: 3}`, `set_commitment` is called with `<D:…><X:…><M:A3>`.
- [x] With `<D:…><X:…><M:A3>` on chain and manifest `{A: 3}`, `set_commitment` is not called.
- [x] `<M:A3><X:…>` (X after M) is rewritten to `<X:…><M:A3>`, with the X bytes unchanged.
- [x] `parse_commitments_to_manifests({hk: b"<D:…><X:…><M:A5>"})` returns `{(hk, always_on__gpu_24gb): 5}`.
- [x] All existing ComputeHorde miner and validator manifest tests pass unchanged.

**Verify:** `cd /home/jan/Projects/reef/ComputeHorde/miner && nox -s test -- -k test_manifest_commitment`, then `cd ../validator && nox -s test -- -k TestParseCommitmentsToManifests` → all selected tests pass.

**What was merged:** In `manifest_commitment.py` the miner keeps every envelope whose tag is a single capital letter other than `M`, and rebuilds its own `<M:…>` after them:

```python
        # Keep envelopes written by others (e.g. shield <D:...>), discarding old manifests and non-envelope garbage.
        # <M:...> is the miner's own envelope and is never preserved.
        preserved = "".join(re.findall(r"<[A-LN-Z]:[^<>]*>", current_commitment))
        merged_commitment = merge_manifest(preserved, new_manifest_string)
```

The tests use a neutral `<X:other-mechanism@…>` envelope (`test_preserves_other_envelopes`, `test_moves_other_envelopes_before_manifest` and the validator's `test_parse_commitments_beside_other_envelopes`). FactoryHorde's only obligation is that its tag stays a single capital letter other than `M`, which `F` is. Kept envelopes count towards ComputeHorde's 128-byte limit, so a stale envelope of a retired writer stays until that writer removes it, as `<D:…>` already did.

**Rollout consequence:** ComputeHorde miners on a release older than `ec106a6e` still erase `<F:…>` within 5 minutes. A FactoryHorde miner whose hotkey also runs a ComputeHorde miner must run that release or newer before it schedules the submitter.

---

### Task 5: Shared commitment fixtures

**Goal:** One JSON file of canonical cross-mechanism commitments, used by both FactoryHorde test suites, so the miner and validator agree on the format: the miner must produce the envelope of every valid case, and the validator must decode every valid case and reject every invalid one.

**Files:**
- Create: `spec/fixtures/commitment-v2/envelopes.json`

**Acceptance Criteria:**
- [ ] The file parses as JSON with `namespace`, `valid` and `invalid` keys.
- [ ] Every `valid[].commitment` is at most 128 UTF-8 bytes.
- [ ] Every `valid[].image` reconstructs from `namespace` plus the envelope.

**Verify:** `uv run --project validator python -c "import json; d=json.load(open('spec/fixtures/commitment-v2/envelopes.json')); assert all(len(v['commitment'].encode())<=128 for v in d['valid']); print(len(d['valid']), len(d['invalid']))"` → `4 6`

**Steps:**

- [ ] **Step 1: Branch in a worktree.** Leave the main checkout on whatever branch it is. Run `cd /home/jan/Projects/reef/factory-horde && git fetch origin && git worktree add ../factory-horde-second-mechanism -b second-mechanism-commitments origin/master`, then `uv sync` in the worktree's `validator/` and `miner/`. If the plan in the worktree differs from the one you are executing (`diff docs/superpowers/plans/2026-11-20-second-mechanism-commitments.md ../factory-horde-second-mechanism/docs/superpowers/plans/`), copy the plan and its `.tasks.json` into the worktree. All later factory-horde paths are relative to that worktree.

- [ ] **Step 2: Compute the base64url digest.** The fixture digest is SHA-256 `"ab" * 32`:

```bash
uv run --project validator python -c "import base64; print(base64.urlsafe_b64encode(bytes.fromhex('ab'*32)).rstrip(b'=').decode())"
```

Expected: `q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s`

- [ ] **Step 3: Write the fixture.**

```json
{
  "namespace": "ghcr.io/backend-developers-ltd",
  "valid": [
    {
      "commitment": "<F:factory-horde-factory@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s>",
      "image": "ghcr.io/backend-developers-ltd/factory-horde-factory@sha256:abababababababababababababababababababababababababababababababab"
    },
    {
      "commitment": "<F:factory-horde-deceptive@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s><M:A3;S2>",
      "image": "ghcr.io/backend-developers-ltd/factory-horde-deceptive@sha256:abababababababababababababababababababababababababababababababab"
    },
    {
      "commitment": "<D:https://example.com/s><F:fh-miner-openhands@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s><M:A3>",
      "image": "ghcr.io/backend-developers-ltd/fh-miner-openhands@sha256:abababababababababababababababababababababababababababababababab"
    },
    {
      "commitment": "<M:A3><F:fh-miner-oneshot@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s>",
      "image": "ghcr.io/backend-developers-ltd/fh-miner-oneshot@sha256:abababababababababababababababababababababababababababababababab"
    }
  ],
  "invalid": [
    {"commitment": "<M:A3>", "reason": "No FactoryHorde submission"},
    {"commitment": "<F:a@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s><F:b@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s>", "reason": "Multiple FactoryHorde submissions"},
    {"commitment": "<F:Factory@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s>", "reason": "Malformed FactoryHorde submission"},
    {"commitment": "<F:factory@q6ur>", "reason": "Malformed FactoryHorde submission"},
    {"commitment": "<F:factory@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6t>", "reason": "Malformed FactoryHorde submission"},
    {"commitment": "ghcr.io/backend-developers-ltd/factory-horde-factory@sha256:abababababababababababababababababababababababababababababababab", "reason": "No FactoryHorde submission"}
  ]
}
```

The fifth invalid entry ends in `t` instead of `s`. It decodes to the same bytes, but it is not the canonical encoding and must be rejected. The last entry is the legacy format, which is deliberately unsupported.

- [ ] **Step 4: Run Verify, then commit.** `git add spec/fixtures/commitment-v2/envelopes.json && git commit -m "Add cross-mechanism commitment fixtures"`

---

### Task 6: Submitter writes only its own envelope

**Goal:** `miner/miner.py` encodes `<F:package@base64url>` under a configured namespace, merges it into the existing commitment without changing other bytes, keeps ComputeHorde's 33-byte reserve and confirms only its own envelope.

**Files:**
- Modify: `miner/miner.py`
- Test: `miner/tests/test_submission.py`

**Acceptance Criteria:**
- [ ] With `<D:x><M:A3;S2>` on chain, the write is `<D:x><F:…><M:A3;S2>`.
- [ ] With `<D:x><F:old><M:A3>` on chain, only the F envelope changes.
- [ ] With the same F already on chain, the outcome is `unchanged` and nothing is written.
- [ ] An image outside `SUBMISSION_NAMESPACE` fails before any read or write.
- [ ] A package name whose envelope cannot fit beside the 33-byte reserve fails before any read or write, with `128` in the message.
- [ ] If the non-`<M:…>` bytes plus 33, or the merged commitment itself, would exceed 128, the submitter raises `SubmissionError` and does not write.
- [ ] If the current commitment is undecodable hex, the submitter raises `SubmissionError` and does not write.
- [ ] Read-back succeeds when the F envelope matches, even if the `<M:…>` part changed meanwhile.
- [ ] The CLI requires `--namespace` / `SUBMISSION_NAMESPACE`.
- [ ] `miner/miner.py` contains no decoder: the submitter only encodes and compares envelopes; decoding belongs to the validator (Task 7).

**Verify:** `cd miner && uv run pytest -q && uv run ruff check . && uv run basedpyright` → all pass, 0 errors.

**Steps:**

- [ ] **Step 1: Write failing tests.** In `miner/tests/test_submission.py`:
  - Replace the `miner` import and the constants below it with:

```python
import json

from miner import COMPUTE_HORDE_MANIFEST_RESERVE, SubmissionError, encode_envelope, main, merge_envelope, submit

FIXTURES = Path(__file__).resolve().parents[2] / "spec/fixtures"
ENVELOPES = json.loads((FIXTURES / "commitment-v2/envelopes.json").read_text())
NAMESPACE = "ghcr.io/example"
IMAGE = "ghcr.io/example/factory@sha256:" + "ab" * 32
ENVELOPE = encode_envelope(IMAGE, NAMESPACE)
BLOCK = Block(number=BlockNumber(100), hash=BlockHash("0x" + "a" * 64))
NEURON = Neuron.model_validate_json((FIXTURES / "discovery-v1/neuron.json").read_bytes())
```

  - Replace `FakeApi` with:

```python
class FakeApi:
    """Model an awaited write that can finish despite a lost HTTP response."""

    netuid: NetUid = NetUid(2)

    def __init__(self, initial: bytes = b"", initial_hex: str | None = None) -> None:
        self.current: GetCommitmentResponse | None = None
        self.writes: list[CommitmentDataBytes] = []
        self.reads = 0
        self.lose_response = False
        self.store_write = True
        self.registered = True
        # Bytes another writer leaves after our envelope while our write is in flight.
        self.after_write: bytes | None = None
        if initial_hex is not None or initial:
            self._store(initial_hex if initial_hex is not None else initial.hex())

    def _store(self, value: str) -> None:
        self.current = GetCommitmentResponse(
            commitment_block_number=BlockNumber(99),
            hotkey=NEURON.hotkey,
            commitment=CommitmentDataHex(value),
            block=BLOCK,
        )

    def get_latest_neurons(self) -> GetNeuronsResponse:
        return self.get_neurons(BLOCK.number)

    def get_neurons(self, block_number: BlockNumber) -> GetNeuronsResponse:
        assert block_number == BLOCK.number
        return GetNeuronsResponse(block=BLOCK, neurons={NEURON.hotkey: NEURON} if self.registered else {})

    def get_own_commitment(self) -> GetCommitmentResponse:
        self.reads += 1
        if self.current is None:
            raise PylonNotFound()
        return self.current

    def set_commitment(self, commitment: CommitmentDataBytes) -> SetCommitmentResponse:
        self.writes.append(commitment)
        if self.store_write:
            stored = bytes(commitment) if self.after_write is None else ENVELOPE + self.after_write
            self._store(stored.hex())
        if self.lose_response:
            raise PylonTimeoutException(TimeoutReason.READ)
        return SetCommitmentResponse()
```

  - Change the existing tests:
    - Pass `NAMESPACE` as the fourth positional argument of every `submit(...)` call.
    - `test_publish_readback_and_skip_unchanged`: assert `api.writes == [ENVELOPE]` and delete the `validate_reference(IMAGE)` line.
    - `test_wrong_readback_identity_is_not_confirmation`: seed with `api.set_commitment(CommitmentDataBytes(ENVELOPE))`.
    - `test_cli_rejects_invalid_image_without_network`: append `"--namespace", NAMESPACE` to the argument list; the expected output stays `Expected ghcr.io`.
    - Delete `test_bad_wire_values` and `test_exact_byte_boundary`. The submitter no longer decodes references; the validator's discovery tests cover wire values, and the size tests below replace the boundary test.
  - Add these tests:

```python
def test_envelope_is_compact() -> None:
    assert ENVELOPE == b"<F:factory@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s>"


@pytest.mark.parametrize("case", ENVELOPES["valid"])
def test_fixture_commitments_hold_the_encoded_envelope(case: dict[str, str]) -> None:
    assert encode_envelope(case["image"], ENVELOPES["namespace"]) in case["commitment"].encode()


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        (b"", ENVELOPE),
        (b"<M:A3;S2>", ENVELOPE + b"<M:A3;S2>"),
        (b"<D:x><M:A3>", b"<D:x>" + ENVELOPE + b"<M:A3>"),
        (b"<D:x><F:old@" + b"A" * 43 + b"><M:A3>", b"<D:x>" + ENVELOPE + b"<M:A3>"),
        (b"<M:A3><F:old@" + b"A" * 43 + b">", b"<M:A3>" + ENVELOPE),
        (b"junk\xff", b"junk\xff" + ENVELOPE),
    ],
)
def test_merge_changes_only_own_envelope(current: bytes, expected: bytes) -> None:
    assert merge_envelope(current, ENVELOPE) == expected


def test_merge_keeps_compute_horde_reserve() -> None:
    room = 128 - COMPUTE_HORDE_MANIFEST_RESERVE - len(ENVELOPE)
    assert merge_envelope(b"<D:" + b"x" * (room - 4) + b">", ENVELOPE)
    with pytest.raises(SubmissionError, match="ComputeHorde"):
        merge_envelope(b"<D:" + b"x" * (room - 3) + b">", ENVELOPE)


def test_merge_never_exceeds_the_writer_limit() -> None:
    # An existing manifest larger than the reserve: only the total length check can catch it.
    fits = b"<M:" + b"A" * (128 - len(ENVELOPE) - 4) + b">"
    assert len(merge_envelope(fits, ENVELOPE)) == 128
    with pytest.raises(SubmissionError, match="128"):
        merge_envelope(b"<M:" + b"A" * (128 - len(ENVELOPE) - 3) + b">", ENVELOPE)


def test_overlong_package_fails_before_any_request() -> None:
    api = FakeApi()
    longest = "p" * (128 - COMPUTE_HORDE_MANIFEST_RESERVE - 48)
    assert encode_envelope(f"{NAMESPACE}/{longest}@sha256:{'ab' * 32}", NAMESPACE)
    with pytest.raises(ValueError, match="128"):
        submit(api, f"{NAMESPACE}/{longest}p@sha256:{'ab' * 32}", 2, NAMESPACE)
    assert api.reads == 0 and api.writes == []


def test_preserves_foreign_content_on_chain() -> None:
    api = FakeApi(initial=b"<D:x><M:A3;S2>")
    result = submit(api, IMAGE, 2, NAMESPACE)
    assert result.outcome == "published"
    assert bytes(api.writes[-1]) == b"<D:x>" + ENVELOPE + b"<M:A3;S2>"


def test_unchanged_when_own_envelope_matches() -> None:
    api = FakeApi(initial=b"<D:x>" + ENVELOPE + b"<M:A3>")
    assert submit(api, IMAGE, 2, NAMESPACE).outcome == "unchanged"
    assert api.writes == []


def test_outside_namespace_fails_before_reading() -> None:
    api = FakeApi()
    with pytest.raises(ValueError, match="namespace"):
        submit(api, "ghcr.io/other/factory@sha256:" + "ab" * 32, 2, NAMESPACE)
    assert api.reads == 0 and api.writes == []


def test_undecodable_commitment_is_never_overwritten() -> None:
    api = FakeApi(initial_hex="0xzz")
    with pytest.raises(SubmissionError, match="preserve"):
        submit(api, IMAGE, 2, NAMESPACE)
    assert api.writes == []


def test_readback_ignores_concurrent_manifest_change() -> None:
    api = FakeApi(initial=b"<M:A3>")
    api.after_write = b"<M:A4>"
    assert submit(api, IMAGE, 2, NAMESPACE).outcome == "published"
```

- [ ] **Step 2: Run to see them fail.** `cd miner && uv run pytest -q`. Expected: ImportError for `encode_envelope`.

- [ ] **Step 3: Implement in `miner/miner.py`.** Add `import base64`. Change the module docstring to `"""Publish one FactoryHorde commitment envelope through the miner's own Pylon identity, keeping every other byte."""`. Replace the constants, `validate_reference` and `decode_reference` with:

```python
# The selected Pylon 2.3.4 writer uses RawN, whose supported maximum is Raw128.
COMMITMENT_MAX_BYTES = 128
# ComputeHorde's largest manifest envelope: "<M:" + six "X999" codes joined by ";" + ">".
COMPUTE_HORDE_MANIFEST_RESERVE = 33
SEGMENT = r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
IMAGE_PATTERN = rf"ghcr\.io/{SEGMENT}(?:/{SEGMENT})+@sha256:[0-9a-f]{{64}}"
NAMESPACE_PATTERN = rf"ghcr\.io(?:/{SEGMENT})+"
OWN_ENVELOPE = re.compile(rb"<F:([^<>]*)>")
MANIFEST_ENVELOPE = re.compile(rb"<M:[^<>]*>")


def validate_reference(image: str) -> str:
    """Accept a complete GHCR digest reference.

    Raises:
        ValueError: The registry or digest syntax is unsupported.
    """
    if re.fullmatch(IMAGE_PATTERN, image) is None:
        raise ValueError("Expected ghcr.io/owner/image@sha256:<64 lowercase hexadecimal characters>")
    return image


def encode_envelope(image: str, namespace: str) -> bytes:
    """Encode IMAGE as FactoryHorde's commitment envelope relative to NAMESPACE.

    Raises:
        ValueError: The image or namespace is invalid, the image lies outside the namespace,
            or the envelope can never fit beside ComputeHorde's manifest.
    """
    validate_reference(image)
    if re.fullmatch(NAMESPACE_PATTERN, namespace) is None:
        raise ValueError("Expected a ghcr.io/owner namespace")
    if not image.startswith(namespace + "/"):
        raise ValueError(f"Image must be published under namespace {namespace}")
    package, digest = image.removeprefix(namespace + "/").split("@sha256:")
    compact = base64.urlsafe_b64encode(bytes.fromhex(digest)).rstrip(b"=")
    envelope = b"<F:" + package.encode() + b"@" + compact + b">"
    if len(envelope) + COMPUTE_HORDE_MANIFEST_RESERVE > COMMITMENT_MAX_BYTES:
        raise ValueError(
            f"Package name is too long: its envelope and ComputeHorde's manifest must fit {COMMITMENT_MAX_BYTES} bytes"
        )
    return envelope


def merge_envelope(current: bytes, envelope: bytes) -> bytes:
    """Replace only FactoryHorde's envelope, keeping every other byte and ComputeHorde's manifest room.

    Raises:
        SubmissionError: The result would exceed the writer's limit or ComputeHorde's manifest reserve.
    """
    own = list(OWN_ENVELOPE.finditer(current))
    if own:
        tail = OWN_ENVELOPE.sub(b"", current[own[0].end() :])
        merged = current[: own[0].start()] + envelope + tail
    else:
        manifest = MANIFEST_ENVELOPE.search(current)
        at = manifest.start() if manifest is not None else len(current)
        merged = current[:at] + envelope + current[at:]
    if len(merged) > COMMITMENT_MAX_BYTES:
        raise SubmissionError(f"Commitment would exceed the writer's {COMMITMENT_MAX_BYTES}-byte limit")
    if len(MANIFEST_ENVELOPE.sub(b"", merged)) + COMPUTE_HORDE_MANIFEST_RESERVE > COMMITMENT_MAX_BYTES:
        raise SubmissionError("Commitment would leave ComputeHorde less than its 33-byte manifest reserve")
    return merged


def _raw(value: str) -> bytes:
    if re.fullmatch(r"(?:0x)?(?:[0-9a-fA-F]{2})*", value) is None:
        raise ValueError("Malformed hexadecimal commitment")
    return bytes(CommitmentDataBytes.fromhex(value.removeprefix("0x")))
```

`merge_envelope` raises `SubmissionError`, so move these definitions below the `SubmissionError` class. Replace `_matches` with:

```python
def _matches(response: GetCommitmentResponse | None, envelope: bytes) -> bool:
    if response is None:
        return False
    try:
        return OWN_ENVELOPE.findall(_raw(response.commitment)) == [envelope[3:-1]]
    except ValueError:
        return False
```

Change the `Publication` docstring to `"""Evidence from a read-back of the own envelope; commitment_block is the commitment's last write by either mechanism."""`. Replace `submit` with:

```python
def submit(api: SubmissionApi, image: str, netuid: int, namespace: str, *, confirm_seconds: float = 150) -> Publication:
    """Read first, replace only the own envelope, write at most once, then require registered read-back.

    HTTP timeouts can leave the service's shielded write running. Recovery polls
    reads only; automatic HTTP write retries must be disabled in the client config.

    Raises:
        SubmissionError: Configuration changes, the commitment cannot be preserved or confirmation never arrives.
        ValueError: The reference, namespace or confirmation period is invalid.
    """
    envelope = encode_envelope(image, namespace)
    if confirm_seconds <= 0:
        raise ValueError("Confirmation period must be positive")
    with _latency.time():
        try:
            api.get_latest_neurons()  # Resolves identity-scoped netuid before any mutation.
            if api.netuid != netuid:
                raise SubmissionError("Configured Pylon identity belongs to a different subnet")
            previous = _read(api)
            if previous is not None and _matches(previous, envelope):
                result = _evidence(api, previous, image, netuid, "unchanged")
            else:
                try:
                    current = b"" if previous is None else _raw(previous.commitment)
                except ValueError as error:
                    raise SubmissionError("Cannot preserve an undecodable commitment; refusing to overwrite") from error
                merged = merge_envelope(current, envelope)
                outcome: Literal["published", "recovered"] = "published"
                try:
                    api.set_commitment(CommitmentDataBytes(merged))
                except PylonRequestException, PylonBadGateway:
                    outcome = "recovered"
                deadline = time.monotonic() + confirm_seconds
                while True:
                    try:
                        response = _read(api)
                    except PylonRequestException:
                        response = None
                    if response is not None and _matches(response, envelope):
                        result = _evidence(api, response, image, netuid, outcome)
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise SubmissionError("Publication remains unconfirmed; read before resubmitting")
                    time.sleep(min(2, remaining))
        except Exception:
            _events.labels("error").inc()
            raise
        _events.labels(result.outcome).inc()
        return result
```

In `main`:
  - Add the option `@click.option("--namespace", envvar="SUBMISSION_NAMESPACE", required=True)` and the parameter `namespace: str`.
  - Replace the early `validate_reference(image)` with `encode_envelope(image, namespace)`, so a bad image, a foreign namespace or an overlong package fails before any network use.
  - Call `submit(client.v1.identity, image, netuid, namespace, confirm_seconds=confirm_seconds)`.
  - Change the command help to `"Publish IMAGE as this miner's FactoryHorde envelope and confirm it on chain. Safe to repeat: an unchanged envelope is not rewritten. Supply PYLON_IDENTITY_TOKEN in the environment."`

- [ ] **Step 4: Run Verify.** Expected: all pass.

- [ ] **Step 5: Commit.** `git add miner/miner.py miner/tests/test_submission.py && git commit -m "Write only FactoryHorde's commitment envelope"`

---

### Task 7: Validator discovers envelopes under the configured namespace

**Goal:** Discovery reads the single `<F:…>` envelope of each commitment and rebuilds the image under `VALIDATOR_SUBMISSION_NAMESPACE`, which dispatch requires.

**Files:**
- Modify: `validator/src/validator/discovery.py`
- Modify: `validator/src/validator/main.py:50-125,168-175`
- Test: `validator/tests/test_discovery.py`, `validator/tests/test_dependency_contract.py`, `validator/tests/test_chain_observer.py` (the `Settings` tests)

**Acceptance Criteria:**
- [ ] Every `valid` fixture yields a cohort member with the fixture's `image`.
- [ ] Every `invalid` fixture yields a `RejectedSubmission` whose `reason` contains the fixture's `reason`.
- [ ] `Settings(dispatch_enabled=True, …)` without `submission_namespace` raises a validation error naming the namespace.
- [ ] `test_published_baseline_references_fit_a_miner_commitment` asserts envelope + 33 ≤ 128 for every baseline kind.

**Verify:** `cd validator && uv run pytest -q && uv run ruff check . && uv run basedpyright` → all pass, 0 errors.

**Steps:**

- [ ] **Step 1: Write failing tests.** In `validator/tests/test_discovery.py`:
  - Load the fixture JSON as `ENVELOPES = json.loads((Path(__file__).resolve().parents[2] / "spec/fixtures/commitment-v2/envelopes.json").read_text())` (add `import json`).
  - Add `NAMESPACE = "ghcr.io/example"` and set `IMAGE = "ghcr.io/example/factory@sha256:" + "ab" * 32`.
  - Add `ENVELOPE = "<F:factory@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s>"` and store the default fake commitment as `CommitmentDataHex(ENVELOPE.encode().hex())`. Wherever a test puts `IMAGE.encode().hex()` on chain as a valid submission, use `ENVELOPE.encode().hex()`.
  - Pass `NAMESPACE` as the new fourth positional argument to every `discover(...)`/`freeze_discovery(...)` call (after `validator_hotkey`; `allowed_hotkeys` moves to fifth).
  - In `test_invalid_wire_data`, call `decode_reference(value, NAMESPACE)` and replace the last three parameters, which were bare references, with their envelope counterparts:

```python
        IMAGE.encode().hex(),  # legacy bare reference
        (ENVELOPE + "x" * 100).encode().hex(),  # over 128 bytes
        ENVELOPE.replace("factory@", "Factory@").encode().hex(),  # malformed package
```

  - Add a test that bytes outside the envelope are ignored: `assert decode_reference(("junk" + ENVELOPE + "<M:A3>\n").encode().hex(), NAMESPACE) == IMAGE`.
  - Add:

```python
@pytest.mark.parametrize("case", ENVELOPES["valid"])
def test_valid_envelopes_join_the_cohort(case: dict[str, str]) -> None:
    api = FakeApi()
    api.commitments = {NEURON.hotkey: CommitmentDataHex(case["commitment"].encode().hex())}
    snapshot = discover(api, 2, VALIDATOR, ENVELOPES["namespace"])
    assert [member.image for member in snapshot.cohort] == [case["image"]]


@pytest.mark.parametrize("case", ENVELOPES["invalid"])
def test_invalid_envelopes_are_rejected_with_reason(case: dict[str, str]) -> None:
    api = FakeApi()
    api.commitments = {NEURON.hotkey: CommitmentDataHex(case["commitment"].encode().hex())}
    snapshot = discover(api, 2, VALIDATOR, ENVELOPES["namespace"])
    assert snapshot.cohort == ()
    assert case["reason"] in snapshot.rejected[0].reason
```

In `test_dependency_contract.py`, replace the body of `test_published_baseline_references_fit_a_miner_commitment`'s loop with:

```python
        # Envelope "<F:" + package + "@" + 43-character base64url digest + ">" plus ComputeHorde's 33-byte reserve.
        assert len(f"<F:factory-horde-{kind}@{'A' * 43}>") + 33 <= 128
```

In `validator/tests/test_chain_observer.py`, below `test_dispatch_requires_explicit_identity_and_judge`, add a complete dispatch configuration and two tests. The dictionary validates on today's code, so the first test fails only because the namespace is not yet required:

```python
DISPATCH = {
    "NETUID": 2,
    "pylon_service_address": "http://pylon.test",
    "pylon_open_access_token": "test",
    "pylon_identity_name": "validator",
    "pylon_identity_token": "token",
    "dispatch_enabled": True,
    "VALIDATOR_HOTKEY": "5" + "c" * 47,
    "judge_image": "ghcr.io/example/judge@sha256:" + "a" * 64,
}


def test_dispatch_requires_submission_namespace() -> None:
    with pytest.raises(ValidationError, match="submission namespace"):
        Settings.model_validate(DISPATCH)
    settings = Settings.model_validate({**DISPATCH, "submission_namespace": "ghcr.io/example"})
    assert settings.submission_namespace == "ghcr.io/example"


def test_submission_namespace_must_be_a_ghcr_owner() -> None:
    with pytest.raises(ValidationError, match="submission_namespace"):
        Settings.model_validate({**DISPATCH, "submission_namespace": "docker.io/example"})
```

- [ ] **Step 2: Run to see them fail.** `cd validator && uv run pytest -q tests/test_discovery.py tests/test_dependency_contract.py tests/test_chain_observer.py`

- [ ] **Step 3: Implement decoding in `discovery.py`.** Add `import base64` and replace `decode_reference`:

```python
COMMITMENT_MAX_BYTES = 128
_SEGMENT = rb"[a-z0-9]+(?:[._-][a-z0-9]+)*"
_OWN_ENVELOPE = re.compile(rb"<F:([^<>]*)>")
_PAYLOAD = re.compile(rb"(" + _SEGMENT + rb"(?:/" + _SEGMENT + rb")*)@([A-Za-z0-9_-]{43})")


def decode_reference(value: str, namespace: str) -> str:
    """Decode the single FactoryHorde envelope of a hex commitment into a namespaced GHCR reference.

    Other mechanisms' envelopes in the same commitment are ignored.

    Raises:
        ValueError: Hex, length, envelope, digest or rebuilt reference is invalid.
    """
    if re.fullmatch(r"(?:0x)?(?:[0-9a-fA-F]{2})+", value) is None:
        raise ValueError("Malformed hexadecimal commitment")
    raw = bytes(CommitmentDataBytes.fromhex(value.removeprefix("0x")))
    if len(raw) > COMMITMENT_MAX_BYTES:
        raise ValueError("Commitment exceeds the selected writer's 128-byte limit")
    payloads = _OWN_ENVELOPE.findall(raw)
    if not payloads:
        raise ValueError("No FactoryHorde submission")
    if len(payloads) > 1:
        raise ValueError("Multiple FactoryHorde submissions")
    match = _PAYLOAD.fullmatch(payloads[0])
    if match is None:
        raise ValueError("Malformed FactoryHorde submission")
    digest = base64.urlsafe_b64decode(match[2] + b"=")
    if base64.urlsafe_b64encode(digest).rstrip(b"=") != match[2]:
        raise ValueError("Malformed FactoryHorde submission")
    return _image.validate_python(f"{namespace}/{match[1].decode()}@sha256:{digest.hex()}")
```

Thread `namespace: str` through `discover(api, netuid, validator_hotkey, namespace, allowed_hotkeys=None)` (`decode_reference(value, namespace)`), `freeze_discovery(files, snapshot_id, api, netuid, validator_hotkey, namespace, allowed_hotkeys=None)` and `freeze_via_pylon(files, config, netuid, validator_hotkey, namespace, snapshot_id)`. Update the module docstring: "version-two envelope discovery".

- [ ] **Step 4: Implement the setting in `main.py`.**
  - In `Settings`, add `submission_namespace: str | None = Field(default=None, pattern=r"^ghcr\.io(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+$")`. The `VALIDATOR_` prefix makes it `VALIDATOR_SUBMISSION_NAMESPACE`.
  - Add `"submission_namespace"` to the `_empty_optional` validator list.
  - In `_dispatch_configuration`, extend the dispatch check with `and self.submission_namespace` and change the message to `"Dispatch requires validator hotkey, judge digest, submission namespace and Pylon identity credentials"`.
  - In the dispatch block, require `settings.submission_namespace is not None` and pass it to the `partial(freeze_via_pylon, …, settings.validator_hotkey, settings.submission_namespace)`.

- [ ] **Step 5: Run Verify.** Fix every remaining caller found with `grep -rn "discover(\|freeze_discovery(\|decode_reference(" validator`. The same grep over `localnet` also has hits (`check_submissions.py`, `check_tasks.py`, `check_adversarial.py`); those scripts are outside the validator's test and type-check scope and are converted in Task 10.

- [ ] **Step 6: Commit.** `git add validator && git commit -m "Discover FactoryHorde envelopes under a configured namespace"`

---

### Task 8: FactoryHorde monitoring reads its own mechanism

**Goal:** `read_chain_view` requests neurons for `MECHANISM_ID`, so `chain_validator_last_update_block` and `chain_incentive` report FactoryHorde's mechanism. Validator and miner Compose files pin the Pylon service release that supports it.

**Files:**
- Modify: `validator/pyproject.toml`, `validator/uv.lock`
- Modify: `validator/src/validator/monitoring.py:74-96`, `validator/src/validator/main.py:159`
- Modify: `envs/deployed/docker-compose.yml:4-5`, `miner/docker-compose.yml:6-7`, then regenerate the release manifest
- Test: `validator/tests/test_monitoring.py`

**Acceptance Criteria:**
- [ ] `uv tree --project validator | grep bittensor-pylon-client` shows the new client version.
- [ ] A test with a fake `PylonClient` asserts that `read_chain_view(config, 2, VALIDATOR, MechanismId(1))` calls `get_latest_neurons(mechanism_id=1)` and maps the returned incentive and last update.
- [ ] `main.py` passes `settings.mechanism_id`.
- [ ] Both Compose files pin the new Pylon service digest, with the version in the comment.
- [ ] `release.py manifest --check` passes.

**Verify:** `cd validator && uv run pytest -q && uv run basedpyright` → pass. Then `uv run --project validator python installer/release.py manifest "$PWD" --check` → pass.

**Steps:**

- [ ] **Step 1: Dependency.** Use the client version recorded in Task 3, written `<client version>` below. Set `"bittensor-pylon-client==<client version>"` in `validator/pyproject.toml` dependencies. Add to `[tool.uv]`:

```toml
# Nexus d54e268 pins bittensor-pylon-client==2.3.1; the newer client is additive (mechanism-aware neuron reads).
override-dependencies = ["bittensor-pylon-client==<client version>"]
```

  Add `bittensor-pylon-client = "P1D"` to `exclude-newer-package` (as `miner/pyproject.toml` does), then run `cd validator && uv lock && uv sync`. The miner keeps 2.3.1; it does not read neurons by mechanism.

- [ ] **Step 2: Failing test.** Add to `validator/tests/test_monitoring.py`:

```python
from pylon_client.artanis import MechanismId

from validator import monitoring


def test_chain_view_reads_the_configured_mechanism(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    neuron = NEURON.model_copy(update={"incentive": 0.25, "last_update": 77, "validator_permit": True})

    class FakeIdentity:
        netuid = 2

        def get_latest_neurons(self, *, mechanism_id: MechanismId) -> GetNeuronsResponse:
            calls.append(mechanism_id)
            return GetNeuronsResponse(block=BLOCK, mechanism_id=mechanism_id, neurons={neuron.hotkey: neuron})

    class FakeClient:
        def __init__(self, _config: object) -> None:
            self.v1 = SimpleNamespace(identity=FakeIdentity())

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(monitoring, "PylonClient", FakeClient)
    view = monitoring.read_chain_view(CONFIG, 2, str(neuron.hotkey), MechanismId(1))

    assert calls == [1]
    assert (view.permit, view.last_update, view.incentives) == (True, 77, {str(neuron.hotkey): 0.25})
```

  The test needs these imports and constants at the top of `validator/tests/test_monitoring.py` (`Path` and `pytest` are already imported there; move the `MechanismId` and `monitoring` imports shown above into the same import block):

```python
from types import SimpleNamespace

from pylon_client.artanis import BlockHash, BlockNumber, Config, MechanismId, PylonAuthToken
from pylon_client.artanis.v1 import Block, GetNeuronsResponse, Neuron

NEURON = Neuron.model_validate_json(
    (Path(__file__).resolve().parents[2] / "spec/fixtures/discovery-v1/neuron.json").read_bytes()
)
BLOCK = Block(number=BlockNumber(100), hash=BlockHash("0x" + "a" * 64))
CONFIG = Config(address="http://pylon:8000", open_access_token=PylonAuthToken("x"))
```

  Run `uv run pytest -q tests/test_monitoring.py -k mechanism`; expected: FAIL (`read_chain_view` takes three arguments).

- [ ] **Step 3: Implement.** In `monitoring.py`, give `read_chain_view` a fourth parameter, `mechanism_id: MechanismId`. Change the read to `response = client.v1.identity.get_latest_neurons(mechanism_id=mechanism_id)`, and update its docstring ("Read current neurons of the configured mechanism…"). In `main.py:159`, append `settings.mechanism_id` to the `partial(read_chain_view, …)` arguments. Update the `ChainView` docstring and the two gauge help strings: "Validator's last weight update on this mechanism", "Nonzero incentive on this mechanism".

- [ ] **Step 4: Pylon service image.** Use the service version and digest recorded in Task 3 (the digest can be read again with `docker buildx imagetools inspect docker.io/backenddevelopersltd/bittensor-pylon:<service version> --format '{{json .Manifest}}' | jq -r .digest`). Replace the digest and version comment in `envs/deployed/docker-compose.yml:4-5` and `miner/docker-compose.yml:6-7`. Run `uv run --project validator python installer/release.py manifest "$PWD"`, then the same command with `--check`.

- [ ] **Step 5: Docs.** In `AGENTS.md`'s Observability paragraph, change "per-miner incentive from a separate Nexus actor" to say the incentive and last update are read for `MECHANISM_ID` (requires Pylon service ≥ the new release).

- [ ] **Step 6: Run Verify and commit.** `git add validator envs miner/docker-compose.yml installer AGENTS.md && git commit -m "Read chain metrics for the configured mechanism"`

---

### Task 9: Configuration surfaces, demo miners and docs

**Goal:** The namespace reaches every deployed and local configuration, the demo miners call the submitter on every sync, and the docs describe the shared format, the size budget, lost updates and the rollout.

**Files:**
- Modify: `envs/deployed/docker-compose.yml` (validator environment, next to `VALIDATOR_JUDGE_IMAGE`)
- Modify: `miner/docker-compose.yml` (`submit.environment`)
- Modify: `refinery/validator.env.example`, `refinery/miner.env.example`, `localnet/.env.example`, `envs/candidate/localnet.env`
- Modify: `miner/README.md`, `validator/README.md`, `installer/README.md`, `refinery/README.md`, `localnet/README.md`, `AGENTS.md`, `subnet_design.md`, `docs/implementation-handoff.md`
- Regenerate: `installer/release-manifest.json` via `installer/release.py`
- Modify (other repo): `/home/jan/Projects/reef/factory-horde-demo-miners/server/docker-compose.yml`, `server/sync.sh`, `server/README.md`

**Acceptance Criteria:**
- [ ] `docker compose -f envs/deployed/docker-compose.yml config` with `SUBMISSION_NAMESPACE=ghcr.io/backend-developers-ltd` shows `VALIDATOR_SUBMISSION_NAMESPACE: ghcr.io/backend-developers-ltd` on the validator.
- [ ] `docker compose -f miner/docker-compose.yml --profile submit config` shows `SUBMISSION_NAMESPACE` on `submit`.
- [ ] `grep -L SUBMISSION_NAMESPACE refinery/validator.env.example refinery/miner.env.example localnet/.env.example envs/candidate/localnet.env` prints nothing.
- [ ] `uv run --project validator python installer/release.py manifest "$PWD" --check` passes.
- [ ] `miner/README.md` states the envelope format, the namespace rule, the 128-byte budget table, the ComputeHorde reserve (and its 999-per-class limit), and that the submitter must be scheduled because another writer can drop the envelope, with a cron line.
- [ ] `installer/README.md` has a "Second mechanism beside ComputeHorde" section listing: `MECHANISM_ID=1`, the shared validator hotkey with its own Pylon, the Pylon service release from Task 3 or newer, ComputeHorde's miner release first, `SUBMISSION_NAMESPACE` in `.env` before the update is applied, and scheduled submitters.
- [ ] In the demo miners repository, `grep -c 'state/\$miner.image' server/sync.sh` prints `0`, `bash -n server/sync.sh` passes, and `server/docker-compose.yml` passes `SUBMISSION_NAMESPACE: "ghcr.io/${IMAGE_NAMESPACE:?}"` to `submit`.

**Verify:** `SUBMISSION_NAMESPACE=ghcr.io/backend-developers-ltd docker compose -f envs/deployed/docker-compose.yml config 2>/dev/null | grep SUBMISSION_NAMESPACE && uv run --project validator python installer/release.py manifest "$PWD" --check` → prints the variable, and the check passes. (Supply whatever other required variables `config` demands from `refinery/validator.env.example` with `--env-file`.)

**Steps:**

- [ ] **Step 1: Compose.**
  - In `envs/deployed/docker-compose.yml`, under the validator's environment next to `VALIDATOR_JUDGE_IMAGE`, add `VALIDATOR_SUBMISSION_NAMESPACE: "${SUBMISSION_NAMESPACE:-}"`.
  - In `miner/docker-compose.yml`, under `submit.environment`, add `SUBMISSION_NAMESPACE: "${SUBMISSION_NAMESPACE:?}"`. Update its header comment: "publishes FACTORY_IMAGE as its FactoryHorde envelope".

- [ ] **Step 2: Environment examples.** Add this block to all four files: after the chain block in `refinery/validator.env.example` and `refinery/miner.env.example`, and directly after the `JUDGE_IMAGE` line in `localnet/.env.example` and `envs/candidate/localnet.env`:

```bash
# Registry owner whose packages hold FactoryHorde submissions; commitments carry only package and digest.
SUBMISSION_NAMESPACE=ghcr.io/backend-developers-ltd
```

  The installer copies unknown `.env` keys through unchanged (`installer/release.py`, `configuration` merges its defaults under the operator's values), so no installer change is needed.

- [ ] **Step 3: Docs.** Keep markdown paragraphs unwrapped (user preference).
  - **`miner/README.md`:**
    - Add `SUBMISSION_NAMESPACE` to the settings table: "Registry owner all submissions must be published under, for example `ghcr.io/backend-developers-ltd`".
    - Replace the "full digest reference committed" description with the envelope format, the namespace, the budget table from this plan and the coexistence rules (FactoryHorde rewrites only `<F:…>`; ComputeHorde rewrites only `<M:…>`; both keep the rest).
    - Replace the statement that the commitment stays on chain and nothing needs to keep submitting. The new text: the chain has no compare-and-set, so another writer of the same hotkey's commitment (ComputeHorde's miner) can drop the envelope from a stale read after the submitter has confirmed it. Schedule the submitter; a run that finds its envelope reports `unchanged` and writes nothing. Give this cron line: `*/5 * * * * cd $HOME/factory-horde/miner && docker compose run --rm -T submit >> submit.log 2>&1`.
    - State the shared quota: commitment writes of one hotkey are limited to about 31 per tempo across both mechanisms, which is why ComputeHorde's miner must be on the release that keeps `<F:…>` before the submitter is scheduled.
  - **`validator/README.md`:** in the sentence listing what dispatch requires (`VALIDATOR_HOTKEY`, `VALIDATOR_JUDGE_IMAGE`, the Pylon identity name/token), add `VALIDATOR_SUBMISSION_NAMESPACE`.
  - **`localnet/README.md`:** in the continuous-scheduling paragraph that names `VALIDATOR_DISPATCH_ENABLED`, `VALIDATOR_HOTKEY` and `JUDGE_IMAGE`, add `SUBMISSION_NAMESPACE`.
  - **`refinery/README.md`:** in the validator installation block, add `SUBMISSION_NAMESPACE` to the comment listing what to fill in. Add an upgrade note below it: an existing installation must have `SUBMISSION_NAMESPACE` in `~/factory-horde/.env` before its configuration branch moves to this release, because the validator refuses to start without it once dispatch is enabled and the updater does not roll back. Say the same for the miners' `.env`.
  - **`installer/README.md`:** add the section described in the acceptance criteria. Its upgrade item repeats the `.env` requirement above.
  - **`AGENTS.md`:** add one paragraph after the discovery description: commitments are tagged envelopes shared with ComputeHorde; FactoryHorde owns `<F:package@base64url-sha256>` under `VALIDATOR_SUBMISSION_NAMESPACE`, which dispatch requires; the submitter preserves all other bytes and a 33-byte manifest reserve; writers can overwrite each other's newest change from a stale read, so submitters are scheduled.
  - **`subnet_design.md`:** in the miner submitter row (line 15), say "commit its FactoryHorde envelope".
  - **`docs/implementation-handoff.md`:** add a short "Second-mechanism commitments" selected-revision paragraph covering the envelope, the namespace and the scheduled submitter.

- [ ] **Step 4: Release manifest.** `uv run --project validator python installer/release.py manifest "$PWD"`, then the same command with `--check`.

- [ ] **Step 5: Demo miners.** `server/sync.sh` currently skips the submitter whenever `state/<miner>.image` holds the newest image, so it would neither re-commit in the new format nor restore a dropped envelope.
  - Branch: `cd /home/jan/Projects/reef/factory-horde-demo-miners && git fetch origin && git checkout -b submission-namespace origin/master`.
  - In `server/docker-compose.yml`, add to `submit.environment`: `SUBMISSION_NAMESPACE: "ghcr.io/${IMAGE_NAMESPACE:?}"`. `IMAGE_NAMESPACE` is already in the server's `.env` (`sync.sh` builds the image names from it), so no new setting is needed.
  - In `server/sync.sh`, replace the header comment's first two lines with:

```bash
# Commits each miner's newest published factory image on chain. Meant for cron: every run asks the
# submitter, which reads the chain first and writes only when the miner's FactoryHorde envelope is
# missing or different. That also restores an envelope another writer of the commitment dropped.
```

  - In the loop, replace everything from the `image=` line to the end of the `if … fi` block with:

```bash
  image="ghcr.io/$repository@$digest"
  token_variable="${miner^^}_PYLON_TOKEN"
  if result=$(MINER="$miner" MINER_PYLON_TOKEN="${!token_variable}" \
    docker compose run --rm -T submit "$image" 2>&1 < /dev/null); then
    # Log only actual writes, so the log stays quiet while nothing changes.
    if [[ "$(tail -n 1 <<< "$result" | jq -r .outcome 2>/dev/null)" != unchanged ]]; then
      log "$miner: committed $image: $(tail -n 1 <<< "$result")"
    fi
  else
    log "$miner: commit failed for $image: $result"
    status=1
  fi
```

  - In `server/README.md`, update the "Operation" bullets: the log gets a line only when a commitment is written or something fails; delete the sentence about `state/<miner>.image` and the "Forcing a commit" bullet (every run already asks the submitter); add that the submitter needs a release with `SUBMISSION_NAMESPACE` support in `SUBMITTER_IMAGE`.
  - Check and commit: `bash -n server/sync.sh && git add server && git commit -m "Submit on every sync and pass the submission namespace"`.

- [ ] **Step 6: Commit (factory-horde).** `git add envs miner/docker-compose.yml miner/README.md validator/README.md refinery installer localnet/.env.example localnet/README.md AGENTS.md subnet_design.md docs/implementation-handoff.md && git commit -m "Configure and document the submission namespace"`

---

### Task 10: Localnet coexistence check

**Goal:** On isolated localnet, the real submitter container preserves a ComputeHorde-shaped commitment, discovery reads the image beside it, a ComputeHorde-style `<M:…>` rewrite leaves it discoverable, an envelope dropped by a stale write is restored by the next submitter run, and every localnet check that writes or decodes commitments uses the envelope format.

**Files:**
- Modify: `localnet/check_submissions.py`
- Modify: `localnet/check_tasks.py`, `localnet/check_rounds.py`, `localnet/check_behavioral.py`, `localnet/check_adversarial.py`

**Acceptance Criteria:**
- [ ] Evidence file `localnet/state/task6-submission-check.json` contains `coexistence.before == "<D:https://example.com/s><M:A3;S2>"`, `coexistence.after_submit == "<D:https://example.com/s><F:…><M:A3;S2>"` and `coexistence.discovered_image == factory`.
- [ ] After writing `<D:https://example.com/s><F:…><M:A4>` (what ComputeHorde's patched miner produces), a fresh discovery still yields `factory` for miner1.
- [ ] After writing `<D:https://example.com/s><M:A4>` (what a stale ComputeHorde write leaves), discovery rejects miner1 with `No FactoryHorde submission`, the next submitter run reports `published`, and the chain value is `<D:https://example.com/s><F:…><M:A4>` again. The evidence records it as `coexistence.after_lost_update` and `coexistence.after_repair`.
- [ ] `check_submissions` prints `PASS:` and its existing assertions still hold; `check_tasks` prints `PASS`.
- [ ] `grep -rn "freeze_discovery(\|decode_reference(\|submit(client\|VALIDATOR_JUDGE_IMAGE" localnet --include=*.py` shows a namespace argument or a `VALIDATOR_SUBMISSION_NAMESPACE` entry at every hit, and `check_adversarial.py` writes no bare image reference as a commitment.

**Verify:** Prepare localnet as in `localnet/README.md` (`localnet/prepare.sh`, Compose up, bootstrap), then `uv run --project validator python -m localnet.check_submissions` → `PASS: …`, `uv run --project validator python -m localnet.check_tasks` → PASS, and `uv run --project validator python -c "import localnet.check_adversarial, localnet.check_behavioral, localnet.check_rounds"` → no error.

**Steps:**

- [ ] **Step 1: Thread the namespace through `check_submissions.py`.**
  - After `factory, judge = …`, add:

```python
    namespace = factory.rsplit("/", 1)[0]
    if judge.rsplit("/", 1)[0] != namespace:
        raise RuntimeError("Localnet images must share one namespace")
```

  - Add `"--env", f"SUBMISSION_NAMESPACE={namespace}",` to the `docker run` arguments, next to `NETUID=2`.
  - Pass `namespace` as the last argument of every `freeze_discovery(...)` call.

- [ ] **Step 2: Add raw-commitment helpers** in `check_submissions.py`, below `publish`. They write through Pylon v1 `set_commitment`, as ComputeHorde's miner does. Add the imports `time`, and `CommitmentDataBytes` from `pylon_client.artanis`:

```python
    def raw(identity: str) -> PylonClient:
        return PylonClient(
            Config(
                address=runtime.pylon_address,
                identity_name=IdentityName(identity),
                identity_token=PylonAuthToken(config[f"{identity.upper()}_PYLON_TOKEN"] or ""),
            )
        )

    def chain_value(identity: str) -> str:
        with raw(identity) as client:
            return bytes.fromhex(client.v1.identity.get_own_commitment().commitment.removeprefix("0x")).decode()

    def write_raw(identity: str, value: str, settle_seconds: float = 150) -> None:
        with raw(identity) as client:
            client.v1.identity.set_commitment(CommitmentDataBytes(value.encode()))
        deadline = time.monotonic() + settle_seconds
        while chain_value(identity) != value:
            if time.monotonic() > deadline:
                raise RuntimeError(f"Raw commitment {value!r} never appeared")
            time.sleep(2)
```

  Consecutive writes need no pause: the chain has no per-hotkey write interval. Each run of this check spends five of miner1's roughly 31 commitment writes per tempo. If a write fails with `SpaceLimitExceeded` after many repeated runs, wait for the next tempo.

- [ ] **Step 3: Add the coexistence sequence** inside the existing validator `with PylonClient(...) as client:` block, before `snapshot_id = uuid4()`. It uses miner1 and ends with a commitment the existing assertions accept (miner1's image is `factory` again):

```python
        shield = "<D:https://example.com/s>"
        miner1 = identities["miner1"].hotkey
        seeded = f"{shield}<M:A3;S2>"
        write_raw("miner1", seeded)
        _ = publish("miner1", factory)
        after_submit = chain_value("miner1")
        envelope = after_submit.removeprefix(shield).removesuffix("<M:A3;S2>")
        if after_submit != f"{shield}{envelope}<M:A3;S2>" or not envelope.startswith("<F:"):
            raise RuntimeError(f"Submitter changed foreign content: {after_submit!r}")
        # ComputeHorde's patched miner rewrites only its manifest.
        compute_horde_rewrite = f"{shield}{envelope}<M:A4>"
        write_raw("miner1", compute_horde_rewrite)
        coexist = freeze_discovery(files, uuid4(), client.v1.identity, 2, own_hotkey, namespace)
        discovered = next((member.image for member in coexist.cohort if member.miner_hotkey == miner1), None)
        if discovered != factory:
            raise RuntimeError("Discovery missed the submission beside ComputeHorde content")
        # A ComputeHorde write computed from a stale read drops the envelope; the next submitter run restores it.
        lost_update = f"{shield}<M:A4>"
        write_raw("miner1", lost_update)
        dropped = freeze_discovery(files, uuid4(), client.v1.identity, 2, own_hotkey, namespace)
        if not any(r.miner_hotkey == miner1 and "No FactoryHorde submission" in r.reason for r in dropped.rejected):
            raise RuntimeError("A commitment without an envelope was not rejected")
        repair = publish("miner1", factory)
        after_repair = chain_value("miner1")
        if repair.outcome != "published" or after_repair != compute_horde_rewrite:
            raise RuntimeError(f"Submitter did not restore its envelope: {after_repair!r}")
```

  Add the evidence entry:

```python
        "coexistence": {
            "before": seeded,
            "after_submit": after_submit,
            "compute_horde_rewrite": compute_horde_rewrite,
            "discovered_image": discovered,
            "after_lost_update": lost_update,
            "after_repair": after_repair,
        },
```

  Miner1 keeps the ComputeHorde-shaped commitment afterwards, so the later round checks also run beside foreign content. On a rerun, miner1's first `publish` reports `unchanged`, which the existing assertions allow.

- [ ] **Step 4: Convert the other discovery callers.**
  - `localnet/check_tasks.py:50`: pass `str(images["GHCR_FACTORY_IMAGE"]).rsplit("/", 1)[0]` as the last argument of `freeze_discovery(...)`.
  - `localnet/check_rounds.py`: next to `"VALIDATOR_JUDGE_IMAGE"`, add `"VALIDATOR_SUBMISSION_NAMESPACE": (images["GHCR_FACTORY_IMAGE"] or "").rsplit("/", 1)[0],`.
  - `localnet/check_behavioral.py`: before the loop over `miners`, add `namespace = args.factory_image.rsplit("/", 1)[0]`; change the call to `submit(client.v1.identity, args.factory_image, 2, namespace)`; next to `"VALIDATOR_JUDGE_IMAGE": judge,` add `"VALIDATOR_SUBMISSION_NAMESPACE": namespace,`.

- [ ] **Step 5: Convert `localnet/check_adversarial.py`.** It writes bare references as raw commitments and checks an overlong reference, both of which the envelope format changes.
  - Add the import `from miner.miner import encode_envelope` (the same import path `check_behavioral.py` uses for `submit`).
  - Add to `Adversarial`:

```python
    @property
    def namespace(self) -> str:
        """Registry owner shared by every published profile image."""
        return self.profiles["factory-valid"].image.rsplit("/", 1)[0]
```

  - In `commitments`, publish envelopes: `self.publish_bytes(miner.identity, encode_envelope(self.profiles[profile].image, self.namespace))`.
  - In `start_validator`, add `"VALIDATOR_SUBMISSION_NAMESPACE": self.namespace,` next to `"VALIDATOR_JUDGE_IMAGE"`.
  - In `rejected_commitments`, replace the three raw writes and the `overlong` value:

```python
        missing = f"{self.namespace}/factory-horde-fixture@sha256:{'0' * 64}"
        self.publish_bytes(self.miners[0].identity, encode_envelope(missing, self.namespace))
        self.publish_bytes(self.miners[1].identity, b"\xff")
        # A tag instead of a digest inside an otherwise well-formed envelope.
        self.publish_bytes(self.miners[2].identity, b"<F:factory-horde-fixture:latest>")
        # Its envelope is 108 bytes and can never fit beside ComputeHorde's 33-byte reserve.
        overlong = f"{self.namespace}/{'x' * 60}@sha256:{'0' * 64}"
```

  - In the submitter invocation below it, add `"--namespace", self.namespace,` after `"2",`. The submitter still fails before any network use with `128` in its message, now from `encode_envelope`.
  - Replace `decode_reference(overlong.encode().hex())` with `decode_reference((b"<F:" + b"x" * 126 + b">").hex(), self.namespace)`, a 130-byte commitment that discovery rejects with its 128-byte message.
  - `publish_bytes` and `cleanup` stay as they are: they save and restore whatever raw bytes were on chain.

- [ ] **Step 6: Run Verify**, keep `localnet/state/task6-submission-check.json`. `check_adversarial` and `check_behavioral` need the isolated systemd executor, the published fault-profile metadata and (for the behavioural check) the inference proxy described in `localnet/README.md`. If those are available on this VM, run `check_adversarial` as well, since its `rejected_commitments` scenario exercises the converted code, and keep its evidence. If they are not, do not claim it passes: state in the pull request that both scripts were converted and import-checked but not executed.

- [ ] **Step 7: Commit.** `git add localnet && git commit -m "Check commitment coexistence on localnet"`

---

### Task 11: Full verification, pull requests and rollout notes

**Goal:** All gates pass in FactoryHorde, ComputeHorde and the demo miners, pull requests are open against `master` for those three (Pylon's was merged in Task 3), and the FactoryHorde description states the rollout order.

**Files:**
- No new code. Pull request descriptions only.

**Acceptance Criteria:**
- [ ] `cd validator && uv run pytest -q`, `cd miner && uv run pytest -q`, `uv run ruff check`, `uv run ruff format --check` and `uv run basedpyright` in both FactoryHorde projects report no failures.
- [ ] `release.py manifest --check` passes.
- [ ] ComputeHorde `nox -s lint` in `miner/` and the two `-k` test selections from Task 4 pass.
- [ ] Pull requests are open against `master` for factory-horde (`second-mechanism-commitments`) and factory-horde-demo-miners (`submission-namespace`); ComputeHorde's (`preserve-factory-horde-commitment`) was merged as #878. Each branch was confirmed with `git branch --show-current` before pushing.
- [ ] The factory-horde description lists the rollout in this order: ComputeHorde miner release; `SUBMISSION_NAMESPACE` in every validator and miner `.env`; FactoryHorde validator and submitter release together with the demo miners' new `sync.sh`; mechanism count and emission split; validator with `MECHANISM_ID=1`; scheduled submitters.

**Verify:** `gh pr list --author @me --state open` in the factory-horde, ComputeHorde and factory-horde-demo-miners checkouts → one open pull request each for the three branches.

**Steps:**

- [ ] **Step 1:** Run every gate listed above and fix failures.
- [ ] **Step 2:** For each of the three repositories: `git branch --show-current` (must not be `master`), `git push -u origin <branch>`, then `gh pr create --base master` with a concise description of what the pull request does.
- [ ] **Step 3:** The factory-horde description additionally links the Task 10 evidence, says whether `check_adversarial` and `check_behavioral` were executed, and contains this rollout list:
  1. The Pylon service and client with per-mechanism neuron reads are released (versions from Task 3).
  2. Release ComputeHorde's miner change and wait until miners run it. Before that, a ComputeHorde miner erases `<F:…>` within 5 minutes, and a scheduled submitter fighting it can exhaust the hotkey's commitment quota.
  3. Add `SUBMISSION_NAMESPACE` to the `.env` of every validator installation and every miner, starting with the Refinery validator and the demo miner host. A validator with dispatch enabled does not start without it, and the updater does not roll back.
  4. Release the FactoryHorde validator, with the new Pylon service digest, and the submitter. Deploy the demo miners' new `sync.sh` and `SUBMITTER_IMAGE` at the same time; miners are left out of rounds until their submitter has run once in the new format.
  5. Set the subnet's mechanism count to 2 and the emission split.
  6. Run the FactoryHorde validator with `MECHANISM_ID=1`, the shared validator hotkey and its own Pylon.
  7. Every miner schedules the submitter. Commitment writes are last-write-wins, so a ComputeHorde write from a stale read can drop the envelope; the next scheduled run restores it.
- [ ] **Step 4:** Image builds for `deploy-build-*` are triggered by the user (pushing those branches is blocked for agents). State this in the factory-horde pull request.
