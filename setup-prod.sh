#!/usr/bin/env bash
# Usage: ./setup-prod.sh
# Prepares this checkout as the production validator deployment: creates the factoryhorde service
# account, copies envs/production/.env.template to envs/production/.env with generated secrets,
# the account's UID/GID and this checkout's wallet and data paths, and links .env and
# docker-compose.yml into the repository root. An existing .env is kept. Edit .env, then run ./deploy.sh.
set -euo pipefail

PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ENV_DIR=envs/production
SERVICE_USER=factoryhorde
cd "$PROJECT_DIR"
[[ $EUID -eq 0 ]] || { echo "Run as root." >&2; exit 1; }

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --user-group --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

if [[ ! -f $ENV_DIR/.env ]]; then
  install -m 0600 "$ENV_DIR/.env.template" "$ENV_DIR/.env"
  fill() { sed -i "s|^$1=\$|$1=$2|" "$ENV_DIR/.env"; }
  for secret in VALIDATOR_PYLON_IDENTITY_TOKEN VALIDATOR_PYLON_OPEN_ACCESS_TOKEN PYLON_METRICS_TOKEN \
    GRAFANA_ADMIN_PASSWORD; do
    fill "$secret" "$(openssl rand -hex 24)"
  done
  fill CONTAINER_UID "$(id -u "$SERVICE_USER")"
  fill CONTAINER_GID "$(id -g "$SERVICE_USER")"
  fill HOST_WALLET_DIR "$PROJECT_DIR/wallets"
  fill FACTORY_HORDE_DATA_ROOT "$PROJECT_DIR/data"
  echo "Created $ENV_DIR/.env; set its required values before running ./deploy.sh."
fi

ln -sf "$ENV_DIR/.env" .env
ln -sf "$ENV_DIR/docker-compose.yml" docker-compose.yml
