#!/usr/bin/env bash
# Usage: refinery/fund-from-alice.sh SS58_ADDRESS AMOUNT_TAO
# Transfers development TAO from the public //Alice key on Refinery. Each run transfers again.
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_commands uv btcli

destination="${1:?Usage: fund-from-alice.sh SS58_ADDRESS AMOUNT_TAO}"
amount="${2:?Usage: fund-from-alice.sh SS58_ADDRESS AMOUNT_TAO}"
[[ "$amount" =~ ^[0-9]+([.][0-9]{1,9})?$ && "$amount" =~ [1-9] ]] ||
  die "Amount must be positive, with at most nine decimal places."

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

log "Transferring $amount development TAO from //Alice to $destination."
btcli wallet transfer "${CHAIN_ARGS[@]}" \
  --wallet refinery-alice --wallet-path "$alice_dir" \
  --dest "$destination" --amount-tao "$amount" \
  --proxy-for self --signer wallet --no-mev-shield --no-prompt
