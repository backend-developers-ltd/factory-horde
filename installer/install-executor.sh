#!/usr/bin/env bash
# Source-checkout executor-only installation uses the same manifest validation and atomic updater.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:?Usage: install-executor.sh /absolute/environment-file [service-name]}"
SERVICE_NAME="${2:-factory-horde-executor}"
exec /usr/bin/python3.14 -I "$REPO_ROOT/installer/release.py" executor \
    --env-file "$ENV_FILE" --service "$SERVICE_NAME" --ref snapshot --source "file://$REPO_ROOT"
