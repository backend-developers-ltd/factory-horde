# Shared helpers for the refinery/ scripts. Source it; do not execute it.
# shellcheck shell=bash

set -euo pipefail

REFINERY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$REFINERY_DIR")"
export PATH="$HOME/.local/bin:$PATH"

log() { printf '[refinery] %s\n' "$*" >&2; }
die() { printf '[refinery] ERROR: %s\n' "$*" >&2; exit 1; }

require_operator() { [[ $EUID -ne 0 ]] || die "Run as the operator account (ubuntu), not as root."; }

require_commands() {
  local command
  for command in "$@"; do
    command -v "$command" >/dev/null || die "$command is missing; run refinery/install-prerequisites.sh first."
  done
}

# Load this server's settings: the validator's .env in the checkout root, else the miner's.
load_env() {
  local file=$REPO_ROOT/.env
  [[ -f $file ]] || file=$REFINERY_DIR/miner/.env
  [[ -f $file ]] || die "No .env yet; run this server's setup.sh first."
  # shellcheck source=/dev/null
  . "$file"
  CHAIN_ARGS=(--network "$BITTENSOR_NETWORK" --fallback-endpoints none --archive-endpoints none)
}

# Create an env file from its committed example on first use, then fill KEY=VALUE pairs left empty.
create_env() {
  local file=$1 example=$2 key
  shift 2
  [[ -f $file ]] && return
  install -m 0600 "$example" "$file"
  for key in "$@"; do sed -i "s|^${key%%=*}=\$|$key|" "$file"; done
  log "Created $file from $example."
}

# SS58 address of this server's coldkey ("coldkey") or hotkey ("hotkey").
wallet_address() {
  local file="$HOST_WALLET_DIR/$BITTENSOR_WALLET_NAME/coldkeypub.txt"
  [[ $1 == hotkey ]] && file="$HOST_WALLET_DIR/$BITTENSOR_WALLET_NAME/hotkeys/${BITTENSOR_WALLET_HOTKEY_NAME}pub.txt"
  jq -er .ss58Address "$file"
}

metagraph() { btcli subnets metagraph "$NETUID" "${CHAIN_ARGS[@]}" --json; }
