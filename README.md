# FactoryHorde

FactoryHorde evaluates software factories submitted as immutable container images.
The V2 prototype will demonstrate on-chain submission, concurrent factory execution,
separate judging, persistent scores, and local-chain weight submission. Generation
and judging are intentionally stubs: no model inference occurs, and random scores
do not measure software quality or validate production economics.

Miners publish public Docker Hub or GHCR image digests using a short-lived submission
tool. A containerized Nexus validator discovers submissions and coordinates rounds.
A single-file host executor runs factories and judges through Docker, exchanging
requests and outcomes with the validator through a shared directory.

## Implementation status

The repository is a rendered scaffold being adapted to the
[V2 specification](spec/FactoryHorde-initial-prototype-specification-v2.md).
The [sequential task list](spec/FactoryHorde-v2-sequential-implementation-tasks.md)
records completion; the [working design](subnet_design.md) records the selected
implementation shape and defaults. Existing HTTP ping examples and deployment
scripts are scaffold code, not an accepted FactoryHorde implementation.

The first milestone is a reproducible Linux localnet run with roughly five miners,
actual Docker execution and independently verified chain weights. Localnet will
use the same application Compose services and host systemd executor as deployment,
with isolated wallets, local Subtensor and bootstrap added. The current
[localnet guide](localnet/README.md) describes the inherited HTTP/tmux setup; the
FactoryHorde Compose/systemd setup is pending task 4. Public deployment, subnet-12
changes and emissions changes are outside this prototype.

## Repository

- `validator/`: independent Python/uv project for the Nexus validator.
- `miner/`: independent Python/uv project being adapted into submission tooling,
  with baseline factory assets planned under `miner/factory/`.
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
