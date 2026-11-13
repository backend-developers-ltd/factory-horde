#!/usr/bin/env bash
# Usage: bin/prepare-os.sh
# Installs the host software of a FactoryHorde server (Ubuntu 26.04, amd64): Docker Engine with
# Compose from Docker's apt repository, Python 3.14 for the host executor, git, jq, curl and openssl,
# plus uv and btcli for the Refinery wallet helpers. Already installed items are kept.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
[[ $(uname -m) == x86_64 ]] || { echo "FactoryHorde requires a Linux amd64 host." >&2; exit 1; }
export PATH="/root/.local/bin:$PATH"

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl git jq openssl python3.14 >/dev/null

if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  echo "Installing Docker Engine from Docker's apt repository."
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  # shellcheck source=/dev/null
  codename=$(. /etc/os-release && echo "$VERSION_CODENAME")
  cat > /etc/apt/sources.list.d/docker.sources <<SOURCES
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $codename
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
SOURCES
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
fi
systemctl enable --now docker >/dev/null

if ! command -v uv >/dev/null; then
  echo "Installing uv 0.13.7."
  curl -LsSf https://astral.sh/uv/0.13.7/install.sh | env UV_NO_MODIFY_PATH=1 sh
fi
if ! command -v btcli >/dev/null; then
  echo "Installing btcli (bittensor 11.3.0)."
  uv tool install --python /usr/bin/python3.14 'bittensor==11.3.0'
fi

docker --version
docker compose version
uv --version
btcli --version
