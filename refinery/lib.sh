# Shared helpers for the refinery/ scripts. Source it; do not execute it.
# shellcheck shell=bash

set -euo pipefail

REFINERY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export REPO_ROOT
REPO_ROOT="$(dirname "$REFINERY_DIR")"
WALLET_PATH=/root/.bittensor/wallets
export PATH="/root/.local/bin:$PATH"

# shellcheck source=refinery.env
. "$REFINERY_DIR/refinery.env"
CHAIN_ARGS=(--network "$BITTENSOR_NETWORK" --fallback-endpoints none --archive-endpoints none)

log() { printf '[refinery] %s\n' "$*" >&2; }
die() { printf '[refinery] ERROR: %s\n' "$*" >&2; exit 1; }

require_root() { [[ $EUID -eq 0 ]] || die "Run as root."; }

require_commands() {
  local command
  for command in "$@"; do
    command -v "$command" >/dev/null || die "$command is missing; run refinery/install-prerequisites.sh first."
  done
}

# SS58 address of a wallet's coldkey ("coldkey") or default hotkey ("hotkey").
wallet_address() {
  local file="$WALLET_PATH/$1/coldkeypub.txt"
  [[ $2 == hotkey ]] && file="$WALLET_PATH/$1/hotkeys/defaultpub.txt"
  jq -er .ss58Address "$file"
}

metagraph() { btcli subnets metagraph "$NETUID" "${CHAIN_ARGS[@]}" --json; }

# Set KEY=VALUE in an env file, replacing an existing assignment.
set_env() {
  local file=$1 key=$2 value=$3
  touch "$file"
  if grep -q "^$key=" "$file"; then
    sed -i "s|^$key=.*|$key=$value|" "$file"
  else
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
}

# Copy only a wallet's hotkey and public coldkey into a runtime wallet directory for Pylon.
install_runtime_wallet() {
  local wallet=$1 destination=$2 owner=$3
  install -d -o "$owner" -g "$owner" -m 0700 "$destination" "$destination/$wallet" "$destination/$wallet/hotkeys"
  install -o "$owner" -g "$owner" -m 0600 "$WALLET_PATH/$wallet/coldkeypub.txt" "$destination/$wallet/coldkeypub.txt"
  install -o "$owner" -g "$owner" -m 0600 "$WALLET_PATH/$wallet/hotkeys/default" "$destination/$wallet/hotkeys/default"
}
