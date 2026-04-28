#!/usr/bin/env bash
set -euo pipefail

SERVICE_USER="${SERVICE_USER:-display_art}"
SERVICE_NAME="${SERVICE_NAME:-display_art.service}"
SERVICE_HOME="${SERVICE_HOME:-/var/lib/display_art}"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}"
KEY_FILE="${SERVICE_HOME}/.agent/api_keys.json"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo $0"
  exit 1
fi

if [[ ! -f "${PROJECT_DIR}/art_loop.py" ]]; then
  echo "art_loop.py not found in ${PROJECT_DIR}"
  exit 1
fi

echo "Installing Display Art from ${PROJECT_DIR}"

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

for group in video render input; do
  if getent group "${group}" >/dev/null; then
    usermod -aG "${group}" "${SERVICE_USER}"
  fi
done

install -d -m 0755 -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${SERVICE_HOME}"
install -d -m 0700 -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${SERVICE_HOME}/.agent"
install -d -m 0775 -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${PROJECT_DIR}/images"

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
WorkingDirectory=${PROJECT_DIR}
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=${PYTHON_BIN} ${PROJECT_DIR}/art_loop.py
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"

echo "Installed ${SERVICE_NAME}"
echo "Start it now with: sudo systemctl start ${SERVICE_NAME}"
echo "Check logs with: sudo journalctl -u ${SERVICE_NAME} -f"
