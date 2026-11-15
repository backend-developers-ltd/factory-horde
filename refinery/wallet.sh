#!/usr/bin/env bash
# Usage: refinery/wallet.sh validator|miner
# Idempotently prepares this server's wallet (BITTENSOR_WALLET_NAME in HOST_WALLET_DIR) on subnet
# NETUID: create, fund from //Alice, register, and for a validator add initial stake. Steps that
# are already satisfied are skipped.
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_operator
require_commands btcli jq
load_env

role="${1:?Usage: wallet.sh validator|miner}"
[[ $role == validator || $role == miner ]] || die "Role must be validator or miner."
wallet=$BITTENSOR_WALLET_NAME
WALLET_ARGS=(--wallet "$wallet" --wallet-hotkey "$BITTENSOR_WALLET_HOTKEY_NAME" --wallet-path "$HOST_WALLET_DIR")
# Development funding from //Alice: top up to FUND_TAO when the free balance drops below
# MIN_BALANCE_TAO. VALIDATOR_STAKE_TAO is added only when the validator hotkey has no stake.
MIN_BALANCE_TAO=${MIN_BALANCE_TAO:-10}
FUND_TAO=${FUND_TAO:-100}
VALIDATOR_STAKE_TAO=${VALIDATOR_STAKE_TAO:-1}

if [[ ! -f "$HOST_WALLET_DIR/$wallet/hotkeys/$BITTENSOR_WALLET_HOTKEY_NAME" ]]; then
  recovery="$HOME/$wallet-recovery.txt"
  log "Creating wallet $wallet; recovery words go to $recovery."
  (umask 077; btcli wallet create "${WALLET_ARGS[@]}" --no-password > "$recovery" 2>&1)
fi
coldkey=$(wallet_address coldkey)
hotkey=$(wallet_address hotkey)

uid() { metagraph | jq -r --arg hk "$hotkey" '.hotkeys | index($hk) // empty'; }

free=$(btcli wallet balance "$coldkey" "${CHAIN_ARGS[@]}" --json | jq -er .free_tao)
if jq -en --argjson free "$free" --argjson min "$MIN_BALANCE_TAO" '$free < $min' >/dev/null; then
  "$REFINERY_DIR/fund-from-alice.sh" "$coldkey" "$FUND_TAO"
fi

if [[ -z "$(uid)" ]]; then
  log "Registering $wallet hotkey $hotkey on subnet $NETUID."
  # btcli requires burned registration to be MEV-shielded; do not add --no-mev-shield.
  btcli subnets register "${CHAIN_ARGS[@]}" --netuid "$NETUID" \
    "${WALLET_ARGS[@]}" --proxy-for self --signer wallet --no-prompt
  for _ in $(seq 20); do [[ -n "$(uid)" ]] && break; sleep 15; done
  [[ -n "$(uid)" ]] || die "Registration of $hotkey did not appear on chain."
fi

if [[ $role == validator ]]; then
  stake=$(metagraph | jq -r --arg hk "$hotkey" '(.hotkeys | index($hk)) as $u | .alpha_stake[$u]')
  if [[ "$stake" == 0 ]]; then
    log "Adding $VALIDATOR_STAKE_TAO TAO stake to $hotkey."
    btcli stake add "${CHAIN_ARGS[@]}" --netuid "$NETUID" --amount-tao "$VALIDATOR_STAKE_TAO" \
      "${WALLET_ARGS[@]}" --hotkey "$hotkey" \
      --slippage-protection --rate-tolerance 0.05 --proxy-for self --signer wallet --no-mev-shield --no-prompt
  fi
fi

metagraph | jq -c --arg hk "$hotkey" --arg wallet "$wallet" '(.hotkeys | index($hk)) as $u |
  {wallet: $wallet, hotkey: $hk, netuid, block, uid: $u, validator_permit: .validator_permit[$u],
   alpha_stake: .alpha_stake[$u]}'
