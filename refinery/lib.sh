# Shared helpers for the refinery/ scripts. Source it; do not execute it.
# shellcheck shell=bash

set -euo pipefail

REFINERY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$REFINERY_DIR")"
WALLET_PATH=/root/.bittensor/wallets
export PATH="/root/.local/bin:$PATH"

log() { printf '[refinery] %s\n' "$*" >&2; }
die() { printf '[refinery] ERROR: %s\n' "$*" >&2; exit 1; }

# Chain, subnet and wallet settings come from this server's single settings file: the validator's
# .env (setup-prod.sh) or the miner's refinery/miner/.env (refinery/miner/setup.sh).
ENV_FILE=$REPO_ROOT/.env
[[ -f $ENV_FILE ]] || ENV_FILE=$REFINERY_DIR/miner/.env
[[ -f $ENV_FILE ]] || die "No settings file; run ./setup-prod.sh (validator) or refinery/miner/setup.sh (miner) first."
# shellcheck source=../envs/production/.env.template
. "$ENV_FILE"
HOTKEY_NAME=${BITTENSOR_WALLET_HOTKEY_NAME:-default}
CHAIN_ARGS=(--network "$BITTENSOR_NETWORK" --fallback-endpoints none --archive-endpoints none)

require_root() { [[ $EUID -eq 0 ]] || die "Run as root."; }

require_commands() {
  local command
  for command in "$@"; do
    command -v "$command" >/dev/null || die "$command is missing; run bin/prepare-os.sh first."
  done
}

# SS58 address of the configured wallet's coldkey ("coldkey") or hotkey ("hotkey").
wallet_address() {
  local file="$WALLET_PATH/$BITTENSOR_WALLET_NAME/coldkeypub.txt"
  [[ $1 == hotkey ]] && file="$WALLET_PATH/$BITTENSOR_WALLET_NAME/hotkeys/${HOTKEY_NAME}pub.txt"
  jq -er .ss58Address "$file"
}

metagraph() { btcli subnets metagraph "$NETUID" "${CHAIN_ARGS[@]}" --json; }
