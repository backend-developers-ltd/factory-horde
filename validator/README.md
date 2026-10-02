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
failures. Unsafe or unreadable artifacts are rejected; genuine host I/O failures
remain unresolved. Accepted decisions survive loss of their raw report without redrawing a
score. Nexus routing targets are observation metadata; miner attribution comes from
the frozen business request. Weighing reads accepted hotkeys, not the router-based
count helpers. Queries use the latest completed round with scores,
independently of epoch ranges used for framework queries.

Weight writes are separately enabled with `VALIDATOR_WEIGHTS_ENABLED=true` and
Pylon identity credentials. Both Nexus weight opportunity and setter nodes use
`MECHANISM_ID`; the setter shares the factory/evaluation store provider. The gate
skips opportunities without usable registered scores. Empty rounds retain the
latest nonempty completed round; chain epoch boundaries do not expire its scores.
Successful zeros remain eligible and failures are excluded. Weighing uses
`exp((score - maximum) / temperature)`, normalized over currently registered
accepted hotkeys, with finite positive `VALIDATOR_WEIGHT_TEMPERATURE` default `0.1`.
Membership is read again after calculation; a detected change aborts that attempt.

`VALIDATOR_WEIGHT_TEMPO` defaults to 360 blocks and must match the subnet;
`VALIDATOR_WEIGHT_EPOCH_OFFSET` defaults to zero. These opportunities do not control
round timing. `control/weight-calculation.json` records the latest calculated
request, not chain inclusion. The [independent localnet check](../localnet/README.md)
reads actual Subtensor weights at a fixed block and accounts for integer encoding.
These weights do not establish final emission percentages.

Use the [localnet guide](../localnet/README.md) to build the validator, bootstrap
isolated identities and start the application. The common Compose file selects the
published [immutable candidate](../envs/candidate/README.md); the developer helper
can instead supply a local image ID. [Task-17 acceptance](../spec/evidence/task17-acceptance.json)
passed on the existing user-selected VM; the [handoff](../docs/implementation-handoff.md)
links exact revisions and reproduction instructions.

The validator uses structured JSON logs. Common Compose disables trace export and
starts pinned Prometheus/node-exporter services with authenticated Pylon scraping.
A Nexus actor owns the validator HTTP thread on port 9101 and joins it on shutdown.
Prometheus scrapes `/metrics` on the Compose network; no validator HTTP port is
published to the host. The executor stays file-only. Its atomic
`control/executor-health.json` records process start/heartbeat, a bounded Docker
probe and completion of initial job observation. This never substitutes for each
job's terminal Docker evidence. Executor counters/histograms are forwarded from
`control/executor-metrics.json` through the same validator endpoint.

`/livez` answers while HTTP runs. `/readyz` returns JSON with individual checks and
HTTP 200 only when records are compatible/readable, owned control/result paths pass
an actual atomic write/fsync probe, the executor has reconciled, Docker and job
observations are current, and this validator runtime has received a chain beat.
Enabled dispatch additionally requires recent successful coordinator reconciliation.
HTTP 503 includes failing checks and diagnostic errors. Retained chain files cannot
satisfy initial connectivity. Checks expire after
`VALIDATOR_OBSERVATION_MAX_AGE_SECONDS` (default 30), including when actor polling
stalls but HTTP stays live. Common Compose uses this readiness URL for its health
check; an unhealthy state is diagnostic and does not itself stop detached jobs.

Metrics include `factory_horde_round_phase{phase}`, `active_jobs`, `unresolved_jobs`,
`blocked_next_round`, `usable_scores` and `usable_round_age_seconds`, all with the
`factory_horde_` prefix. Age is measured since the latest scored round completed;
`-1` means no such round. Scores here are accepted results; current registration
eligibility is checked separately by the weight gate. Executor/Docker/chain/monitor
age gauges expose stale observations. Interpret job/score gauges only when the
`records` readiness check passes. Labels contain bounded operations/outcomes,
checks and phases; identities stay in records and logs.

`job_terminal` executor logs distinguish pull cancellation/failure from an inspected
factory/judge exit. `job_unresolved` retains host uncertainty. Validator
`job_result_finalized` includes a rejection reason or successful acceptance;
`readiness_changed` explains blocked operation. Result/record operation counters
and histograms expose storage errors. `pylon_weight_submission_acknowledged` and
`factory_horde_pylon_submissions_total` mean only that Pylon accepted the request.
Independently verified effects remain the direct-chain evidence from
`localnet/check_weights.py`; no application metric claims chain inclusion.

Use the [monitoring check](../localnet/README.md#monitoring) to verify executor
staleness, result-store write failure and repair on an idle localnet.

## Observe and restart the accepted installation

Run from the repository root after the packaged acceptance setup. These commands
select that installation's common Compose files and isolated project:

```sh
TASK_INSTALL="$PWD/localnet/state/acceptance"
TASK_COMPOSE=(docker compose --project-name factory-horde-acceptance
  --env-file "$TASK_INSTALL/.env"
  -f "$TASK_INSTALL/envs/deployed/docker-compose.yml"
  -f "$TASK_INSTALL/localnet/compose.yml")
"${TASK_COMPOSE[@]}" ps
"${TASK_COMPOSE[@]}" logs --tail 20 validator
"${TASK_COMPOSE[@]}" exec -T validator /opt/venv/bin/python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9101/readyz').read().decode())"
env -u UV_EXCLUDE_NEWER uv run --project validator python -m json.tool \
  "$TASK_INSTALL/data/control/schedule.json"
env -u UV_EXCLUDE_NEWER uv run --project validator python -m json.tool \
  "$TASK_INSTALL/data/control/weight-calculation.json"
rg --files "$TASK_INSTALL/data/rounds" | rg '(round|factory-result|result|report)\.json$'
```

Round JSON records the frozen deadlines, cohort, stage and unresolved IDs. Requests,
permanent stops and executor statuses live under `data/control/`; each miner's
round directory holds generated files, judge report and immutable accepted decision.
The weight-calculation file is a request; use the independent chain checker for
inclusion proof. The same host tree is mounted at `/var/lib/factory-horde` inside
the validator. Keep tokens and wallets out of logs and evidence exports.

To resume observation after a service interruption, keep the same data root,
wallets and chain volume, repair Docker/path permissions if necessary, then run:

```sh
sudo systemctl restart factory-horde-acceptance-executor
"${TASK_COMPOSE[@]}" restart validator
"${TASK_COMPOSE[@]}" up -d --no-deps --wait --wait-timeout 180 validator
```

Stopping/restarting the executor leaves detached work intact. Reconciliation reads
its retained Docker identities and terminal ledger. If an expected execution is
missing or its start remains ambiguous, preserve that unresolved record and stop
new admission while diagnosing it. Do not delete a request, permanent stop,
container or result to force progress. There is no operator command that invents
terminal evidence or authorizes a replacement execution.

To apply the installation's selected compatible revision, use
`"$TASK_INSTALL/installer/update_compose.sh" "$TASK_INSTALL"`. See the
[installer guide](../installer/README.md#release-assets-and-update-behavior) for
checksum publication, exact-unit permissions and repair after unhealthy updates.

For development, run the QA gates from `validator/`:

```sh
env -u UV_EXCLUDE_NEWER uv run ruff check --fix
env -u UV_EXCLUDE_NEWER uv run ruff format
env -u UV_EXCLUDE_NEWER uv run basedpyright
env -u UV_EXCLUDE_NEWER uv run pytest -q --tb=line -r f
```
