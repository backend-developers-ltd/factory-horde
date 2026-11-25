# Full Image References in Commitments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let any miner commit its factory as a full `ghcr.io/OWNER/PACKAGE@DIGEST` reference, so third parties can publish under their own GHCR owner, without breaking ComputeHorde on hotkeys shared by both mechanisms.

**Architecture:** turbobt writes commitments longer than 128 bytes as the chain's `BigRaw` variant (up to 512 bytes), and Pylon picks that up and rejects values over 512 bytes up front. ComputeHorde raises its whole-commitment limit from 128 to 512. FactoryHorde's envelope becomes `<F:ghcr.io/OWNER/PACKAGE@BASE64URL-DIGEST>`. The submitter gets an optional `COMMITMENT_MAX_BYTES` cap (unset means the chain's 512), which operators of hotkeys shared with ComputeHorde set to 200 to stay inside the per-hotkey 3,100-byte quota per tempo. The validator decodes full references and keeps reading the old short envelopes only while `SUBMISSION_NAMESPACE` is still configured.

**Tech Stack:** Python 3.11+ (turbobt), 3.13/3.14 (Pylon, FactoryHorde), scalecodec, Litestar, pydantic, click, pytest, uv, nox, Docker localnet subtensor.

---

## Background and decisions

- Chain facts (finney spec 494, checked 2026-11-24): `pallet_commitments::types::Data` has `Raw0`–`Raw128` and `BigRaw` (`BoundedVec<u8, 512>`). The quota is `UsedSpaceOf(netuid, hotkey)`, charged `max(actual bytes, 100)` per write and reset every tempo (360 blocks on SN12), with `MaxSpace = 3100`. ComputeHorde and FactoryHorde share one commitment and one quota per hotkey on SN12.
- Root cause of today's 128-byte limit: `turbobt/subnet.py:134` always encodes `{f"Raw{len(data)}": data}`; `Raw129` does not exist. ComputeHorde additionally refuses merged commitments over 128 bytes (`MAX_COMMITMENT_BYTES`, `miner/app/src/compute_horde_miner/miner/manifest_commitment.py:17`).
- Envelope format: `<F:` + `ghcr.io/OWNER/PACKAGE` + `@` + 43-character unpadded base64url SHA-256 + `>`. The compact digest saves 28 bytes over `sha256:<hex>`; registry, owner and package are all explicit. FactoryHorde's own factory becomes a 100-byte envelope.
- Values of 128 bytes or less stay `RawN`, so existing commitments are encoded exactly as before. Only longer values use `BigRaw`.
- `COMMITMENT_MAX_BYTES` is a submitter-side write policy. Unset (or empty) means 512. The ComputeHorde 33-byte manifest reserve always applies within the effective limit. The validator never applies the operator cap; it only enforces the chain's 512.
- `SUBMISSION_NAMESPACE` is no longer used by the submitter. The validator keeps `VALIDATOR_SUBMISSION_NAMESPACE` as an optional, transitional setting: when set, envelopes without the `ghcr.io/` prefix are read as `NAMESPACE/PACKAGE` (the current format); when unset, they are rejected. Dispatch no longer requires it. Remove it in a later release after every miner has resubmitted.
- Order of release: turbobt → Pylon → ComputeHorde (and its miners upgraded on SN12) → FactoryHorde validator → FactoryHorde submitter and miners. A ComputeHorde miner on 128 bytes would stop updating its manifest as soon as a long `<F:…>` lands on its hotkey.
- Each repository's work goes on a feature branch with a PR against `master`. No `Co-Authored-By` in commits. FactoryHorde commits use a short title and no body.

## Execution notes

- turbobt also needed a read fix: scalecodec decodes a `BigRaw` byte vector to text when it is valid UTF-8, so `CommitmentOf` reads now take the exact bytes from the decoded scale object (turbobt#33, second commit). Verified on a local subtensor for ASCII, `0x`-prefixed text, non-UTF-8 and `RawN` values.
- Pylon image pins (Task 3 Step 6, Task 6 Step 1) need a published Pylon release, so they move to Task 9. Pylon (bittensor-pylon#107) and ComputeHorde (ComputeHorde#880) are draft PRs until then.
- ComputeHorde has a separate 128-byte cap on the manifest text itself (`MAX_MANIFEST_COMMITMENT_BYTES` in `compute_horde/manifest_utils.py`); it stays, because a manifest is at most 33 bytes.

## File structure

| Repository | File | Change |
|---|---|---|
| turbobt | `turbobt/subnet.py` | `_commitment_field()` chooses `RawN`/`BigRaw`, rejects over 512 |
| turbobt | `tests/test_bittensor/test_bittensor_commitments.py` | Encoding and BigRaw decoding tests |
| bittensor-pylon | `pylon_service/pyproject.toml`, `uv.lock` | turbobt with BigRaw |
| bittensor-pylon | `pylon_commons/pylon_commons/_unstable/bodies.py` | `SetCommitmentBody` rejects over 512 bytes |
| bittensor-pylon | `pylon_service/tests/unit/identity_endpoints/test_set_commitment_endpoint.py` | 512/513 endpoint test |
| bittensor-pylon | `pylon_service/tests/integration/contact/test_writes.py` | 300-byte localchain round trip |
| ComputeHorde | `miner/app/src/compute_horde_miner/miner/manifest_commitment.py` | `MAX_COMMITMENT_BYTES = 512` |
| ComputeHorde | `miner/app/src/compute_horde_miner/miner/tests/test_manifest_commitment.py` | Limit tests follow the constant |
| ComputeHorde | `{miner,validator}/envs/runner/data/docker-compose.yml`, `facilitator/envs/prod/docker-compose.yml`, `facilitator/devops/tf/main/files/docker-compose.yml` | Pylon pin |
| factory-horde | `miner/miner.py`, `miner/tests/test_submission.py` | Full-reference envelope, optional cap, no namespace |
| factory-horde | `validator/src/validator/discovery.py`, `validator/src/validator/main.py`, `validator/tests/test_discovery.py`, `validator/tests/test_chain_observer.py`, `validator/tests/test_dependency_contract.py` | Full-reference decoding, 512 limit, optional legacy namespace |
| factory-horde | `spec/fixtures/commitment-v2/envelopes.json` | Full-reference, legacy and invalid cases |
| factory-horde | `localnet/check_submissions.py`, `localnet/check_adversarial.py`, `localnet/check_rounds.py` | New format, cap rejection, 512 bound |
| factory-horde | `miner/docker-compose.yml`, `envs/deployed/docker-compose.yml` | Pylon pin, `COMMITMENT_MAX_BYTES` |
| factory-horde | `miner/README.md`, `AGENTS.md`, `spec/file-protocol.md`, `localnet/README.md`, `validator/README.md`, `installer/README.md`, `production/*`, `refinery/*` | Docs and env examples |
| factory-horde-demo-miners | `server/docker-compose.yml`, `server/.env.example`, `server/README.md` | Drop `SUBMISSION_NAMESPACE`, add the cap for production |

---

### Task 1: turbobt writes BigRaw for long commitments

**Goal:** `commitments.set()` encodes values of 129–512 bytes as `BigRaw`, keeps `RawN` up to 128 bytes, rejects anything longer, and BigRaw values read back as `hex_data`.

**Files:**
- Modify: `/home/jan/Projects/reef/turbobt/turbobt/subnet.py:125-142`
- Test: `/home/jan/Projects/reef/turbobt/tests/test_bittensor/test_bittensor_commitments.py`

**Acceptance Criteria:**
- [ ] `set(b"x" * 128)` sends `{"Raw128": ...}`; `set(b"x" * 129)` and `set(b"x" * 512)` send `{"BigRaw": ...}`.
- [ ] `set(b"x" * 513)` raises `ValueError` mentioning `512` and sends nothing.
- [ ] A registration with `{"BigRaw": "0x…"}` maps to `kind == "hex_data"` with the original bytes.
- [ ] The full turbobt test suite passes.

**Verify:** `cd /home/jan/Projects/reef/turbobt && uv run pytest tests/test_bittensor/test_bittensor_commitments.py -v` → all pass

**Steps:**

- [ ] **Step 1: Branch**

```bash
cd /home/jan/Projects/reef/turbobt && git checkout master && git pull && git checkout -b commitment-bigraw
```

- [ ] **Step 2: Write the failing tests** (append to `tests/test_bittensor/test_bittensor_commitments.py`)

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("size", "variant"),
    [(1, "Raw1"), (128, "Raw128"), (129, "BigRaw"), (512, "BigRaw")],
)
async def test_set_commitment_variant(mocked_subtensor, bittensor, alice_wallet, size, variant):
    data = b"x" * size

    await bittensor.subnets[1].commitments.set(data, wallet=alice_wallet)

    mocked_subtensor.commitments.set_commitment.assert_called_once_with(
        1, {"fields": [[{variant: data}]]}, wallet=alice_wallet
    )


@pytest.mark.asyncio
async def test_set_commitment_rejects_over_512_bytes(mocked_subtensor, bittensor, alice_wallet):
    with pytest.raises(ValueError, match="512"):
        await bittensor.subnets[1].commitments.set(b"x" * 513, wallet=alice_wallet)

    mocked_subtensor.commitments.set_commitment.assert_not_called()


@pytest.mark.asyncio
async def test_get_big_raw_commitment(mocked_subtensor, bittensor, alice_wallet):
    data = b"y" * 300
    mocked_subtensor.commitments.CommitmentOf.get.return_value = {
        "block": 100,
        "deposit": 0,
        "info": {"fields": [{"BigRaw": "0x" + data.hex()}]},
    }

    commitment = await bittensor.subnets[1].commitments.get(alice_wallet.hotkey.ss58_address)

    assert commitment["kind"] == "hex_data"
    assert commitment["data"] == data
```

- [ ] **Step 3: Run tests to verify the new ones fail**

Run: `uv run pytest tests/test_bittensor/test_bittensor_commitments.py -v`
Expected: `test_set_commitment_variant[129-BigRaw]`, `[512-BigRaw]` and `test_set_commitment_rejects_over_512_bytes` FAIL; `test_get_big_raw_commitment` may already pass, because `_map_to_commitment` reads the first field value generically. Keep it as a regression test.

- [ ] **Step 4: Implement** (in `turbobt/subnet.py`, module level near `_map_to_commitment`, and in `SubnetCommitments.set`)

```python
COMMITMENT_RAW_MAX_BYTES = 128
COMMITMENT_BIG_RAW_MAX_BYTES = 512


def _commitment_field(data: bytes) -> dict[str, bytes]:
    """
    Chooses the smallest chain Data variant for raw commitment bytes.
    """
    if len(data) <= COMMITMENT_RAW_MAX_BYTES:
        return {f"Raw{len(data)}": data}
    if len(data) <= COMMITMENT_BIG_RAW_MAX_BYTES:
        return {"BigRaw": data}
    raise ValueError(f"Commitment of {len(data)} bytes exceeds the chain's {COMMITMENT_BIG_RAW_MAX_BYTES}-byte limit")
```

```python
    async def set(
        self,
        data: bytes,
        wallet: Wallet | None = None,
    ):
        info: SetCommitmentInfo = {
            "fields": [
                [
                    _commitment_field(data),
                ],
            ],
        }
        extrinsic = await self.client.subtensor.commitments.set_commitment(
            self.subnet.netuid, info, wallet=wallet or self.client.wallet
        )
        await extrinsic.wait_for_finalization()
```

- [ ] **Step 5: Run the suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit and open the PR**

```bash
git add turbobt/subnet.py tests/test_bittensor/test_bittensor_commitments.py
git commit -m "Write commitments longer than 128 bytes as BigRaw"
git branch --show-current   # must print commitment-bigraw
git push -u origin commitment-bigraw
gh pr create --base master --title "Write commitments longer than 128 bytes as BigRaw" --body "Commitments of 129–512 bytes were impossible because set() always used the RawN variant, which stops at Raw128. Longer values now use the chain's BigRaw variant (up to 512 bytes); shorter ones are encoded exactly as before."
```

The scalecodec encoding of `BigRaw` against real metadata is proven by Pylon's localchain test in Task 2. turbobt's bundled test metadata (runtime 318) predates `BigRaw`.

---

### Task 2: Pylon uses BigRaw and rejects oversized commitments

**Goal:** Pylon writes and reads 129–512-byte commitments against a real chain and answers 400 for longer values instead of retrying a write that can never succeed.

**Files:**
- Modify: `/home/jan/Projects/reef/bittensor-pylon/pylon_service/pyproject.toml` (turbobt floor), `pylon_service/uv.lock`
- Modify: `/home/jan/Projects/reef/bittensor-pylon/pylon_commons/pylon_commons/_unstable/bodies.py:38-57`
- Test: `/home/jan/Projects/reef/bittensor-pylon/pylon_service/tests/unit/identity_endpoints/test_set_commitment_endpoint.py`
- Test: `/home/jan/Projects/reef/bittensor-pylon/pylon_service/tests/integration/contact/test_writes.py`

**Acceptance Criteria:**
- [ ] `POST …/commitments` with 513 bytes returns 400 and never calls `set_commitment`; 512 bytes returns 201.
- [ ] On a localchain whose metadata has `BigRaw`, a 300-byte commitment written through the contact reads back byte-identical.
- [ ] `nox -s test` and `nox -s test-integration` pass in `pylon_service`.

**Verify:** `cd /home/jan/Projects/reef/bittensor-pylon/pylon_service && nox -s test -- -k set_commitment && nox -s test-integration -- -k set_commitment` → all pass

**Steps:**

- [ ] **Step 1: Branch, and check that the localchain image has BigRaw**

```bash
cd /home/jan/Projects/reef/bittensor-pylon && git checkout master && git pull && git checkout -b commitment-bigraw
docker pull ghcr.io/opentensor/subtensor-localnet:main
docker run -d --rm --name bigraw-probe -p 127.0.0.1:19944:9944 ghcr.io/opentensor/subtensor-localnet:main
# wait until the node serves RPC (up to ~60 s), then:
cd /home/jan/Projects/reef/factory-horde/miner && uv run --group bootstrap python - <<'EOF'
from async_substrate_interface.sync_substrate import SubstrateInterface
md = SubstrateInterface(url="ws://127.0.0.1:19944").init_runtime().metadata
print(any(v["name"] == "BigRaw" for t in md.portable_registry.value["types"] if t["type"].get("path", [None])[-1] == "Data" and "pallet_commitments" in t["type"]["path"] for v in t["type"]["def"]["variant"]["variants"]))
EOF
docker stop bigraw-probe
```

Expected: `True`. If `False`, stop and report: the integration test needs a newer localnet image, and so does FactoryHorde's localnet (`localnet/compose.yml:5`).

- [ ] **Step 2: Depend on the BigRaw turbobt**

Until the turbobt PR is released to PyPI, point at its branch. Before merging, replace this with the released version (`turbobt>=<release>`).

```bash
cd pylon_service
uv add "turbobt @ git+https://github.com/bittensor-church/turbobt@commitment-bigraw"
```

- [ ] **Step 3: Write the failing tests**

Append to `pylon_service/tests/unit/identity_endpoints/test_set_commitment_endpoint.py`:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(("size", "status"), [(512, HTTP_201_CREATED), (513, HTTP_400_BAD_REQUEST)])
async def test_set_commitment_identity_size_limit(
    identity_test_client_factory, mock_bt_client_factory, size, status
):
    async with mock_bt_client_factory("sn2") as mock_client:
        async with mock_client.mock_behavior(set_commitment=[None]):
            async with identity_test_client_factory("sn2") as client:
                response = await client.post(
                    "/api/v1/identity/sn2/subnet/2/commitments",
                    json={"commitment": "ab" * size},
                )

        assert response.status_code == status
        assert len(mock_client.calls["set_commitment"]) == (1 if status == HTTP_201_CREATED else 0)
```

Append to `pylon_service/tests/integration/contact/test_writes.py`:

```python
@pytest.mark.asyncio
async def test_set_big_raw_commitment_round_trips(write_contact, direct_netuid):
    expected = CommitmentDataBytes(b"<F:" + b"x" * 296 + b">")

    await write_contact.set_commitment(direct_netuid, expected)

    latest_block = await write_contact.get_latest_block()
    commitment = await write_contact.get_commitment(direct_netuid, latest_block)

    assert commitment is not None
    assert commitment.commitment == expected.hex()
```

If `commitment.commitment` carries a `0x` prefix in this model, compare with `CommitmentDataBytes.fromhex(commitment.commitment) == expected` instead. Check one existing snapshot in `tests/integration/contact/__snapshots__/` to see which.

- [ ] **Step 4: Run to verify failure**

Run: `nox -s test -- -k test_set_commitment_identity_size_limit`
Expected: `[513-400]` FAILS (the request is accepted).

- [ ] **Step 5: Implement the body limit** (`pylon_commons/pylon_commons/_unstable/bodies.py`)

```python
COMMITMENT_MAX_BYTES = 512  # The chain's BigRaw bound.


class SetCommitmentBody(PylonBody):
    """
    Class used to perform setting commitment via the API.
    """

    commitment: CommitmentDataBytes

    @field_validator("commitment", mode="before")
    @classmethod
    def validate_commitment(cls, v):
        if isinstance(v, str):
            try:
                v = CommitmentDataBytes.fromhex(v)
            except ValueError as e:
                # Give more user-friendly message to the api.
                raise ValueError("passed commitment data is not a valid hex string.") from e
        if not isinstance(v, bytes):
            raise ValueError("commitment must be bytes or hex string")
        if len(v) > COMMITMENT_MAX_BYTES:
            raise ValueError(f"commitment must not exceed {COMMITMENT_MAX_BYTES} bytes")
        return v
```

- [ ] **Step 6: Run unit, integration and lint**

```bash
nox -s test -- -k set_commitment
nox -s test-integration -- -k set_commitment
cd .. && nox -s lint
```

Expected: all pass, including the 300-byte round trip.

- [ ] **Step 7: Commit and open the PR**

```bash
git add pylon_commons pylon_service/pyproject.toml pylon_service/uv.lock pylon_service/tests
git commit -m "Support commitments up to 512 bytes"
git branch --show-current   # must print commitment-bigraw
git push -u origin commitment-bigraw
gh pr create --base master --title "Support commitments up to 512 bytes" --body "Uses turbobt's BigRaw encoding so identities can commit up to 512 bytes; the API rejects longer commitments with 400 instead of retrying a write the chain cannot accept."
```

The PR stays in draft until turbobt is released and the git dependency has been replaced by the released version.

---

### Task 3: ComputeHorde accepts 512-byte commitments

**Goal:** ComputeHorde's miner keeps updating its manifest when the hotkey's commitment also holds a FactoryHorde envelope longer than 128 bytes in total.

**Files:**
- Modify: `/home/jan/Projects/reef/ComputeHorde/miner/app/src/compute_horde_miner/miner/manifest_commitment.py:15-17`
- Modify: `/home/jan/Projects/reef/ComputeHorde/miner/app/src/compute_horde_miner/miner/tests/test_manifest_commitment.py:151-205`
- Modify (Pylon pin, after the Pylon release): `miner/envs/runner/data/docker-compose.yml:29`, `validator/envs/runner/data/docker-compose.yml:29`, `facilitator/envs/prod/docker-compose.yml:16`, `facilitator/devops/tf/main/files/docker-compose.yml:21`

**Acceptance Criteria:**
- [ ] `commit_manifest` writes a 512-byte merged commitment and refuses 513 bytes without calling `set_commitment`.
- [ ] A commitment holding `<F:ghcr.io/backend-developers-ltd/factory-horde-factory@…>` (100 bytes) plus a manifest is rewritten with only `<M:…>` changed.
- [ ] Every Pylon image pin in the repository names the BigRaw-capable release.
- [ ] Miner tests pass.

**Verify:** `cd /home/jan/Projects/reef/ComputeHorde/miner && uv run pytest app/src/compute_horde_miner/miner/tests/test_manifest_commitment.py -v` → all pass (use the repository's usual test invocation if it differs; check `miner/noxfile.py`)

**Steps:**

- [ ] **Step 1: Branch**

```bash
cd /home/jan/Projects/reef/ComputeHorde && git checkout master && git pull && git checkout -b commitment-512-bytes
```

- [ ] **Step 2: Make the limit tests follow the constant, and add a FactoryHorde case** (`test_manifest_commitment.py`)

Import the constant next to `commit_manifest`:

```python
from compute_horde_miner.miner.manifest_commitment import MAX_COMMITMENT_BYTES, commit_manifest
```

Replace both `@pytest.mark.parametrize("size, success", [(128, True), (129, False)])` lines with:

```python
    @pytest.mark.parametrize("size, success", [(MAX_COMMITMENT_BYTES, True), (MAX_COMMITMENT_BYTES + 1, False)])
```

`test_shield_counts_towards_byte_limit` builds a `size - 6` byte shield (`"é" * 59` plus `size - 128` ASCII bytes) beside the 6-byte `<M:A3>`, so it scales with the parameter and only its parametrize line changes.

Add a test with a realistic shared commitment of 136 bytes (30-byte shield, 100-byte FactoryHorde envelope, 6-byte manifest):

```python
    def test_keeps_full_factory_horde_reference(self, mock_pylon_client):
        shield = "<D:" + "s" * 26 + ">"
        factory = "<F:ghcr.io/backend-developers-ltd/factory-horde-factory@" + "q" * 43 + ">"
        mock_pylon_client.v1.identity.get_own_commitment.return_value = own_commitment(
            commitment=pylon_hex(shield + factory + "<M:A2>")
        )

        assert commit_manifest({ExecutorClass.always_on__gpu_24gb: 3}, mock_pylon_client)
        mock_pylon_client.v1.identity.set_commitment.assert_called_once_with(
            (shield + factory + "<M:A3>").encode()
        )
```

- [ ] **Step 3: Run to verify failure**

Run: the verify command.
Expected: `test_keeps_full_factory_horde_reference` FAILS (refused at 128). The parametrized limit tests pass vacuously at 128/129 until the constant changes.

- [ ] **Step 4: Raise the limit** (`manifest_commitment.py`)

```python
# Maximum size for the entire commitment (other envelopes + own manifest): the chain's BigRaw bound.
# Pylon writes up to 128 bytes as RawN and longer values as BigRaw.
MAX_COMMITMENT_BYTES = 512
```

- [ ] **Step 5: Run the tests**

Run: the verify command.
Expected: all pass, including `[512-True]` and `[513-False]`. `test_commitment_byte_limit` uses `10 ** (size - 6)` executors, a 506-digit count; that is fine for Python integers.

- [ ] **Step 6: Pin the BigRaw Pylon release** (after Task 2's release exists)

```bash
docker pull docker.io/backenddevelopersltd/bittensor-pylon:<released version>
docker inspect --format '{{index .RepoDigests 0}}' docker.io/backenddevelopersltd/bittensor-pylon:<released version>
grep -rln "bittensor-pylon:2.3.4@sha256:2e7a3470" . | xargs sed -i 's#bittensor-pylon:2.3.4@sha256:2e7a3470b1ff6f4a72f53a075c1f215cd352a0d289387bad10f328825f396dbb#bittensor-pylon:<released version>@sha256:<digest from inspect>#'
grep -rn "bittensor-pylon:" --include=*.yml . 
```

Expected: the four compose files name the new tag and digest. The validator's Pylon needs it so `get_recent_commitments` decodes `BigRaw` values through the new turbobt.

- [ ] **Step 7: Commit and open the PR**

```bash
git add -A miner validator facilitator
git commit -m "Allow commitments up to 512 bytes"
git branch --show-current   # must print commitment-512-bytes
git push -u origin commitment-512-bytes
gh pr create --base master --title "Allow commitments up to 512 bytes" --body "The miner refused to update its manifest when the shared commitment exceeded 128 bytes. Pylon now writes up to 512 bytes, so the limit follows; this keeps the manifest updatable beside FactoryHorde's full image reference. Pylon pins move to the release that supports this."
```

---

### Task 4: FactoryHorde submitter commits the full reference with an optional cap

**Goal:** The submitter commits `<F:ghcr.io/OWNER/PACKAGE@DIGEST>` for any GHCR owner and limits the merged commitment to `COMMITMENT_MAX_BYTES` when set, otherwise to 512.

**Files:**
- Modify: `/home/jan/Projects/reef/factory-horde/miner/miner.py`
- Modify: `/home/jan/Projects/reef/factory-horde/miner/tests/test_submission.py`
- Modify: `/home/jan/Projects/reef/factory-horde/spec/fixtures/commitment-v2/envelopes.json`

**Acceptance Criteria:**
- [ ] `encode_envelope("ghcr.io/backend-developers-ltd/factory-horde-factory@sha256:" + "ab" * 32)` returns `b"<F:ghcr.io/backend-developers-ltd/factory-horde-factory@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s>"` (100 bytes).
- [ ] With no cap, a merged commitment of 512 bytes is written and 513 is refused; with `max_bytes=200`, 200 is written and 201 is refused, with the limit in the message.
- [ ] The ComputeHorde 33-byte reserve is enforced within the effective limit.
- [ ] `COMMITMENT_MAX_BYTES` unset or empty means no cap; a value is accepted from the environment and from `--commitment-max-bytes`; an envelope that cannot fit the cap fails before any network request.
- [ ] `SUBMISSION_NAMESPACE` and `--namespace` are gone.
- [ ] Miner ruff, basedpyright and tests pass.

**Verify:** `cd /home/jan/Projects/reef/factory-horde/miner && uv run ruff check . && uv run basedpyright && uv run pytest -q` → all pass

**Steps:**

- [ ] **Step 1: Branch** (covers Tasks 4–7)

```bash
cd /home/jan/Projects/reef/factory-horde && git checkout master && git pull && git checkout -b full-image-commitments
```

- [ ] **Step 2: Rewrite the shared fixture** (`spec/fixtures/commitment-v2/envelopes.json`)

```json
{
  "legacy_namespace": "ghcr.io/backend-developers-ltd",
  "valid": [
    {
      "commitment": "<F:ghcr.io/backend-developers-ltd/factory-horde-factory@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s>",
      "image": "ghcr.io/backend-developers-ltd/factory-horde-factory@sha256:abababababababababababababababababababababababababababababababab"
    },
    {
      "commitment": "<F:ghcr.io/backend-developers-ltd/factory-horde-deceptive@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s><M:A3;S2>",
      "image": "ghcr.io/backend-developers-ltd/factory-horde-deceptive@sha256:abababababababababababababababababababababababababababababababab"
    },
    {
      "commitment": "<D:https://example.com/s><F:ghcr.io/third-party/fh-miner-openhands@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s><M:A3>",
      "image": "ghcr.io/third-party/fh-miner-openhands@sha256:abababababababababababababababababababababababababababababababab"
    },
    {
      "commitment": "<M:A3><F:ghcr.io/a-very-long-github-owner-name-of-39-ch/team/nested-package-name@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s>",
      "image": "ghcr.io/a-very-long-github-owner-name-of-39-ch/team/nested-package-name@sha256:abababababababababababababababababababababababababababababababab"
    }
  ],
  "legacy": [
    {
      "commitment": "<F:factory-horde-factory@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s><M:A3>",
      "image": "ghcr.io/backend-developers-ltd/factory-horde-factory@sha256:abababababababababababababababababababababababababababababababab"
    }
  ],
  "invalid": [
    {"commitment": "<M:A3>", "reason": "No FactoryHorde submission"},
    {"commitment": "<F:ghcr.io/o/a@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s><F:ghcr.io/o/b@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s>", "reason": "Multiple FactoryHorde submissions"},
    {"commitment": "<F:ghcr.io/o/Factory@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s>", "reason": "Malformed FactoryHorde submission"},
    {"commitment": "<F:ghcr.io/o/factory@q6ur>", "reason": "Malformed FactoryHorde submission"},
    {"commitment": "<F:ghcr.io/o/factory@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6t>", "reason": "Malformed FactoryHorde submission"},
    {"commitment": "<F:ghcr.io/owner-only@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s>", "reason": "Malformed FactoryHorde submission"},
    {"commitment": "ghcr.io/backend-developers-ltd/factory-horde-factory@sha256:abababababababababababababababababababababababababababababababab", "reason": "No FactoryHorde submission"}
  ]
}
```

The fourth valid case uses a 38-character owner (GitHub allows up to 39) and a nested package path.

- [ ] **Step 3: Update the tests first** (`miner/tests/test_submission.py`)

Replace the module constants and import:

```python
from miner import (
    CHAIN_COMMITMENT_MAX_BYTES,
    COMPUTE_HORDE_MANIFEST_RESERVE,
    SubmissionError,
    encode_envelope,
    main,
    merge_envelope,
    submit,
)

FIXTURES = Path(__file__).resolve().parents[2] / "spec/fixtures"
ENVELOPES = json.loads((FIXTURES / "commitment-v2/envelopes.json").read_text())
IMAGE = "ghcr.io/example/factory@sha256:" + "ab" * 32
ENVELOPE = encode_envelope(IMAGE)
```

Delete every `NAMESPACE` argument: `submit(api, IMAGE, 2, NAMESPACE)` becomes `submit(api, IMAGE, 2)`, and CLI invocations drop `--namespace …` and the `SUBMISSION_NAMESPACE` env. Delete `test_outside_namespace_fails_before_reading` (line 227); there is no namespace any more. Replace `test_envelope_is_compact`, `test_fixture_commitments_hold_the_encoded_envelope`, `test_merge_keeps_compute_horde_reserve`, `test_merge_never_exceeds_the_writer_limit` and `test_overlong_package_fails_before_any_request` with:

```python
def test_envelope_holds_the_full_reference() -> None:
    assert ENVELOPE == b"<F:ghcr.io/example/factory@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s>"


@pytest.mark.parametrize("case", ENVELOPES["valid"])
def test_fixture_commitments_hold_the_encoded_envelope(case: dict[str, str]) -> None:
    assert encode_envelope(case["image"]) in case["commitment"].encode()


@pytest.mark.parametrize("limit", [CHAIN_COMMITMENT_MAX_BYTES, 200])
def test_merge_keeps_compute_horde_reserve(limit: int) -> None:
    room = limit - COMPUTE_HORDE_MANIFEST_RESERVE - len(ENVELOPE)
    assert merge_envelope(b"<D:" + b"x" * (room - 4) + b">", ENVELOPE, limit)
    with pytest.raises(SubmissionError, match="ComputeHorde"):
        merge_envelope(b"<D:" + b"x" * (room - 3) + b">", ENVELOPE, limit)


@pytest.mark.parametrize("limit", [CHAIN_COMMITMENT_MAX_BYTES, 200])
def test_merge_never_exceeds_the_limit(limit: int) -> None:
    # An existing manifest larger than the reserve: only the total length check can catch it.
    fits = b"<M:" + b"A" * (limit - len(ENVELOPE) - 4) + b">"
    assert len(merge_envelope(fits, ENVELOPE, limit)) == limit
    with pytest.raises(SubmissionError, match=str(limit)):
        merge_envelope(b"<M:" + b"A" * (limit - len(ENVELOPE) - 3) + b">", ENVELOPE, limit)


def test_default_limit_is_the_chain_bound() -> None:
    assert CHAIN_COMMITMENT_MAX_BYTES == 512
    fits = b"<M:" + b"A" * (512 - len(ENVELOPE) - 4) + b">"
    assert len(merge_envelope(fits, ENVELOPE)) == 512


@pytest.mark.parametrize("limit", [CHAIN_COMMITMENT_MAX_BYTES, 200])
def test_overlong_reference_fails_before_any_request(limit: int) -> None:
    api = FakeApi()
    # Envelope = 3 + len("ghcr.io/example/") + package + 1 + 43 + 1.
    longest = "p" * (limit - COMPUTE_HORDE_MANIFEST_RESERVE - 64)
    assert len(encode_envelope(f"ghcr.io/example/{longest}@sha256:{'ab' * 32}", limit)) + 33 == limit
    with pytest.raises(ValueError, match=str(limit)):
        submit(api, f"ghcr.io/example/{longest}p@sha256:{'ab' * 32}", 2, max_bytes=limit)
    assert api.reads == 0 and api.writes == []


@pytest.mark.parametrize(("env", "expected"), [({}, "512"), ({"COMMITMENT_MAX_BYTES": ""}, "512"), ({"COMMITMENT_MAX_BYTES": "150"}, "150")])
def test_cli_cap_comes_from_the_environment(env: dict[str, str], expected: str) -> None:
    image = f"ghcr.io/example/{'p' * 100}@sha256:{'ab' * 32}"  # 164-byte envelope
    result = CliRunner().invoke(
        main,
        [image, "--pylon-address", "http://127.0.0.1:1", "--identity", "miner", "--netuid", "2"],
        env={"PYLON_IDENTITY_TOKEN": "", **env},
    )
    assert result.exit_code != 0
    if expected == "512":
        assert "PYLON_IDENTITY_TOKEN" in result.output  # passed the size check, stopped at the token
    else:
        assert "150" in result.output  # refused before any network request
```

- [ ] **Step 4: Run to verify failure**

Run: `uv run pytest tests/test_submission.py -q`
Expected: ImportError for `CHAIN_COMMITMENT_MAX_BYTES`.

- [ ] **Step 5: Implement** (`miner/miner.py`)

Replace the constants:

```python
# The chain's BigRaw bound for one commitment; Pylon writes up to 128 bytes as RawN and longer values as BigRaw.
CHAIN_COMMITMENT_MAX_BYTES = 512
# ComputeHorde's largest manifest envelope: "<M:" + six "X999" codes joined by ";" + ">".
COMPUTE_HORDE_MANIFEST_RESERVE = 33
SEGMENT = r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
IMAGE_PATTERN = rf"ghcr\.io/{SEGMENT}(?:/{SEGMENT})+@sha256:[0-9a-f]{{64}}"
```

(`NAMESPACE_PATTERN` is deleted.)

```python
def encode_envelope(image: str, max_bytes: int = CHAIN_COMMITMENT_MAX_BYTES) -> bytes:
    """Encode IMAGE as FactoryHorde's commitment envelope: registry, owner, package and compact digest.

    Raises:
        ValueError: The image is invalid or its envelope can never fit MAX_BYTES beside ComputeHorde's manifest.
    """
    validate_reference(image)
    path, digest = image.split("@sha256:")
    compact = base64.urlsafe_b64encode(bytes.fromhex(digest)).rstrip(b"=")
    envelope = b"<F:" + path.encode() + b"@" + compact + b">"
    if len(envelope) + COMPUTE_HORDE_MANIFEST_RESERVE > max_bytes:
        raise ValueError(
            f"Image reference is too long: its envelope and ComputeHorde's manifest must fit {max_bytes} bytes"
        )
    return envelope


def merge_envelope(current: bytes, envelope: bytes, max_bytes: int = CHAIN_COMMITMENT_MAX_BYTES) -> bytes:
    """Replace only FactoryHorde's envelope, keeping every other byte and ComputeHorde's manifest room.

    Raises:
        SubmissionError: The result would exceed MAX_BYTES or ComputeHorde's manifest reserve.
    """
    own = list(OWN_ENVELOPE.finditer(current))
    if own:
        tail = OWN_ENVELOPE.sub(b"", current[own[0].end() :])
        merged = current[: own[0].start()] + envelope + tail
    else:
        manifest = MANIFEST_ENVELOPE.search(current)
        at = manifest.start() if manifest is not None else len(current)
        merged = current[:at] + envelope + current[at:]
    if len(merged) > max_bytes:
        raise SubmissionError(f"Commitment would exceed the {max_bytes}-byte limit")
    if len(MANIFEST_ENVELOPE.sub(b"", merged)) + COMPUTE_HORDE_MANIFEST_RESERVE > max_bytes:
        raise SubmissionError(
            f"Commitment would leave ComputeHorde less than its 33-byte manifest reserve within {max_bytes} bytes"
        )
    return merged
```

`submit` signature and first lines:

```python
def submit(
    api: SubmissionApi,
    image: str,
    netuid: int,
    *,
    max_bytes: int = CHAIN_COMMITMENT_MAX_BYTES,
    confirm_seconds: float = 150,
) -> Publication:
    ...
    envelope = encode_envelope(image, max_bytes)
```

and inside it `merged = merge_envelope(current, envelope, max_bytes)`. Update its docstring's `ValueError` line to "The reference, size limit or confirmation period is invalid."

CLI: replace the `--namespace` option with:

```python
@click.option(
    "--commitment-max-bytes",
    type=click.IntRange(min=1, max=CHAIN_COMMITMENT_MAX_BYTES),
    envvar="COMMITMENT_MAX_BYTES",
    default=None,
    help="Cap on the whole commitment (default: the chain's 512 bytes). Set it on hotkeys shared with ComputeHorde.",
)
```

and in `main(image, pylon_address, identity, netuid, commitment_max_bytes, confirm_seconds)`:

```python
    max_bytes = commitment_max_bytes or CHAIN_COMMITMENT_MAX_BYTES
    ...
        encode_envelope(image, max_bytes)
    ...
            result = submit(client.v1.identity, image, netuid, max_bytes=max_bytes, confirm_seconds=confirm_seconds)
```

Click ignores an empty environment variable, so `COMMITMENT_MAX_BYTES=` from Compose means no cap; the CLI test above proves it.

- [ ] **Step 6: Run the miner gates**

Run: the verify command.
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add miner/miner.py miner/tests/test_submission.py spec/fixtures/commitment-v2/envelopes.json
git commit -m "Commit full image references with an optional size cap"
```

---

### Task 5: FactoryHorde validator decodes full references

**Goal:** Discovery accepts `<F:ghcr.io/OWNER/PACKAGE@DIGEST>` from any owner, bounds commitments at 512 bytes, and reads the old short envelopes only while a legacy namespace is configured; dispatch no longer requires a namespace.

**Files:**
- Modify: `/home/jan/Projects/reef/factory-horde/validator/src/validator/discovery.py:17-80,84-178`
- Modify: `/home/jan/Projects/reef/factory-horde/validator/src/validator/main.py:62,113-124,183-196`
- Test: `/home/jan/Projects/reef/factory-horde/validator/tests/test_discovery.py`, `validator/tests/test_chain_observer.py:58-67`, `validator/tests/test_dependency_contract.py:247`

**Acceptance Criteria:**
- [ ] Every `valid` fixture decodes to its `image` with no namespace; every `legacy` fixture decodes only with `legacy_namespace` and otherwise fails with "FactoryHorde submission lacks its registry owner".
- [ ] Every `invalid` fixture fails with its `reason`.
- [ ] A 512-byte commitment containing a valid envelope decodes; 513 bytes fails with a message containing `512`.
- [ ] Dispatch settings validate without `submission_namespace`; a set namespace must still match `^ghcr\.io(/segment)+$`.
- [ ] Validator ruff, basedpyright and tests pass.

**Verify:** `cd /home/jan/Projects/reef/factory-horde/validator && uv run ruff check . && uv run basedpyright && uv run pytest -q` → all pass

**Steps:**

- [ ] **Step 1: Update the tests first**

`validator/tests/test_discovery.py`: change the constants to the new format and keep a legacy pair:

```python
LEGACY_NAMESPACE = "ghcr.io/example"
IMAGE = "ghcr.io/example/factory@sha256:" + "ab" * 32
ENVELOPE = "<F:ghcr.io/example/factory@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s>"
LEGACY_ENVELOPE = "<F:factory@q6urq6urq6urq6urq6urq6urq6urq6urq6urq6urq6s>"
```

Replace `decode_reference(value, NAMESPACE)` calls with `decode_reference(value)`, replace the `# over 128 bytes` case with `(ENVELOPE + "x" * (513 - len(ENVELOPE))).encode().hex(),  # over 512 bytes`, change the malformed-package case to `ENVELOPE.replace("/factory@", "/Factory@")`, and pass `None` wherever `discover`/`freeze_discovery` received `NAMESPACE`. Then make the fixture tests use the new sections and add:

```python
@pytest.mark.parametrize("case", ENVELOPES["valid"])
def test_valid_envelopes_decode_without_namespace(case: dict[str, str]) -> None:
    assert decode_reference(case["commitment"].encode().hex()) == case["image"]


@pytest.mark.parametrize("case", ENVELOPES["legacy"])
def test_legacy_envelopes_need_the_namespace(case: dict[str, str]) -> None:
    value = case["commitment"].encode().hex()
    assert decode_reference(value, ENVELOPES["legacy_namespace"]) == case["image"]
    with pytest.raises(ValueError, match="registry owner"):
        decode_reference(value)


@pytest.mark.parametrize("case", ENVELOPES["invalid"])
def test_invalid_envelopes_are_rejected(case: dict[str, str]) -> None:
    with pytest.raises(ValueError, match=case["reason"]):
        decode_reference(case["commitment"].encode().hex(), ENVELOPES["legacy_namespace"])


def test_commitment_bound_is_the_chain_limit() -> None:
    fits = ENVELOPE + "x" * (512 - len(ENVELOPE))
    assert decode_reference(fits.encode().hex()) == IMAGE
    with pytest.raises(ValueError, match="512"):
        decode_reference((fits + "x").encode().hex())
```

If the file already has fixture-driven tests with these purposes (around line 114), replace them rather than duplicating.

`validator/tests/test_chain_observer.py`: replace the two namespace tests with:

```python
def test_dispatch_does_not_require_a_submission_namespace() -> None:
    assert Settings.model_validate(DISPATCH).submission_namespace is None


def test_legacy_submission_namespace_must_be_a_ghcr_owner() -> None:
    assert Settings.model_validate({**DISPATCH, "submission_namespace": "ghcr.io/example"}).submission_namespace
    with pytest.raises(ValidationError, match="submission_namespace"):
        Settings.model_validate({**DISPATCH, "submission_namespace": "docker.io/example"})
```

`validator/tests/test_dependency_contract.py:247`: the baseline package names must fit the production cap with their full reference:

```python
        assert len(f"<F:ghcr.io/backend-developers-ltd/factory-horde-{kind}@{'A' * 43}>") + 33 <= 200
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_discovery.py tests/test_chain_observer.py -q`
Expected: failures on the new format, the 512 bound and the dispatch requirement.

- [ ] **Step 3: Implement discovery** (`validator/src/validator/discovery.py`)

```python
# The chain's BigRaw bound for one commitment.
COMMITMENT_MAX_BYTES = 512
```

```python
def decode_reference(value: str, legacy_namespace: str | None = None) -> str:
    """Decode the single FactoryHorde envelope of a hex commitment into a GHCR digest reference.

    Envelopes carry ghcr.io/OWNER/PACKAGE. Older envelopes carry only PACKAGE and are read under
    LEGACY_NAMESPACE when it is configured. Other mechanisms' envelopes are ignored.

    Raises:
        ValueError: Hex, length, envelope, digest or rebuilt reference is invalid.
    """
    if re.fullmatch(r"(?:0x)?(?:[0-9a-fA-F]{2})+", value) is None:
        raise ValueError("Malformed hexadecimal commitment")
    raw = bytes(CommitmentDataBytes.fromhex(value.removeprefix("0x")))
    if len(raw) > COMMITMENT_MAX_BYTES:
        raise ValueError(f"Commitment exceeds the chain's {COMMITMENT_MAX_BYTES}-byte limit")
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
    path = match[1].decode()
    if not path.startswith("ghcr.io/"):
        if legacy_namespace is None:
            raise ValueError("FactoryHorde submission lacks its registry owner")
        path = f"{legacy_namespace}/{path}"
    try:
        return _image.validate_python(f"{path}@sha256:{digest.hex()}")
    except ValueError as error:
        raise ValueError("Malformed FactoryHorde submission") from error
```

The `ghcr.io/owner-only@…` fixture fails `ImageReference` (it needs owner and package), which the `try` turns into the fixture's reason.

Change `namespace: str` to `legacy_namespace: str | None` in `discover`, `freeze_discovery` and `freeze_via_pylon`, passing it through to `decode_reference(value, legacy_namespace)`. Adjust their docstrings to say the namespace is only for legacy envelopes.

- [ ] **Step 4: Implement settings** (`validator/src/validator/main.py`)

Field (line 62) keeps its pattern; add a short comment:

```python
    # Transitional: reads envelopes written before they carried ghcr.io/OWNER. Remove once all miners resubmitted.
    submission_namespace: str | None = Field(default=None, pattern=r"^ghcr\.io(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+$")
```

Dispatch check (lines 115-124):

```python
        if self.dispatch_enabled and not (
            self.validator_hotkey and self.judge_image and self.pylon_identity_name and self.pylon_identity_token
        ):
            raise ValueError("Dispatch requires validator hotkey, judge digest and Pylon identity credentials")
```

Wiring (lines 184-196): drop `and settings.submission_namespace is not None` from the condition; keep passing `settings.submission_namespace` as the last `partial` argument.

- [ ] **Step 5: Run the validator gates**

Run: the verify command.
Expected: all pass. Fix any other test that still passes a namespace positionally (`grep -rn "NAMESPACE" validator/tests`).

- [ ] **Step 6: Commit**

```bash
git add validator spec/fixtures/commitment-v2/envelopes.json
git commit -m "Decode full image references in discovery"
```

---

### Task 6: FactoryHorde localnet checks and Pylon pins

**Goal:** The localnet acceptance checks exercise full references, the operator cap and the 512-byte bound against a real chain through the BigRaw-capable Pylon.

**Files:**
- Modify: `/home/jan/Projects/reef/factory-horde/localnet/check_submissions.py:45-95,155-195`
- Modify: `/home/jan/Projects/reef/factory-horde/localnet/check_adversarial.py:95-165,285-415`
- Modify: `/home/jan/Projects/reef/factory-horde/localnet/check_rounds.py:84`
- Modify: `/home/jan/Projects/reef/factory-horde/miner/docker-compose.yml:7-8`, `envs/deployed/docker-compose.yml:4-5` (Pylon pin)

**Acceptance Criteria:**
- [ ] `check_submissions.py` publishes through the submitter without `SUBMISSION_NAMESPACE`, discovers with no legacy namespace, and its coexistence case keeps `<D:…>`/`<M:…>` beside a full-reference envelope.
- [ ] `check_adversarial.py` proves (a) the submitter with `--commitment-max-bytes 200` rejects a 170-byte envelope before network I/O with `200` in stderr, (b) the submitter rejects an envelope that cannot fit 512 with `512` in stderr, (c) `decode_reference` rejects a 513-byte commitment with `512` in the message, and (d) a 300-byte commitment published through Pylon reads back unchanged and its envelope is discovered.
- [ ] Both compose files pin the same BigRaw-capable Pylon digest.
- [ ] Localnet check scripts pass their offline unit tests (`localnet/test_*.py`).

**Verify:** `cd /home/jan/Projects/reef/factory-horde && uv run --project validator pytest localnet -q` → all pass; the live checks run in Task 9.

**Steps:**

- [ ] **Step 1: Pin Pylon** (after Task 2's release)

```bash
docker pull docker.io/backenddevelopersltd/bittensor-pylon:<released version>
docker inspect --format '{{index .RepoDigests 0}}' docker.io/backenddevelopersltd/bittensor-pylon:<released version>
```

Replace the digest `b0b4f4d532aed7dba6ffc979ce270ac3286b3c98678a87f8d2c3ec8086453184` in `miner/docker-compose.yml` and `envs/deployed/docker-compose.yml` and update both `# bittensor-pylon:2.4.0` comments to the released version. Check `localnet/compose.yml` for its Pylon service (it may build or reference the deployed pin) and keep it consistent.

- [ ] **Step 2: `check_submissions.py`**

Remove the namespace derivation's use for the submitter (keep the "images share one owner" check only if something else needs it; otherwise delete lines 50-52), delete the `--env SUBMISSION_NAMESPACE=…` pair (lines 89-90), and pass `None` instead of `namespace` to each `freeze_discovery(...)` call (lines 160-192).

- [ ] **Step 3: `check_adversarial.py`**

- `encode_envelope(x, self.namespace)` → `encode_envelope(x)` (lines 141, 292, 334, 409).
- Line 161: delete `"VALIDATOR_SUBMISSION_NAMESPACE": self.namespace,` and the `namespace` property if nothing else uses it.
- Replace the overlong block (lines 338-375) with a cap rejection, a chain-bound rejection and a discovery bound:

```python
        # Envelope = 3 + len(owner path) + 1 + package + 1 + 43 + 1; choose the package for a 170-byte envelope,
        # valid on chain but over a 200-byte operator cap once ComputeHorde's 33-byte reserve is added.
        capped = f"{self.namespace}/{'x' * (170 - 49 - len(self.namespace))}@sha256:{'0' * 64}"
        if len(encode_envelope(capped)) != 170:
            raise RuntimeError("Capped fixture has the wrong length")
        # Can never fit 512 bytes beside ComputeHorde's reserve.
        overlong = f"{self.namespace}/{'x' * 450}@sha256:{'0' * 64}"
        submitter = self.runtime.image("submitter")
        with PylonClient(self.client_config(self.miners[3].identity)) as client:
            before = client.v1.identity.get_own_commitment()
            for image, extra, limit in ((capped, ["--commitment-max-bytes", "200"], "200"), (overlong, [], "512")):
                rejected = subprocess.run(
                    [
                        "docker", "run", "--rm", "--network", "none", "--read-only", submitter, image,
                        "--pylon-address", "http://127.0.0.1:1", "--identity", self.miners[3].identity,
                        "--netuid", "2", *extra,
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if rejected.returncode == 0 or limit not in rejected.stderr:
                    raise RuntimeError(f"Submitter did not reject a reference over {limit} bytes before network I/O")
            try:
                decode_reference((b"<F:" + b"x" * 510 + b">").hex())
            except ValueError as error:
                if "512" not in str(error):
                    raise RuntimeError("Unexpected overlong discovery rejection") from error
            else:
                raise RuntimeError("Discovery accepted an overlong commitment")
            after = client.v1.identity.get_own_commitment()
```

- Add a BigRaw round trip next to the existing coexistence checks: publish `b"<D:" + b"s" * 150 + b">" + encode_envelope(baseline) + b"<M:A3>"` (well over 128 bytes) with `self.publish_bytes(...)`, read it back with `get_own_commitment()`, require byte equality, and require `freeze_discovery(...)` to put that miner in the cohort with `baseline`.

- [ ] **Step 4: `check_rounds.py:84`**

Delete the `VALIDATOR_SUBMISSION_NAMESPACE` entry; rounds now discover full references.

- [ ] **Step 5: Run the offline tests and commit**

```bash
uv run --project validator pytest localnet -q
git add localnet miner/docker-compose.yml envs/deployed/docker-compose.yml
git commit -m "Exercise full references and the BigRaw bound on localnet"
```

---

### Task 7: FactoryHorde configuration and documentation

**Goal:** Operators and third-party miners can configure the cap, and every doc describes the full-reference format, the 512 limit, the cap and the ComputeHorde requirement consistently.

**Files:**
- Modify: `/home/jan/Projects/reef/factory-horde/miner/docker-compose.yml:47-56` (submit environment)
- Modify: `production/miner.env.example`, `refinery/miner.env.example`, `production/validator.env.example:15`, `refinery/validator.env.example:15`, `envs/deployed/docker-compose.yml:56`
- Modify: `miner/README.md`, `AGENTS.md` (commitments paragraph), `spec/file-protocol.md:~73`, `localnet/README.md:~398,~463`, `validator/README.md`, `installer/README.md`, `refinery/README.md`, `production/README.md`
- Modify: `installer/release-manifest.json` (regenerated)

**Acceptance Criteria:**
- [ ] The `submit` service passes `COMMITMENT_MAX_BYTES: "${COMMITMENT_MAX_BYTES:-}"` and no `SUBMISSION_NAMESPACE`.
- [ ] `production/miner.env.example` sets `COMMITMENT_MAX_BYTES=200` with a one-line reason; `refinery/miner.env.example` leaves it commented out; neither has `SUBMISSION_NAMESPACE`.
- [ ] Validator env examples and `envs/deployed` describe `SUBMISSION_NAMESPACE` as optional and transitional.
- [ ] `grep -rn "128-byte\|128 bytes\|RawN\|Raw128" --include=*.md . | grep -v docs/superpowers | grep -v evidence` returns only historical statements that are explicitly dated or marked as superseded.
- [ ] `uv run --project validator python installer/release.py manifest "$PWD"` followed by `--check` passes.
- [ ] `docker compose -f miner/docker-compose.yml --env-file production/miner.env.example config --quiet` succeeds after filling the empty required values with dummies.

**Verify:** `cd /home/jan/Projects/reef/factory-horde && uv run --project validator python installer/release.py manifest "$PWD" --check` → exit 0

**Steps:**

- [ ] **Step 1: Compose**

`miner/docker-compose.yml`, `submit.environment`:

```yaml
      PYLON_ADDRESS: http://pylon:8000
      PYLON_IDENTITY: miner
      PYLON_IDENTITY_TOKEN: "${MINER_PYLON_TOKEN:?}"
      NETUID: "${NETUID:?}"
      # Optional cap on the whole commitment; empty means the chain's 512 bytes.
      COMMITMENT_MAX_BYTES: "${COMMITMENT_MAX_BYTES:-}"
```

`envs/deployed/docker-compose.yml:56` keeps `VALIDATOR_SUBMISSION_NAMESPACE: "${SUBMISSION_NAMESPACE:-}"` with a comment: `# Optional: reads envelopes from before they carried ghcr.io/OWNER.`

- [ ] **Step 2: Env examples**

`production/miner.env.example`: delete the `SUBMISSION_NAMESPACE` block and add:

```sh
# This hotkey's commitment is shared with ComputeHorde, whose manifest changes can be written every 5 minutes.
# 200 bytes keeps both writers within the hotkey's 3,100-byte commitment quota per tempo.
COMMITMENT_MAX_BYTES=200
```

`refinery/miner.env.example`: delete `SUBMISSION_NAMESPACE` and add:

```sh
# Optional cap on the whole commitment (default: the chain's 512 bytes). Set it when the hotkey also runs ComputeHorde.
# COMMITMENT_MAX_BYTES=200
```

Both validator examples: replace the namespace comment with `# Optional and transitional: owner of packages committed before envelopes carried ghcr.io/OWNER.` and keep the value until Task 9's cleanup.

- [ ] **Step 3: `miner/README.md`**

- Settings table: delete the `SUBMISSION_NAMESPACE` row; add `| COMMITMENT_MAX_BYTES | Optional cap on the whole commitment, default the chain's 512 bytes. Set 200 when the hotkey also runs ComputeHorde |`. Change `FACTORY_IMAGE`'s meaning to "Default image to commit: any public `ghcr.io/OWNER/PACKAGE@sha256:…` reference".
- Replace the commitment-quota paragraph's version requirement with: a ComputeHorde miner on the same hotkey must run a release with 512-byte commitments (name the ComputeHorde PR from Task 3) before the submitter commits, or it stops updating its manifest; and set `COMMITMENT_MAX_BYTES=200` there.
- "Commitment format": the envelope is `<F:ghcr.io/OWNER/PACKAGE@DIGEST>` with the 43-character unpadded base64url SHA-256; images under any GHCR owner are accepted; validators read older `<F:PACKAGE@DIGEST>` envelopes only while they configure `SUBMISSION_NAMESPACE`.
- Replace the 128-byte paragraph and table with: Pylon writes up to 128 bytes as `RawN` and longer values as `BigRaw`, so a commitment holds at most 512 bytes. Table rows: ComputeHorde reserve 33; envelope `57 + len(OWNER) + len(PACKAGE)` bytes; FactoryHorde's own factory 100; budget under the 200-byte cap leaves `OWNER + PACKAGE ≤ 110` bytes. Add the quota fact: each write is charged its actual size (at least 100 bytes) against the hotkey's 3,100 bytes per tempo, shared by every writer of that hotkey's commitment.
- Delete "Images outside the namespace are rejected before any chain access" and the `--namespace` parts of the localnet example.

- [ ] **Step 4: Other docs**

- `AGENTS.md`: in the commitments paragraph, replace "`discovery.py` rebuilds the image under `VALIDATOR_SUBMISSION_NAMESPACE`, which dispatch requires" with "`discovery.py` reads `ghcr.io/OWNER/PACKAGE@digest` envelopes (older package-only envelopes only while `VALIDATOR_SUBMISSION_NAMESPACE` is set)", and "keeps a 33-byte reserve … within the 128-byte limit" with "keeps a 33-byte reserve for ComputeHorde's `<M:…>` manifest within the 512-byte `BigRaw` limit or the optional `COMMITMENT_MAX_BYTES` cap".
- `spec/file-protocol.md` (~line 73), `localnet/README.md` (~398, ~463), `validator/README.md`, `installer/README.md`, `refinery/README.md`, `production/README.md`: replace 128-byte/`RawN`/namespace statements with the same facts. In `production/README.md` "Before the first commitment", add the ComputeHorde 512-byte release requirement and the cap.
- Leave `docs/implementation-handoff.md`, `spec/evidence/*` and older plans unchanged: they record history.

- [ ] **Step 5: Manifest and checks**

```bash
uv run --project validator python installer/release.py manifest "$PWD"
uv run --project validator python installer/release.py manifest "$PWD" --check
grep -rn "128-byte\|128 bytes\|RawN\|Raw128\|SUBMISSION_NAMESPACE" --include=*.md --include=*.example --include=*.yml . | grep -v "docs/superpowers\|evidence\|implementation-handoff"
```

Expected: `--check` exits 0; the grep shows only the transitional validator namespace mentions.

- [ ] **Step 6: Commit, push, PR**

```bash
git add -A miner envs production refinery AGENTS.md spec localnet validator installer
git commit -m "Document full image references and the commitment cap"
git branch --show-current   # must print full-image-commitments
git push -u origin full-image-commitments
gh pr create --base master --title "Commit full image references" --body "Miners now commit their factory as ghcr.io/OWNER/PACKAGE@digest, so images from any GHCR owner can take part. Commitments may use up to 512 bytes; operators whose hotkey also runs ComputeHorde set COMMITMENT_MAX_BYTES=200 to stay within the shared commitment quota. Validators still read the old package-only envelopes while SUBMISSION_NAMESPACE is set. Requires the Pylon release with BigRaw support and, on shared hotkeys, ComputeHorde's 512-byte release."
```

---

### Task 8: Demo miners use the new submitter settings

**Goal:** The demo-miner server stops passing `SUBMISSION_NAMESPACE` and sets the cap on production, where its hotkeys are shared with ComputeHorde.

**Files:**
- Modify: `/home/jan/Projects/reef/factory-horde-demo-miners/server/docker-compose.yml`, `server/.env.example:32-34`, `server/README.md:70,76`

**Acceptance Criteria:**
- [ ] The submitter service no longer derives or passes `SUBMISSION_NAMESPACE`; it passes `COMMITMENT_MAX_BYTES: "${COMMITMENT_MAX_BYTES:-}"`.
- [ ] `.env.example` documents `COMMITMENT_MAX_BYTES` (200 on production, unset on Refinery) and that `SUBMITTER_IMAGE` must be a release with full-reference envelopes.
- [ ] `docker compose -f server/docker-compose.yml --env-file server/.env.example config --quiet` succeeds with dummy values.

**Verify:** `cd /home/jan/Projects/reef/factory-horde-demo-miners && docker compose -f server/docker-compose.yml --env-file server/.env.example config --quiet` → exit 0

**Steps:**

- [ ] **Step 1:** Branch `full-image-commitments` from `master`.
- [ ] **Step 2:** In `server/docker-compose.yml`, delete the `SUBMISSION_NAMESPACE` environment entry of the submitter service and add `COMMITMENT_MAX_BYTES: "${COMMITMENT_MAX_BYTES:-}"`.
- [ ] **Step 3:** In `server/.env.example`, replace lines 32-33 with `# The submitter release for this chain, as pinned in factory-horde's miner/docker-compose.yml. It must commit full ghcr.io/OWNER/PACKAGE references.` and add the `COMMITMENT_MAX_BYTES` block from Task 7 Step 2 (production wording).
- [ ] **Step 4:** In `server/README.md`, change the "Submitter release" bullet to require full-reference support, and in the production paragraph require ComputeHorde's 512-byte release and `COMMITMENT_MAX_BYTES=200`.
- [ ] **Step 5:** Run the verify command, commit `Use full-reference submitter settings`, confirm the branch, push, and open a PR against `master`.

---

### Task 9: Releases, live localnet acceptance and rollout (user-performed steps marked)

**Goal:** Every component is released in an order in which no running writer or reader breaks, and the live localnet checks pass against the released images.

**Files:** none beyond pin updates already covered in Tasks 3, 6 and 8.

**Acceptance Criteria:**
- [ ] turbobt release on PyPI contains `_commitment_field` (user/maintainer publishes).
- [ ] Pylon PR merged with the released turbobt; service image and client released (user/maintainer).
- [ ] ComputeHorde PR merged and released; every ComputeHorde miner whose hotkey commits a FactoryHorde envelope on SN12 runs it before Step 6.
- [ ] `localnet/check_submissions.py` and `localnet/check_adversarial.py` pass against the new Pylon pin and a submitter built from the branch.
- [ ] Production validator runs the new release before any production miner commits a full reference; the next discovery snapshot after miners resubmit shows every demo miner in the cohort with its `ghcr.io/…` reference.

**Verify:** `uv run --project validator python localnet/check_submissions.py` and the adversarial check (see `localnet/README.md` for the exact invocations) → both pass

**Steps:**

- [ ] **Step 1 (user):** merge the turbobt PR and publish the release; replace Pylon's git dependency with it (`uv add "turbobt>=<version>"`), merge Pylon and publish the service image and client.
- [ ] **Step 2:** update the Pylon pins (Task 3 Step 6, Task 6 Step 1) to the published digest.
- [ ] **Step 3:** run the live localnet checks with `localnet/build-submitter.sh` and the new pins, as described in `localnet/README.md`. Record the output in the PR.
- [ ] **Step 4 (user):** merge and release ComputeHorde; upgrade the ComputeHorde miners on SN12 whose hotkeys also mine FactoryHorde (the two demo-miner hotkeys).
- [ ] **Step 5 (user):** merge FactoryHorde and trigger the production image builds (`deploy-build-production`), then promote `deploy-config-production`; the validator updater picks up the new release. Keep `SUBMISSION_NAMESPACE` set on validators for now.
- [ ] **Step 6 (user):** update the miner servers: new `miner/docker-compose.yml` from the release manifest, `COMMITMENT_MAX_BYTES=200` on production, and the demo-miner server from Task 8. The scheduled submitter rewrites each envelope to the full reference on its next run.
- [ ] **Step 7:** after one round in which every miner was discovered with a full reference, open a follow-up PR that removes `SUBMISSION_NAMESPACE`, the legacy decoding branch and the `legacy` fixture section.

---

## Self-review

- Coverage: BigRaw in turbobt (Task 1) and Pylon (Task 2); ComputeHorde 512 (Task 3); full owner/package/digest and the optional cap defaulting to no limit (Task 4); validator decoding (Task 5); localnet proof (Task 6, 9); docs and env (Task 7); demo miners (Task 8); release order (Task 9).
- External-miner requirement: with `COMMITMENT_MAX_BYTES` unset, the only limit is the chain's 512 minus ComputeHorde's 33-byte reserve, which a GHCR reference only reaches with an owner and package of over 400 bytes combined.
- Names used across tasks: `CHAIN_COMMITMENT_MAX_BYTES`, `COMPUTE_HORDE_MANIFEST_RESERVE`, `encode_envelope(image, max_bytes)`, `merge_envelope(current, envelope, max_bytes)`, `submit(..., max_bytes=, confirm_seconds=)`, `decode_reference(value, legacy_namespace=None)`, `COMMITMENT_MAX_BYTES` (env and validator constant), `_commitment_field` (turbobt).
- Values known only after a release (Pylon version and digest, turbobt version, ComputeHorde PR) are obtained with the commands given in the steps.
