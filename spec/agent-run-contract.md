# FactoryHorde agent-run contract

This is a proposed instruction set for a future coding-agent run. It is not generated subnet code and does not assert that any operational acceptance test has passed. Preserve `draft-specification-2.md` as the authoritative product intent, and record decisions where that draft explicitly leaves choices open.

## Initial task for the implementation agent

> Build FactoryHorde using the supplied Nexus template and helper-library source. Read `draft-specification-2.md`, the repository's `AGENTS.md`, `knowledge/tasks.project-bootstrap.md`, and the installed docs for the exact selected Nexus revision at the appropriate workflow phase. First render the template into a fresh sibling repository using Copier; do not hand-edit template filenames into a supposed rendered state. Keep a record of input hashes, dependency resolution and the actual imported Nexus source.
>
> For this project, the FactoryHorde specification takes precedence over generic knowledge-base patterns. In particular, implement the baseline miner/factory and submission tools despite `design_flow.yaml` excluding miner/reference code; keep validator-controlled isolated execution of submitted factories; keep per-validator encrypted submissions; and keep provider credentials outside the factory, in the authorized model proxy. Do not replace the reusable factory asset with a miner-hosted black-box API.
>
> Use the supplied library deliberately, not whichever Git revision the original template lockfile happens to resolve. Repair missing shared capabilities in Nexus and its tests; do not patch `.venv` or copy private internals into the subnet as a shortcut. Coordinate dependency and Pylon-service versions, and preserve the dependency-age security policy. Development editable-source overrides must not survive as unreachable paths in the production Docker build.
>
> Treat the first local milestone and later deployment as separate acceptance levels. Localnet must exercise the real critical path: encrypted commitment discovery, image retrieval, isolated generation with input/output volumes, actual repository collection, isolated application evaluation, and mechanism-correct weight setting. Unit tests may mock dependencies; the milestone must not replace the factory, sandbox, proxy, evaluator or chain write with canned success output. Use test credentials and a preapproved spending budget for any real-provider check.
>
> Return a decision log and requirement-to-component-to-test matrix before implementing open choices that materially affect security, costs, scoring or rewards. Identify unresolved choices as such; do not claim they came from the draft. Do not redesign away the owner's initial single-validator trust model. TEE integration, multi-validator score coordination and long-horizon application maintenance are not V1 prerequisites.
>
> Stop at the agreed review gates. Do not deploy to mainnet, alter Compute Horde, authorize spending beyond the agreed cap, publish credentials, or change mechanism emission allocation without separate authorization. Localnet wallet/bootstrap assumptions must never be applied to the production hotkey.

## Phase 0 — Reproducible sources and approved minimum contract

Deliver an input manifest with source hashes/revisions, agent/harness/model version and settings, full prompt references, supported Python version and resolved library/service versions. Verify the rendered output contains no `.jinja` inputs or `copier.yml`. Verify both Python project environments separately.

Specify the minimum runnable contract: recipient identity/key discovery and encryption; submission manifest and update/freeze semantics; image pull authentication and digest pinning; volume schemas and runtime command; artifact ownership/storage; sandbox boundary; networking/model proxy; limits, timeouts and budget; initial task cohort; evaluator and requirement scoring; initial threshold/duplicate/tie/all-fail policy; score lifetime; and mechanism/hotkey mapping.

Document any remaining open decisions with an owner and effect on acceptance. A decision left open cannot be replaced by a passing check for a different implementation.

## Phase 1 — Shared Nexus corrections

Make configuration validation fail before worker startup. Fix the `PylonClientSettingsMixin` incompatibility. Add explicit mechanism configuration across weight nodes, status polling, Pylon protocols, fake clients and tests; include a test where mechanism 0 is submitted and mechanism 1 is not. Verify the actual client/server combination rather than assuming semantic-version similarity proves compatibility.

Add the generic submission/executor/proxy integration contracts needed by the chosen architecture. Distinguish trusted orchestration code from untrusted workload code; do not use the in-process embedded executor as a sandbox. Plan a durable state backend and its public configuration path. Add required CI for shared library changes.

