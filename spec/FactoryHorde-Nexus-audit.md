# FactoryHorde / Nexus audit

**Audit date:** 25 September 2026  
**Scope:** the supplied `nexus-subnet-template.zip`, `bittensor-nexus-library.zip`, and `draft-specification-2.md`. This is an audit of those snapshots, not a claim about the latest upstream implementation.

## Executive conclusion

Nexus is a useful agent-guided validator scaffold and extensible runtime. **The supplied combination is not a ready-made FactoryHorde generator, and its current completion criteria do not guarantee a whole subnet.** There is a feasible implementation path, but some prompt rules need explicit exceptions, shared library behavior needs correction, and most FactoryHorde-specific execution and evaluation components still need implementation.

The most consequential findings are:

1. The design rules explicitly exclude miner code and reference implementations, although FactoryHorde requires an accessible baseline miner/factory. The workflow implements a validator and localnet fixtures, not an explicit complete miner deliverable.
2. The template's settings model fails the supplied library's Pylon-settings subtype check. This was reproduced with an isolated exact-source probe.
3. The shared weight setter and its scheduling/status path do not forward a mechanism ID. The status interface defaults to mechanism 0. This is unsafe to assume compatible with FactoryHorde's mechanism 1 requirement alongside Compute Horde.
4. Container execution appears as knowledge-base guidance, not an implemented secure execution service. The recipe injects a provider API key into the container, contradicting FactoryHorde's credential-hiding proxy contract.
5. The library's default context and result stores are in-memory despite documentation promising persistence across restarts.
6. The template contains no test function definitions, and its supplied CI builds/publishes an image without running lint, type checking, or tests. Its localnet instructions permit mocked supporting services.

These are not equivalent kinds of failure: some are source-confirmed defects, some are incompatible generic instructions, some are ordinary missing application features, and some are unresolved product/security decisions. The findings below preserve that distinction.

## What was actually checked

Both archives were extracted and inventoried. Python syntax was parsed for 10 template files and 161 library/demo/test files; no syntax errors were found under the available Python 3.13.5 interpreter. Six shell scripts passed `bash -n`. The template has zero test function definitions; the library and its demo have 228 definitions. **Those counts are not a test-suite result.**

Dependency-isolated probes executed selected source class/method bodies with real Pydantic settings and fake Pylon dependencies. They reproduced the settings mismatch, recorded the weight writer omitting a mechanism argument, and demonstrated that a mechanism-0 submitted status suppresses the supplied beat method in the constructed scenario.

**Not performed:** full dependency installation, Copier rendering, the repository's test suites, Python 3.14 execution, Docker/localnet startup, real provider calls, live weight submission, security-boundary testing, or deployment. Python 3.14, Docker, Copier, and the runtime dependency set were unavailable; the package-network DNS check also failed. No production wallet or service was touched. See [recorded results](audit-results.json), [reproduction script](audit_probes.py), and [input hashes](source-manifest.json).

Evidence references E02–E18 link to exact archive excerpts with original file line numbers in [source-evidence.html](source-evidence.html). `T/` means the template archive; `L/` means the library archive. The specification is reproduced in E17 for an offline record.

## 1. The correct way to use the template

### 1.1 Treat it as instructions for a coding agent, not a finished generator

