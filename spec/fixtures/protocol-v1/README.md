# Protocol v1 fixtures

These examples are a frozen, single-miner UTC round with distinct factory and judge
IDs. They exercise [the protocol](../../file-protocol.md), not an actual execution.
The `example` image names and repeated-letter digests are deliberately non-pullable
placeholders, not published baseline images or registry evidence.

`round.json`, `input-manifest.json`, both `*-request.json` files, `stop.json`,
`status.json`, `report.json` and `accepted.json` are valid records. The successful
judge score is zero, demonstrating that zero differs from a failed evaluation.
The accepted report hash uses canonical compact JSON, not this fixture's pretty
formatting. `specification.md` is the exact UTF-8 input hashed by the round plan.

`invalid-request-*.json` each violates the named schema requirement.
`mismatched-report.json` is structurally valid but belongs to a different round;
repository correlation must reject it. `validator/tests/test_records.py` also
exercises malformed/duplicate JSON, unsupported version types, non-finite scores,
unsafe paths, status evidence, concurrency, restart and interrupted publication.
