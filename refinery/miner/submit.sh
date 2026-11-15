#!/usr/bin/env bash
# Usage: refinery/miner/submit.sh [IMAGE]
# Publishes IMAGE (default FACTORY_IMAGE) as the miner's commitment through the local Pylon with the
# published one-shot submitter. Unchanged commitments are left as they are. Run by the
# factory-horde-submit systemd timer.
# shellcheck source=../lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
require_root

MINER_HOME=/root/factory-horde-miner
image="${1:-$FACTORY_IMAGE}"
MINER_PYLON_TOKEN=$(sed -n 's/^MINER_PYLON_TOKEN=//p' "$MINER_HOME/.env")
PYLON_HOST_PORT=$(sed -n 's/^PYLON_HOST_PORT=//p' "$MINER_HOME/.env")
export PYLON_IDENTITY_TOKEN="$MINER_PYLON_TOKEN"

docker run --rm --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
  --network host --env PYLON_IDENTITY_TOKEN "$SUBMITTER_IMAGE" \
  --pylon-address "http://127.0.0.1:${PYLON_HOST_PORT:-8000}" --identity miner --netuid "$NETUID" "$image"
