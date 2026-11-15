# Validator deploy procedures

This document describes how the **subnet developer** ships changes to operators.

The deploy is split into **three independent procedures**:

1. **Build a new validator image** — pure CI action, produces an artifact in
   the registry. Nothing changes for the operator. Can be run many times
   without ever promoting.
2. **Promote a validator build** — pins a specific built image into
   `envs/deployed/docker-compose.yml` and ships it to operators. Comes after
   procedure 1.
3. **Promote a non-validator service** (e.g. pylon) — pins a new version of
   another service in `envs/deployed/docker-compose.yml` and ships it. Fully
   independent of procedures 1 and 2; can be triggered by an upstream hotfix
   or required by procedure 2 (when the new validator uses features from a
   newer pylon).

In a typical release that introduces a validator-side feature requiring a new
pylon you run procedure 1, then procedure 3 (to land the pylon bump), then
procedure 2 (to land the validator bump with smoke test on the new pylon). In
the most common case — a plain validator bump — you run procedure 1 followed
by procedure 2 only. A pure pylon hotfix is procedure 3 alone.

## The pinning rule (read this first)

> **All image references — in `envs/deployed/docker-compose.yml`, in `docker
> pull` commands, in smoke tests, anywhere — must use `@sha256:<digest>`, the
> Docker manifest digest reported by the registry.**
>
> Tags like `:v0-latest`, `:sha-<commit>`, `:1.4.0`, `:latest` are **mutable
> from the registry's perspective** — anyone with push rights to that
> repository can later re-point the tag at different bytes. They are used
> **once**, to look up the corresponding digest, and then thrown away. Once
> you have a digest, the registry is contractually obliged to serve those
> exact bytes forever.

This rule applies to the validator image we build ourselves **and** to every
third-party image in the stack (pylon, anything else). There is no "but this
tag is semver, so it's safe" exception — the registry doesn't care about
semver.

## V2 topology and release assets

The prototype currently uses pinned official Prometheus and node-exporter images,
authenticated Pylon scraping and the validator's actor-owned metrics/readiness
endpoint. Tracing, Alloy and remote-write are disabled. The inherited Alloy file is
not an active service or an updater input.

The maintained installer consumes `installer/release-manifest.json`, the standalone
executor/checksum, common Compose and installer assets from one resolved Git
revision. A moving `deploy-config-*` branch resolves once before downloading any
asset. Operator updates serialize, verify every checksum and protocol compatibility,
atomically replace the executor on its destination filesystem and restart its exact
system unit. The operator's cron has only that restart sudo grant. A changed system
unit requires installation privileges; unhealthy replacement requires ordinary
repair and is not rolled back automatically. See [installer instructions](../installer/README.md).

