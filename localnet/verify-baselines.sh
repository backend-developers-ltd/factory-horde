#!/usr/bin/env bash
# Pull the exact CI manifest references anonymously before exercising their contract.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FACTORY_IMAGE="${1:?Usage: verify-baselines.sh factory-digest-reference judge-digest-reference}"
JUDGE_IMAGE="${2:?Usage: verify-baselines.sh factory-digest-reference judge-digest-reference}"
for reference in "$FACTORY_IMAGE" "$JUDGE_IMAGE"; do
    if [[ ! "$reference" =~ ^ghcr.io/[a-z0-9_-]+/[a-z0-9._-]+@sha256:[a-f0-9]{64}$ ]]; then
        echo "Expected a complete GHCR digest reference: $reference" >&2
        exit 1
    fi
done
cd "$REPO_ROOT"
mkdir -p localnet/state
ANONYMOUS_CONFIG="$(mktemp -d)"
TEMP_FILE="$(mktemp localnet/state/.published-images.XXXXXX)"
trap 'rm -f "$TEMP_FILE"; rm -rf "$ANONYMOUS_CONFIG"' EXIT
for reference in "$FACTORY_IMAGE" "$JUDGE_IMAGE"; do
    docker --config "$ANONYMOUS_CONFIG" pull --platform linux/amd64 "$reference"
done
TEST_FACTORY_IMAGE="$FACTORY_IMAGE" TEST_JUDGE_IMAGE="$JUDGE_IMAGE" \
    env -u UV_EXCLUDE_NEWER uv run --project validator pytest validator/tests/test_baseline_images.py \
    -q --tb=short -r f --junitxml=localnet/state/task5-published-tests.xml
printf 'GHCR_FACTORY_IMAGE=%s\nGHCR_JUDGE_IMAGE=%s\n' "$FACTORY_IMAGE" "$JUDGE_IMAGE" > "$TEMP_FILE"
mv "$TEMP_FILE" localnet/state/published-images.env
echo 'Both public GHCR references passed; saved localnet/state/published-images.env'
