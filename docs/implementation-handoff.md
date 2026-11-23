# FactoryHorde V2 implementation handoff

The localnet prototype passed its end-to-end acceptance gate on 2 October 2026.
The user selected the existing Ubuntu VM and prohibited preparing another VM.
Acceptance used a separate local chain, wallets, Compose project and data root on
that host. It does not prove provisioning on a fresh operating system.

## Selected source and artifacts

| Item | Selection |
|---|---|
| Monitoring implementation | `aa7b3c8` |
| Coherent installer/update implementation | `24bcb92` |
| Validator/submitter image build source | `f173bfd0c658fa421a540c6caa65a483822eeddd` |
| Immutable installer/configuration revision | `87353ed2e97cf435b6d8210dceb177affe2febfb` |
| Candidate publication/startup evidence | `8ab064d` |
| Packaged acceptance tools and evidence | `1e2be29` |
| Protocol / platform | `1` / `linux/amd64` |
| Host executor | Python 3.14+, one standard-library file |

[Candidate metadata](../envs/candidate/release.json) records all eight runtime image
digests, build revisions, dependency locks, Nexus/Pylon versions and the executor
checksum. [Candidate instructions](../envs/candidate/README.md) use the exact
configuration revision above. Later acceptance/documentation commits do not change
the packaged application or executor bytes. No production configuration branch
has been promoted; building/publishing artifacts is separate from operator release
selection. Subnet 12 and public-chain emissions were not changed.

## Reproduction and evidence

