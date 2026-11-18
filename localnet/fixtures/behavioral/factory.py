#!/usr/local/bin/python3
"""Emit one controlled application, selected deterministically by public hotkey."""

import hashlib
import json
from pathlib import Path

manifest = json.loads(Path("/input/task.json").read_text())
variant = int(hashlib.sha256(manifest["miner_hotkey"].encode()).hexdigest(), 16) % 3
specification = Path("/input/specification.md").read_text()
if "samplelib" in specification:
    package = Path("/output/samplelib")
    package.mkdir()
    source = Path("/fixture/library.py").read_text()
    if variant == 1:
        source = source.replace('raise ValueError("empty")', "return 0").replace(
            "return list(dict.fromkeys(values))", "return sorted(set(values))"
        )
    elif variant == 2:
        source = "def mean(values): raise NotImplementedError()\ndef unique(values): raise NotImplementedError()\n"
    (package / "__init__.py").write_text("from .operations import mean, unique\n")
    (package / "operations.py").write_text(source)
else:
    source = Path("/fixture/main.py").read_text()
    if variant == 1:
        source = source.replace("path.write_text(json.dumps(tasks))", "# Persistence intentionally absent.")
    elif variant == 2:
        source = 'raise SystemExit("Not implemented")\n'
    Path("/output/main.py").write_text(source)
    Path("/output/README.md").write_text(
        "# To-do CLI\n\nRequires Python 3 and no dependencies. Run `python main.py add buy milk`, "
        "`python main.py list`, `python main.py done 1`, or `python main.py remove 1`. "
        "Tasks are stored in tasks.json in the current directory.\n"
    )
print(json.dumps({"fixture_variant": ["working", "partial", "broken"][variant]}))
