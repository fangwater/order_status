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
PYTHON_BIN="${PYTHON_BIN:-/home/ubuntu/jupyter_env/bin/python}"

if ! command -v pm2 >/dev/null 2>&1; then
    echo "[ERROR] pm2 not found in PATH." >&2
    exit 1
fi

if [ -z "${PYTHON_BIN}" ]; then
    if [ -n "${VENV_PATH}" ]; then
        PYTHON_BIN="${VENV_PATH}/bin/python"
    else
        PYTHON_BIN="python"
    fi
fi

if [ ! -x "${PYTHON_BIN}" ]; then
    if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
        echo "[ERROR] python not found. Set PYTHON_BIN or VENV_PATH." >&2
        exit 1
    fi
    PYTHON_BIN="$(command -v "${PYTHON_BIN}")"
fi

export LOG_LEVEL
export APP_BASE_PATH

pm2 start \
    "${PYTHON_BIN}" \
    --name "${APP_NAME}" \
    --cwd "${ROOT_DIR}" \
    --time \
    --merge-logs \
    -- -m uvicorn "${APP_MODULE}" --host "${HOST}" --port "${PORT}"

pm2 save
pm2 status "${APP_NAME}"