[Task 16's candidate](../envs/candidate/README.md) uses this build/promotion structure. Production
configuration promotion, subnet-12 deployment and emissions changes remain outside
the prototype. The promotion commands below describe a separate, later authorized
operator release; they are not part of localnet acceptance.

## Branches and what they do

Two independent branches drive the deploy. They are **not** the same thing —
different consumers, different roles:

- `deploy-build-<env>` — triggers the `build-validator.yml` GitHub Actions
  workflow, which builds validator and submitter images and pushes them to the configured
  registry as `<image_registry>/<github_org>/<image_basename>-<env>:v0-latest`
  and `...:sha-<commit>`. Nothing else reads this branch — it exists to fire CI.
  Used by procedure 1. Both builds use pinned base/uv images and frozen project
  locks, then smoke-test each published digest and upload its source/digest metadata.
- `deploy-config-<env>` — the source of truth for what the **operator** pulls.
  Their cron-driven `update_compose.sh` resolves this branch to one SHA, verifies
  the release manifest and applies compatible application/executor assets. The first-time
  `installer/install.sh` is also fetched from here. Used by procedures 2 and 3.

## Procedure 1 — Build a new validator image

Trigger: the developer wants CI to produce a fresh validator image from
`master` (or any working branch). This is just CI — nothing is decided about
operators here.

The default environment is `production` (the branch suffix and the validator's
OTel `deployment.environment.name` attribute share this single value); for other
environments substitute `<env>` consistently.

1. Confirm the source branch is green locally (QA gates), `validator/Dockerfile`
   builds, and the container starts.
2. Fast-forward push the source branch to `deploy-build-<env>`:

   ```sh
   git push origin master:deploy-build-production
   ```

   The `build-validator.yml` workflow (triggered on `deploy-build-*`) builds
   the image and pushes it to the registry as `:v0-latest` and `:sha-<commit>`.
3. Verify in GitHub Actions that the job succeeded and the image landed in the
   registry under `:sha-<commit>`.

Procedure ends here. The artifact exists in the registry; the operator sees
nothing new.

## Procedure 2 — Promote a validator build

Trigger: the developer wants operators to start running a specific image that
was already built in procedure 1.

**Prerequisite:** if this validator release relies on a feature only present
in a newer pylon (or any other service), run **procedure 3 first** for that
service. Otherwise the smoke test in step 2 below would test against the wrong
stack.

1. **Look up the Docker digest of the image built in procedure 1** — without
   pulling it:

   ```sh
   docker buildx imagetools inspect \
     <image_registry>/<github_org>/<image_basename>-production:sha-<commit> \
     --format '{{json .Manifest.Digest}}'
   ```

   Save the resulting `sha256:<digest>`. From this point on, the tag
   `:sha-<commit>` is **never used again** — every subsequent command refers
   to the image by its digest.

2. Smoke test under `envs/deployed/docker-compose.yml` with a real `.env`.
   Pull and run **by digest only**:

   ```sh
   docker pull <image_registry>/<github_org>/<image_basename>-production@sha256:<digest>
   ```

   Bring up the full stack and confirm validator and pylon are healthy. This
   is where you decide whether this particular image is promotable.

3. On `master`, edit `envs/deployed/docker-compose.yml`, the `validator`
   service's `image:` field:

   ```yaml
   image: <image_registry>/<github_org>/<image_basename>-${ENVIRONMENT:?}@sha256:<digest>
   ```

4. Regenerate/check release metadata after changing any listed asset:

   ```sh
   env -u UV_EXCLUDE_NEWER uv run --project validator python installer/release.py manifest "$PWD"
   env -u UV_EXCLUDE_NEWER uv run --project validator python installer/release.py manifest "$PWD" --check
   ```

   Commit (e.g. `chore(deploy): pin production validator to <digest-prefix>`), push
   `master`, then fast-forward `master` → `deploy-config-production`:

   ```sh
   git push origin master:deploy-config-production
   ```

   From this point, the cron-driven `update_compose.sh` on operator hosts will
   pick up the new `docker-compose.yml` and — because the `image:` digest
   changed — restart the stack onto the pinned image.

5. Smoke test on a clean Linux host using the explicit environment/revision
   procedure in `installer/README.md`. Confirm validator and Pylon readiness,
   the operator-owned command in `/etc/cron.d/<executor-service>`, executor health
   and the exact selected validator image digest. The initial prototype performs
   this on isolated localnet only.

## Procedure 3 — Promote a non-validator service (e.g. pylon)

Trigger is one of:

- Procedure 2 needs a newer pylon (or other service) — validator started using
  a feature available from, say, `pylon 1.4.0`.
- The developer explicitly wants to bump a service (e.g. upstream CVE hotfix
  for pylon), independently of any validator build.

This procedure does not depend on procedures 1 or 2 and can be run on its own.

Note: `envs/deployed/docker-compose.yml` ships from this template with a real
pylon digest already pinned (not a placeholder). At template bootstrap time
this procedure is therefore optional — only run it if the freshly built
validator needs a newer pylon than the one the template ships with.

1. Pick the target upstream tag (e.g. `backenddevelopersltd/bittensor-pylon:1.4.0`).
2. **Look up the Docker digest for that tag** — without pulling it:

   ```sh
   docker buildx imagetools inspect \
     backenddevelopersltd/bittensor-pylon:1.4.0 \
     --format '{{json .Manifest.Digest}}'
   ```

   Save the resulting `sha256:<digest>`. From here on, the tag `:1.4.0` is
   **never used again**.

3. Smoke test the full stack under `envs/deployed/docker-compose.yml` with a
   real `.env`, pulling the new service **by digest only**:

   ```sh
   docker pull backenddevelopersltd/bittensor-pylon@sha256:<digest>
   ```

   Use the currently pinned validator and any other currently pinned services.
   For pylon, confirm basic health endpoints respond and that the validator
   can talk to it under the operator's open-access token.

4. On `master`, edit `envs/deployed/docker-compose.yml`, the relevant service's
   `image:` field:

   ```yaml
   image: backenddevelopersltd/bittensor-pylon@sha256:<digest>
   ```

5. Regenerate/check the release manifest as in Procedure 2, step 4.
   Commit (e.g. `chore(deploy): pin production pylon to <digest-prefix>`), push
   `master`, then fast-forward `master` → `deploy-config-production`:

   ```sh
   git push origin master:deploy-config-production
   ```

If procedure 2 needs an accompanying service bump, run procedure 3 first
(landing the service digest commit on `master`), then procedure 2 (the smoke
test runs against the already-pinned new service). If you prefer, both digest
edits can sit in a single commit; the requirement is that the smoke test in
procedure 2 step 2 happens against the stack the operator will end up running.

## Other environments

Mirror the same flow with a matching pair of branches — `deploy-build-<env>`
triggers a CI build of `<image_basename>-<env>:v0-latest`. Operators explicitly
select `--ref deploy-config-<env>` (or a full commit SHA) and supply `ENVIRONMENT`
in their operator environment. No installer argument defaults the subnet/network.

## After done

The template-bootstrap workflow is complete. Day-to-day releases are
independent runs of procedures 1, 2, and 3 — not always in a fixed pair. Pick
the procedures the change actually requires:

- New validator code only → 1 then 2.
- New validator code that depends on a newer pylon → 1, 3, 2.
- Pylon hotfix only → 3.
- Test build, not yet promoting → 1.

Further changes to the subnet itself fall outside this document.
