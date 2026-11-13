#!/usr/bin/env bash
# Usage: refinery/check-chain.sh
# Read-only Refinery RPC checks: chain identity, health and existing subnets. Requires curl and jq.
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_commands curl jq
REFINERY_GENESIS=0xe185ddf8629910ec509d81f6568ccb227e6f2721747f7468d64261f8425ec0a9
# The node serves JSON-RPC over HTTP on its WebSocket port.
endpoint="${BITTENSOR_NETWORK/#ws/http}"

rpc() {
  local payload
  payload=$(jq -nc --arg method "$1" --argjson params "$2" \
    '{jsonrpc:"2.0", id:1, method:$method, params:$params}')
  curl --silent --show-error --fail-with-body \
    --connect-timeout 5 --max-time 30 \
    --header 'Content-Type: application/json' \
    --data "$payload" "$endpoint" |
    jq -c 'if has("error") then error(.error | tostring)
            elif has("result") then .result
            else error("Missing JSON-RPC result") end'
}

printf 'Checking Refinery RPC: %s\n' "$endpoint"
genesis=$(rpc chain_getBlockHash '[0]' | jq -r .)
[[ "$genesis" == "$REFINERY_GENESIS" ]] || die "Unexpected genesis $genesis; expected $REFINERY_GENESIS."
printf 'Genesis %s matches Refinery.\n' "$genesis"
for method in system_chain system_health chain_getHeader; do
  printf '\n%s\n' "$method"
  rpc "$method" '[]'
done

# Pin pagination and storage reads to one finalized block.
block=$(rpc chain_getFinalizedHead '[]' | jq -r .)
# Twox128("SubtensorModule") + Twox128("NetworksAdded").
prefix="0x658faa385070e074c85bf6b568cf05550e30450fc4d507a846032a7fa65d9a43"
start=null
netuids=()

printf '\nReading subnets at finalized block %s...\n' "$block"
while :; do
  params=$(jq -nc --arg prefix "$prefix" --argjson start "$start" \
    --arg block "$block" '[$prefix, 100, $start, $block]')
  keys=$(rpc state_getKeysPaged "$params")
  [[ $(jq 'length' <<<"$keys") -gt 0 ]] || break

  while IFS= read -r key; do
    params=$(jq -nc --arg key "$key" --arg block "$block" '[$key, $block]')
    value=$(rpc state_getStorage "$params")
    [[ "$value" == '"0x01"' ]] || continue

    # Identity-hashed subnet IDs are SCALE little-endian u16 values.
    hex="${key: -4}"
    netuids+=("$((16#${hex:2:2}${hex:0:2}))")
  done < <(jq -r '.[]' <<<"$keys")

  start=$(jq -c '.[-1]' <<<"$keys")
done

printf '\nExisting subnets (including root subnet 0 if present):\n'
if (( ${#netuids[@]} )); then
  # Twox128("SubtensorModule") + Twox128("SubnetworkN").
  count_prefix="0x658faa385070e074c85bf6b568cf0555a1048e9d244171852dfe8db314dc68ca"
  printf '%-10s %s\n' 'NETUID' 'REGISTERED NODES'
  while IFS= read -r netuid; do
    printf -v suffix '%02x%02x' "$((netuid & 255))" "$((netuid >> 8))"
    params=$(jq -nc --arg key "${count_prefix}${suffix}" \
      --arg block "$block" '[$key, $block]')
    count_hex=$(rpc state_getStorage "$params" | jq -r .)
    if [[ "$count_hex" == null ]]; then
      count='unavailable'
    elif [[ "$count_hex" =~ ^0x[[:xdigit:]]{4}$ ]]; then
      count="$((16#${count_hex:4:2}${count_hex:2:2}))"
    else
      printf 'Unexpected SubnetworkN value for subnet %s: %s\n' "$netuid" "$count_hex" >&2
      exit 1
    fi
    printf '%-10s %s\n' "$netuid" "$count"
  done < <(printf '%s\n' "${netuids[@]}" | sort -n)
else
  echo "No subnets found."
fi

printf '\nRPC checks passed. Review isSyncing and the header block number above.\n'
