#!/usr/bin/env bash
# Usage: refinery/fund-from-alice.sh SS58_ADDRESS AMOUNT_TAO
# Transfers development TAO from the public //Alice key on Refinery (or the chain named by
# BITTENSOR_NETWORK). Each run transfers again. Requires uv and btcli.
set -euo pipefail

destination="${1:?Usage: fund-from-alice.sh SS58_ADDRESS AMOUNT_TAO}"
amount="${2:?Usage: fund-from-alice.sh SS58_ADDRESS AMOUNT_TAO}"
network="${BITTENSOR_NETWORK:-ws://10.0.0.10:9944}"
if [[ ! "$amount" =~ ^[0-9]+([.][0-9]{1,9})?$ || ! "$amount" =~ [1-9] ]]; then
  echo "Amount must be positive, with at most nine decimal places." >&2
  exit 1
fi

umask 077
alice_dir=$(mktemp -d)
trap 'rm -rf -- "$alice_dir"' EXIT
# btcli cannot import a development URI, so write a temporary unencrypted Alice wallet.
uv run --no-project --python 3.14 --with 'bittensor-wallet==4.0.1' python - "$alice_dir" <<'PY'
import sys

from bittensor_wallet import Keypair, Wallet

key = Keypair.create_from_uri("//Alice")
wallet = Wallet(name="refinery-alice", path=sys.argv[1])
wallet.set_coldkey(keypair=key, encrypt=False, overwrite=False)
wallet.set_coldkeypub(keypair=key, overwrite=False)
PY

echo "Transferring $amount development TAO from //Alice to $destination on $network." >&2
btcli wallet transfer --network "$network" --fallback-endpoints none --archive-endpoints none \
  --wallet refinery-alice --wallet-path "$alice_dir" \
  --dest "$destination" --amount-tao "$amount" \
  --proxy-for self --signer wallet --no-mev-shield --no-prompt
