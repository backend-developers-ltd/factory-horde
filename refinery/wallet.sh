#!/usr/bin/env bash
# Usage: refinery/wallet.sh validator|miner
# Idempotently prepares this server's wallet (BITTENSOR_WALLET_NAME) on subnet NETUID of the Refinery
# development chain: create, fund from //Alice, register, for a validator add initial stake, and copy
# only the hotkey and public coldkey file to HOST_WALLET_DIR for Pylon. Satisfied steps are skipped.
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_root
require_commands btcli jq

role="${1:?Usage: wallet.sh validator|miner}"
[[ $role == validator || $role == miner ]] || die "Role must be validator or miner."
wallet=$BITTENSOR_WALLET_NAME
# Development funding: top up to FUND_TAO when the free balance drops below MIN_BALANCE_TAO.
MIN_BALANCE_TAO=${MIN_BALANCE_TAO:-10}
FUND_TAO=${FUND_TAO:-100}
# Initial validator stake, added only when the validator hotkey has none.
VALIDATOR_STAKE_TAO=${VALIDATOR_STAKE_TAO:-1}

if [[ ! -f "$WALLET_PATH/$wallet/hotkeys/$HOTKEY_NAME" ]]; then
  recovery="/root/$wallet-recovery.txt"
  log "Creating wallet $wallet; recovery words go to $recovery."
  (umask 077; btcli wallet create --wallet "$wallet" --wallet-hotkey "$HOTKEY_NAME" \
    --wallet-path "$WALLET_PATH" --no-password > "$recovery" 2>&1)
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
    --wallet "$wallet" --wallet-hotkey "$HOTKEY_NAME" --wallet-path "$WALLET_PATH" \
    --proxy-for self --signer wallet --no-prompt
  for _ in $(seq 20); do [[ -n "$(uid)" ]] && break; sleep 15; done
  [[ -n "$(uid)" ]] || die "Registration of $hotkey did not appear on chain."
fi

if [[ $role == validator ]]; then
  stake=$(metagraph | jq -r --arg hk "$hotkey" '(.hotkeys | index($hk)) as $u | .alpha_stake[$u]')
  if [[ "$stake" == 0 ]]; then
    log "Adding $VALIDATOR_STAKE_TAO TAO stake to $hotkey."
    btcli stake add "${CHAIN_ARGS[@]}" --netuid "$NETUID" --amount-tao "$VALIDATOR_STAKE_TAO" \
      --wallet "$wallet" --wallet-hotkey "$HOTKEY_NAME" --wallet-path "$WALLET_PATH" --hotkey "$hotkey" \
      --slippage-protection --rate-tolerance 0.05 --proxy-for self --signer wallet --no-mev-shield --no-prompt
  fi
fi

# Pylon runs as root in its container and needs neither the coldkey nor write access.
install -d -m 0700 "$HOST_WALLET_DIR" "$HOST_WALLET_DIR/$wallet" "$HOST_WALLET_DIR/$wallet/hotkeys"
install -m 0600 "$WALLET_PATH/$wallet/coldkeypub.txt" "$HOST_WALLET_DIR/$wallet/coldkeypub.txt"
install -m 0600 "$WALLET_PATH/$wallet/hotkeys/$HOTKEY_NAME" "$HOST_WALLET_DIR/$wallet/hotkeys/$HOTKEY_NAME"

metagraph | jq -c --arg hk "$hotkey" --arg wallet "$wallet" '(.hotkeys | index($hk)) as $u |
  {wallet: $wallet, hotkey: $hk, netuid, block, uid: $u, validator_permit: .validator_permit[$u],
   alpha_stake: .alpha_stake[$u]}'
[[ $role != validator ]] || log "Set VALIDATOR_HOTKEY=$hotkey in .env."
