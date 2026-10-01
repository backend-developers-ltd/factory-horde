#!/usr/bin/env bash
# Create isolated operator configuration; repeated preparation preserves secrets and evidence.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCALNET_ROOT="$REPO_ROOT/localnet"
if [[ ! "$REPO_ROOT" =~ ^[-/._a-zA-Z0-9]+$ ]]; then
    echo "Use a canonical checkout path without spaces or shell metacharacters." >&2
    exit 1
fi
umask 027
mkdir -p "$LOCALNET_ROOT/state/data" "$LOCALNET_ROOT/wallets"
"$REPO_ROOT/installer/prepare-data-root.sh" "$LOCALNET_ROOT/state/data"
if [[ ! -f "$LOCALNET_ROOT/.env" ]]; then
    cp "$LOCALNET_ROOT/.env.example" "$LOCALNET_ROOT/.env"
    {
        printf '\nFACTORY_HORDE_DATA_ROOT=%q\n' "$LOCALNET_ROOT/state/data"
        printf 'HOST_WALLET_DIR=%q\n' "$LOCALNET_ROOT/wallets"
        printf 'CONTAINER_UID=%s\nCONTAINER_GID=%s\n' "$(id -u)" "$(id -g)"
        printf 'EXECUTOR_USER=%q\nEXECUTOR_GROUP=%q\n' "$(id -un)" "$(id -gn)"
        for key in VALIDATOR_PYLON_OPEN_ACCESS_TOKEN VALIDATOR_PYLON_IDENTITY_TOKEN PYLON_METRICS_TOKEN \
                   MINER1_PYLON_TOKEN MINER2_PYLON_TOKEN MINER3_PYLON_TOKEN MINER4_PYLON_TOKEN MINER5_PYLON_TOKEN; do
            printf '%s=%s\n' "$key" "$(openssl rand -hex 24)"
        done
    } >> "$LOCALNET_ROOT/.env"
    chmod 600 "$LOCALNET_ROOT/.env"
fi
"$LOCALNET_ROOT/compose.sh" config --quiet
echo "Localnet configuration prepared; persistent data and wallets are isolated under localnet/."
