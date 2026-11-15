#!/usr/bin/env bash
# Usage: refinery/validator/setup.sh
# Installs or updates the FactoryHorde validator on this server with the repository installer:
# wallet preparation, a non-root operator, the systemd executor, the Compose application and the
# fifteen-minute release updater. Safe to rerun; rerun after changing refinery/refinery.env.
# shellcheck source=../lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
require_root
require_commands docker btcli jq git sudo crontab /usr/bin/python3.14

OPERATOR=factoryhorde
OPERATOR_HOME=/home/$OPERATOR
INSTALL_DIR=$OPERATOR_HOME/factory-horde
RUNTIME_WALLETS=$OPERATOR_HOME/wallets
SERVICE=factory-horde-refinery-executor
PROJECT=factory-horde-refinery

"$REFINERY_DIR/check-chain.sh" >/dev/null
"$REFINERY_DIR/wallet.sh" "$VALIDATOR_WALLET" validator

if ! id "$OPERATOR" >/dev/null 2>&1; then
  log "Creating operator $OPERATOR."
  adduser --disabled-password --gecos '' "$OPERATOR" >/dev/null
fi
usermod -aG docker "$OPERATOR"
install_runtime_wallet "$VALIDATOR_WALLET" "$RUNTIME_WALLETS" "$OPERATOR"
install -d -o "$OPERATOR" -g "$OPERATOR" -m 0750 "$INSTALL_DIR"

# Settings owned by refinery.env; the installer adds identity defaults and Pylon tokens.
env_file=$INSTALL_DIR/.env
(umask 077
 for setting in \
   "ENVIRONMENT=$ENVIRONMENT" "BITTENSOR_NETWORK=$BITTENSOR_NETWORK" "NETUID=$NETUID" \
   "MECHANISM_ID=$MECHANISM_ID" "SUBNET_TEMPO=$SUBNET_TEMPO" "BLOCK_DURATION_SECONDS=$BLOCK_DURATION_SECONDS" \
   "BITTENSOR_WALLET_NAME=$VALIDATOR_WALLET" "BITTENSOR_WALLET_HOTKEY_NAME=default" \
   "HOST_WALLET_DIR=$RUNTIME_WALLETS" "VALIDATOR_HOTKEY=$(wallet_address "$VALIDATOR_WALLET" hotkey)" \
   "JUDGE_IMAGE=$JUDGE_IMAGE" "VALIDATOR_DISPATCH_ENABLED=true" "VALIDATOR_WEIGHTS_ENABLED=true" \
   "GENERATION_SECONDS=$GENERATION_SECONDS" "CONFIRMATION_SECONDS=$CONFIRMATION_SECONDS" \
   "EVALUATION_SECONDS=$EVALUATION_SECONDS" "JUDGE_STOP_RESERVE_SECONDS=$JUDGE_STOP_RESERVE_SECONDS" \
   "STOP_GRACE_SECONDS=$STOP_GRACE_SECONDS"; do
   set_env "$env_file" "${setting%%=*}" "${setting#*=}"
 done)
chown "$OPERATOR:$OPERATOR" "$env_file"

if [[ -f $INSTALL_DIR/installation.json ]]; then
  log "Updating the installation to $RELEASE_REF."
  sudo -u "$OPERATOR" "$INSTALL_DIR/installer/update_compose.sh" "$INSTALL_DIR" --ref "$RELEASE_REF"
else
  # The installer runs as the operator and needs administrator rights only to install its unit,
  # sudoers rule and cron entry. Grant exactly that for this run and always remove it.
  # The operator cannot read this root-owned checkout, so it runs a private copy of the
  # standard-library installer; all release assets are downloaded for RELEASE_REF.
  grant=/etc/sudoers.d/factory-horde-refinery-bootstrap
  bootstrap=$(mktemp -d)
  trap 'rm -f "$grant"; rm -rf -- "$bootstrap"' EXIT
  install -m 0555 "$REPO_ROOT/installer/install.sh" "$REPO_ROOT/installer/release.py" "$bootstrap/"
  chown -R "$OPERATOR:$OPERATOR" "$bootstrap"
  printf '%s ALL=(root) NOPASSWD: /usr/bin/install, /usr/bin/systemctl\n' "$OPERATOR" > "$grant"
  chmod 0440 "$grant"
  visudo -cf "$grant" >/dev/null
  log "Installing release $RELEASE_REF."
  sudo -u "$OPERATOR" "$bootstrap/install.sh" "$INSTALL_DIR" --env-file "$env_file" \
    --ref "$RELEASE_REF" --service "$SERVICE" --project "$PROJECT"
  rm -f "$grant"
  rm -rf -- "$bootstrap"
  trap - EXIT
fi

systemctl is-active --quiet "$SERVICE" || die "$SERVICE is not active."
sudo -u "$OPERATOR" docker compose --project-name "$PROJECT" --env-file "$env_file" \
  -f "$INSTALL_DIR/envs/deployed/docker-compose.yml" ps --format '{{.Service}}: {{.Status}}'
"$REFINERY_DIR/validator/setup-monitoring.sh"
log "Validator installed in $INSTALL_DIR. Status: refinery/validator/status.sh"
