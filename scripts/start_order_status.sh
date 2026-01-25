#!/usr/bin/env bash
# Start order_status with pm2.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

APP_NAME="${APP_NAME:-order_status}"
APP_MODULE="${APP_MODULE:-app.main:app}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-19997}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
APP_BASE_PATH="${APP_BASE_PATH:-/order_status}"
VENV_PATH="${VENV_PATH:-}"

if ! command -v pm2 >/dev/null 2>&1; then
    echo "[ERROR] pm2 not found in PATH." >&2
    exit 1
fi

if [ -n "${VENV_PATH}" ]; then
    if [ ! -x "${VENV_PATH}/bin/uvicorn" ]; then
        echo "[ERROR] uvicorn not found in ${VENV_PATH}/bin/uvicorn" >&2
        exit 1
    fi
    UVICORN_BIN="${VENV_PATH}/bin/uvicorn"
else
    if ! command -v uvicorn >/dev/null 2>&1; then
        echo "[ERROR] uvicorn not found in PATH. Set VENV_PATH or install uvicorn." >&2
        exit 1
    fi
    UVICORN_BIN="uvicorn"
fi

export LOG_LEVEL
export APP_BASE_PATH

pm2 start \
    --name "${APP_NAME}" \
    --cwd "${ROOT_DIR}" \
    --time \
    --merge-logs \
    --interpreter bash \
    -- bash -lc "${UVICORN_BIN} ${APP_MODULE} --host ${HOST} --port ${PORT}"

pm2 save
pm2 status "${APP_NAME}"
