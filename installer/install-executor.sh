#!/usr/bin/env bash
# Install the same standalone executor/service for localnet and operator deployments.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:?Usage: install-executor.sh /absolute/environment-file [service-name]}"
SERVICE_NAME="${2:-factory-horde-executor}"
if [[ ! "$ENV_FILE" =~ ^/[-/._a-zA-Z0-9]+$ || ! "$SERVICE_NAME" =~ ^factory-horde-[a-z0-9-]+$ ]]; then
    echo 'Use canonical paths and a factory-horde- service name.' >&2
    exit 1
fi
# Operator-controlled configuration. Only executor settings are copied into its service environment.
source "$ENV_FILE"
: "${FACTORY_HORDE_DATA_ROOT:?}" "${EXECUTOR_USER:?}" "${EXECUTOR_GROUP:?}" "${EXECUTOR_PYTHON:?}"
: "${CONTAINER_UID:?}" "${CONTAINER_GID:?}" "${EXECUTOR_MEMORY:?}" "${EXECUTOR_CPUS:?}"
: "${EXECUTOR_PIDS_LIMIT:?}" "${EXECUTOR_POLL_SECONDS:?}"
if [[ "$EXECUTOR_USER" != "$(id -un)" || "$EXECUTOR_GROUP" != "$(id -gn)" ||
      "$CONTAINER_UID" != "$(id -u)" || "$CONTAINER_GID" != "$(id -g)" || "$CONTAINER_UID" == 0 ||
      ! "$FACTORY_HORDE_DATA_ROOT" =~ ^/[-/._a-zA-Z0-9]+$ || ! "$EXECUTOR_PYTHON" =~ ^/[-/._a-zA-Z0-9]+$ ]]; then
    echo 'Run as the configured non-root operator with matching numeric container UID/GID.' >&2
    exit 1
fi
"$EXECUTOR_PYTHON" -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 14) else 1)'
docker info > /dev/null
"$REPO_ROOT/installer/prepare-data-root.sh" "$FACTORY_HORDE_DATA_ROOT"
INSTALL_ROOT="$(dirname "$FACTORY_HORDE_DATA_ROOT")/executor"
if [[ "$(realpath -m "$INSTALL_ROOT")" != "$INSTALL_ROOT" ]]; then
    echo 'Executor installation path must not contain symlinks.' >&2
    exit 1
fi
umask 027
mkdir -p "$INSTALL_ROOT"
SCRIPT_TMP="$(mktemp "$INSTALL_ROOT/.executor.XXXXXX")"
UNIT_TMP="$(mktemp --suffix=.service)"
trap '[ ! -e "$SCRIPT_TMP" ] || unlink "$SCRIPT_TMP"; unlink "$UNIT_TMP"' EXIT
install -m 0555 "$REPO_ROOT/executor/executor.py" "$SCRIPT_TMP"
"$EXECUTOR_PYTHON" -I "$SCRIPT_TMP" --help > /dev/null
mv "$SCRIPT_TMP" "$INSTALL_ROOT/executor.py"
cat > "$INSTALL_ROOT/executor.env" <<ENV
CONTAINER_UID=$CONTAINER_UID
CONTAINER_GID=$CONTAINER_GID
EXECUTOR_MEMORY=$EXECUTOR_MEMORY
EXECUTOR_CPUS=$EXECUTOR_CPUS
EXECUTOR_PIDS_LIMIT=$EXECUTOR_PIDS_LIMIT
EXECUTOR_POLL_SECONDS=$EXECUTOR_POLL_SECONDS
ENV
chmod 0600 "$INSTALL_ROOT/executor.env"
sed -e "s|@EXECUTOR_USER@|$EXECUTOR_USER|g" -e "s|@EXECUTOR_GROUP@|$EXECUTOR_GROUP|g" \
    -e "s|@EXECUTOR_PYTHON@|$EXECUTOR_PYTHON|g" -e "s|@INSTALL_ROOT@|$INSTALL_ROOT|g" \
    -e "s|@DATA_ROOT@|$FACTORY_HORDE_DATA_ROOT|g" \
    "$REPO_ROOT/installer/factory-horde-executor.service" > "$UNIT_TMP"
systemd-analyze verify "$UNIT_TMP"
sudo install -m 0644 "$UNIT_TMP" "/etc/systemd/system/$SERVICE_NAME.service"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME.service"
sudo systemctl restart "$SERVICE_NAME.service"
systemctl is-active "$SERVICE_NAME.service"
sha256sum "$INSTALL_ROOT/executor.py"
