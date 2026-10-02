#!/usr/bin/env bash
# Install one explicitly selected application/executor release as the non-root operator.
set -euo pipefail
INSTALLER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec /usr/bin/python3.14 -I "$INSTALLER_ROOT/release.py" install "$@"
