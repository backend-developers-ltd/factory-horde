# FactoryHorde behavioral judge

The judge reads the frozen task, criteria, `main.py` and `README.md`. In review mode it makes two tool-less Claude calls using the same configured model (default `sonnet`): plan up to six representative CLI checks, then grade their captured observations. Behavioral criteria require observed behavior; README claims cannot substitute for execution. Source and documentation criteria can use the submitted files. The controller averages `met=1`, `partial=0.5`, and `unmet=0`. An accepted zero remains eligible.

## Application isolation

Bubblewrap 0.12.0 could not create namespaces under the executor's production restrictions. The host executor therefore owns a separate application container associated with the judge job. It copies `main.py` into a disposable 256 MiB `/app` workspace and runs the fixed packaged interpreter with an argument vector and optional stdin. Checks share that workspace, which is also their writable `HOME`; no judge home directory is inherited. The application has no network, inference environment, input task, report directory, runner channel, controller processes or Docker socket. Its read-only submission mount preserves the original output. Container memory, CPU and PID limits also apply; `/tmp` has a separate 256 MiB bound.

Each command has at most 60 seconds and 16 KiB per captured stream. A timeout kills the entire application container, including descendants, and ends further checks in that workspace. The judge can grade the observed timeout. Sandbox, capture or model failures instead produce an unavailable evaluation. The whole evaluation session has a 1,200-second limit, also constrained by the frozen job deadline. The parent result cannot become terminal until Docker confirms application termination. Interrupted commands are never silently replayed.

## Evidence and recovery

The judge publishes `checks.json`, then bounded `execution.json`, then protocol-v1 `review.json` and `report.json`. Evidence includes job/round/hotkey attribution, the model, check arguments and intended observations, captured stdout/stderr, exit status, duration, truncation and timeout flags. The frozen round plan carries the judge image digest; `task.json` carries the specification hash. No credentials or process environment are recorded. A directory lock serializes the session. Saved execution supports pending grading; saved reviews and reports are reused without fresh model calls. Accepted results remain immutable.

Missing or unsafe required files produce `Invalid submission:` and exclude that miner. Model/proxy failures, unavailable isolation or unusable replies produce `Review unavailable:` (exit 75) and make the whole review round unusable. Fallback history must match the configured judge image digest, so earlier static reviews cannot enter behavioral comparisons. Explicit `JUDGE_MODE=fixture` retains random scores for offline orchestration tests.

Factories and judges each receive their own internal proxy-only network. The real subscription credential stays at the proxy. Application checks always use `--network none`.

## Verification

Run `uv run --frozen --project validator pytest judge/test_judge.py executor/test_executor.py`. Explicit real container tests use `FH_RUNNER_IMAGE_TEST=1` with `executor/test_application_runner.py`; set `FH_TEST_PROXY_NETWORK` to a configured test proxy network to include peer/internet boundary checks. The development controls in `localnet/fixtures/behavioral` test a working CLI, missing persistence and a broken primary function through the real judge. Retain each round's execution and review files as the grading evidence.
