#!/usr/bin/env bash
# Usage: refinery/validator/setup.sh
# Installs or updates the FactoryHorde validator in this checkout with the repository installer:
# .env from .env.example, wallet preparation, the systemd executor, the Compose application with
# Grafana and the fifteen-minute updater. Run as the operator account; safe to rerun.
# shellcheck source=../lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
require_operator
require_commands docker btcli jq git openssl sudo ip /usr/bin/python3.14

ENV_FILE=$REPO_ROOT/.env
# Caddy serves Grafana on the source address of the default route unless .env names another.
create_env "$ENV_FILE" "$REFINERY_DIR/validator/.env.example" \
  "HOST_WALLET_DIR=$HOME/.bittensor/wallets" \
  "GRAFANA_ADDRESS=$(ip -4 -o route get 1.1.1.1 | sed -n 's/.* src \([0-9.]*\).*/\1/p')" \
  "GRAFANA_ADMIN_PASSWORD=$(openssl rand -hex 16)"
load_env

"$REFINERY_DIR/check-chain.sh" >/dev/null
"$REFINERY_DIR/wallet.sh" validator
sed -i "s|^VALIDATOR_HOTKEY=\$|VALIDATOR_HOTKEY=$(wallet_address hotkey)|" "$ENV_FILE"

# The checkout is its own installation: the installer verifies it against its release manifest,
# and the updater applies later "git pull" and .env changes.
if [[ -f $REPO_ROOT/installation.json ]]; then
  "$REPO_ROOT/installer/update_compose.sh" "$REPO_ROOT"
else
  "$REPO_ROOT/installer/install.sh" "$REPO_ROOT" --ref snapshot --source "file://$REPO_ROOT"
fi

cd "$REPO_ROOT"
# Grafana applies its admin password only when it creates its database; keep it in sync with .env.
docker compose exec -T grafana grafana cli --homepath /usr/share/grafana admin reset-admin-password \
  "$GRAFANA_ADMIN_PASSWORD" >/dev/null
docker compose ps --format '{{.Service}}: {{.Status}}'
log "Validator installed in $REPO_ROOT. Grafana: https://$GRAFANA_ADDRESS/. Status: refinery/validator/status.sh"
