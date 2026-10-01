# FactoryHorde Validator

The validator runs inside the common Compose stack. Nexus observes actual blocks
through Pylon and atomically records `control/chain-observation.json` in the shared
data root. This proves chain connectivity; it is not full executor/application
readiness. Dispatch remains disabled, and enabling it currently fails configuration.

The pinned Nexus `mechanism-id` revision, Pylon versions and verified API checks are
documented in [dependency selection](../spec/dependency-selection.md).
The [file protocol](../spec/file-protocol.md) supplies typed records and atomic
publication. Host factory/judge execution and recovery are verified. The result
repository validates confirmed clean factory/judge termination and linked reports,
then persists an immutable decision and score. Its public Nexus store/provider
keeps original result IDs, Docker times and completion-block metadata across replay.
The factory and evaluation Nexus tasks share this provider and use separate instances
of a nonblocking file communicator. Input publishes or reobserves a frozen request;
poll ticks deliver results on the original context. Five consecutive file errors
emit an explicit framework error; unconfirmed jobs remain pending. A new context
can resubscribe to the same durable job without executing it again. Both tasks use
one attempt, distinct node IDs and Nexus-installed block clocks. Automatic round
input is implemented in task 11.

A successful score of zero is eligible; failed, forced, nonzero, malformed or
unconfirmed outcomes carry no accepted score. Missing final reports become retained
failures. Accepted decisions survive loss of their raw report without redrawing a
score. Nexus routing targets are observation metadata; miner attribution comes from
the frozen business request. Future weighing must read accepted hotkeys, not the
router-based count helpers. Queries use the latest completed round with scores,
independently of epoch ranges used for framework queries.

Use the [localnet guide](../localnet/README.md) to build the validator, bootstrap
isolated identities and start the application. The common Compose file still has
a placeholder public validator digest; a local build supplies its Docker image ID.
This is not yet an accepted installable release candidate.

The validator uses structured JSON logs. Common Compose disables trace export and
starts pinned Prometheus/node-exporter services with authenticated Pylon scraping.
Validator metrics exposure and full readiness arrive in task 14.

For development, run the QA gates from `validator/`:

```sh
env -u UV_EXCLUDE_NEWER uv run ruff check --fix
env -u UV_EXCLUDE_NEWER uv run ruff format
env -u UV_EXCLUDE_NEWER uv run basedpyright
env -u UV_EXCLUDE_NEWER uv run pytest -q --tb=line -r f
```
