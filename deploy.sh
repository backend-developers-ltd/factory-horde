#!/usr/bin/env bash
# Usage: ./deploy.sh
# Deploys this checkout as the production validator: installs the host executor as a systemd unit
# running executor/executor.py from here, pulls the pinned images and starts the Compose stack
# (Pylon, validator, Prometheus, node-exporter, Grafana, Caddy). Safe to rerun; run it after
# `git pull` and after changing .env. Requires ./setup-prod.sh and a completed .env.
set -euo pipefail

PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SERVICE=factory-horde-executor
UNIT=/etc/systemd/system/$SERVICE.service
UNIT_TEMPLATE=installer/factory-horde-executor.service
EXECUTOR_KEYS=(CONTAINER_UID CONTAINER_GID EXECUTOR_MEMORY EXECUTOR_CPUS EXECUTOR_PIDS_LIMIT
  EXECUTOR_POLL_SECONDS EXECUTOR_WORKERS)
cd "$PROJECT_DIR"

die() { echo "ERROR: $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "Run as root."
[[ -f .env && -L docker-compose.yml ]] || die "Run ./setup-prod.sh and complete .env first."
# shellcheck source=envs/production/.env.template
. ./.env
for key in "${EXECUTOR_KEYS[@]}" FACTORY_HORDE_DATA_ROOT HOST_WALLET_DIR VALIDATOR_HOTKEY GRAFANA_ADDRESS \
  GRAFANA_ADMIN_PASSWORD; do
  [[ -n ${!key:-} ]] || die "Set $key in .env."
done
[[ -f $HOST_WALLET_DIR/$BITTENSOR_WALLET_NAME/hotkeys/$BITTENSOR_WALLET_HOTKEY_NAME ]] ||
  die "No validator hotkey in $HOST_WALLET_DIR; see refinery/wallet.sh."

as_service_account() { setpriv --reuid "$CONTAINER_UID" --regid "$CONTAINER_GID" --clear-groups "$@"; }
as_service_account test -r executor/executor.py ||
  die "UID $CONTAINER_UID cannot read this checkout; keep it outside /root, for example in /opt/factory-horde."
install -d -o "$CONTAINER_UID" -g "$CONTAINER_GID" -m 0750 "$FACTORY_HORDE_DATA_ROOT"
as_service_account installer/prepare-data-root.sh "$FACTORY_HORDE_DATA_ROOT"

# The unit carries only the executor's own settings, never the Pylon or Grafana secrets in .env.
# Its header changes with executor.py, so a new executor restarts the service like a new setting.
render_unit() {
  grep -q '^EnvironmentFile=' "$UNIT_TEMPLATE" || die "$UNIT_TEMPLATE has no EnvironmentFile line to replace."
  {
    echo "# Rendered by deploy.sh for executor.py sha256 $(sha256sum < executor/executor.py | cut -d' ' -f1)."
    for key in "${EXECUTOR_KEYS[@]}"; do echo "Environment=$key=${!key}"; done
  } | sed -e "s|@EXECUTOR_USER@|$CONTAINER_UID|" -e "s|@EXECUTOR_GROUP@|$CONTAINER_GID|" \
    -e "s|@EXECUTOR_PYTHON@|/usr/bin/python3.14|" -e "s|@INSTALL_ROOT@|$PROJECT_DIR/executor|g" \
    -e "s|@DATA_ROOT@|$FACTORY_HORDE_DATA_ROOT|g" \
    -e '/^EnvironmentFile=/{r /dev/stdin' -e 'd' -e '}' "$UNIT_TEMPLATE"
}
unit=$(render_unit)
if [[ ! -f $UNIT || $unit != "$(<"$UNIT")" ]]; then
  echo "Installing $SERVICE and restarting it."
  printf '%s\n' "$unit" > "$UNIT"
  systemctl daemon-reload
  systemctl enable --quiet "$SERVICE"
  systemctl restart "$SERVICE"
else
  systemctl start "$SERVICE"
fi

docker compose pull --quiet
# The validator becomes healthy only with fresh executor and Docker observations.
docker compose up -d --remove-orphans --wait --wait-timeout 300
docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile
# Grafana applies its admin password only when it creates its database; keep it in sync with .env.
docker compose exec -T grafana grafana cli --homepath /usr/share/grafana admin reset-admin-password \
  "$GRAFANA_ADMIN_PASSWORD" >/dev/null

systemctl is-active --quiet "$SERVICE" || die "$SERVICE is not active; see journalctl -u $SERVICE."
docker compose ps --format '{{.Service}}: {{.Status}}'
echo "Deployed $(git rev-parse --short HEAD 2>/dev/null || echo unknown). Status: bin/status.sh. Grafana: https://$GRAFANA_ADDRESS/"
