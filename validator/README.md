# FactoryHorde Validator

Validator node for the FactoryHorde Bittensor subnet.

Implementation is in progress. The entry point currently contains the inherited
HTTP ping graph; the deployment still has a placeholder validator image digest.
The pinned Nexus `mechanism-id` revision, Pylon versions and verified API checks are
documented in [dependency selection](../spec/dependency-selection.md). This is not
yet an installable accepted FactoryHorde candidate.

The [shared-file protocol](../spec/file-protocol.md) implements typed round/job
records, atomic publication and recovery primitives. Executor integration and
application task wiring remain later steps in the sequential plan.

## What this is

A `docker compose` stack whose core containers are:

- **pylon** — sidecar that proxies all Bittensor / subtensor communication for the
  validator (handles wallet, weight setting, metagraph reads).
- **validator** — the FactoryHorde validator process built from this repo.

The optional **alloy** trace sidecar is commented out in the rendered Compose file.

alongside a Prometheus-based metrics stack (see [`installer/README.md`](../installer/README.md)).

## Observability

The validator ships structured JSON logs (`structlog`). OpenTelemetry export is disabled
unless an OTLP endpoint is configured. If Alloy is deliberately enabled, its upstream
requires `TRACES_UPSTREAM_URL` / `TRACES_UPSTREAM_USER` / `TRACES_UPSTREAM_PASSWORD`.

## Running a validator

See [`installer/README.md`](../installer/README.md) for installation, configuration,
updates and prerequisites.

## More

- Repository root `README.md` — what FactoryHorde is and how the subnet works.
