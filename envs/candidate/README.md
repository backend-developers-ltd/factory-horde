# Immutable localnet candidate

[`release.json`](release.json) selects the public registry digests, build commits,
dependency locks and executor checksum for Linux amd64. The validator and submitter
were published by the existing [`deploy-build-prototype` workflow](https://github.com/backend-developers-ltd/factory-horde/actions/runs/36997766725).
The factory and judge retain their verified task-5 images. Common Compose uses this
validator digest by default; the candidate environment also supplies exact factory,
judge and submitter references. There are no mutable runtime image selections.

All eight selected images passed anonymous pulls. Installation from Git revision
`87353ed2e97cf435b6d8210dceb177affe2febfb` passed application readiness, identity and
monitoring checks on the prepared Linux host; see [task-16 evidence](../../spec/evidence/task16-candidate.json).
This isolated localnet candidate passed [task-17 end-to-end acceptance](../../spec/evidence/task17-acceptance.json).
The user selected the existing VM and prohibited preparing another VM; no clean-OS claim is made.
No production configuration branch has been promoted.

## Install the selected application

Use a clean, published checkout containing this candidate, and satisfy the
[installer prerequisites](../../installer/README.md#requirements-and-ownership).
Run from the repository root as the Docker operator. The example uses ports 19944,
18000 and 19090; copy/edit `localnet.env` before installation if these are occupied.
Tokens, wallets and data are generated below the installation, separately from the
developer localnet. Initial dispatch and weight writes are disabled, with the
production 60/5/55-minute windows explicitly configured.

```sh
TASK_REVISION="$(git rev-parse HEAD)"
TASK_INSTALL="$PWD/localnet/state/candidate"
env -u UV_EXCLUDE_NEWER uv sync --project validator
env -u UV_EXCLUDE_NEWER uv sync --project miner --group bootstrap
env -u UV_EXCLUDE_NEWER uv run --project validator python installer/release.py manifest "$PWD" --check
installer/install.sh "$TASK_INSTALL" --env-file "$PWD/envs/candidate/localnet.env" \
  --ref "$TASK_REVISION" --service factory-horde-candidate-executor \
  --project factory-horde-candidate --localnet --prepare-only
docker compose --project-name factory-horde-candidate --env-file "$TASK_INSTALL/.env" \
  -f "$TASK_INSTALL/envs/deployed/docker-compose.yml" \
  -f "$TASK_INSTALL/localnet/compose.yml" up -d --wait subtensor
env -u UV_EXCLUDE_NEWER uv run --project miner --group bootstrap \
  python localnet/bootstrap.py --env-file "$TASK_INSTALL/.env"
"$TASK_INSTALL/installer/update_compose.sh" "$TASK_INSTALL"
env -u UV_EXCLUDE_NEWER uv run --project validator \
  python localnet/check.py --env-file "$TASK_INSTALL/.env"
```

The final check needs one 15-second scrape interval after startup. It verifies a
fresh validator chain observation, all six authenticated Pylon identities against
independent Subtensor registration evidence, cross-identity token rejection and
all three healthy Prometheus targets. Installed `applied-release.json` records the
exact resolved Git revision/manifest and current executor PID. Later updates use
the same installed updater command; an explicit `--ref <full-sha>` selects a new
compatible revision. Complete bootstrap before the first fifteen-minute cron tick.

The source-build developer helper `localnet/compose.sh` can select a local image ID;
the candidate procedure above invokes the installed common Compose directly and
therefore uses only the candidate registry selection. Do not copy local image IDs
into this candidate.

## Separate promotion

The digest change is prepared in `envs/deployed/docker-compose.yml`, with regenerated
`installer/release-manifest.json`. Building and pushing `deploy-build-prototype`
publishes artifacts; pushing ordinary source commits does not promote operator
configuration. A future authorized promotion would first review acceptance evidence
and environment/identity settings, then fast-forward the selected commit to the
intended `deploy-config-<environment>` branch using the
[existing deployment procedure](../../knowledge/validator.deploy.md). No such
operator promotion is part of this prototype.
