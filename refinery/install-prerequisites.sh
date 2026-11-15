#!/usr/bin/env bash
# Usage: refinery/install-prerequisites.sh
# Installs what both servers need (Ubuntu 26.04, amd64). Run as the operator account, which needs
# sudo: Docker Engine with Compose from Docker's apt repository, Python 3.14, git, jq, curl, cron
# and openssl system-wide, Docker access for the operator, and uv and btcli in the operator's home.
# Already installed items are kept.
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_operator
[[ $(uname -m) == x86_64 ]] || die "FactoryHorde requires a Linux amd64 host."

sudo env DEBIAN_FRONTEND=noninteractive apt-get update -qq
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ca-certificates curl git jq cron openssl python3.14 >/dev/null

if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  log "Installing Docker Engine from Docker's apt repository."
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  # shellcheck source=/dev/null
  codename=$(. /etc/os-release && echo "$VERSION_CODENAME")
  sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<SOURCES
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $codename
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
SOURCES
  sudo apt-get update -qq
  sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
fi
sudo systemctl enable --now docker cron >/dev/null
if ! id -nG | grep -qw docker; then
  sudo usermod -aG docker "$USER"
  log "Added $USER to the docker group; log out and in again before running a setup script."
fi

if ! command -v uv >/dev/null; then
  log "Installing uv 0.13.7."
  curl -LsSf https://astral.sh/uv/0.13.7/install.sh | env UV_NO_MODIFY_PATH=1 sh
fi
if ! command -v btcli >/dev/null; then
  log "Installing btcli (bittensor 11.3.0)."
  uv tool install --python /usr/bin/python3.14 'bittensor==11.3.0'
fi

docker --version
docker compose version
uv --version
btcli --version
