#!/usr/bin/env bash
# Usage: refinery/miner/setup.sh
# Installs or updates the FactoryHorde miner on this server: settings in refinery/miner/.env (created
# from .env.template on the first run), wallet preparation, a Pylon holding only the miner hotkey,
# and a systemd timer that resubmits FACTORY_IMAGE every SUBMIT_INTERVAL. Safe to rerun; rerun after
# changing .env.
set -euo pipefail

MINER_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [[ ! -f $MINER_DIR/.env ]]; then
  install -m 0600 "$MINER_DIR/.env.template" "$MINER_DIR/.env"
  fill() { sed -i "s|^$1=\$|$1=$2|" "$MINER_DIR/.env"; }
  for token in MINER_PYLON_TOKEN PYLON_OPEN_ACCESS_TOKEN PYLON_METRICS_TOKEN; do
    fill "$token" "$(openssl rand -hex 24)"
  done
  fill HOST_WALLET_DIR "$MINER_DIR/wallets"
fi
# shellcheck source=../lib.sh
. "$MINER_DIR/../lib.sh"
require_root
require_commands docker btcli jq openssl

"$REFINERY_DIR/check-chain.sh" >/dev/null
"$REFINERY_DIR/wallet.sh" miner

log "Starting the miner Pylon."
(cd "$MINER_DIR" && docker compose up -d --wait --wait-timeout 180)

cat > /etc/systemd/system/factory-horde-submit.service <<UNIT
[Unit]
Description=FactoryHorde miner: submit the configured factory image
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
ExecStart=$MINER_DIR/submit.sh
UNIT
cat > /etc/systemd/system/factory-horde-submit.timer <<UNIT
[Unit]
Description=FactoryHorde miner: periodic factory image submission

[Timer]
OnBootSec=2min
OnUnitActiveSec=$SUBMIT_INTERVAL
Persistent=true

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now factory-horde-submit.timer >/dev/null

log "Submitting $FACTORY_IMAGE now."
systemctl start factory-horde-submit.service
journalctl -u factory-horde-submit.service -n 5 --no-pager -o cat
log "Miner installed. Status: refinery/miner/status.sh"
