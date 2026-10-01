#!/usr/bin/env bash
# Source-build checks only; publication separately supplies registry manifest references.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
mkdir -p localnet/state
docker build --platform linux/amd64 --iidfile localnet/state/factory-image.id miner/factory
docker build --platform linux/amd64 --iidfile localnet/state/judge-image.id judge
TEST_FACTORY_IMAGE="$(cat localnet/state/factory-image.id)"
TEST_JUDGE_IMAGE="$(cat localnet/state/judge-image.id)"
export TEST_FACTORY_IMAGE TEST_JUDGE_IMAGE
env -u UV_EXCLUDE_NEWER uv run --project validator pytest validator/tests/test_baseline_images.py -q --tb=short -r f
