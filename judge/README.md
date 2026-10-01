# FactoryHorde prototype judge

The judge checks only presence and readability of `main.py` and `README.md`.
It never executes or imports the submitted project. These checks do not measure
software quality. A successful check draws a uniform random score in `[0,1]` and
writes one linked [protocol-v1 report](../spec/file-protocol.md).

Run `/usr/local/bin/factory-horde-judge` in the pinned Python image with:

- `/input:ro`: immutable `task.json` and `specification.md`.
- `/submission:ro`: the stopped factory's output.
- `/report:rw`: the judge's own report directory.

The executable uses only Python's standard library. It verifies input version,
UUIDs, miner attribution and specification hash, rejects duplicate/non-finite JSON,
and reads only bounded regular files (one MiB each). Missing, unreadable, oversized,
symlinked or non-regular submission files produce a failure report and exit 1.
Malformed attribution fails without fabricating a linked report. Success exits 0.

Reports are written with file/directory fsync and atomic create-if-absent publication.
Rerunning the same judge preserves its first valid report, including a failed one,
and never replaces mismatched or malformed existing evidence. Validator acceptance
still requires independent matching request/status and successful Docker exit.

Use `localnet/build-baselines.sh` from the repository root for source-build Docker
checks. These verify read-only input/submission mounts, writable report storage,
no execution of submitted code, stable reruns, failure cases and exact protocol
parsing. GHCR publication uses the baseline GitHub Actions workflow; see the
[factory guide](../miner/factory/README.md) for publication and anonymous verification.
