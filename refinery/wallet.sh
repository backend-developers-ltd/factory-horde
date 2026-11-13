#!/usr/bin/env bash
# Usage: refinery/wallet.sh WALLET validator|miner
# Idempotently prepares a wallet on subnet NETUID: create, fund from //Alice, register, and for a
# validator add initial stake. Steps that are already satisfied are skipped.
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_root
require_commands btcli jq

wallet="${1:?Usage: wallet.sh WALLET validator|miner}"
role="${2:?Usage: wallet.sh WALLET validator|miner}"
[[ $role == validator || $role == miner ]] || die "Role must be validator or miner."

if [[ ! -f "$WALLET_PATH/$wallet/hotkeys/default" ]]; then
  recovery="/root/$wallet-recovery.txt"
  log "Creating wallet $wallet; recovery words go to $recovery."
  (umask 077; btcli wallet create --wallet "$wallet" --wallet-hotkey default \
    --wallet-path "$WALLET_PATH" --no-password > "$recovery" 2>&1)
fi
coldkey=$(wallet_address "$wallet" coldkey)
hotkey=$(wallet_address "$wallet" hotkey)

uid() { metagraph | jq -r --arg hk "$hotkey" '.hotkeys | index($hk) // empty'; }

free=$(btcli wallet balance "$coldkey" "${CHAIN_ARGS[@]}" --json | jq -er .free_tao)
if jq -en --argjson free "$free" --argjson min "$MIN_BALANCE_TAO" '$free < $min' >/dev/null; then
  "$REFINERY_DIR/fund-from-alice.sh" "$coldkey" "$FUND_TAO"
fi

if [[ -z "$(uid)" ]]; then
  log "Registering $wallet hotkey $hotkey on subnet $NETUID."
  # btcli requires burned registration to be MEV-shielded; do not add --no-mev-shield.
  btcli subnets register "${CHAIN_ARGS[@]}" --netuid "$NETUID" \
    --wallet "$wallet" --wallet-hotkey default --wallet-path "$WALLET_PATH" \
    --proxy-for self --signer wallet --no-prompt
  for _ in $(seq 20); do [[ -n "$(uid)" ]] && break; sleep 15; done
  [[ -n "$(uid)" ]] || die "Registration of $hotkey did not appear on chain."
fi

if [[ $role == validator ]]; then
  stake=$(metagraph | jq -r --arg hk "$hotkey" '(.hotkeys | index($hk)) as $u | .alpha_stake[$u]')
  if [[ "$stake" == 0 ]]; then
    log "Adding $VALIDATOR_STAKE_TAO TAO stake to $hotkey."
    btcli stake add "${CHAIN_ARGS[@]}" --netuid "$NETUID" --amount-tao "$VALIDATOR_STAKE_TAO" \
      --wallet "$wallet" --wallet-hotkey default --wallet-path "$WALLET_PATH" --hotkey "$hotkey" \
      --slippage-protection --rate-tolerance 0.05 --proxy-for self --signer wallet --no-mev-shield --no-prompt
  fi
fi

metagraph | jq -c --arg hk "$hotkey" --arg wallet "$wallet" '(.hotkeys | index($hk)) as $u |
  {wallet: $wallet, hotkey: $hk, netuid, block, uid: $u, validator_permit: .validator_permit[$u],
   alpha_stake: .alpha_stake[$u]}'
