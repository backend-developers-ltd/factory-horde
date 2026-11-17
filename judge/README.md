# FactoryHorde judge

The judge reads the frozen specification and the stopped factory's `main.py` and
`README.md`. It evaluates them without importing or executing the submission.
The executor mounts `/input` and `/submission` read-only and gives each job a
writable `/report` directory.

## Scoring

`JUDGE_MODE=review` is the validator default. At admission the validator freezes
`TASK_REQUIREMENTS`, an ordered JSON list of acceptance criteria, alongside the
specification. Omitting the list makes the full specification one criterion.
Every miner receives the same `/input/evaluation.json`. IDs start at 1 and
follow the order in the list. With older inputs that have only `task.json` and
`specification.md`, this judge uses review mode and the whole-task criterion.

Claude Code receives its grading instructions through `--append-system-prompt`.
Standard input contains the criteria, specification and both files, delimited by
a fresh random boundary. The CLI disables its ordinary tools. Its structured
response must supply exactly one known ID, verdict and reason per criterion.
The judge assigns 1 for met, 0.5 for partial and 0 for unmet, then averages
these points. The model defaults to `sonnet`. One CLI invocation has a 600-second
limit. The specification and generated files together can use up to 200,000 bytes.

An explicit `JUDGE_MODE=fixture` in the round configuration produces a random
score for offline orchestration tests. Losing access to the proxy in review mode
produces an unavailable evaluation.

## Outcomes and recovery

- A successful judge exits 0 and publishes a linked protocol-v1 `report.json`.
  For review scores the validator also checks the job identity, ordered criteria,
  verdicts and arithmetic in `review.json`.
- A missing, oversized or invalid submission exits 1 with a linked failure
  starting with `Invalid submission: `. Other miners remain eligible.
- Missing inference configuration, a timeout, an unusable model reply or invalid
  review inputs produce exit 75 and a failure starting with `Review unavailable: `.
  The validator skips the entire round when selecting review weights and can use
  the previous comparable round. Confirmed judge crashes or missing evidence also
  prevent selection of a partial ranking.
- A per-job directory lock covers the first check through publication. The judge
  fsyncs and publishes review evidence before the report. After an interruption
  between those writes it reconstructs the score from the saved verdicts. A
  completed report, including its failure classification, is reused on replay.

Run the focused tests from the repository root.

```sh
env -u UV_EXCLUDE_NEWER uv run --frozen --project validator pytest judge/test_judge.py -q
```

The [image checks](../localnet/build-baselines.sh) exercise the explicit offline
fixture. The [file protocol](../spec/file-protocol.md) defines the mounted inputs
and acceptance rules.