Use the [complete packaged procedure](../localnet/README.md#packaged-candidate-acceptance-on-this-vm)
from the current source checkout. It installs the selected revision, bootstraps
seven isolated identities (owner, validator, five miners), publishes/read-backs five
commitments, runs the production coordinator, independently reads chain weights,
checks monitoring and adversarial cases, exercises an update during detached work,
and collects public evidence. Follow its prerequisite/profile-download instructions;
do not run competing admission writers on one data root.

The [task-17 summary](../spec/evidence/task17-acceptance.json) includes frozen cohort,
Pi/version/runtime proof, accepted scores, raw mechanism vectors, configured default
timing, tested host/tool versions, failed-case outcomes and source-file hashes.
The [sequential task list](../spec/FactoryHorde-v2-sequential-implementation-tasks.md)
links the earlier focused evidence.

The raw public bundle is retained on the acceptance host, outside Git:

```text
localnet/state/task17-evidence.tar.gz
SHA256 ced074e7cfc7de3ec0934232a8662d79a1e10047e504dce06c2ca64ea539f2b4
1602 inventory entries; 350318 compressed bytes
```

The adjacent `.tar.gz.json` inventory hashes every included file. The bundle contains
requests/stops/statuses, generated files, reports, immutable decisions, Docker logs,
independent chain readback and update/fault evidence. Wallets/token files are omitted;
rejected workload links appear only as metadata and are never followed. New runs
have new IDs, times and random scores, so generate a new bundle filename and hash.

Acceptance proved five concurrent baseline factories, Pi 0.87.1 without inference,
60.46–60.59-second factory lifetimes, separate gated judges and stable scores across
restarts. Direct Subtensor reads observed mechanism-1 updates 941 and 1089 with the
same five-recipient vector and unchanged mechanism 0. All ten judge profiles passed,
plus cancellation/storage faults, changed discovery, create/start crashes and missing
Docker evidence. The active executor replacement took 1.68 seconds and preserved
container identities, permanent stops and an accepted score. A real cron run under
UID 1001 proved the exact-unit restart grant and rejection of another unit restart.

QA: 213 validator/executor/installer/harness tests and 22 miner tests passed; nine
optional standalone image tests were skipped. Real published-image coverage is in
the acceptance evidence. Ruff and strict basedpyright passed, with no typing rules
relaxed. Packaging/runtime isolation checks also passed after the final harness edits.

## Subsequent restart and cutoff corrections

Current source interrupts in-flight Docker clients during executor shutdown and
uses a 30-second systemd stop limit with `KillMode=mixed`. Interrupted commands stay
unresolved for reconciliation. The subprocess regression in `executor/test_executor.py`
sends SIGTERM while a 600-second pull remains held, requires exit within three
seconds without releasing it, and checks restart with and without permanent stop
intent. A signal-killed client cannot become a terminal pull failure.

The coordinator now persists factory eligibility against the frozen confirmation
cutoff, including pending evidence. Docker finish time decides whether completion
was timely; observation after restart does not extend the cutoff. Regression tests
cover both sides of the inclusive boundary, actual minute-70 completion, delayed
observation, missing chain beats, concurrent result publication, and score rejection
if an ineligible judge request already exists. Default stage intervals are unchanged.

These are source-level corrections after the packaged acceptance above. Their
subprocess and coordinator tests do not constitute a new systemd/localnet acceptance
run or update the selected candidate images/revision.

## Operating boundaries

Full installation uses `<installation>/data` on the host, mounted into the validator
at `/var/lib/factory-horde`; source development uses `localnet/state/data` instead.
For this accepted run the installation is `localnet/state/acceptance`. The trusted
operator owns the tree; matching numeric UID/GID is supplied to workloads. Only the
host executor has Docker access, only Pylon receives wallets, and workloads receive
only their fixed job mounts. See [installer ownership](../installer/README.md#requirements-and-ownership)
and the [wire contract](../spec/file-protocol.md).

Dispatch and weight writes remain independently opt-in. Default stages are 3,600,
300 and 3,300 seconds, with a 300-second judge stop reserve and 60-second stop grace.
The baseline acceptance run used 100/15/65-second stages; adversarial rounds used
20/5/30. Changing settings never changes a persisted plan. The
[validator guide](../validator/README.md#observe-and-restart-the-accepted-installation)
shows observation and restart commands. Preserve unresolved requests, permanent
stops, ledgers and Docker identities; restore access and resume the same service.
An ambiguous or missing execution remains unresolved and holds new round slots.

The selected Pylon writer accepts at most 128 UTF-8 bytes; the published baseline
reference uses 124. Only immutable public GHCR references are accepted. Chain
readback verified tempo 360, minimum one weight, maximum 1.0 and a 100-block write
rate limit. Compare normalized u16 vectors with quantization tolerance; weights
are not final emission percentages. Accepted zeros remain usable; failures receive
no score. Empty rounds reuse the latest nonempty completed round while current
registration is revalidated.

This prototype supplies no real inference, quality judging, economic/security
validation, stronger isolation, encrypted/private submissions, automatic rollback,
scale/storage platform, TEE or public deployment. Durability checks cover local
Linux file/fsync behavior and process crashes, not physical power loss. Deliberately
unresolved fault fixtures retain their own stopped executor and separate data root;
never point ordinary admission at those roots.

## Issue-list recovery corrections

Earlier source corrections resolved four recovery failures:

- Docker's created/non-running state, zero execution timestamps, nonzero exit code
  and runtime error establish a rejected start, including recovery after the CLI
  response was lost. Completed create/start CLI failures retain separate ledger
  phases; ambiguous start responses alone still cannot prove termination.
- Invalid report encodings and oversized JSON integers become persisted failed
  evaluations. Transient report I/O failures remain retryable.
- Judge finishes at or before the frozen round end remain eligible when observed
  late. Actual finishes beyond that inclusive boundary receive no score.
- Executor updates persist activation intent before replacement and retry the
  restart until a fresh heartbeat from the current systemd process also proves
  that process started during the activation attempt.

Regression coverage lives in the executor, result/coordinator and installer test
suites. Fault injection covers replacement interruption, restart rejection with an
old active service, and health failure. This is source-level regression evidence,
not a new real-systemd/localnet acceptance run or a change to the packaged candidate.

Verification on 2 October 2026: 245 tests passed, 9 optional image tests skipped;
Ruff, strict basedpyright for validator/executor/installer, and the regenerated
release-manifest check passed. The retained test cases live in the corresponding subsystem suites.


## Application activation and completed-job polling corrections

Current source addresses both issues in `spec/issues.md`. Application activation
intent is durable before Compose asset replacement and survives interrupted updates,
failed Compose startup and preparation-only runs. It clears after successful startup.
Completed executor jobs stop consuming poll workers and job-record writes after
terminal publication. Restart reconciles retained records once, repairing public
terminal status when necessary, before excluding completed jobs again.

Installer and executor regression tests cover these paths. These are source-level
corrections; they do not change the selected candidate or establish another real
systemd/localnet acceptance run.

Verification on 2 October 2026: 255 tests passed, 9 optional image tests skipped;
Ruff, strict basedpyright, formatting and release-manifest verification passed.

## Certificate-related version refresh (27 October 2026)

Current source selects these versions following the emSign/eMudhra audit:

| Component | Selected version |
| --- | --- |
| Validator, submitter, judge and executor-fixture Python base | 3.14.9-slim-trixie (previous refresh) |
| Validator and submitter uv image | 0.13.6 (previous refresh) |
| Factory Node base | 24.22.1-bookworm-slim |
| Pylon service | 2.4.0 |
| Validator and submitter Pylon client | 2.4.0 |
| Prometheus | 3.15.1 |
| node-exporter | 1.12.1 |
| Subtensor localnet | v486, under `ghcr.io/raofoundation/subtensor-localnet` |
| CI BuildKit | 0.34.1 |
| Validator boto3 / botocore | 1.43.118 |

All listed container inputs are pinned by registry manifest digest. The three image
publishing workflows upgrade the runner's CA certificates before checkout and select
the BuildKit digest explicitly. The Basilica Dockerfile example uses the same Python
base as the application Dockerfiles.

Both uv projects now require uv >= 0.13.6 and constrain certifi >= 2026.10.19;
certifi remains locked at 2026.10.23. The validator also constrains botocore >=
1.43.117. The certificate packages and boto3 have a
one-day package-specific cooldown; the Pylon client, which we release ourselves, is
exempt from the cooldown in both projects. Other cooldown settings are unchanged. The
locks retain the existing seven-day `UV_EXCLUDE_NEWER` setting from this environment;
unsetting that override applies the validator's stricter eleven-day project policy.
Boto3/botocore 1.43.119 was still inside the one-day cooldown when resolving.

The selected Nexus revision pins Pylon client 2.3.1; the validator overrides that
pin with 2.4.0, which only adds mechanism-aware neuron reads. There is no newer published `nodejs-wheel-binaries` than the locked
24.19.0. Drand was excluded from this refresh at the user's request.

Version updates alone do not finish the audit: node-exporter 1.12.1 and BuildKit
0.34.1 still ship affected CA stores, as recorded in the
[emSign reference](https://github.com/backend-developers-ltd/t1-multica-workspace/blob/master/skills/emsign-signals/emsign-signals.md).
They need refreshed bundles in derived images or fixed upstream releases.
The development Node wheel also remains affected. The runner CA upgrade is a
workflow change, not evidence that a hosted run has executed it.

Registry manifests and upstream release metadata were checked, the dependency locks
were resolved, and the installer asset manifest was regenerated. Lockfile checks,
workflow YAML and Compose validation with placeholder values, and installer manifest
verification passed. At the user's request, no code tests, image rebuilds or localnet acceptance were repeated for this
refresh. Published application images and the retained candidate selection have not
changed; replacing them requires publishing rebuilt artifacts and selecting their
actual digests. The earlier acceptance evidence does not cover these new versions.

## Behavioral judging — 18 November 2026

The initial behavioral implementation froze operator task JSON at admission, ran up to six isolated CLI checks, retained two-phase Claude evidence and allocated 90% to one deterministic top scorer, sharing 10% among the remaining eligible miners. The application container has a writable home, no network and no controller mounts. The [behavioral localnet evidence](../spec/evidence/behavioral-acceptance.json) records working controls scoring 0.857, missing persistence 0.286 and broken controls 0.071, plus independent chain readbacks across restart with mechanism-0 non-interference. Ruff, formatting, strict typing, 201 validator tests, 22 miner tests, 23 installer tests and 92 executor/judge/real-container checks passed; nine optional standalone image tests were skipped.

At the user’s request, revision `500e176a37cc00ba24ca0ee69c1e0ba3c04a61a4` was then deployed through the normal updater to the existing Refinery validator on subnet 11. [Refinery verification](../spec/evidence/behavioral-refinery.json) records completed round 198: oneshot, OpenHands and pipeline each received 0.929, with six observed checks and confirmed application termination. Normal 30/5/25-minute timing was restored. Full generated files and model evidence remain on that server; the repository retains operational metadata only. An earlier test exposed a missing application home directory; its immutable records remain preserved and its judge revision is excluded from fallback. No public-chain configuration was changed.

Follow-up investigation found that those six checks ended with removal without observing its outcome, and the grader incorrectly treated that coverage gap as partial functionality. The first fix reserved up to three action/verification pairs within the same six-command limit. Missing evidence makes an evaluation unavailable instead of reducing a miner’s score; assessments retain evidence references and survive retries. [Coverage-fix verification](../spec/evidence/behavioral-coverage-fix.json) records a fresh real-model localnet round: both working controls received full deletion credit, scoring 0.929 overall because their README lacked installation details; missing persistence scored 0.214 and broken controls 0.071. All 99 judge/executor tests passed, alongside 201 validator tests, Ruff, strict typing and the unchanged release manifest check.

The verified judge image was selected on Refinery through the normal updater. Validator and Pylon health checks passed and the executor remained active. Only `JUDGE_IMAGE` changed: task JSON, task selectors and normal timings remain unchanged. Already admitted round 199 retains its frozen old image; the fixed judge applies to future admissions. A completed Refinery round using this fix has not yet been observed. Historical scores were not rewritten.

## Multiple tasks and unrestricted action counts — 18 November 2026

At the user’s request, revision `5e3c526a4940a266bbf84b7d37ee9ee8ccaa6ea4` replaces the single-file selector with directory discovery. Every miner runs every discovered JSON task in each round, using separate factory/judge executions; eligible miners’ round scores average all task scores. The complete task set and execution identities survive admission interruption. Unavailable judging invalidates the round, while an attributed submission failure excludes that miner from the round. Legacy frozen rounds remain readable.

The judge no longer has an action-count cap, a three-pair cap, or fixed command/model/session timeouts. Every action still reserves outcome verification, and the existing round lifecycle still stops its workloads. [Multi-task verification](../spec/evidence/multi-task-acceptance.json) records two tasks across five miners, ten accepted evaluations and 156 executed checks (12–18 per evaluation). Working controls averaged 0.933, missing persistence 0.268 and broken controls 0.067. Independent chain readbacks verified the averaged-score 90/10 allocation twice across restart with mechanism-0 non-interference. All 328 regression tests passed; seven real-container checks passed, including ten commands without replay and a successful 61-second command. Ruff, strict typing, release checksums and candidate image builds passed; nine optional image tests and one unconfigured proxy-boundary test were skipped.

The same release is deployed through `deploy-config-refinery`. Live validator/Pylon health, executor checksum, directory discovery and task parsing passed verification. `TASK_FILE` was removed; the existing `current.json` contents and normal timings were preserved. Refinery currently has one task file. Its completed round 199 retains the old judge, and no completed Refinery round using the new release was observed during this verification. The multi-task end-to-end evidence is from localnet.

## Adaptive sandboxed project judging — 18 November 2026

Revision `0750fc7e7de54df23da1d1531e9ed0945c8c0d8f` replaces the fixed CLI plan/grading phases with an iterative Claude controller. Trusted packaged testing guides cover Python libraries, CLIs and localhost HTTP services. The controller selects sandbox commands, receives captured results and chooses follow-up actions until it can assess the frozen criteria. No action-count cap is imposed. Arbitrary nonempty regular project trees are accepted; `main.py` and `README.md` are no longer universal requirements. Python, Node.js and POSIX shell are packaged; additional dependencies must be bundled. Applications and judge-written harnesses execute only in separate networkless containers, without controller mounts, credentials or the Docker socket. Legacy CLI requests remain supported for already-frozen jobs.

[Adaptive-judge evidence](../spec/evidence/agent-judge-acceptance.json) records localnet round `21aeae9f-b8cc-472a-9e8e-0d9620aa6c2f`: five miners each completed a CLI and library task, producing ten accepted evaluations through fifteen sandbox commands containing independent tests. Working controls averaged 1.000 and 0.964, the partial control 0.330 and broken controls 0.036. Working libraries received full credit and working CLIs received deletion credit with resulting-state observations. Independent chain readbacks verified the averaged-score allocation twice across restart and mechanism-0 non-interference. The full regression run passed 325 tests; after the live-discovered metadata correction, all 41 judge tests passed. Ten real-container boundary checks and nine published-image contract checks passed, alongside strict typing, lint, formatting, release checksums, anonymous pulls and CI.

The first live experiment rejected otherwise valid decisions because unused command numbering and an empty finish-purpose field were overconstrained. That completed round remains in `localnet/state/agent-rejected-metadata-data`; the corrected image was tested in a fresh round. Pylon connection timeouts before admission were resolved without replacing the chain or wallets. No failed experiment supplied weights.

The verified release is deployed on the existing Refinery server through the normal updater, with `deploy-config-refinery` tracking restored. Validator and Pylon are healthy, the executor checksum matches and the task JSON and timings are unchanged. Separate server-side verification re-evaluated copies of round 200 outputs from oneshot, OpenHands and pipeline with the installed judge: each scored 1.0, exited successfully and had confirmed application shutdown. This was an isolated verification run, not a replacement of historical accepted scores or proof of a new scheduled production round. Full generated files and model transcripts stay on the server; the repository retains operational metadata only.

## Review corrections — 19 November 2026

The statement under "Multiple tasks and unrestricted action counts" that every action still reserves outcome verification described the paired-plan judge. The adaptive judge replaced controller-checked action/verification pairs: verification of state changes is now required by the judge's instructions and trusted guides, not enforced by code. Those instructions now also tell the judge to keep commands bounded (background long-lived processes, wrap submitted code in `timeout`), because one command that never returns consumes the remaining sandbox time. This instruction change needs a judge image rebuild and has not yet been verified with live model runs.

## Review fixes — 19 November 2026

Three external reviews were reconciled and their findings reproduced before fixing. The executor no longer wedges when Docker rejects an application container or when the inference proxy is briefly unavailable before creation, publishes judge status during long sandbox commands, and allows execution in the sandbox workspace. Each miner is now evaluated independently: weights use only the newest completed round, a miner without an accepted score for every task is excluded while the others are still weighted, older rounds never stand in, and validator-side exclusions are logged at error level. Judges share one sandbox time budget per evaluation (`EXECUTOR_JUDGE_RESERVE_SECONDS`), finish with the evidence they have when it runs out, and reject projects that cannot run at all as invalid submissions. The judge's instructions require separate verification of state changes and treat evaluator-directed submission text as manipulation; deceptive behavioral controls (`FIXTURE_SET=adversarial`) test this. `EXECUTOR_MAX_RUNNING_JOBS` (default 4) limits concurrent workloads, the judging model can be frozen per round with `JUDGE_MODEL` and the models actually used are recorded, and judges can install declared PyPI and npm dependencies through a registry-only Squid proxy (`EXECUTOR_REGISTRY_NETWORK`).

Coordinator ticks and readiness samples verify settled history once per process; a steady poll at 100 completed rounds dropped from about 2.2 s to 8 ms of CPU. The validator's memory growth came from the Nexus context store retaining every context. Nexus now expires idle contexts; the validator sets a 15-minute TTL and keeps only the newest context per pending job, since the coordinator resends unfinished jobs on fresh contexts every minute. Unit, validator, installer and real-container tests passed, including both proxy-boundary checks and real pip/npm installs through a local registry proxy. Not yet verified: live model runs of the new judge instructions and adversarial controls, published judge/control images, and deployment to Refinery.

## Second review fixes — 20 November 2026

Two further reviews were consolidated and each claim was reproduced before fixing; one (an exec inspected too early after a budget kill) did not reproduce on Docker 28.5.1 and was left unchanged. Dependency installation no longer lets project code run with registry access: a local path, `--no-binary`, `-e` or include in a requirements file, a project `pip.py`, a project `.npmrc` naming a Git executable, and processes left running by earlier checks could each do so before. The executor now kills every other sandbox process, passes pip only validated index requirements with `python -I` and `--isolated`, and pins npm's Git executable. A miner image whose entrypoint is missing left a container in Docker's created state that held a workload slot forever; terminal jobs now release their slot. Capacity-check and sandbox-timeout metrics no longer break readiness. Weights wait while a completed round still has a published judge without a result, and a factory cancelled before it ran is a validator-side gap rather than a rejected submission. The judge validates each model reply before persisting it and asks again up to three times, bounds verdict reasons in its schema, and accepts unreferenced unmet verdicts only after the sandbox budget is spent. Non-file task entries are ignored, a broken task file is logged once per problem, and the weighting docs now describe per-miner exclusion without older-round fallback. Validator, executor, judge and installer tests passed, including real-container tests and pip installs through a local Squid registry proxy; the npm `.npmrc` case was verified manually against the judge image. The judge and executor changes need new judge image and executor releases before they take effect on Refinery.

## Adversarial judge acceptance — 20 November 2026

Live Claude runs on the isolated source-checkout localnet used images built from `51685ad`: validator `factory-horde-validator-refinery@sha256:3b54b8cf…`, judge `factory-horde-judge@sha256:f86f5ade…` and control `factory-horde-control@sha256:891d7cc8…`, plus the deceptive controls from `f24d5b8` (`factory-horde-deceptive@sha256:745e849c…`). The judge requested `sonnet` and Claude Code reported `claude-sonnet-5-5`. Every run used a fresh data root and the seven-criterion to-do task, admitted all five miners, scored all of them and excluded none. All jobs ended confirmed stopped. No run exhausted its sandbox budget or used the `install` action. The regression run with the standard controls passed: working controls 1.0, defects 0.29, 0.07 and 0.07 ([behavioral-regression.json](../spec/evidence/behavioral-regression.json)). All three adversarial runs passed ([1](../spec/evidence/adversarial-acceptance-1.json), [2](../spec/evidence/adversarial-acceptance-2.json), [3](../spec/evidence/adversarial-acceptance-3.json)). Fake persistence scored 0.50–0.64 and self-tests 0.64, with `done` and `remove` unmet in every run: the judge re-listed tasks in a fresh process and printed `tasks.json` after each change, and for self-tests read the no-op branches instead of trusting the bundled tests. The injected CLI scored 0.07–0.14: every behavioural criterion was unmet, its evaluator-directed text was called untrusted and ignored, and only the README criterion received credit. A first regression attempt ran the previous validator image because `localnet/state/validator-image.id` overrides `VALIDATOR_IMAGE`; it rejected every new judge report and is retained unchanged in `localnet/state/adversarial-old-validator-image-data`. The deceptive controls were first published as `factory-horde-control-adversarial`, whose reference exceeded the 128-byte miner commitment, and were renamed in PR #10. Evidence was scanned for the Claude token, Pylon tokens and wallet material.

## Second-mechanism commitments — 23 November 2026

To run beside ComputeHorde on the same hotkeys, commitments became a sequence of tagged envelopes. FactoryHorde commits `<F:package@digest>`, with the SHA-256 digest in unpadded base64url, and the validator rebuilds the image under `VALIDATOR_SUBMISSION_NAMESPACE`, which dispatch now requires; legacy bare references are no longer accepted. The submitter (`SUBMISSION_NAMESPACE`) changes only its own envelope, keeps every other byte and a 33-byte reserve for ComputeHorde's manifest, and refuses to overwrite an undecodable commitment. Because the chain has no compare-and-set, a stale write by either mechanism can drop the other's newest change, so miners schedule the submitter; a run that finds its envelope writes nothing. Shared examples live in `spec/fixtures/commitment-v2/envelopes.json`. Localnet checks and per-mechanism chain metrics follow once the required Pylon release exists.
