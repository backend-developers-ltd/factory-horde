# FactoryHorde behavioral judge

The judge reads the frozen task, criteria, `main.py` and `README.md`. In review mode it makes two tool-less Claude calls using the same configured model (default `sonnet`): plan up to six representative CLI checks, then grade their captured observations. Behavioral criteria require observed behavior; README claims cannot substitute for execution. Source and documentation criteria can use the submitted files. The controller averages `met=1`, `partial=0.5`, and `unmet=0`. An accepted zero remains eligible.

Plans contain up to three action/verification pairs, with at most six commands total. Each pair reserves an actual read-only follow-up command; the controller flattens pairs into linked commands. The controller rejects incomplete plans before executing any command. A six-command to-do plan can use `add → list → done → list → remove → list`; success messages alone do not verify persisted changes. The model still interprets application semantics, so structural validation does not guarantee that every planned observation is useful.

The grader cites actual check IDs for behavioral verdicts and distinguishes observed defects from missing evidence. `partial` means observed incomplete functionality. An unassessed criterion or insufficient evidence makes the evaluation unavailable, without assigning a miner score.

## Application isolation

Bubblewrap 0.12.0 could not create namespaces under the executor's production restrictions. The host executor therefore owns a separate application container associated with the judge job. It copies `main.py` into a disposable 256 MiB `/app` workspace and runs the fixed packaged interpreter with an argument vector and optional stdin. Checks share that workspace, which is also their writable `HOME`; no judge home directory is inherited. The application has no network, inference environment, input task, report directory, runner channel, controller processes or Docker socket. Its read-only submission mount preserves the original output. Container memory, CPU and PID limits also apply; `/tmp` has a separate 256 MiB bound.

Each command has at most 60 seconds and 16 KiB per captured stream. A timeout kills the entire application container, including descendants, and ends further checks in that workspace. The judge can grade the observed timeout. Sandbox, capture or model failures instead produce an unavailable evaluation. The whole evaluation session has a 1,200-second limit, also constrained by the frozen job deadline. The parent result cannot become terminal until Docker confirms application termination. Interrupted commands are never silently replayed.

## Evidence and recovery

The judge publishes `checks.json`, then bounded `execution.json`, then `assessment.json` containing evidence references and any unassessed reasons, then protocol-v1 `review.json` and `report.json`. Evidence includes job/round/hotkey attribution, the model, check arguments and intended observations, captured stdout/stderr, exit status, duration, truncation and timeout flags. The frozen round plan carries the judge image digest; `task.json` carries the specification hash. No credentials or process environment are recorded. A directory lock serializes the session. Saved execution supports pending grading; saved assessments, reviews and reports are reused without fresh model calls. Accepted results remain immutable.

Missing or unsafe required files produce `Invalid submission:` and exclude that miner. Model/proxy failures, unavailable isolation or unusable replies produce `Review unavailable:` (exit 75) and make the whole review round unusable. Fallback history must match the configured judge image digest, so earlier static reviews cannot enter behavioral comparisons. Explicit `JUDGE_MODE=fixture` retains random scores for offline orchestration tests.

Factories and judges each receive their own internal proxy-only network. The real subscription credential stays at the proxy. Application checks always use `--network none`.

## Verification

Run `uv run --frozen --project validator pytest judge/test_judge.py executor/test_executor.py`. Explicit real container tests use `FH_RUNNER_IMAGE_TEST=1` with `executor/test_application_runner.py`; set `FH_TEST_PROXY_NETWORK` to a configured test proxy network to include peer/internet boundary checks. The development controls in `localnet/fixtures/behavioral` test a working CLI, missing persistence and a broken primary function through the real judge. Retain each round's execution and review files as the grading evidence.
