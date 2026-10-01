# FactoryHorde Validator

The validator runs inside the common Compose stack. Nexus observes actual blocks
through Pylon and atomically records `control/chain-observation.json` in the shared
data root. This proves chain connectivity; it is not full executor/application
readiness. Dispatch remains disabled, and enabling it currently fails configuration.

The pinned Nexus `mechanism-id` revision, Pylon versions and verified API checks are
documented in [dependency selection](../spec/dependency-selection.md).
The [file protocol](../spec/file-protocol.md) supplies typed records and atomic
publication. Factory execution and application task wiring remain later tasks.

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
