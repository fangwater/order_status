#!/usr/bin/env bash
# Configure nginx HTTP reverse proxy and reload nginx.

set -euo pipefail

PORT="${PORT:-4190}"
LISTEN="${LISTEN:-${PORT}}"
UPSTREAM="${UPSTREAM:-127.0.0.1:19997}"
PREFIX="${PREFIX:-/order_status}"
SERVER_NAME="${SERVER_NAME:-_}"
CONF_DIR="${CONF_DIR:-/etc/nginx/sites-enabled}"
SITE_NAME="${SITE_NAME:-order_status_http_${PORT}}"
CONF_PATH="${CONF_DIR}/${SITE_NAME}.conf"

need_sudo() {
    if [ "$(id -u)" -ne 0 ]; then
        echo "sudo"
    else
        echo ""
    fi
}

SUDO="$(need_sudo)"

ensure_nginx() {
    if ! command -v nginx >/dev/null 2>&1; then
        echo "[ERROR] nginx is not installed. Install it first and rerun." >&2
        exit 1
    fi
}

normalize_listen() {
    local raw="$1"
    if [[ "${raw}" =~ ^[0-9]+$ ]]; then
        echo "0.0.0.0:${raw}"
        return
    fi
    if [[ "${raw}" =~ ^:[0-9]+$ ]]; then
        echo "0.0.0.0${raw}"
        return
    fi
    echo "${raw}"
}

normalize_upstream() {
    local raw="$1"
    if [[ "${raw}" =~ ^https?:// ]]; then
        echo "${raw}"
        return
    fi
    echo "http://${raw}"
}

normalize_prefix() {
    local raw="$1"
    if [[ -z "${raw}" ]]; then
        echo "/"
        return
    fi
    if [[ "${raw}" != /* ]]; then
        raw="/${raw}"
    fi
    if [[ "${raw}" != "/" ]]; then
        raw="${raw%/}"
    fi
    echo "${raw}"
}

ensure_nginx

${SUDO} mkdir -p "${CONF_DIR}"

listen_value="$(normalize_listen "${LISTEN}")"
upstream_value="$(normalize_upstream "${UPSTREAM}")"
prefix_value="$(normalize_prefix "${PREFIX}")"
prefix_escaped="${prefix_value//\//\\/}"

cat <<EOF | ${SUDO} tee "${CONF_PATH}" >/dev/null
server {
    listen ${listen_value};
    server_name ${SERVER_NAME};

    location = ${prefix_value} {
        return 301 ${prefix_value}/;
    }

    location ^~ ${prefix_value}/ {
        rewrite ^${prefix_escaped}/?(.*)$ /\$1 break;
        proxy_pass ${upstream_value};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
    }
}
EOF

${SUDO} nginx -t
if command -v systemctl >/dev/null 2>&1; then
    ${SUDO} systemctl reload nginx
else
    ${SUDO} nginx -s reload
fi

echo "Nginx HTTP proxy configured: ${CONF_PATH} -> ${upstream_value}"
