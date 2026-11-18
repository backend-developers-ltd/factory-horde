"""Small deterministic acceptance control; not a production miner."""

import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser(description="Persistent to-do manager")
commands = parser.add_subparsers(dest="command", required=True)
commands.add_parser("add").add_argument("text", nargs="+")
commands.add_parser("list")
for command in ("done", "remove"):
    commands.add_parser(command).add_argument("id", type=int)
args = parser.parse_args()
path = Path("tasks.json")
tasks = json.loads(path.read_text()) if path.exists() else []
if args.command == "add":
    identity = max((task["id"] for task in tasks), default=0) + 1
    tasks.append({"id": identity, "text": " ".join(args.text), "done": False})
    print(f"Added {identity}")
elif args.command == "list":
    for task in tasks:
        print(f"{task['id']} [{'x' if task['done'] else ' '}] {task['text']}")
else:
    found = next((task for task in tasks if task["id"] == args.id), None)
    if found is None:
        parser.error("No such task")
    if args.command == "done":
        found["done"] = True
    else:
        tasks.remove(found)
    print("Updated")
path.write_text(json.dumps(tasks))
