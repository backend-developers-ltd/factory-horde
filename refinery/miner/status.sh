#!/usr/bin/env bash
# Usage: refinery/miner/status.sh
# Read-only miner status: Pylon, submission timer, last submission and on-chain registration.
# shellcheck source=../lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
require_root

echo "== Pylon"
(cd "$REFINERY_DIR/miner" && docker compose ps --format '{{.Service}}: {{.Status}}')

echo "== Submission timer"
systemctl list-timers factory-horde-submit.timer --no-pager --no-legend
journalctl -u factory-horde-submit.service -n 3 --no-pager -o cat

echo "== Chain"
hotkey=$(wallet_address hotkey)
metagraph | jq -c --arg hk "$hotkey" '(.hotkeys | index($hk)) as $u |
  {block, hotkey: $hk, uid: $u, incentive: .incentives[$u], emission: .emission[$u], last_update: .last_update[$u]}'
