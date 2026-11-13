#!/usr/bin/env bash
# Usage: bin/status.sh
# Read-only status of the production validator: services, readiness, latest rounds and accepted
# scores, the weight calculation and the validator's on-chain weight row.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ $EUID -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
# shellcheck source=../envs/production/.env.template
. ./.env
DATA=$FACTORY_HORDE_DATA_ROOT

echo "== Services ($(git rev-parse --short HEAD 2>/dev/null || echo unknown))"
systemctl is-active factory-horde-executor | sed 's/^/executor: /' || true
docker compose ps --format '{{.Service}}: {{.Status}}'

echo "== Readiness"
docker compose exec -T validator /opt/venv/bin/python -c "
import urllib.error, urllib.request
try:
    print(urllib.request.urlopen('http://127.0.0.1:9101/readyz', timeout=5).read().decode())
except urllib.error.HTTPError as error:
    print(error.code, error.read().decode())" || true

echo "== Latest rounds"
find "$DATA/rounds" -name round.json -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -3 | cut -d' ' -f2- |
  while read -r round; do
    jq -c '{round: .plan.sequence, stage, start: .plan.deadlines.start, round_end: .plan.deadlines.round_end,
            miners: (.plan.cohort | length), unresolved: (.unresolved_jobs | length), completed_at}' "$round"
  done

echo "== Latest accepted results"
find "$DATA/rounds" -name result.json -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -5 | cut -d' ' -f2- |
  while read -r result; do printf '%s ' "${result#"$DATA"/}"; jq -c . "$result"; done

echo "== Weight calculation"
cat "$DATA/control/weight-calculation.json" 2>/dev/null || echo "none yet"
echo

echo "== Chain weights (mechanism $MECHANISM_ID): revealed row and pending commit-reveal commits"
FH_NETUID=$NETUID FH_MECHANISM=$MECHANISM_ID FH_HOTKEY=$VALIDATOR_HOTKEY FH_NETWORK=$BITTENSOR_NETWORK \
  /root/.local/share/uv/tools/bittensor/bin/python -B - <<'PY'
import asyncio
import json
import os

from bittensor import Client


async def main() -> None:
    netuid = int(os.environ["FH_NETUID"])
    async with Client(network=os.environ["FH_NETWORK"], fallback_endpoints=[], archive_endpoints=[]) as client:
        graph = await client.read("metagraph", netuid=netuid)
        uid = graph["hotkeys"].index(os.environ["FH_HOTKEY"])
        weights = await client.read("weights", netuid=netuid, mechid=int(os.environ["FH_MECHANISM"]))
        row = {graph["hotkeys"][target]: value for target, value in dict(weights.get(uid, {})).items()}
        commits = await client.read("timelocked_weight_commits", netuid=netuid, mechid=int(os.environ["FH_MECHANISM"]))
        pending = [
            {"commit_block": commit["commit_block"], "reveals_at": str(commit["reveals_at"])}
            for entries in commits.values()
            for commit in entries
            if commit["hotkey"] == os.environ["FH_HOTKEY"]
        ]
        print(json.dumps({"block": graph["block"], "uid": uid, "permit": graph["validator_permit"][uid],
                          "last_update": graph["last_update"][uid], "revealed_weights_by_hotkey": row,
                          "pending_commits": pending}))


asyncio.run(main())
PY
