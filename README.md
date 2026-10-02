# FactoryHorde

FactoryHorde is a localnet orchestration prototype for software factories submitted
as immutable public GHCR images. A short-lived miner tool publishes a digest through
its Pylon identity. A containerized Nexus validator freezes submissions into rounds,
and a host systemd executor runs factories and separate judges through Docker.
Versioned shared files preserve requests, permanent stops, execution evidence and
accepted scores across restarts and executor updates.

The baseline invokes Pi 0.87.1 without inference, waits approximately one minute,
and writes a fixed greeting project. The judge checks the required files without
executing them and draws a random score. Stable softmax weights come from accepted
scores, filtered against current registration. Random scores demonstrate the
orchestration flow; they measure no software quality, economics or security.

## Run the accepted prototype

Follow the [packaged localnet acceptance procedure](localnet/README.md#packaged-candidate-acceptance-on-this-vm)
for installation, isolated bootstrap, five miner submissions, staged rounds,
independent chain readback, failure checks and evidence collection. The
[immutable candidate](envs/candidate/README.md) pins all application images and
installer assets. The [handoff](docs/implementation-handoff.md) records the selected
revisions, evidence and operating limits.

Acceptance passed on the existing user-selected Linux VM, using a separate local
chain, wallets and data root. It includes concurrent factories, separate judging,
validator/executor recovery, updates during detached work, actual mechanism-1 chain
weights and mechanism-0 non-interference. This is an application installation proof
on that host; it makes no clean-OS provisioning claim. See
[task-17 evidence](spec/evidence/task17-acceptance.json) and the
[completed sequential tasks](spec/FactoryHorde-v2-sequential-implementation-tasks.md).

Dispatch and weight writes are independently disabled by default. The selected
schedule is 60 minutes of generation, 5 minutes of stop confirmation and 55 minutes
of evaluation. Acceptance uses shortened stages with the same gates. Unresolved
execution holds new rounds; recovery never deletes evidence to force a rerun or
redraws an accepted score.

Current source also reconciles definitive Docker startup rejections, persists
malformed-report failures, enforces the judge's actual round-end finish cutoff,
and retries pending executor activation. These corrections have focused regression
coverage; the immutable candidate above predates them.

## Repository and operation

- [Validator](validator/README.md): Nexus tasks, round/weight settings, readiness and metrics.
- [Miner](miner/README.md): one-shot submission and exact commitment readback.
- [Baseline factory](miner/factory/README.md) and [judge](judge/README.md): separate published fixture images.
- [Installer](installer/README.md): the standalone host executor, common Compose stack and coherent updates.
- [Localnet](localnet/README.md): isolated chain, source-build development and real-container acceptance tools.
- [File protocol](spec/file-protocol.md) and [design](subnet_design.md): ownership, durable records and execution policy.

There are two independent uv projects, `validator/` and `miner/`, with Python 3.14
or newer. There is no root Python project or uv workspace. Run `uv sync` in the
relevant project and preserve its dependency-age restriction. The host executor is
one standard-library Python file; the validator has no Docker socket or wallet mount.

No operator configuration branch has been promoted, and no subnet-12, public-chain
or emissions change is part of this prototype. Real inference, quality judging,
stronger isolation, private/encrypted submissions, automatic rollback, scale/storage
systems and TEE remain future work.

Origin: the Copier-rendered Nexus subnet template.
