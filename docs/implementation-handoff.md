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

Current source resolves the four failures recorded in `spec/issues.md`:

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
release-manifest check passed. The retained test cases are linked in `spec/issues.md`.
