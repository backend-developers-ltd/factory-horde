#!/usr/bin/env bash
# Usage: refinery/miner/status.sh
# Read-only miner status: Pylon, submission timer, last submission and on-chain registration.
# shellcheck source=../lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
require_root

MINER_HOME=/root/factory-horde-miner
echo "== Pylon"
docker compose --project-name factory-horde-miner --env-file "$MINER_HOME/.env" \
  -f "$REFINERY_DIR/miner/docker-compose.yml" ps --format '{{.Service}}: {{.Status}}'

echo "== Submission timer"
systemctl list-timers factory-horde-submit.timer --no-pager | head -2
journalctl -u factory-horde-submit.service -n 3 --no-pager -o cat

echo "== Chain"
hotkey=$(wallet_address "$MINER_WALLET" hotkey)
metagraph | jq -c --arg hk "$hotkey" '(.hotkeys | index($hk)) as $u |
  {block, hotkey: $hk, uid: $u, incentive: .incentives[$u], emission: .emission[$u], last_update: .last_update[$u]}'
