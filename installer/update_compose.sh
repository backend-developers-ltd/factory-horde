#!/usr/bin/env bash
# Serialized coherent release update; only this installation's system unit may be restarted.
set -euo pipefail
INSTALLER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec /usr/bin/python3.14 -I "$INSTALLER_ROOT/release.py" update "$@"