Deliver a capability ledger: implemented and verified, implemented but unverified, planned, or explicitly out of scope. The ledger must distinguish documentation recipes from executable capabilities.

## Phase 2 — One real FactoryHorde vertical slice

Produce the baseline factory container and miner publishing/commitment CLI, validator, actual sandbox runner and proxy, artifact storage, task/evaluation implementation and localnet integration. A miner fixture is not the baseline product.

Use a tiny self-contained app specification with predetermined requirements, a real factory and known-good/partial/broken controls. Discover the encrypted manifest via an actual local-chain commitment. Authorize the owner's validator, reject a different validator, freeze manifest and image, then execute the factory using the input volume and collect the generated repository from the output volume. Run the application/tests in the evaluation sandbox. Retain requirement evidence and generation/validation costs separately.

Exercise an approved provider through the actual proxy with a fixed budget in a separately labelled integration run. Demonstrate the same image running with replacement customer credentials without baking credentials into the image. Verify no provider credentials appear in the workload environment, output, logs or public artifacts.

Set and verify mechanism-1 weights directly against subtensor, independent of Nexus/Pylon logging. Keep a mechanism-0 sentinel unchanged. Document localnet chain limitations, including commit-reveal differences, rather than claiming parity with deployment from a default configuration.

## Phase 3 — Independent acceptance, failures and recovery

Run the checks in `acceptance-matrix.json`; each result requires a recorded expected behavior, actual observation, source version and evidence location. Preserve the original acceptance criteria in review. Any necessary correction to a test must be independently reviewed and cannot simply turn a failing requirement into a pass.

Add malformed/tampered/unauthorized submissions, failed pulls, missing/invalid output, provider failures, timeouts, exhausted quotas, duplicate callbacks/results, malicious repository content, no-eligible-miner/all-fail cohorts and incorrect-mechanism attempts. Failures must be classified; infrastructure and provider failures must not silently become accusations of miner misconduct.

Kill and restart fresh processes at job boundaries. Preserve completed artifacts and scores and reconcile ambiguous attempts before repeating billed work. Check jobs, budgets, scores and chain writes for idempotency. Require actor/queue readiness and last-success metrics, not just a living container.

The independent reviewer runs the production validator path from a clean checkout using fixed input hashes and checks actual generated applications and chain state. The generating agent's final message is not an acceptance artifact.

## Phase 4 — Deployment gate, separately authorized

Build and pin tested immutable images, replacing the template's zero digest. Run clean-host install and rollback tests. Verify the selected Pylon service supports the exact client and required mechanism APIs. Check the actual subnet-12 mechanism arrangement, zero allocation and signing identity; preserve existing Compute Horde services, resource budgets, commitments/registrations as applicable, and mechanism-0 behavior. Explicitly coordinate any shared hotkey signing/nonce behavior. Do not run localnet registration/funding helpers against mainnet.

Before admitting arbitrary public submissions, obtain a review of the selected isolation design and proxy controls. Local adversarial tests exercise controls but do not prove the absence of sandbox escapes.

## Required final agent output

Return the output commit, build and input manifests, requirement traceability matrix, decision log, passing/failing/blocked/not-run test report, evidence for the actual local milestone, outstanding risks, and commands for another operator to reproduce the run. Include actual collected application artifacts and independent chain evidence. Use secret references/redaction in every report.

Use these states precisely: `PASS` means the expected result was actually observed; `FAIL` means it was tested and disagreed; `BLOCKED` means a dependency/environment/decision prevented execution; `NOT_RUN` means it has not been executed. Do not count a stub, mocked critical component, documentation paragraph, planned test, or manually asserted score as a passing operational check.

## Comparing multiple Nexus agent runs

To measure the template itself rather than one output, repeat independent fresh builds against fixed input snapshots and an unchanged acceptance harness. Record mandatory-gate pass rate, omitted requirements, human intervention and repair effort, generated-code/runtime defects, agent cost and elapsed build time, and unsupported completion claims. Keep these build-process metrics separate from FactoryHorde's miner factory quality scores and from stochastic variation in generated apps. A higher code coverage percentage alone is not a successful subnet build.
