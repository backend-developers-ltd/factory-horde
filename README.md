# FactoryHorde

FactoryHorde evaluates software factories submitted as immutable container images.
The V2 prototype will demonstrate on-chain submission, concurrent factory execution,
separate judging, persistent scores, and local-chain weight submission. Generation
and judging are intentionally stubs: no model inference occurs, and random scores
do not measure software quality or validate production economics.

Miners publish public GHCR image digests using a short-lived submission
tool. A containerized Nexus validator discovers submissions and coordinates rounds.
A single-file host executor runs factories and judges through Docker, exchanging
requests and outcomes with the validator through a shared directory.

## Implementation status

The repository is a rendered scaffold being adapted to the
[V2 specification](spec/FactoryHorde-initial-prototype-specification-v2.md).
The [sequential task list](spec/FactoryHorde-v2-sequential-implementation-tasks.md)
records completion; the [file protocol](spec/file-protocol.md) documents the
implemented record/publication layer, and the [working design](subnet_design.md) records the selected
implementation shape and defaults. The validator now observes the local chain
through Nexus/Pylon with dispatch disabled. The [miner submitter](miner/README.md)
and frozen commitment discovery are implemented and verified on localnet. Accepted scores
and the public Nexus result-store adapter persist/recover existing execution evidence.
Two Nexus tasks now publish factory/judge requests and poll durable results without
waiting for containers. A five-miner containerized check verifies both tasks across a
validator restart. The round coordinator now freezes discovery and deadlines, gates
judging on clean stopped output, and holds future slots while any job remains
unresolved. Nexus now submits stable softmax weights from accepted scores through
Pylon; direct Subtensor checks verify mechanism 1 and mechanism-0 non-interference.
This completes the first localnet milestone. The real-container adversarial suite
also passes. Application readiness and a single validator metrics endpoint expose
round/job state and file-only executor health; packaging remains in the task list.
The installer now validates a coherent checksummed release, installs the systemd
executor and common Compose settings, and grants the operator's cron only the
required service restart. The [immutable candidate](envs/candidate/README.md) selects
published images and coherent installer assets; clean-host acceptance remains task 17.

The first milestone is a reproducible Linux localnet run with roughly five miners,
actual Docker execution and independently verified chain weights. Localnet now
uses the same application Compose services and host systemd executor as deployment,
with isolated wallets, local Subtensor and bootstrap added. The current
[localnet guide](localnet/README.md) provides the verified Compose/bootstrap setup;
the systemd executor runs concurrent factory/judge requests with permanent cancellation and restart recovery. Public deployment, subnet-12
changes and emissions changes are outside this prototype.

## Repository

- `validator/`: independent Python/uv project for the Nexus validator.
- `miner/`: independent Python/uv project being adapted into submission tooling;
  [baseline factory](miner/factory/README.md) and [judge](judge/README.md) images are published publicly on GHCR and verified by digest.
- `judge/`: separate fixture judge, verified through Docker without executing submissions.
- `executor/executor.py`: standalone standard-library host Docker executor, installed through systemd.
- `envs/deployed/`, `installer/`: application deployment and operator installation.
- `localnet/`: isolated development chain, bootstrap and acceptance fixtures.
- `spec/`, `subnet_design.md`: requirements, implementation order and design.

There is no root Python project or uv workspace. Run `uv sync` from the relevant
project directory; both projects require Python 3.14 or newer. Preserve each
project's dependency-age constraint, including when the shell defines uv overrides.
Operator documentation lives in [validator/README.md](validator/README.md) and
[installer/README.md](installer/README.md); it will be verified as the implementation
and localnet acceptance tasks complete.

Origin: the Copier-rendered Nexus subnet template.
