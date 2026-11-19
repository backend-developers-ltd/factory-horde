# FactoryHorde adaptive judge

The judge evaluates the full submitted project against frozen task requirements. Its Claude controller (default model `sonnet`) chooses one action at a time: run a sandbox command, retrieve earlier evidence, or finish with an assessment. It receives each command's captured result before choosing the next action. Trusted packaged guides in `skills/` cover Python libraries, CLIs and HTTP services; they guide the model without hard-coding a program interface. The judge can inspect files, create independent harnesses and adapt inputs to observed outputs. Its instructions require it to verify changes, including the final mutation, through a later observation of the resulting state before grading; this is prompt guidance, not a controller-enforced check.

There is no action-count cap. Recent evidence is included in model context; older command evidence remains available by step number. Every criterion needs a verdict and actual evidence references. Behavioral criteria require observed behavior; source/documentation criteria cite commands that read the relevant files. The controller averages `met=1`, `partial=0.5`, and `unmet=0`. An accepted zero remains eligible. Missing evidence or an unsupported runtime makes evaluation unavailable rather than inventing a miner score.

## Application isolation

Claude runs tool-less in the judge controller. The controller publishes argument vectors and stdin through the executor-owned runner exchange; it never executes these commands locally. The host executor sends them through Docker's exec API to a separate application container. Shell syntax is supported by explicitly invoking `/bin/sh` inside that sandbox. Neither submitted code nor judge-generated harnesses run on the validator host.

The sandbox contains a writable full-project copy in a 256 MiB `/app` workspace and the read-only original at `/submission`. It has no network except localhost, inference environment, task input, reports, runner channel, controller processes or Docker socket. Its root filesystem is read-only, all capabilities are dropped, privilege escalation is disabled, and CPU, memory and PID bounds apply. `/tmp` has its own 256 MiB bound. Both mounts allow execution, so project scripts, built binaries and bundled native extensions run. These are Docker isolation boundaries on the host kernel, not a separate virtual machine.

Python and its standard library, Node.js and POSIX shell are packaged. Local HTTP servers and clients run together inside the sandbox. Other dependencies must already be packaged or included in the submission; internet downloads, graphical browsers and arbitrary language toolchains are not provided. Extending the image and trusted guides can add further runtimes without changing the agent loop.

Sandbox time is shared by all commands of an evaluation, and the judge sees a result only after its command returns. Its instructions and guides therefore tell it to start servers and other long-lived processes in the background with redirected streams, to wrap invocations of submitted code in `timeout` with a bound it chooses, and to treat an expired bound (exit 124 or 137) as an observation. Empty stdin is closed, so interactive prompts receive end-of-file.

The project need not contain `main.py` or `README.md`. The judge rejects symlinks, special or unreadable files and inventories up to 4096 regular files totaling 64 MiB. Captured output is bounded to 16 KiB per stream. There is no fixed per-command, model-call or judge-session timeout. The existing frozen round lifecycle stops the judge and application; the parent result cannot become terminal until Docker confirms application termination. Interrupted commands are never silently replayed.

## Evidence and recovery

Each `step-N.decision.json` is persisted before execution, followed by `step-N.observation.json` containing the executor capture or retrieved evidence. On completion, `assessment.json` retains verdicts and check references, `execution.json` summarizes the `agent-v1` session, and protocol-v1 `review.json` and `report.json` retain the accepted score. Attribution includes round, job, miner, specification hash, model and judge image. No credentials or process environment are recorded. A directory lock serializes each session; recovery reuses durable decisions and observations.

Unsafe or empty projects produce `Invalid submission:` and exclude that miner. Model/proxy failures, unavailable isolation or unusable replies produce `Review unavailable:` (exit 75) and make the whole review round unusable. Fallback history must match the configured judge image. Explicit `JUDGE_MODE=fixture` retains random scores for offline orchestration tests. The executor still accepts the earlier CLI `args` exchange for already-frozen jobs during an upgrade.

Factories and judge controllers each receive their own internal proxy-only network. The real subscription credential stays at the proxy. Application execution always uses `--network none`.

## Verification

Run `uv run --frozen --project validator pytest judge/test_judge.py executor/test_executor.py`. Real container tests use `FH_RUNNER_IMAGE_TEST=1` with `executor/test_application_runner.py`; set `FH_TEST_PROXY_NETWORK` to a configured test proxy network for workload peer/internet checks. The development controls in `localnet/fixtures/behavioral` test both a CLI and Python library through a complete validator round. Keep the per-step evidence with the final grading records.
