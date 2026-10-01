#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
mkdir -p localnet/state
docker build --platform linux/amd64 --iidfile localnet/state/submitter-image.id miner
