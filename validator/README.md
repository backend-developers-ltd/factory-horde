# FactoryHorde Validator

The validator runs inside the common Compose stack. Nexus observes actual blocks
through Pylon and atomically records `control/chain-observation.json` in the shared
data root. This proves chain connectivity; it is not full executor/application
readiness. Dispatch defaults to disabled. Enabling it requires `VALIDATOR_HOTKEY`,
`VALIDATOR_JUDGE_IMAGE`, and the Pylon identity name/token, in addition to normal
chain and data-root settings.

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
input comes from the persisted wall-clock coordinator.

The coordinator saves `control/schedule.json` before discovery, including a pending
round identity and absolute deadlines. Restart completes that admission, prepares
the same inputs and reobserves the same jobs. Factory completion cannot launch a
judge before evaluation. Failed factories and missing required output receive a
retained skipped-evaluation reason. Due stops remain permanent even when observation
fails. A corrupt job does not prevent stopping other jobs. Old unresolved factories
or judges prevent another round; skipped slots are not replayed later.

Default windows are generation 3,600 seconds, confirmation 300 seconds and
evaluation 3,300 seconds. Evaluation reserves its final 300 seconds for judge stops;
stop grace defaults to 60 seconds. Configure `VALIDATOR_GENERATION_WINDOW`,
`VALIDATOR_CONFIRMATION_WINDOW`, `VALIDATOR_EVALUATION_WINDOW`,
`VALIDATOR_JUDGE_STOP_RESERVE` and `VALIDATOR_STOP_GRACE_SECONDS`; duration values
accept integer seconds or ISO durations. Existing plans keep their frozen timing
when configuration changes. Fully settled results may complete during evaluation,
but the next round still waits for its scheduled slot. Unresolved rounds may retain
usable scores after their end while continuing to hold admission.

Run only one validator coordinator for a shared data root. Disabling dispatch stops
the coordinator, including stage reconciliation; retained executor requests still
obey their absolute deadlines. Re-enable the same root to resume its round.

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
