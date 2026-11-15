#!/usr/bin/env bash
# Usage: refinery/miner/setup.sh
# Installs or updates the FactoryHorde miner in this checkout: .env from .env.example, wallet
# preparation, Pylon with the miner identity, and a systemd timer that resubmits FACTORY_IMAGE every
# SUBMIT_INTERVAL. Run as the operator account; safe to rerun.
# shellcheck source=../lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
require_operator
require_commands docker btcli jq openssl sudo

MINER_DIR=$REFINERY_DIR/miner
create_env "$MINER_DIR/.env" "$MINER_DIR/.env.example" \
  "HOST_WALLET_DIR=$HOME/.bittensor/wallets" \
  "MINER_PYLON_TOKEN=$(openssl rand -hex 24)" \
  "PYLON_OPEN_ACCESS_TOKEN=$(openssl rand -hex 24)" \
  "PYLON_METRICS_TOKEN=$(openssl rand -hex 24)"
load_env

"$REFINERY_DIR/check-chain.sh" >/dev/null
"$REFINERY_DIR/wallet.sh" miner

log "Starting the miner Pylon."
(cd "$MINER_DIR" && docker compose up -d --wait --wait-timeout 180)

sudo tee /etc/systemd/system/factory-horde-submit.service >/dev/null <<UNIT
[Unit]
Description=FactoryHorde miner: submit the configured factory image
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
User=$(id -un)
ExecStart=$MINER_DIR/submit.sh
UNIT
sudo tee /etc/systemd/system/factory-horde-submit.timer >/dev/null <<UNIT
[Unit]
Description=FactoryHorde miner: periodic factory image submission

[Timer]
OnBootSec=2min
OnUnitActiveSec=$SUBMIT_INTERVAL
Persistent=true

[Install]
WantedBy=timers.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now factory-horde-submit.timer >/dev/null

log "Submitting $FACTORY_IMAGE now."
sudo systemctl start factory-horde-submit.service
journalctl -u factory-horde-submit.service -n 5 --no-pager -o cat
log "Miner installed. Status: refinery/miner/status.sh"
