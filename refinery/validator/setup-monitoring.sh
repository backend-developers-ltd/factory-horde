#!/usr/bin/env bash
# Usage: refinery/validator/setup-monitoring.sh
# Starts Grafana with the provisioned validator dashboard behind Caddy, which serves it over HTTPS
# with a Let's Encrypt certificate for the server's public IP (or GRAFANA_ADDRESS from
# refinery.env). Requires the validator application (refinery/validator/setup.sh) for Prometheus.
# Safe to rerun; dashboard file changes are picked up within a minute without a rerun.
# shellcheck source=../lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
require_root
require_commands docker openssl ip

MONITORING_HOME=/root/factory-horde-monitoring
PROJECT=factory-horde-refinery-monitoring
COMPOSE_FILE=$REFINERY_DIR/validator/monitoring/docker-compose.yml

docker network inspect factory-horde-refinery_default >/dev/null 2>&1 ||
  die "The validator application is not running; run refinery/validator/setup.sh first."

# The public address is the source address of the default route unless refinery.env overrides it.
address=${GRAFANA_ADDRESS:-$(ip -4 -o route get 1.1.1.1 | sed -n 's/.* src \([0-9.]*\).*/\1/p')}
[[ -n $address ]] || die "Cannot determine the public address; set GRAFANA_ADDRESS in refinery.env."

install -d -m 0700 "$MONITORING_HOME"
env_file=$MONITORING_HOME/.env
(umask 077
 grep -q '^GRAFANA_ADMIN_PASSWORD=' "$env_file" 2>/dev/null ||
   set_env "$env_file" GRAFANA_ADMIN_PASSWORD "$(openssl rand -hex 16)"
 set_env "$env_file" GRAFANA_ADDRESS "$address")

compose() { docker compose --project-name "$PROJECT" --env-file "$env_file" -f "$COMPOSE_FILE" "$@"; }
log "Starting Grafana and Caddy for https://$address/."
compose up -d --wait --wait-timeout 180
# Grafana applies its admin password only when it creates its database; keep it in sync with .env.
password=$(sed -n 's/^GRAFANA_ADMIN_PASSWORD=//p' "$env_file")
compose exec -T grafana grafana cli --homepath /usr/share/grafana admin reset-admin-password "$password" >/dev/null
compose ps --format '{{.Service}}: {{.Status}}'
log "Grafana: https://$address/ (user admin, password GRAFANA_ADMIN_PASSWORD in $env_file)."
