#!/usr/bin/env bash
# Run as the selected executor/operator account; containers use this account's UID/GID.
set -euo pipefail
DATA_ROOT="${1:?Usage: prepare-data-root.sh /absolute/data/root}"
if [[ "$DATA_ROOT" != /* || "$(realpath -m -s "$DATA_ROOT")" != "$DATA_ROOT" ||
      "$(realpath -m "$DATA_ROOT")" != "$DATA_ROOT" ]]; then
    echo "Data root must be an absolute canonical path without symlinks." >&2
    exit 1
fi
umask 027
for directory in "$DATA_ROOT" "$DATA_ROOT/control" "$DATA_ROOT/rounds" \
                 "$DATA_ROOT/control/rounds" "$DATA_ROOT/control/requests" \
                 "$DATA_ROOT/control/stops" "$DATA_ROOT/control/statuses"; do
    if [[ -L "$directory" || ( -e "$directory" && ! -d "$directory" ) ]]; then
        echo "Unsafe data-root directory: $directory" >&2
        exit 1
    fi
    mkdir -p "$directory"
    if [[ "$(stat -c %u "$directory")" != "$(id -u)" || "$(stat -c %g "$directory")" != "$(id -g)" ]]; then
        echo "Data directories must belong to the executor account and primary group: $directory" >&2
        exit 1
    fi
    chmod 0750 "$directory"
done
echo "Data root prepared for UID $(id -u), GID $(id -g): $DATA_ROOT"
