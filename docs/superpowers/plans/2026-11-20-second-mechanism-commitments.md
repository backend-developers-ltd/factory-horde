# FactoryHorde as a Second Mechanism beside ComputeHorde — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let FactoryHorde run as mechanism 1 on ComputeHorde's subnet with the same miner and validator hotkeys, so both mechanisms share one commitment per hotkey and each only ever rewrites its own part.

**Architecture:** The commitment becomes a sequence of tagged envelopes, extending the format ComputeHorde already uses (`<M:…>` manifest, legacy `<D:…>` shield). FactoryHorde owns `<F:package@digest>`, where the digest is base64url and the registry/owner namespace (for example `ghcr.io/backend-developers-ltd`) comes from configuration. FactoryHorde's submitter replaces only its own envelope and keeps every other byte. ComputeHorde's miner gets a one-line change so that its rebuild also keeps `<F:…>` envelopes. ComputeHorde's validator already ignores anything outside `<M:…>`.

Pylon's neuron reads also become mechanism-aware. Subtensor stores `Incentive` and `LastUpdate` per mechanism, but Pylon builds neurons from the `get_neurons_lite` runtime call, which only reads mechanism 0. An optional `mechanism_id` query parameter (default 0) overlays the requested mechanism's values. FactoryHorde's monitoring then requests `MECHANISM_ID`, so its incentive and last-update gauges stop showing ComputeHorde's values.

**Tech Stack:** Python 3.14, uv, pytest, Pylon client 2.3.x → next minor, Litestar (Pylon service), Nexus (FactoryHorde validator); Django/Celery, nox (ComputeHorde miner and Pylon).

---

## Background and decisions

**Why the envelope format.** Commitments are stored per (netuid, hotkey). Mechanisms have no separate slot. ComputeHorde's miner (`miner/app/src/compute_horde_miner/miner/manifest_commitment.py:43`) rebuilds the commitment every 5 minutes, keeps only `<D:…>` envelopes and appends a fresh `<M:…>`. ComputeHorde's validator (`compute_horde/compute_horde/manifest_utils.py`, `extract_manifest_payload`) searches for the first `<M:…>` anywhere and ignores the rest.

**Size budget.** Pylon writes a single `Raw` field, so the whole commitment is limited to 128 bytes. ComputeHorde's writer enforces the same limit.

| Part | Bytes |
|---|---|
| ComputeHorde manifest reserve: `<M:` + six classes as `X999`, joined by `;` + `>` | 33 |
| FactoryHorde envelope: `<F:` + package + `@` + 43-character base64url SHA-256 + `>` | 48 + package |
| `factory-horde-deceptive` (longest current baseline package, 23 bytes) | 71 |
| Remaining room for a legacy `<D:…>` shield envelope (worst current package) | 24 |

A hex digest would cost 21 more bytes, leaving almost no room for anything else, so FactoryHorde commits base64url. The current bt-ddos-shield no longer writes commitments (its manifest moved to `http://{axon}/shield_manifest.json`), but old `<D:…>` values may still exist on chain, and ComputeHorde keeps them.

The FactoryHorde writer guarantees ComputeHorde's room before writing. It refuses to write if every byte except `<M:…>` plus the 33-byte reserve would exceed 128. ComputeHorde never writes more than 128 bytes and refuses its own commit if it would. The reserve assumes at most 999 executors per class; that limitation is documented in the miner README.

**Ownership rules.**

- FactoryHorde rewrites only `<F:…>` envelopes. It keeps every other byte unchanged, including `<D:…>`, `<M:…>` and unrecognized data. A new envelope goes immediately before the first `<M:…>` (else at the end). That matches ComputeHorde's canonical order (preserved envelopes, then the manifest), so ComputeHorde sees no change and does no extra rewrite. If FactoryHorde cannot decode the current commitment, it refuses to write rather than risk destroying the other mechanism's data.
- ComputeHorde rewrites only `<M:…>`. It keeps `<D:…>` and `<F:…>`, and still discards non-envelope garbage, which is its existing cleanup behaviour.

