# Executor lifecycle fixture

This localnet-only image implements the fixed factory executable. Input specification
`exit` records one startup under `/output/starts` and exits zero. `ignore-term`
records startup and ignores TERM until killed. It is separate from the baseline
Pi factory and is used to verify concurrent stops and restart recovery.

`build-executor-fixture.yml` publishes the image to public GHCR. Acceptance uses
its registry digest, anonymous pulls and the installed host executor. Run `python -m localnet.check_executor_recovery IMAGE` through the validator uv
environment as documented in the localnet guide. Task 8 records the real fault
checks and evidence.
