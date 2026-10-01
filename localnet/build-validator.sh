#!/usr/bin/env bash
# A source-build smoke image; registry release selection remains the task-16 workflow.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$REPO_ROOT/localnet/prepare.sh"
docker build --platform linux/amd64 --iidfile "$REPO_ROOT/localnet/state/validator-image.id" "$REPO_ROOT/validator"
"$REPO_ROOT/localnet/compose.sh" config --quiet