**Concurrent writes.** Both writers read, modify, then write. Two writes to the same hotkey cannot both land within the commitments pallet's per-hotkey rate limit (about 100 blocks on mainnet configurations). The later write fails on chain and is retried on that writer's next run, with a fresh read. Both writers re-run periodically (ComputeHorde every 5 minutes; FactoryHorde miners through cron, such as the demo miners' `sync.sh`), so they converge. Task 6 measures the actual behaviour on localnet, and Task 7 records how to check the limit on the target chain.

**Namespace configuration.** All submissions must live under one configured namespace: `VALIDATOR_SUBMISSION_NAMESPACE` on the validator (Compose variable `SUBMISSION_NAMESPACE`) and `SUBMISSION_NAMESPACE` on the submitter. Images outside it are rejected. This restricts miners to packages the namespace owner publishes, which the user accepted.

**Rollout order.** ComputeHorde's miner change goes first; otherwise it erases `<F:…>` within 5 minutes. The FactoryHorde validator and submitter follow together. Legacy bare-reference commitments are not accepted: on Refinery the demo miners' 5-minute sync re-commits in the new format, so at most one round admission misses them.

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

**Out of scope.** Actual deployment to the user's subnet, setting the mechanism count or emission split, any turbobt change, the Nexus pin bump and publishing Pylon releases (maintainers run `rt release`). Per `AGENTS.md`, no active operator configuration is promoted by this plan.

## File structure

FactoryHorde (branch `second-mechanism-commitments`, from `master`):

- Modify `miner/miner.py`: envelope encoding, byte-preserving merge, namespace option.
- Modify `miner/tests/test_submission.py`.
- Modify `miner/docker-compose.yml`, `miner/README.md`.
- Modify `validator/src/validator/discovery.py`: envelope decoding with the namespace.
- Modify `validator/src/validator/main.py`: `submission_namespace` setting, required for dispatch.
- Modify `validator/tests/test_discovery.py`, `validator/tests/test_dependency_contract.py`, `validator/tests/test_main.py` (or wherever `Settings` is tested; find it with `grep -rln "Settings(" validator/tests`).
- Create `spec/fixtures/commitment-v2/envelopes.json`: shared cross-mechanism examples.
- Modify `envs/deployed/docker-compose.yml`, `installer/release-manifest` (regenerated), `refinery/validator.env.example`, `refinery/miner.env.example`, `installer/README.md`.
- Modify `localnet/check_submissions.py`, `localnet/check_tasks.py`, `localnet/check_rounds.py` and any other `freeze_discovery` / submitter call sites.
- Modify `AGENTS.md`, `subnet_design.md`, `docs/implementation-handoff.md`.

ComputeHorde (branch `preserve-factory-horde-commitment`, from `master`):

- Modify `miner/app/src/compute_horde_miner/miner/manifest_commitment.py:42-44`.
- Modify `miner/app/src/compute_horde_miner/miner/tests/test_manifest_commitment.py`.
- Modify `validator/app/src/compute_horde_validator/validator/allowance/tests/test_manifests.py` (test only).

factory-horde-demo-miners (branch `submission-namespace`):

- Modify `server/docker-compose.yml`: pass `SUBMISSION_NAMESPACE`.

bittensor-pylon (branch `mechanism-neuron-reads`, from `master`; paths relative to `/home/jan/Projects/reef/bittensor-pylon`):

- Modify `pylon_commons/pylon_commons/models.py`: `SubnetNeurons.mechanism_id`.
- Modify `pylon_commons/pylon_commons/exceptions.py`: `PylonMechanismMismatch`.
- Modify `pylon_commons/pylon_commons/_unstable/requests.py`: `mechanism_id` on `GetNeuronsRequest` and `GetLatestNeuronsRequest`.
- Modify `pylon_service/pylon_service/bittensor/contact.py`: `MechanismMetrics`, `get_mechanism_metrics` on the port, abstract client and turbobt contact.
- Modify `pylon_service/pylon_service/bittensor/contact_router.py`, `pylon_service/pylon_service/bittensor/mock_contact.py`.
- Modify `pylon_service/pylon_service/api/services.py` (`MechanismNotFoundError`), `pylon_service/pylon_service/api/utils.py` (404 mapping), `pylon_service/pylon_service/api/_unstable/services.py` (`NeuronService`), `pylon_service/pylon_service/api/_unstable/api.py` (query parameter).
- Modify `pylon_client/pylon_client/_internal/api/_unstable/translators.py`, `abstract_sync.py`, `abstract_async.py`, `_unstable/sync/api.py`, `_unstable/asynchronous/api.py`.
- Tests: `pylon_service/tests/unit/_unstable_identity_endpoints/test_get_neurons_mechanism.py` (new), `pylon_service/tests/unit/open_access_endpoints/test_get_neurons_mechanism.py` (new), `pylon_service/tests/integration/contact/test_reads.py`, `pylon_service/tests/pact/state_handlers.py`, `pylon_client/tests/unit/{synchronous,asynchronous}/identity/test_get_latest_neurons.py`, `pylon_client/tests/pact/{synchronous,asynchronous}/identity/test_get_latest_neurons.py`, the regenerated pact JSON files and snapshot files.
- Docs: `CLAUDE.md` (Neuron Data endpoints), `docs/SERVICE.md`, `docs/CLIENT.md`.

FactoryHorde monitoring (same worktree, after the Pylon release):

- Modify `validator/pyproject.toml`, `validator/uv.lock`, `validator/src/validator/monitoring.py:74-96`, `validator/src/validator/main.py:159`, `validator/tests/test_monitoring.py`, `envs/deployed/docker-compose.yml` and `miner/docker-compose.yml` (Pylon service digest).

---

### Task 1: ComputeHorde keeps FactoryHorde envelopes

**Goal:** ComputeHorde's miner preserves `<F:…>` envelopes when it rewrites its manifest, and tests show its validator still parses manifests next to them.

**Files:**
- Modify: `/home/jan/Projects/reef/ComputeHorde/miner/app/src/compute_horde_miner/miner/manifest_commitment.py:42-44`
- Test: `/home/jan/Projects/reef/ComputeHorde/miner/app/src/compute_horde_miner/miner/tests/test_manifest_commitment.py`
- Test: `/home/jan/Projects/reef/ComputeHorde/validator/app/src/compute_horde_validator/validator/allowance/tests/test_manifests.py`

**Acceptance Criteria:**
- [ ] With `<D:…><F:…><M:A2>` on chain and manifest `{A: 3}`, `set_commitment` is called with `<D:…><F:…><M:A3>`.
- [ ] With `<D:…><F:…><M:A3>` on chain and manifest `{A: 3}`, `set_commitment` is not called.
- [ ] `<M:A3><F:…>` (F after M) is rewritten to `<F:…><M:A3>`, with the F bytes unchanged.
- [ ] `parse_commitments_to_manifests({hk: b"<F:…><M:A5>"})` returns `{(hk, always_on__gpu_24gb): 5}`.
- [ ] All existing ComputeHorde miner and validator manifest tests pass unchanged.

**Verify:** `cd /home/jan/Projects/reef/ComputeHorde/miner && nox -s test -- compute_horde_miner/miner/tests/test_manifest_commitment.py` → all pass. Then the validator `test` session on `compute_horde_validator/validator/allowance/tests/test_manifests.py` → all pass.

**Steps:**

- [ ] **Step 1: Branch.** `cd /home/jan/Projects/reef/ComputeHorde && git checkout master && git pull && git checkout -b preserve-factory-horde-commitment`

- [ ] **Step 2: Write the failing miner tests.** Append to class `TestCommitManifest` in `test_manifest_commitment.py`:

```python
    FACTORY = "<F:factory-horde-factory@" + "A" * 43 + ">"

    @pytest.mark.parametrize("old_manifest", ["", "<M:A2>", "<M:A3>"])
    @pytest.mark.parametrize("shield", ["", "<D:https://example.com/s>"])
    def test_preserves_factory_horde_submission(self, mock_pylon_client, old_manifest, shield):
        current = shield + self.FACTORY + old_manifest
        mock_pylon_client.v1.identity.get_own_commitment.return_value = own_commitment(
            commitment=pylon_hex(current)
        )
        expected = shield + self.FACTORY + "<M:A3>"

        assert commit_manifest({ExecutorClass.always_on__gpu_24gb: 3}, mock_pylon_client) is True
        if current == expected:
            mock_pylon_client.v1.identity.set_commitment.assert_not_called()
        else:
            mock_pylon_client.v1.identity.set_commitment.assert_called_once_with(
                expected.encode("utf-8")
            )

    def test_moves_factory_horde_submission_before_manifest(self, mock_pylon_client):
        mock_pylon_client.v1.identity.get_own_commitment.return_value = own_commitment(
            commitment=pylon_hex("<M:A3>" + self.FACTORY)
        )

        assert commit_manifest({ExecutorClass.always_on__gpu_24gb: 3}, mock_pylon_client) is True
        mock_pylon_client.v1.identity.set_commitment.assert_called_once_with(
            (self.FACTORY + "<M:A3>").encode("utf-8")
        )
```

- [ ] **Step 3: Run to see them fail.** `cd /home/jan/Projects/reef/ComputeHorde/miner && nox -s test -- compute_horde_miner/miner/tests/test_manifest_commitment.py -k factory_horde`. Expected: FAIL, because the F envelope is dropped from the written value.

- [ ] **Step 4: Implement.** In `manifest_commitment.py`, replace lines 42-44:

```python
        # Preserve shield and FactoryHorde (mechanism 1) envelopes, discarding old manifests and hex garbage.
        preserved = "".join(re.findall(r"<[DF]:[^<>]*>", current_commitment))
        merged_commitment = merge_manifest(preserved, new_manifest_string)
```

- [ ] **Step 5: Run the whole module.** Same command without `-k`. Expected: all pass, including `test_preserves_visible_shield` and `test_replaces_nested_commitment`.

- [ ] **Step 6: Validator compatibility test (test only).** Append to `TestParseCommitmentsToManifests` in `test_manifests.py`:

```python
    def test_parse_commitments_beside_factory_horde(self):
        commitments = {
            "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY": (
                b"<D:https://example.com/s><F:factory-horde-factory@" + b"A" * 43 + b"><M:A5>"
            ),
        }

        result = parse_commitments_to_manifests(commitments)

        assert result == {
            (
                "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
                ExecutorClass.always_on__gpu_24gb,
            ): 5
        }
```

Run the validator's `test` nox session for that file. Expected: PASS without any production change.

- [ ] **Step 7: Lint and commit.** Run `nox -s lint` in `miner/`, then:

```bash
git add miner/app/src/compute_horde_miner/miner/manifest_commitment.py miner/app/src/compute_horde_miner/miner/tests/test_manifest_commitment.py validator/app/src/compute_horde_validator/validator/allowance/tests/test_manifests.py
git commit -m "Preserve FactoryHorde commitment envelopes"
```

---

### Task 2: Shared commitment fixtures

**Goal:** One JSON file of canonical cross-mechanism commitments, used by both FactoryHorde test suites, so the miner and validator agree on the format.

**Files:**
- Create: `spec/fixtures/commitment-v2/envelopes.json`

**Acceptance Criteria:**
- [ ] The file parses as JSON with `namespace`, `valid` and `invalid` keys.
- [ ] Every `valid[].commitment` is at most 128 UTF-8 bytes.
- [ ] Every `valid[].image` reconstructs from `namespace` plus the envelope.

**Verify:** `uv run --project validator python -c "import json; d=json.load(open('spec/fixtures/commitment-v2/envelopes.json')); assert all(len(v['commitment'].encode())<=128 for v in d['valid']); print(len(d['valid']), len(d['invalid']))"` → `4 6`

**Steps:**

- [ ] **Step 1: Branch in a worktree.** The main checkout has an unrelated uncommitted change on `claude-budget-partial-windows`; leave it untouched. Run `cd /home/jan/Projects/reef/factory-horde && git fetch origin && git worktree add ../factory-horde-second-mechanism -b second-mechanism-commitments origin/master`. Copy this plan file into the worktree's `docs/superpowers/plans/` and work there (run `uv sync` in `validator/` and `miner/`). All later factory-horde paths are relative to that worktree.

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

### Task 3: Submitter writes only its own envelope

**Goal:** `miner/miner.py` encodes `<F:package@base64url>` under a configured namespace, merges it into the existing commitment without changing other bytes, keeps ComputeHorde's 33-byte reserve and confirms only its own envelope.

**Files:**
- Modify: `miner/miner.py`
- Test: `miner/tests/test_submission.py`

**Acceptance Criteria:**
- [ ] With `<D:x><M:A3;S2>` on chain, the write is `<D:x><F:…><M:A3;S2>`.
- [ ] With `<D:x><F:old><M:A3>` on chain, only the F envelope changes.
- [ ] With the same F already on chain, the outcome is `unchanged` and nothing is written.
- [ ] An image outside `SUBMISSION_NAMESPACE` fails before any read or write.
- [ ] If the non-`<M:…>` bytes plus 33 would exceed 128, the submitter raises `SubmissionError` and does not write.
- [ ] If the current commitment is undecodable hex, the submitter raises `SubmissionError` and does not write.
- [ ] Read-back succeeds when the F envelope matches, even if the `<M:…>` part changed meanwhile.
- [ ] The CLI requires `--namespace` / `SUBMISSION_NAMESPACE`.

**Verify:** `cd miner && uv run pytest -q && uv run ruff check . && uv run basedpyright` → all pass, 0 errors.

**Steps:**

- [ ] **Step 1: Write failing tests.** In `miner/tests/test_submission.py`:
  - Add `NAMESPACE = "ghcr.io/example"` and make `IMAGE` `"ghcr.io/example/factory@sha256:" + "ab" * 32`.
  - Let `FakeApi` accept an initial raw commitment.
  - Pass `NAMESPACE` to every `submit(...)` call.
  - Add these tests:

```python
import json
from pathlib import Path

from miner import (
    COMPUTE_HORDE_MANIFEST_RESERVE,
    decode_envelope,
    encode_envelope,
    merge_envelope,
)

FIXTURES = json.loads(
    (Path(__file__).resolve().parents[2] / "spec/fixtures/commitment-v2/envelopes.json").read_text()
)
ENVELOPE = encode_envelope(IMAGE, NAMESPACE)


def test_envelope_is_compact() -> None:
    assert ENVELOPE == b"<F:factory@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s>"


@pytest.mark.parametrize("case", FIXTURES["valid"])
def test_fixture_round_trip(case: dict[str, str]) -> None:
    assert decode_envelope(case["commitment"].encode(), FIXTURES["namespace"]) == case["image"]
    assert encode_envelope(case["image"], FIXTURES["namespace"]) in case["commitment"].encode()


@pytest.mark.parametrize("case", FIXTURES["invalid"])
def test_fixture_rejections(case: dict[str, str]) -> None:
    with pytest.raises(ValueError, match=case["reason"]):
        decode_envelope(case["commitment"].encode(), FIXTURES["namespace"])


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
    api.after_write = b"<M:A4>"  # FakeApi stores ENVELOPE + after_write instead of the write
    assert submit(api, IMAGE, 2, NAMESPACE).outcome == "published"
```

Extend `FakeApi` to match:
  - `__init__(self, initial: bytes = b"", initial_hex: str | None = None)`. The initial hex is `initial_hex`, or `initial.hex()` when non-empty; with neither, `get_own_commitment` raises `PylonNotFound` as today.
  - Count reads in `self.reads`, incremented in `get_own_commitment`.
  - Add an optional `after_write: bytes | None`. When set, `set_commitment` stores `ENVELOPE + after_write` as the chain value, simulating ComputeHorde rewriting `<M:…>` right after our write.

Update `test_exact_byte_boundary` and the bad-reference tests to the new API. The 128-byte limit now applies to the merged commitment, not to the image reference.

- [ ] **Step 2: Run to see them fail.** `cd miner && uv run pytest -q`. Expected: ImportError for `encode_envelope` and failures.

- [ ] **Step 3: Implement in `miner/miner.py`.** Add `import base64`. Replace the constants and the reference helpers with:

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
        ValueError: The image or namespace is invalid, or the image lies outside the namespace.
    """
    validate_reference(image)
    if re.fullmatch(NAMESPACE_PATTERN, namespace) is None:
        raise ValueError("Expected a ghcr.io/owner namespace")
    if not image.startswith(namespace + "/"):
        raise ValueError(f"Image must be published under namespace {namespace}")
    package, digest = image.removeprefix(namespace + "/").split("@sha256:")
    compact = base64.urlsafe_b64encode(bytes.fromhex(digest)).rstrip(b"=")
    return b"<F:" + package.encode() + b"@" + compact + b">"


def decode_envelope(commitment: bytes, namespace: str) -> str:
    """Return the image of the single FactoryHorde envelope in a raw commitment.

    Raises:
        ValueError: The envelope is missing, repeated or malformed.
    """
    payloads = OWN_ENVELOPE.findall(commitment)
    if not payloads:
        raise ValueError("No FactoryHorde submission")
    if len(payloads) > 1:
        raise ValueError("Multiple FactoryHorde submissions")
    match = re.fullmatch(rb"(" + SEGMENT.encode() + rb"(?:/" + SEGMENT.encode() + rb")*)@([A-Za-z0-9_-]{43})", payloads[0])
    if match is None:
        raise ValueError("Malformed FactoryHorde submission")
    digest = base64.urlsafe_b64decode(match[2] + b"=")
    if base64.urlsafe_b64encode(digest).rstrip(b"=") != match[2]:
        raise ValueError("Malformed FactoryHorde submission")
    return validate_reference(f"{namespace}/{match[1].decode()}@sha256:{digest.hex()}")


def merge_envelope(current: bytes, envelope: bytes) -> bytes:
    """Replace only FactoryHorde's envelope, keeping every other byte and ComputeHorde's manifest room.

    Raises:
        SubmissionError: The result would leave ComputeHorde less than its manifest reserve.
    """
    own = list(OWN_ENVELOPE.finditer(current))
    if own:
        tail = OWN_ENVELOPE.sub(b"", current[own[0].end() :])
        merged = current[: own[0].start()] + envelope + tail
    else:
        manifest = MANIFEST_ENVELOPE.search(current)
        at = manifest.start() if manifest is not None else len(current)
        merged = current[:at] + envelope + current[at:]
    if len(MANIFEST_ENVELOPE.sub(b"", merged)) + COMPUTE_HORDE_MANIFEST_RESERVE > COMMITMENT_MAX_BYTES:
        raise SubmissionError("Commitment would leave ComputeHorde less than its 33-byte manifest reserve")
    return merged


def _raw(value: str) -> bytes:
    if re.fullmatch(r"(?:0x)?(?:[0-9a-fA-F]{2})*", value) is None:
        raise ValueError("Malformed hexadecimal commitment")
    return bytes(CommitmentDataBytes.fromhex(value.removeprefix("0x")))
```

Delete `decode_reference`. Replace `_matches` with:

```python
def _matches(response: GetCommitmentResponse | None, envelope: bytes) -> bool:
    if response is None:
        return False
    try:
        return OWN_ENVELOPE.findall(_raw(response.commitment)) == [envelope[3:-1]]
    except ValueError:
        return False
```

In `submit(api, image, netuid, namespace, *, confirm_seconds=150)`:
  - Call `envelope = encode_envelope(image, namespace)` first, before any API call; it replaces `validate_reference(image)`.
  - Use `_matches(previous, envelope)`.
  - In the write branch, build the value from the current commitment:

```python
            else:
                try:
                    current = b"" if previous is None else _raw(previous.commitment)
                except ValueError as error:
                    raise SubmissionError("Cannot preserve an undecodable commitment; refusing to overwrite") from error
                merged = merge_envelope(current, envelope)
                outcome: Literal["published", "recovered"] = "published"
                try:
                    api.set_commitment(CommitmentDataBytes(merged))
                ...
                    if response is not None and _matches(response, envelope):
```

Update the module and `submit` docstrings ("Publish one FactoryHorde envelope…"; read first, replace only the own envelope, write at most once). Add `Publication.commitment_block` docs: it is the block of the commitment's last write by either mechanism.

In `main`, add the option `@click.option("--namespace", envvar="SUBMISSION_NAMESPACE", required=True)` and pass it to `encode_envelope(image, namespace)` (replacing the early `validate_reference(image)`) and to `submit(...)`.

- [ ] **Step 4: Run Verify.** Expected: all pass.

- [ ] **Step 5: Commit.** `git add miner/miner.py miner/tests/test_submission.py && git commit -m "Write only FactoryHorde's commitment envelope"`

---

### Task 4: Validator discovers envelopes under the configured namespace

**Goal:** Discovery reads the single `<F:…>` envelope of each commitment and rebuilds the image under `VALIDATOR_SUBMISSION_NAMESPACE`, which dispatch requires.

**Files:**
- Modify: `validator/src/validator/discovery.py`
- Modify: `validator/src/validator/main.py:50-125,168-175`
- Test: `validator/tests/test_discovery.py`, `validator/tests/test_dependency_contract.py`, and the `Settings` test module

**Acceptance Criteria:**
- [ ] Every `valid` fixture yields a cohort member with the fixture's `image`.
- [ ] Every `invalid` fixture yields a `RejectedSubmission` whose `reason` contains the fixture's `reason`.
- [ ] `Settings(dispatch_enabled=True, …)` without `submission_namespace` raises a validation error naming the namespace.
- [ ] `test_published_baseline_references_fit_a_miner_commitment` asserts envelope + 33 ≤ 128 for every baseline kind.

**Verify:** `cd validator && uv run pytest -q && uv run ruff check . && uv run basedpyright` → all pass, 0 errors.

**Steps:**

- [ ] **Step 1: Write failing tests.** In `validator/tests/test_discovery.py`:
  - Load the fixture JSON (path `Path(__file__).resolve().parents[2] / "spec/fixtures/commitment-v2/envelopes.json"`).
  - Add `NAMESPACE = "ghcr.io/example"` and set `IMAGE = "ghcr.io/example/factory@sha256:" + "ab" * 32`.
  - Store the default fake commitment as `"<F:factory@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s>".encode().hex()`.
  - Pass `NAMESPACE` as the new fourth positional argument to every `discover(...)`/`freeze_discovery(...)` call (after `validator_hotkey`; `allowed_hotkeys` moves to fifth).
  - Change `decode_reference(value)` tests to `decode_reference(value, NAMESPACE)`.
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

Add a `Settings` test, next to the existing dispatch-validation test (`grep -rn "Dispatch requires" validator/tests`):

```python
def test_dispatch_requires_submission_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    # reuse the existing dispatch-enabled environment fixture, omitting VALIDATOR_SUBMISSION_NAMESPACE
    with pytest.raises(ValidationError, match="submission namespace"):
        Settings(**dispatch_settings_without_namespace)
```

Build `dispatch_settings_without_namespace` from the existing valid dispatch settings in that module, minus the new field.

- [ ] **Step 2: Run to see them fail.** `cd validator && uv run pytest -q tests/test_discovery.py tests/test_dependency_contract.py`

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

- [ ] **Step 5: Run Verify.** Fix every remaining caller found with `grep -rn "discover(\|freeze_discovery(\|decode_reference(" validator`.

- [ ] **Step 6: Commit.** `git add validator && git commit -m "Discover FactoryHorde envelopes under a configured namespace"`

---

### Task 5: Configuration surfaces and docs

**Goal:** The namespace reaches the deployed validator, the miner's Compose submitter, the Refinery examples and the demo miners, and the docs describe the shared format, the size budget and the rollout.

**Files:**
- Modify: `envs/deployed/docker-compose.yml` (validator environment, next to `VALIDATOR_JUDGE_IMAGE`)
- Modify: `miner/docker-compose.yml` (`submit.environment`)
- Modify: `refinery/validator.env.example`, `refinery/miner.env.example`
- Modify: `installer/README.md`, `miner/README.md`, `AGENTS.md`, `subnet_design.md`, `docs/implementation-handoff.md`
- Regenerate: the release manifest via `installer/release.py`
- Modify (other repo): `/home/jan/Projects/reef/factory-horde-demo-miners/server/docker-compose.yml`

**Acceptance Criteria:**
- [ ] `docker compose -f envs/deployed/docker-compose.yml config` with `SUBMISSION_NAMESPACE=ghcr.io/backend-developers-ltd` shows `VALIDATOR_SUBMISSION_NAMESPACE: ghcr.io/backend-developers-ltd` on the validator.
- [ ] `docker compose -f miner/docker-compose.yml --profile submit config` shows `SUBMISSION_NAMESPACE` on `submit`.
- [ ] `uv run --project validator python installer/release.py manifest "$PWD" --check` passes.
- [ ] `miner/README.md` states the envelope format, the namespace rule, the 128-byte budget table and the ComputeHorde reserve (and its 999-per-class limit).
- [ ] `installer/README.md` has a "Second mechanism beside ComputeHorde" section listing: `MECHANISM_ID=1`, the shared validator hotkey with its own Pylon, ComputeHorde's miner release first, and the rate-limit check from Task 7.

**Verify:** `cd /home/jan/Projects/reef/factory-horde && SUBMISSION_NAMESPACE=ghcr.io/backend-developers-ltd docker compose -f envs/deployed/docker-compose.yml config 2>/dev/null | grep SUBMISSION_NAMESPACE && uv run --project validator python installer/release.py manifest "$PWD" --check` → prints the variable, and the check passes. (Supply whatever other required variables `config` demands from `refinery/validator.env.example` with `--env-file`.)

**Steps:**

- [ ] **Step 1: Compose.**
  - In `envs/deployed/docker-compose.yml`, under the validator's environment next to `VALIDATOR_JUDGE_IMAGE`, add `VALIDATOR_SUBMISSION_NAMESPACE: "${SUBMISSION_NAMESPACE:-}"`.
  - In `miner/docker-compose.yml`, under `submit.environment`, add `SUBMISSION_NAMESPACE: "${SUBMISSION_NAMESPACE:?}"`. Update its header comment: "publishes FACTORY_IMAGE as its FactoryHorde envelope".

- [ ] **Step 2: Examples.**
  - In both Refinery examples, add after the chain block:

```bash
# Registry owner whose packages hold FactoryHorde submissions; commitments carry only package and digest.
SUBMISSION_NAMESPACE=ghcr.io/backend-developers-ltd
```

  - Ensure `installer/install.sh`/`update_compose.sh` pass unknown `.env` keys through; check with `grep -n "JUDGE_IMAGE" installer/*.sh installer/release.py`. If they whitelist keys, add `SUBMISSION_NAMESPACE` the same way `JUDGE_IMAGE` is handled.

- [ ] **Step 3: Docs.**
  - **`miner/README.md`:** replace the "full digest reference committed" description with the envelope format, the namespace, the budget table from this plan and the coexistence rules (FactoryHorde rewrites only `<F:…>`; ComputeHorde rewrites only `<M:…>`; both keep the rest).
  - **`AGENTS.md`:** add one paragraph after the discovery description: commitments are tagged envelopes shared with ComputeHorde; FactoryHorde owns `<F:package@base64url-sha256>` under `VALIDATOR_SUBMISSION_NAMESPACE`; the submitter preserves all other bytes and a 33-byte manifest reserve.
  - **`subnet_design.md`:** in the miner submitter row (line 15), say "commit its FactoryHorde envelope".
  - **`docs/implementation-handoff.md`:** add a short "Second-mechanism commitments" selected-revision paragraph.
  - **`installer/README.md`:** add the section described in the acceptance criteria.
  - Keep markdown paragraphs unwrapped (user preference).

- [ ] **Step 4: Release manifest.** `uv run --project validator python installer/release.py manifest "$PWD"`, then `--check`.

- [ ] **Step 5: Demo miners.** In `/home/jan/Projects/reef/factory-horde-demo-miners`:
  - Branch `submission-namespace`.
  - Add `SUBMISSION_NAMESPACE: "${SUBMISSION_NAMESPACE:?}"` to `server/docker-compose.yml` `submit.environment`.
  - Document it in `server/README.md`'s `.env` section.
  - Commit `Pass the FactoryHorde submission namespace`.

- [ ] **Step 6: Commit (factory-horde).** `git add envs miner/docker-compose.yml miner/README.md refinery installer AGENTS.md subnet_design.md docs/implementation-handoff.md && git commit -m "Configure and document the submission namespace"`

---

### Task 6: Localnet coexistence check

**Goal:** On isolated localnet, the real submitter container preserves a ComputeHorde-shaped commitment, discovery reads the image beside it, a later ComputeHorde-style `<M:…>` rewrite leaves it discoverable, and the observed back-to-back write behaviour is recorded.

**Files:**
- Modify: `localnet/check_submissions.py`
- Modify: `localnet/check_tasks.py`, `localnet/check_rounds.py`, plus every hit of `grep -rn 'freeze_discovery(\|runtime.image("submitter")\|VALIDATOR_JUDGE_IMAGE' localnet`

**Acceptance Criteria:**
- [ ] Evidence file `localnet/state/task6-submission-check.json` contains `coexistence.before == "<D:https://example.com/s><M:A3;S2>"`, `coexistence.after_submit == "<D:https://example.com/s><F:…><M:A3;S2>"`, and `coexistence.discovered_image == factory`.
- [ ] After writing `<D:https://example.com/s><F:…><M:A4>` (what ComputeHorde's patched miner produces), a fresh discovery still yields `factory` for miner1.
- [ ] The evidence records `back_to_back_write_rejected: true|false`, from a second raw write issued immediately after the first.
- [ ] The check prints `PASS:` and existing assertions still hold.

**Verify:** Prepare localnet as in `localnet/README.md` (`localnet/prepare.sh`, Compose up, bootstrap), then `uv run --project validator python -m localnet.check_submissions` → `PASS: …`. Run `uv run --project validator python -m localnet.check_tasks` as well → PASS.

**Steps:**

- [ ] **Step 1: Thread the namespace.**
  - In `check_submissions.py`, set `namespace = factory.rsplit("/", 1)[0]` (localnet's published images share one owner). If `judge.rsplit("/", 1)[0] != namespace`, fail with `RuntimeError("Localnet images must share one namespace")`.
  - Add `"--env", f"SUBMISSION_NAMESPACE={namespace}",` to the `docker run` arguments.
  - Pass `namespace` to every `freeze_discovery(...)`.
  - Do the same for the other call sites from the grep. In `check_rounds.py`, add `"VALIDATOR_SUBMISSION_NAMESPACE": namespace` next to `VALIDATOR_JUDGE_IMAGE`.

- [ ] **Step 2: Add the coexistence step** in `check_submissions.py`, after `unauthorized` and before discovery, using miner1's own identity. Write raw commitments through Pylon v1 `set_commitment`, as ComputeHorde's miner does:

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

    seeded = "<D:https://example.com/s><M:A3;S2>"
    write_raw("miner1", seeded)
    merged = publish("miner1", factory)
    after_submit = chain_value("miner1")
    if not (after_submit.startswith("<D:https://example.com/s><F:") and after_submit.endswith("><M:A3;S2>")):
        raise RuntimeError(f"Submitter changed foreign content: {after_submit!r}")
    compute_horde_rewrite = after_submit.replace("<M:A3;S2>", "<M:A4>")
    write_raw("miner1", compute_horde_rewrite)
```

Each `write_raw` waits for read-back, so a rate-limited write shows up as a timeout. If localnet rejects writes this close together, wait for its configured interval before `write_raw` (read it from the chain error Pylon logs) and record the wait in the evidence.

- [ ] **Step 3: Probe back-to-back writes.** Immediately after the rewrite, issue a second write without waiting, and record whether the chain accepts it:

```python
    with raw("miner1") as client:
        client.v1.identity.set_commitment(CommitmentDataBytes((compute_horde_rewrite + " ").encode()))
    time.sleep(30)
    back_to_back_rejected = chain_value("miner1") == compute_horde_rewrite
    if not back_to_back_rejected:
        write_raw("miner1", compute_horde_rewrite)
```

- [ ] **Step 4: Assert discovery beside ComputeHorde content.** Inside the existing validator `PylonClient` block, after `before`, add a fresh `coexist = freeze_discovery(files, uuid4(), client.v1.identity, 2, own_hotkey, namespace)` and require miner1's member image to equal `factory`. At the very end, restore miner1 to its plain envelope, so later checks see what they did before: `write_raw("miner1", re.search(r"<F:[^<>]*>", after_submit)[0])` (add `import re`). Add the evidence entry:

```python
        "coexistence": {
            "before": seeded,
            "after_submit": after_submit,
            "compute_horde_rewrite": compute_horde_rewrite,
            "discovered_image": next(m.image for m in coexist.cohort if m.miner_hotkey == identities["miner1"].hotkey),
            "back_to_back_write_rejected": back_to_back_rejected,
        },
```

Add imports: `time`, and `CommitmentDataBytes` from `pylon_client.artanis`.

- [ ] **Step 5: Run Verify**, keep `localnet/state/task6-submission-check.json`, and commit: `git add localnet && git commit -m "Check commitment coexistence on localnet"`

---

### Task 7: Pylon service returns per-mechanism incentive and last update

**Goal:** Pylon's neuron endpoints (open access and identity, latest and at-block, `_unstable` and `v1`) accept an optional `mechanism_id` query parameter, default 0, and return that mechanism's `incentive` and `last_update`.

**Files:**
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

**Verify:** `cd pylon_service && nox -s test` → all pass. Then `nox -s test-integration-contact -- -k mechanism_metrics` → the new integration test passes (needs Docker for the local chain).

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

Create the open-access twin `pylon_service/tests/unit/open_access_endpoints/test_get_neurons_mechanism.py`. Copy the existing open-access neuron test's client fixture and URL (`grep -n "block/latest/neurons" pylon_service/tests/unit/open_access_endpoints/*.py`), and include the default and overlay cases above with `mock_bt_client_factory` for the open-access client. If `NeuronFactory.build` does not accept these keyword overrides, use the factory's field names from `pylon_service/tests/factories.py`.

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

### Task 8: Pylon client requests a mechanism's neurons

**Goal:** `get_neurons` and `get_latest_neurons` (open access and identity, sync and async, `unstable` and `v1`) take a keyword-only `mechanism_id` (default 0). The client sends it only when non-zero and raises `PylonMechanismMismatch` when the service answers for a different mechanism.

**Files:**
- Modify: `pylon_commons/pylon_commons/_unstable/requests.py:88-104`, `pylon_commons/pylon_commons/exceptions.py`
- Modify: `pylon_client/pylon_client/_internal/api/_unstable/translators.py:75-87`
- Modify: `pylon_client/pylon_client/_internal/api/abstract_sync.py`, `abstract_async.py` (open-access `get_neurons`/`get_latest_neurons` near lines 130-155, identity ones near lines 506-528, abstract builders near line 380)
- Modify: `pylon_client/pylon_client/_internal/api/_unstable/sync/api.py:54-61,135-146`, `_unstable/asynchronous/api.py` (same builders)
- Test: `pylon_client/tests/unit/{synchronous,asynchronous}/identity/test_get_latest_neurons.py`, `pylon_client/tests/pact/{synchronous,asynchronous}/identity/test_get_latest_neurons.py`, `pylon_service/tests/pact/state_handlers.py`, regenerated `pylon_client/tests/pact/pacts/*.json`
- Docs: `docs/CLIENT.md`

**Acceptance Criteria:**
- [ ] `client.unstable.identity.get_latest_neurons()` sends no query string (the existing pact interactions are unchanged).
- [ ] `client.unstable.identity.get_latest_neurons(mechanism_id=MechanismId(1))` sends `?mechanism_id=1` and returns the response.
- [ ] A response with `mechanism_id` 0 to a mechanism-1 request raises `PylonMechanismMismatch` (an old service ignored the parameter).
- [ ] A file-backed `neurons_file` serves only mechanism 0; other mechanisms go to the service.
- [ ] Client pact tests generate the new interaction and the service pact verification (`nox -s test-pact` in `pylon_service`) passes.

**Verify:** `cd pylon_client && nox -s test-3.14` → pass. Then `cd .. && nox -s test-pact` → pass.

**Steps:**

- [ ] **Step 1: Failing unit test.** Add to `pylon_client/tests/unit/synchronous/identity/test_get_latest_neurons.py`, following the file's existing mock-transport setup (reuse its fixtures for the logged-in client and response body). Replace `respond_with`/`requests` with whatever the file uses to stub HTTP and to inspect the sent request:

```python
def test_get_latest_neurons_for_mechanism_sends_query_and_checks_answer(...):
    # stub: respond with the file's existing neurons body plus "mechanism_id": 1
    response = client.unstable.identity.get_latest_neurons(mechanism_id=MechanismId(1))
    assert requests[-1].url.params["mechanism_id"] == "1"
    assert response.mechanism_id == 1


def test_get_latest_neurons_rejects_service_without_mechanism_support(...):
    # stub: respond with the existing neurons body unchanged (no mechanism_id, so 0)
    with pytest.raises(PylonMechanismMismatch):
        client.unstable.identity.get_latest_neurons(mechanism_id=MechanismId(1))


def test_get_latest_neurons_default_sends_no_query(...):
    client.unstable.identity.get_latest_neurons()
    assert "mechanism_id" not in requests[-1].url.params
```

Mirror the three tests in the asynchronous twin. Run `cd pylon_client && nox -s test-3.14 -- tests/unit -k mechanism`. Expected: FAIL (unexpected keyword `mechanism_id`).

- [ ] **Step 2: Requests and exception.** In `pylon_commons/pylon_commons/_unstable/requests.py`, add `mechanism_id: MechanismId = MechanismId(0)` to `GetNeuronsRequest` and `GetLatestNeuronsRequest`. In `pylon_commons/pylon_commons/exceptions.py`:

```python
class PylonMechanismMismatch(BasePylonException):
    """
    Error raised when the service answers a neuron request for a different mechanism, e.g. a service without mechanism support.
    """
```

Export it wherever `PylonMisconfigured` is exported (`grep -rn "PylonMisconfigured" pylon_client/pylon_client --include=__init__.py`).

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
  - In `pylon_service/tests/pact/state_handlers.py`, add:

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

  - In both client pact files `pylon_client/tests/pact/{synchronous,asynchronous}/identity/test_get_latest_neurons.py`, add an interaction copied from `test_get_latest_neurons_success` with:
    - `upon_receiving("an identity request for latest neurons of mechanism 1")`
    - `.given("neurons exist for mechanism", identity_name=IDENTITY_NAME, netuid=NETUID, neuron_count=2)`
    - `.with_query_parameters({"mechanism_id": "1"})`
    - a body matcher whose `mechanism_id` is 1 and whose neurons have `incentive` 0.0 and `last_update` 0
    - the call `get_latest_neurons(mechanism_id=MechanismId(1))`
  - Add `mechanism_id` (0) to the shared `get_neurons_response_matcher` fixture so existing interactions describe the new field.
  - Regenerate the pacts by running the client pact tests, then run `nox -s test-pact` from the repository root.

- [ ] **Step 6: Docs.** In `docs/CLIENT.md`, document `mechanism_id` on the neuron methods, the version requirement (service at or above the release from Task 7) and `PylonMechanismMismatch`.

- [ ] **Step 7: Format, run Verify, commit.**

```bash
nox -s format
git add pylon_commons pylon_client pylon_service/tests/pact docs/CLIENT.md
git commit -m "feat: request neuron incentive and last update per mechanism" -m "Impacts: client, commons"
```

- [ ] **Step 8: PR and release hand-off.** `git branch --show-current` (must be `mechanism-neuron-reads`), `git push -u origin mechanism-neuron-reads`, `gh pr create --base master`. The description should say what the PR adds, the default-0 compatibility guarantee and the old-service guard. Releases are cut by a maintainer (`cd pylon_service && rt release`, `cd pylon_client && rt release`). Task 9 needs the published versions and the service image digest.

---

### Task 9: FactoryHorde monitoring reads its own mechanism

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

- [ ] **Step 1: Dependency.** After the Task 8 client release is on PyPI (version `X.Y.Z`, the next minor after 2.3.1), set `"bittensor-pylon-client==X.Y.Z"` in `validator/pyproject.toml` dependencies. Add to `[tool.uv]`:

```toml
# Nexus d54e268 pins bittensor-pylon-client==2.3.1; the newer client is additive (mechanism-aware neuron reads).
override-dependencies = ["bittensor-pylon-client==X.Y.Z"]
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

  Define `NEURON`, `BLOCK` and `CONFIG` in the test module from `spec/fixtures/discovery-v1/neuron.json` (as `test_discovery.py` does) and a minimal `Config(address="http://pylon:8000", open_access_token=PylonAuthToken("x"))`. Import `SimpleNamespace` and `GetNeuronsResponse`. Run `uv run pytest -q tests/test_monitoring.py -k mechanism`; expected: FAIL (unexpected argument).

- [ ] **Step 3: Implement.** In `monitoring.py`, give `read_chain_view` a fourth parameter, `mechanism_id: MechanismId`. Change the read to `response = client.v1.identity.get_latest_neurons(mechanism_id=mechanism_id)`, and update its docstring ("Read current neurons of the configured mechanism…"). In `main.py:159`, append `settings.mechanism_id` to the `partial(read_chain_view, …)` arguments. Update the `ChainView` docstring and the two gauge help strings: "Validator's last weight update on this mechanism", "Nonzero incentive on this mechanism".

- [ ] **Step 4: Pylon service image.** Resolve the new service digest: `docker buildx imagetools inspect docker.io/backenddevelopersltd/bittensor-pylon:<service version> --format '{{json .Manifest}}' | jq -r .digest`. Replace the digest and version comment in `envs/deployed/docker-compose.yml:4-5` and `miner/docker-compose.yml:6-7`. Run `uv run --project validator python installer/release.py manifest "$PWD"`, then the same command with `--check`.

- [ ] **Step 5: Docs.** In `AGENTS.md`'s Observability paragraph, change "per-miner incentive from a separate Nexus actor" to say the incentive and last update are read for `MECHANISM_ID` (requires Pylon service ≥ the new release).

- [ ] **Step 6: Run Verify and commit.** `git add validator envs miner/docker-compose.yml installer AGENTS.md && git commit -m "Read chain metrics for the configured mechanism"`

---

### Task 10: Full verification, PRs and rollout notes

**Goal:** All gates pass in the four repositories, PRs are open against `master`, and the PR descriptions state the rollout order and the target-chain rate-limit check.

**Files:**
- No new code. PR descriptions only.

**Acceptance Criteria:**
- [ ] `cd validator && uv run pytest -q`, `cd miner && uv run pytest -q`, `uv run ruff check`, `uv run ruff format --check` and `uv run basedpyright` in both FactoryHorde projects report no failures.
- [ ] `release.py manifest --check` passes.
- [ ] ComputeHorde `nox -s lint` and the manifest test modules pass.
- [ ] Pylon `nox -s test` (service), `nox -s test-3.14` (client) and `nox -s test-pact` pass.
- [ ] Four PRs are open against `master`: factory-horde, ComputeHorde, factory-horde-demo-miners and bittensor-pylon (opened in Task 8). Each branch was confirmed with `git branch --show-current` before pushing.
- [ ] The factory-horde PR description lists the rollout:
  1. Release ComputeHorde's miner change.
  2. Release Pylon (service and client) with mechanism-aware neuron reads.
  3. Release the FactoryHorde validator, with the new Pylon service digest, and the submitter, with `SUBMISSION_NAMESPACE`.
  4. Set the subnet's mechanism count to 2 and the emission split.
  5. Run the FactoryHorde validator with `MECHANISM_ID=1`, the shared validator hotkey and its own Pylon.
  6. On the target chain, write one commitment twice within 30 seconds from a test hotkey and confirm the second is rejected. Otherwise a concurrent read-modify-write can lose an update, and the README must say so.

**Verify:** `gh pr list --author @me --state open` in each repository → one PR each for the four branches.

**Steps:**

- [ ] **Step 1:** Run every gate listed above and fix failures.
- [ ] **Step 2:** For each repository without a PR yet: `git branch --show-current` (must not be `master`), `git push -u origin <branch>`, then `gh pr create --base master` with a concise description. The factory-horde description includes the rollout list from the acceptance criteria and links the Task 6 evidence.
- [ ] **Step 3:** Image builds for `deploy-build-*` are triggered by the user (pushing those branches is blocked for agents). State this in the factory-horde PR.
