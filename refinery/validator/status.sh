#!/usr/bin/env bash
# Usage: refinery/validator/status.sh
# Read-only validator status: services, readiness, latest rounds and accepted scores, and the
# validator's on-chain weight row.
# shellcheck source=../lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
require_root

INSTALL_DIR=/home/factoryhorde/factory-horde
DATA=$INSTALL_DIR/data
compose() {
  sudo -u factoryhorde docker compose --project-name factory-horde-refinery --env-file "$INSTALL_DIR/.env" \
    -f "$INSTALL_DIR/envs/deployed/docker-compose.yml" "$@"
}

echo "== Services"
systemctl is-active factory-horde-refinery-executor | sed 's/^/executor: /'
compose ps --format '{{.Service}}: {{.Status}}'
jq -c '{revision, health, applied_at}' "$INSTALL_DIR/applied-release.json"

echo "== Readiness"
compose exec -T validator /opt/venv/bin/python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9101/readyz').read().decode())" || true

echo "== Latest rounds"
find "$DATA/rounds" -name round.json -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -3 | cut -d' ' -f2- |
  while read -r round; do jq -c '{round_id, state, created_at} | with_entries(select(.value != null))' "$round" 2>/dev/null || echo "$round"; done

echo "== Latest accepted results"
find "$DATA/rounds" -name result.json -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -5 | cut -d' ' -f2- |
  while read -r result; do printf '%s ' "${result#"$DATA"/}"; jq -c . "$result"; done

echo "== Weight calculation"
cat "$DATA/control/weight-calculation.json" 2>/dev/null || echo "none yet"
echo

echo "== Chain weights (mechanism $MECHANISM_ID, revealed)"
hotkey=$(wallet_address "$VALIDATOR_WALLET" hotkey)
FH_NETUID=$NETUID FH_MECHANISM=$MECHANISM_ID FH_HOTKEY=$hotkey FH_NETWORK=$BITTENSOR_NETWORK \
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
        print(json.dumps({"block": graph["block"], "uid": uid, "permit": graph["validator_permit"][uid],
                          "last_update": graph["last_update"][uid], "weights_by_hotkey": row}))


asyncio.run(main())
PY
