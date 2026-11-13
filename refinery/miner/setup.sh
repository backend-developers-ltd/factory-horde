#!/usr/bin/env bash
# Usage: refinery/miner/setup.sh
# Installs or updates the FactoryHorde miner on this server: wallet preparation, a Pylon holding
# only the miner hotkey, and a systemd timer that resubmits FACTORY_IMAGE every SUBMIT_INTERVAL.
# Safe to rerun; rerun after changing refinery/refinery.env.
# shellcheck source=../lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
require_root
require_commands docker btcli jq openssl

MINER_HOME=/root/factory-horde-miner
PROJECT=factory-horde-miner

"$REFINERY_DIR/check-chain.sh" >/dev/null
"$REFINERY_DIR/wallet.sh" "$MINER_WALLET" miner

install -d -m 0700 "$MINER_HOME"
install_runtime_wallet "$MINER_WALLET" "$MINER_HOME/wallets" root

env_file=$MINER_HOME/.env
(umask 077
 for token in MINER_PYLON_TOKEN PYLON_OPEN_ACCESS_TOKEN PYLON_METRICS_TOKEN; do
   grep -q "^$token=" "$env_file" 2>/dev/null || set_env "$env_file" "$token" "$(openssl rand -hex 24)"
 done
 for setting in "ENVIRONMENT=$ENVIRONMENT" "BITTENSOR_NETWORK=$BITTENSOR_NETWORK" "NETUID=$NETUID" \
   "BLOCK_DURATION_SECONDS=$BLOCK_DURATION_SECONDS" "MINER_WALLET=$MINER_WALLET" \
   "MINER_RUNTIME_WALLETS=$MINER_HOME/wallets" "PYLON_HOST_PORT=8000"; do
   set_env "$env_file" "${setting%%=*}" "${setting#*=}"
 done)

log "Starting the miner Pylon."
docker compose --project-name "$PROJECT" --env-file "$env_file" -f "$REFINERY_DIR/miner/docker-compose.yml" \
  up -d --wait --wait-timeout 180

cat > /etc/systemd/system/factory-horde-submit.service <<EOF
[Unit]
Description=FactoryHorde miner: submit the configured factory image
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
ExecStart=$REFINERY_DIR/miner/submit.sh
EOF
cat > /etc/systemd/system/factory-horde-submit.timer <<EOF
[Unit]
Description=FactoryHorde miner: periodic factory image submission

[Timer]
OnBootSec=2min
OnUnitActiveSec=$SUBMIT_INTERVAL
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now factory-horde-submit.timer >/dev/null

log "Submitting $FACTORY_IMAGE now."
systemctl start factory-horde-submit.service
journalctl -u factory-horde-submit.service -n 5 --no-pager -o cat
log "Miner installed. Status: refinery/miner/status.sh"
