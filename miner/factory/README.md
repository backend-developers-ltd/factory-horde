# Baseline FactoryHorde factory

This image is a fixed prototype fixture. It reads the input manifest/specification,
invokes Pi's `--version`, verifies `0.87.1`, waits 60 seconds, atomically writes
`main.py` and `README.md` into `/output`, then exits. It supplies no generation
prompt or model credentials. The generated Python prints `Hello from FactoryHorde!`.

Pi's official package is now
[`@earendil-works/pi-coding-agent`](https://pi.dev/changelog/2026/5/7/pi-has-a-new-home).
This build pins [version 0.87.1](https://www.npmjs.com/package/@earendil-works/pi-coding-agent/v/0.87.1),
published 22 September 2026, and uses a committed npm lock with package integrity
hashes. The Node base is pinned by registry manifest digest in `Dockerfile`.
`npm ci` installs the locked package tree, including upstream's shrinkwrap.

The fixed command is `/usr/local/bin/factory-horde-factory`, with `/input:ro` and
`/output:rw`. The executor selects numeric UID/GID and resource limits. No wallet,
submission tool or chain library enters this image. The image can run with a
read-only root filesystem, writable `/tmp`, no capabilities and no network.

From the repository root, `localnet/build-baselines.sh` builds both images and runs
the nine opt-in Docker contract tests. The tests exercise the real 60-second wait
and judge mount contract with networking disabled. Local build IDs are recorded
under `localnet/state`; these are not published registry manifest references.

The GHCR namespace is `backend-developers-ltd`. The
[baseline workflow](../../.github/workflows/build-baselines.yml) publishes
`factory-horde-factory` and `factory-horde-judge` using its GitHub job token, on
relevant pushes to `deploy-build-*` branches. Each matrix job uploads a JSON artifact
with its exact registry manifest reference, source commit and build URL. Execution
and chain submission use these full digest references; tags only identify builds.
Docker Hub publication is not required for this prototype.

New GHCR packages must be public before anonymous pulls can pass. After publication,
set each package's visibility to public in GitHub's package settings, then run:

```sh
localnet/verify-baselines.sh GHCR_FACTORY_DIGEST_REFERENCE GHCR_JUDGE_DIGEST_REFERENCE
```

The verifier uses an empty Docker configuration to pull both manifests for
`linux/amd64`, then runs the nine container tests against those exact references.
Only after all checks pass does it save `localnet/state/published-images.env`.
This uses GitHub Actions because the organization's policy rejected the earlier
local classic-token push; [attempt evidence](../../spec/evidence/task5-ghcr-attempt.json)
records that failure.

Both published references passed anonymous pulls and all nine container tests.
[Publication evidence](../../spec/evidence/task5-published-images.json) records the
exact image digests and build commit; [local evidence](../../spec/evidence/task5-local-images.json)
records the 59 passing source-build validator/image checks.

Verified prototype image references:

```text
ghcr.io/backend-developers-ltd/factory-horde-factory@sha256:8e38b2081f7628ca9dab545fb00ee756e23c1ed5d4d292db0e41c1012acbc8a3
ghcr.io/backend-developers-ltd/factory-horde-judge@sha256:e46ef2808251ee3bd7df35e321b9e8383d82373f335da890920ad93724c289cf
```
