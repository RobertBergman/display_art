#!/usr/bin/env bash
set -euo pipefail

SERVICE_USER="${SERVICE_USER:-display_art}"
SERVICE_NAME="${SERVICE_NAME:-display_art.service}"
SERVICE_HOME="${SERVICE_HOME:-/var/lib/display_art}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}"
KEY_FILE="${SERVICE_HOME}/.agent/api_keys.json"

APP_SHARE="/usr/local/share/display_art"
APP_BIN="/usr/local/bin/display_art_display"
IMAGES_DIR="${SERVICE_HOME}/images"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo $0"
  exit 1
fi

if [[ ! -f "${PROJECT_DIR}/art_loop.py" ]]; then
  echo "art_loop.py not found in ${PROJECT_DIR}"
  exit 1
fi

echo "Installing Display Art from ${PROJECT_DIR}"

# --- Build C binary if not already compiled ---
if [[ ! -f "${PROJECT_DIR}/display_image" ]]; then
  echo "Building display_image..."
  gcc "${PROJECT_DIR}/display_image.c" -o "${PROJECT_DIR}/display_image" \
    $(pkg-config --cflags --libs libdrm)
fi

# --- Create service user ---
if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd \
    --system \
    --create-home \
    --home-dir "${SERVICE_HOME}" \
    --shell /usr/sbin/nologin \
    "${SERVICE_USER}"
  echo "Created service user: ${SERVICE_USER}"
else
  echo "Service user already exists: ${SERVICE_USER}"
fi

# --- Grant device access ---
for group in video render input; do
  if getent group "${group}" >/dev/null; then
    usermod -aG "${group}" "${SERVICE_USER}"
  fi
done

# --- Install files to system locations ---
install -d -m 0755 "${APP_SHARE}"
install -m 0755 "${PROJECT_DIR}/art_loop.py" "${APP_SHARE}/art_loop.py"
echo "Installed art_loop.py to ${APP_SHARE}/"

install -d -m 0755 "$(dirname "${APP_BIN}")"
install -m 0755 "${PROJECT_DIR}/display_image" "${APP_BIN}"
echo "Installed display_image to ${APP_BIN}"

# --- Create writable directories in service home ---
install -d -m 0755 -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${SERVICE_HOME}"
install -d -m 0700 -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${SERVICE_HOME}/.agent"
install -d -m 0775 -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${IMAGES_DIR}"
echo "Service home: ${SERVICE_HOME}"

# --- Install API key ---
if [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
  api_key="${OPENROUTER_API_KEY}"
else
  read -rsp "OpenRouter API key: " api_key
  echo
fi

if [[ -z "${api_key}" ]]; then
  echo "OpenRouter API key is required"
  exit 1
fi

API_KEY="${api_key}" "${PYTHON_BIN}" - <<'PY' > "${KEY_FILE}"
import json
import os

print(json.dumps({"OPENROUTER_API_KEY": os.environ["API_KEY"]}, indent=2))
PY
chown "${SERVICE_USER}:${SERVICE_USER}" "${KEY_FILE}"
chmod 0600 "${KEY_FILE}"
echo "API key installed"

# --- Write systemd unit ---
cat > "${UNIT_PATH}" <<EOF
[Unit]
Description=Display Art idle screen
After=systemd-udev-settle.service network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
SupplementaryGroups=video render input
WorkingDirectory=${SERVICE_HOME}
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONUNBUFFERED=1
ExecStart=${PYTHON_BIN} ${APP_SHARE}/art_loop.py
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"

echo
echo "Installed ${SERVICE_NAME}"
echo "Start it now with: sudo systemctl start ${SERVICE_NAME}"
echo "Check logs with: sudo journalctl -u ${SERVICE_NAME} -f"
