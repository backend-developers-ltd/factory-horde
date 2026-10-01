# Localnet execution and report fixtures

This localnet-only image implements the fixed factory executable. Input specification
`exit` records one startup under `/output/starts` and exits zero. `ignore-term`
records startup and ignores TERM until killed. It is separate from the baseline
Pi factory and is used to verify concurrent stops and restart recovery.

The default `lifecycle` build profile preserves that contract. The image also
provides the fixed judge executable. `FIXTURE_PROFILE` is baked into each image
at build time; different profiles have different immutable registry digests.

| Profile | Deliberate behavior |
|---|---|
| `factory-valid` | Writes both required files and exits zero immediately |
| `factory-missing` | Omits README.md |
| `factory-nonzero` | Leaves files but exits 7 |
| `factory-hang` | Leaves files and ignores TERM |
| `factory-symlink` | Replaces main.py with a symlink |
| `judge-valid` | Writes one linked random report and exits zero |
| `judge-malformed`, `judge-mismatch`, `judge-missing` | Truncated JSON, wrong job ID, or absent report |
| `judge-nan`, `judge-infinite`, `judge-range` | Invalid numeric scores |
| `judge-hang`, `judge-nonzero` | Writes a valid-looking report, then hangs or exits 7 |
| `judge-symlink` | Replaces the report with a symlink |

Each actual process appends one line to its job-local `starts` file. These profiles
are fault fixtures, not Pi baselines or software-quality judges. Task 13's suite
uses real executor and Docker evidence to check their classifications.

`build-executor-fixture.yml` publishes every profile to the existing public GHCR
fixture package, with one digest artifact per profile. Acceptance uses
its registry digest, anonymous pulls and the installed host executor. Run `python -m localnet.check_executor_recovery IMAGE` through the validator uv
environment as documented in the localnet guide. Task 8 records the real fault
checks and evidence.

The complete profile suite is `python -m localnet.check_adversarial METADATA_DIR` in
the validator uv environment. See the [localnet guide](../../README.md#adversarial-application-acceptance)
for downloading the exact GHCR digest artifacts, prerequisites and retained evidence.