`AGENTS.md` directs an agent through `knowledge/tasks.project-bootstrap.md`: render, design, implement validator, adapt localnet, build, and release. There is no automatic assurance that reading these prompts produces a complete miner, validator, executor, and evaluator. Running the existing `validator` command executes a ping demonstration, not code generation. [E02](source-evidence.html#e02), [E05](source-evidence.html#e05)

Give the agent the FactoryHorde specification, both source roots, and a written acceptance contract. Separate design approval from implementation and deployment authorization. The draft specification is authoritative about intent, but explicitly leaves decisions open; record those decisions instead of letting an agent silently choose an unrelated tutorial architecture.

### 1.2 Render once into a fresh sibling directory

The template's documented command, from its root, is:

```sh
uv run --with copier copier copy . ../factoryhorde
```

This command was inspected, not executed here. Supply the actual repository/image identifiers through Copier. Rendered output should contain no `.jinja` inputs or `copier.yml`; use the full set of template inputs, not only the slightly incomplete six-file checklist in the prose. Continue in the destination, leaving the original archive extraction unchanged. Do not simply rename `.jinja` files. [E02](source-evidence.html#e02)

Use separate configurations for local and deployment environments. The provided bootstrap expects the locally allocated subnet, normally netuid 2; the deployment requirement is subnet 12. Mechanism 1 needs a new explicit configuration path—it is not a Copier input in this snapshot.

### 1.3 Explicitly select the supplied library

The template manifest points at a Git repository, and its lockfile resolves Nexus to `0.0.1.dev165+ge1f0b6829`, commit `e1f0b682968a09742e82215d9a512572ba33b7e5`, with Pylon client 2.1.0. The supplied library requires Pylon client 2.3.0. Localnet also pins the Pylon service to 2.1.0. Therefore, merely putting both ZIPs beside each other does not make the validator use the supplied library. Version skew alone does not prove all client/server calls fail, but compatibility is unverified. [E06](source-evidence.html#e06)

For the sibling layout below, a development source override in `factoryhorde/validator/pyproject.toml` can replace the Git source:

```text
work/
  nexus-subnet-template/
  bittensor-nexus-library/
  factoryhorde/
    validator/
    miner/
```

```toml
[tool.uv.sources]
bittensor-nexus-library = { path = "../../bittensor-nexus-library", editable = true }
```

Then regenerate and review the validator lockfile; verify the imported module path and installed distribution versions. This is a proposed development setup, not a tested installation command sequence. Preserve the template's dependency-age security policy.

Use Python 3.14 and run `uv` project operations inside `validator/` and `miner/`; they are independent projects, not a root workspace. Read the Nexus docs corresponding to the actual installed source. Reuse `nexus.v1` and Pylon rather than copying private Nexus internals into application code. The shipped `demos/cat-images` provides examples of task/scoring/weight composition, not a FactoryHorde implementation or a suitable reward formula by default. [E02](source-evidence.html#e02), [E12](source-evidence.html#e12), [E16](source-evidence.html#e16)

Before an image build, replace the editable sibling source with a reproducibly available pinned commit/wheel, or deliberately change the build context to include that source. The current image build context is `./validator`; it cannot see a sibling directory by magic. [E14](source-evidence.html#e14)

### 1.4 Resolve instruction conflicts before implementation

`T/knowledge/bittensor/design_flow.yaml:77–100` excludes `miner_code` and `reference_implementations`, makes `no_miner_code` mandatory, and requires the generic miner-hosted-endpoint pattern. Earlier rules warn against validator-controlled ground truth and forbid secret evaluation sets. In contrast, FactoryHorde requires an independently runnable factory submitted as a container and evaluated by a trusted owner validator. The container-specific recipe also contradicts the generic compute advice. [E03](source-evidence.html#e03), [E04](source-evidence.html#e04)

Add a project-specific precedence rule: preserve FactoryHorde's container asset, owner-operated evaluation, baseline miner, proxy-only credentials, and private submissions even where generic patterns disagree. Private submission details are not automatically the same thing as a secret benchmark. Task secrecy/overfitting policy remains a design decision, not permission to silently remove the specification's privacy model.

### 1.5 Add the missing miner and execution phases

A complete FactoryHorde deliverable should include a baseline factory image; miner tooling to publish its image and encrypted recipient manifest and commit the manifest URL; validator orchestration; isolated execution and app evaluation; the credential-hiding proxy; durable state/artifact handling; and a reproducible localnet acceptance runner. Localnet fixtures remain separate from the reusable baseline factory. [Specification §2, §5, §8; E17](source-evidence.html#e17)

Use a staged implementation: approved contract and capability inventory → shared Nexus fixes → one complete vertical slice → adversarial/economic checks → deployment validation. The accompanying [agent-run-contract.md](agent-run-contract.md) makes this workflow explicit without pretending these phases are already implemented.

## 2. Detailed findings and FactoryHorde fit

### F01 — Settings compatibility failure: fix before the first run

The template declares `class Settings(BaseSettings)` at `T/validator/src/validator/main.py:29`. The supplied environment Pylon providers call `get_subnet_settings_as(PylonClientSettingsMixin)`. The registry checks `isinstance` and rejects this template model with:

```text
Subnet settings Settings do not implement PylonClientSettingsMixin.
```

The isolated probe reproduced that check and showed that adding the mixin resolves this particular subtype failure. Preserve the existing settings and inherit the public `PylonClientSettingsMixin`, supply its required address/open-token and paired identity fields, and fail configuration validation before worker threads start. Environment variables alone do not change the model's type. This is a confirmed incompatibility between these supplied snapshots, not proof that the template's differently locked upstream revision has the same defect. [E07](source-evidence.html#e07)

### F02 — Mechanism 1 is not wired through shared Nexus weight handling

`L/src/nexus/_internal/actors/weight_setter.py:99` calls `put_weights({**weights})`. `chain_beat/set_weights_beat.py:148` polls `get_weights_status(block_number=...)` without a mechanism. The latter's Nexus protocol defaults to `MechanismId(0)`, and the fake client mirrors that default. The node constructors do not expose a mechanism parameter. [E08](source-evidence.html#e08)

The probe recorded the omitted write argument and a mechanism-0 poll. In the constructed state, mechanism 0 already having submitted weights caused zero beat events, even though mechanism 1 was the intended workload. This was not a real chain test. Depending on the installed Pylon behavior, an omitted write mechanism might use a default or fail; it does not explicitly select mechanism 1.

Patch settings → node construction → status poll → write call → fake clients/tests. Scope cached submission state and persistence by netuid, mechanism, epoch, and identity as appropriate. A correct write alone is insufficient if its scheduler still sees mechanism 0 as already submitted.

External cross-check O01: current official Pylon documentation lists `put_weights(weights, mechanism_id)` and commitment read/write APIs. Thus the task is not to assume those blockchain capabilities are absent; it is to integrate and contract-test a pinned Pylon client/service pair. Current documentation does not certify the archived client/service combination.

Before subnet-12 deployment, independently verify mechanism-1 weights and allocation, preserve mechanism-0 state and existing Compute Horde behavior, and check shared hotkey/nonce/commitment/registration interactions. The existing Compute Horde configuration was not supplied, so coexistence cannot be certified by this audit. Zero emissions is a mechanism-allocation requirement, not a reason to skip proving weight submission.

### F03 — No delivered secure factory/container execution stack

The knowledge base describes a Basilica/Chutes `Actor.evaluate` pattern. It is not a ready-to-use Nexus runtime adapter implementing FactoryHorde's input-volume/output-volume protocol, private image retrieval, or isolation contract. The library has reusable actors, tasks, retry/timeout primitives, HTTP communicators, Pylon providers, S3 helpers, and an outbound OpenRouter client. I found no shipped implementation of the full required untrusted factory execution service. [E04](source-evidence.html#e04), [E10](source-evidence.html#e10), [E15](source-evidence.html#e15), [E18](source-evidence.html#e18)

`EmbeddedExecutorCommunicator` directly calls a Python function in the validator process. It is appropriate for trusted code, not a sandbox for miner factories or generated applications. An adapter can orchestrate an external isolated executor from Nexus; both factory generation and application execution must stay behind the specified security boundary. Reusing an existing executor is possible, but must be demonstrated rather than inferred from the presence of container documentation.

Minimum contract decisions: image retrieval/access controls; digest pinning; input schema and output repository format; working directory; dependencies/build policy; CPU/RAM/process/disk/time limits; cleanup; artifact hashing and storage; crash outcomes; job identity and cancellation. Containerization alone is explicitly insufficient in the user specification. [Specification §2 and §7; E17](source-evidence.html#e17)

### F04 — Proxy-only secrets and private submission protocol are missing

The container recipe puts `CHUTES_API_KEY` into the container environment. FactoryHorde instead requires the factory to make model requests without provider credentials, while an operator proxy supplies the authorized run's credentials. A normal outbound OpenRouter client does not satisfy this inbound proxy role. [E04](source-evidence.html#e04), [E10](source-evidence.html#e10), [Specification §2; E17](source-evidence.html#e17)

Implement recipient-bound encrypted entries, authorized-key discovery/rotation, schema/version validation, image location/access controls, multi-provider credentials, and an on-chain commitment adapter. Do not assume a Bittensor signing identity automatically specifies an encryption algorithm or key-distribution protocol; that detail is open.

The proxy needs authenticated per-run access, approved provider routing, no arbitrary miner endpoint selection, external egress enforcement, request/response limits, provider-specific adapters where needed, streaming/tool-call compatibility for the baseline, spend accounting/caps, and secret-redacted logs. Provider keys should not enter factory environments, generated repositories, task context records, or public result artifacts. Approved validators remain trusted with plaintext credentials, as the specification explicitly states.

A committed URL does not freeze mutable JSON content. Define how a round snapshots the manifest bytes and image digest; distinguish adding new recipient entries from changing the factory/credentials being evaluated. This is an audit recommendation, not a schema already settled by the draft.

### F05 — Durable recovery is not provided by default

`L/docs/nexus.md` says contexts and task results survive restarts. The default result provider constructs `InMemoryTaskResultStore`; `SubnetBuilder` uses `InMemoryContextStorePersistence`; pending HTTP requests are also in-memory by default. The public convenience validator builds a `SubnetBuilder` without injecting a persistent context store. Interfaces exist for extension, but their existence is not durable storage. [E09](source-evidence.html#e09)

Add a durable backend and public configuration/injection path, or a durable execution-state service integrated with those interfaces. Persist submission snapshots, tasks, attempts, artifacts, score history, budgets and pending chain operations, using secret references rather than plaintext keys. Test actual process termination and fresh-process restart. Jobs must not be blindly rerun, double-scored, or billed twice after an uncertain completion.

This is not necessary merely to print one successful toy run, but it is an operational-release blocker and a misleading capability claim to fix within Nexus.

### F06 — Miner, evaluator, and economics are application work, not supplied implementations

The starter validator sends ping payloads and logs responses; it does not connect a scorer or a weight setter. The starter miner returns its input, hardcodes localnet netuid 2/Alice development funding, and even suggests plausible mock data for heavy work. It must not be treated as a publishable FactoryHorde baseline. [E05](source-evidence.html#e05)

The specification intentionally leaves task construction, evaluator methodology, thresholds, duplicate/tie handling, and costs open. Nexus can orchestrate a scoring pipeline but cannot provide evidence that its score measures application correctness merely by running that pipeline. [Specification §4, §5, §7; E17](source-evidence.html#e17)

Use common task specifications across a comparison cohort, explicit requirement-level tests, controlled evaluator versions, artifacts proving results, and calibrated partial-credit rules. Keep the two clocks separate: new generation tasks are proposed at two or three per day, while chain weight opportunities occur on the chain's own schedule. Decide score freshness/rollover explicitly; do not copy a previous-epoch-only example and unintentionally discard almost all sparse evaluations.

Duplicate handling needs its own experiments. Replicating an equivalent factory across multiple UIDs should not increase its combined expected reward under the chosen policy. Image hashes only detect exact copies, not functionally equivalent repackagings; a per-UID dust rule can reintroduce replication incentives. The specification offers starting ideas, not a solved anti-duplication formula.

The intended Pi project/version and actual provider compatibility should be fixed during implementation. External cross-check O02/O03: OpenRouter documents key/request limits and variable free-model availability, while `openrouter/free` selects among available models. Count requests across an entire agent loop, not just tasks per day; record the actual model and separate rate-limit/provider failures from factory correctness. Do not promise the proposed free baseline is production-reliable without measurements.

### F07 — Single owner validator is compatible; multi-validator coordination is not a V1 blocker

The supplied localnet bootstraps one validator, and the runtime does not require a multi-validator coordination protocol. That aligns with the initial trusted-validator arrangement. It does not prevent other on-chain validators from existing; access control comes from authorized encrypted recipient entries. Do not add independent-score consensus, TEE integration, or long-horizon development sequences as first-milestone prerequisites. They are future scope in the supplied draft. [E11](source-evidence.html#e11), [Specification §1 and §3; E17](source-evidence.html#e17)

### F08 — Completion claims can be false positives

Localnet's own acceptance text has a strong requirement: verify chain state directly against subtensor, bypassing Nexus and Pylon. Preserve it. But the same knowledge bundle permits supporting services to be mocked, and adversarial miner profiles are TODO in the README. Mocks are useful for unit and deterministic integration tests; they cannot stand in for the FactoryHorde milestone's encrypted submission, isolated execution, proxy access, generated repository, evaluation and real weight path. [E11](source-evidence.html#e11)

There are no template test function definitions; the only supplied CI workflow builds/publishes an image. Add independent required checks. Additionally, actor startup runs in threads, and the validator's top-level run loop can remain alive without demonstrating productive work. Require actor readiness, last-success timestamps, error rates and end-to-end progress—not just a container status or the message “Validator running.” [E12](source-evidence.html#e12), [E13](source-evidence.html#e13)

The production compose file still has an all-zero validator image digest. The workflow expects promotion to replace it. An image build or installer file existing is not release evidence. [E14](source-evidence.html#e14)

## 3. How to evaluate an agent-generated result

Evaluate four independent questions: did the agent follow the intended contract; did it deliver/build the necessary components; do those components execute the real subnet workflow; and does the evaluation/reward mechanism distinguish useful factories from failures and gaming? Passing one does not imply the others.

The [acceptance-matrix.json](acceptance-matrix.json) provides named proposed checks, expected evidence and scope. Every operational check is initially `NOT_RUN`; none is presented as passed by this audit.

| Gate | Required evidence | Interpretation |
|---|---|---|
| G0 — Sources and contract | Source hashes/lockfiles, actual import path, agreed FactoryHorde decisions, rendered repository, component ownership | Correct use of the supplied materials, not accidental use of another Nexus revision |
| G1 — Code/build | Non-mutating lint/format checks, strict typing, meaningful nonzero tests, build of every delivered component, shared schema contract tests | Necessary but insufficient for behavioral correctness |
| G2 — Submissions/privacy | Real URL commitment, authorized decryption, unauthorized/tampered rejection, fixed evaluated manifest and image, no secret leakage | The private reusable asset contract works |
| G3 — Execution/proxy | Actual baseline image, input spec volume, generated repo output, application evaluation, an approved provider through the real proxy, credential-swap demonstration | The central FactoryHorde path is real, not fixtures returning a fake repository |
| G4 — Security controls | Denied direct egress/host/metadata/validator access; time/resource/spend limits; cleanup; malicious output handling | Required controls exercised locally; not a certification of sandbox security |
| G5 — Evaluation/economics | Known-good/partial/broken/deceptive controls, common tasks, requirement evidence, repeatability observations, duplication/tie/all-fail tests | Scores and weights reflect the chosen intended behavior |
| G6 — Chain | Local mechanism-1 proof from subtensor, mechanism-0 sentinel unchanged, identity/subnet mapping, confirmed inclusion/status beyond an HTTP acknowledgment | Actual on-chain effect, including correct mechanism |
| G7 — Recovery/health | Fresh-process restart at failure boundaries, no lost scores/double billing, actor/queue liveness, classified failures | Unattended operation rather than a single happy-path demo |
| G8 — Deployment | Tested immutable images, clean-host install and rollback, compatible service versions, zero allocation verified, Compute Horde non-interference | Separate deployment approval; not inferred from localnet |

Use all applicable gates as hard gates rather than averaging them into a success percentage. `BLOCKED`, `NOT_RUN`, or an evidence gap is not `PASS`. Some gates have a local-milestone subset and a stricter release extension; keep those statuses separate. In particular, a generic default-mechanism localnet run is useful plumbing evidence but does not satisfy FactoryHorde's mechanism-1 verification requirement.

### Evaluating Nexus as an agent-driven build process

For each fresh agent build, record the agent/harness/model configuration, prompt and input hashes, source/dependency selection, output commit, independently obtained acceptance results, manual interventions, repair effort, agent cost and elapsed build time. Repeat against the same frozen acceptance harness to measure build reliability. Keep this success rate separate from miner factory quality and from stochastic generated-app evaluation. Do not count changes that weaken the test contract as improvements in build success.

### A concrete first end-to-end scenario

Start clean local infrastructure with an owner validator and controlled miners. One miner publishes an encrypted manifest for a real runnable baseline factory. Commit the URL, let the production validator discover/decrypt it, snapshot the submission, run a small self-contained app task in the chosen isolated environment, collect its actual repository, and run requirement-level checks on the app in a separate isolated evaluation environment. Record generation and validation costs separately. Set mechanism-1 weights and verify them directly from subtensor. Include known-broken and partial controls so “everyone gets the same success score” cannot pass unnoticed.

Deterministic stub providers are valuable for contract tests. At least one separately labelled real-provider run is needed to show the chosen baseline works through the implemented model proxy; it should use a preapproved budget, not production secrets. Swapping to customer-supplied credentials must not require rebuilding the factory image. The exact repeat counts, quality thresholds and numerical limits remain decisions to freeze before the corresponding acceptance run.

### An independent success oracle

Do not let the generating agent or the untrusted factory define its own pass result. The verifier should inspect actual process exits, produced files and hashes, externally run tests, proxy records and chain state. Preserve holdout inputs outside the implementation agent's editable acceptance fixtures where appropriate. Include factories that fabricate “tests passed” logs, generate attractive but nonfunctional interfaces, or place instructions to the evaluator in repository content. Check that changing a required feature changes the score and that swapping hotkeys/UIDs does not arbitrarily change task quality results.

Calibrate the evaluator against known-correct, deliberately incomplete and deliberately broken applications, then compare actual OSS factory versions as the specification proposes. Repeated-run variance and provider nondeterminism should be observed and documented; they are not automatically miner misconduct. No benchmark result or economics validation was available for this audit.

### Required run record

For every accepted evaluation, retain: run/task IDs; specification and evaluator versions/hashes; miner hotkey and UID at a recorded block; frozen submission-manifest hash; image digest; provider/model identifiers; execution limits and attempts; repository/artifact hashes; requirement-level results; generation/validation cost and token usage; classified errors; raw-to-final score transformation; chosen weight vector; and independent chain evidence with subnet, mechanism and block reference. Persist credential references only, never provider keys or decrypted recipient payloads in the evidence bundle.

## 4. Recommended implementation order

**First: make the prompt and dependency contract truthful.** Add the explicit baseline-miner phase and FactoryHorde exceptions; select the exact supplied library; repair the Pylon settings model; pin and test the client/service combination; add mechanism-aware setters and status polling; add source/build/contract CI.

**Second: build one real vertical slice.** Freeze a minimum envelope/image/volume/proxy/security contract. Implement the generic execution/proxy/submission adapters in Nexus or reusable services with Nexus adapters, and FactoryHorde-specific baseline/task/evaluation logic in the subnet. Demonstrate the entire local milestone with real artifacts and chain proof, not a comprehensive product interface first.

**Third: make repeated operation safe and informative.** Add persistent state, restart/idempotency tests, classified failures, spend controls, evaluator calibration and duplicate/reward experiments. Keep minimum local controls distinct from production security assurance.

**Fourth: validate coexistence before enabling the planned deployment.** Use the existing team's authorized identity arrangement without copying bootstrap assumptions onto mainnet; prove correct mechanism selection, zero allocation and preservation of the existing Compute Horde service; use an immutable tested image and rollback procedure.

**Bottom line:** use Nexus as the orchestration foundation, but change the definition of done to independently observed FactoryHorde behavior. The first decisive proof is the specified encrypted-submission → isolated factory → actual repository → isolated evaluation → mechanism-correct weight sequence. An agent declaring completion, a running validator, an image build, or mocked localnet scores are not substitutes.

## External verification references

The uploaded sources remain the basis of this audit. These current primary sources were consulted only for specific integration checks; they do not replace the archived code:

- **O01:** official bittensor-pylon `docs/CLIENT.md`, retrieved 25 September 2026: mechanism-aware weight submission; commitment APIs; unstable API warning.
- **O02:** OpenRouter API credit/rate-limit documentation, retrieved 25 September 2026.
- **O03:** OpenRouter Free Models Router documentation, retrieved 25 September 2026.

Exact URLs and the limited claims derived from them are retained in [external-sources.json](external-sources.json).
