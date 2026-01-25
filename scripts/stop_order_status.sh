#!/usr/bin/env bash
# Stop order_status pm2 process.

set -euo pipefail

APP_NAME="${APP_NAME:-order_status}"

if ! command -v pm2 >/dev/null 2>&1; then
    echo "[ERROR] pm2 not found in PATH." >&2
    exit 1
fi

if pm2 describe "${APP_NAME}" >/dev/null 2>&1; then
    pm2 stop "${APP_NAME}"
    if [ "${DELETE:-0}" = "1" ]; then
        pm2 delete "${APP_NAME}"
    fi
    pm2 save
else
    echo "[INFO] pm2 process not found: ${APP_NAME}"
fi
