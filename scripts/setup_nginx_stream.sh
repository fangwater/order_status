#!/usr/bin/env bash
# Configure nginx stream proxies from ~/nginx_streams.txt and reload nginx.

set -euo pipefail

PORT="${PORT:-19997}"
LISTEN="${LISTEN:-${PORT}}"
DEFAULT_UPSTREAM="${UPSTREAM:-127.0.0.1:${PORT}}"
MAPPING_FILE="${MAPPING_FILE:-$HOME/nginx_streams.txt}"
SITE_NAME="${SITE_NAME:-order_status_stream_${PORT}}"
STREAM_DIR="${STREAM_DIR:-/etc/nginx/stream-enabled}"
STREAM_CONF="${STREAM_CONF:-${STREAM_DIR}/${SITE_NAME}.conf}"
STREAM_MAIN_CONF="${STREAM_MAIN_CONF:-/etc/nginx/stream.conf}"
NGINX_CONF="${NGINX_CONF:-/etc/nginx/nginx.conf}"

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

ensure_stream_include() {
    ${SUDO} mkdir -p "${STREAM_DIR}"
    if [ ! -f "${STREAM_MAIN_CONF}" ]; then
        cat <<EOF | ${SUDO} tee "${STREAM_MAIN_CONF}" >/dev/null
stream {
    include ${STREAM_DIR}/*.conf;
}
EOF
    fi

    if ! ${SUDO} grep -Fqx "include ${STREAM_MAIN_CONF};" "${NGINX_CONF}"; then
        local tmp
        tmp="$(mktemp)"
        ${SUDO} awk -v inc_line="include ${STREAM_MAIN_CONF};" '
            {print}
            $0 ~ /^include \/etc\/nginx\/modules-enabled\/\*\.conf;$/ && !done {
                print inc_line;
                done=1;
                next
            }
            END {
                if (!done) {
                    print inc_line;
                }
            }
        ' "${NGINX_CONF}" > "${tmp}"
        ${SUDO} mv "${tmp}" "${NGINX_CONF}"
    fi
}

ensure_mapping_file() {
    if [ ! -f "${MAPPING_FILE}" ]; then
        mkdir -p "$(dirname "${MAPPING_FILE}")" >/dev/null 2>&1 || true
        cat <<EOF > "${MAPPING_FILE}"
# listen_addr:port upstream_addr:port
# Examples:
# 4190 127.0.0.1:6379
# 19997 127.0.0.1:19997
${LISTEN} ${DEFAULT_UPSTREAM}
EOF
        echo "[INFO] Created mapping file: ${MAPPING_FILE}"
        echo "[INFO] Edit it and rerun if you need different mappings."
    fi
}

strip_redis_url() {
    local raw="$1"
    if [[ "${raw}" == redis://* || "${raw}" == rediss://* ]]; then
        raw="${raw#redis://}"
        raw="${raw#rediss://}"
        raw="${raw#*@}"
        raw="${raw%%/*}"
    fi
    echo "${raw}"
}

normalize_listen() {
    local raw
    raw="$(strip_redis_url "$1")"
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
    local raw
    raw="$(strip_redis_url "$1")"
    if [[ "${raw}" =~ ^[0-9]+$ ]]; then
        echo "127.0.0.1:${raw}"
        return
    fi
    if [[ "${raw}" =~ ^:[0-9]+$ ]]; then
        echo "127.0.0.1${raw}"
        return
    fi
    echo "${raw}"
}

stream_servers() {
    if [ -f "${MAPPING_FILE}" ]; then
        while IFS= read -r line; do
            [[ -z "${line// }" || "${line}" =~ ^# ]] && continue
            local listen upstream
            listen="$(echo "${line}" | awk '{print $1}')"
            upstream="$(echo "${line}" | awk '{print $2}')"
            if [ -z "${listen}" ] || [ -z "${upstream}" ]; then
                echo "Ignoring invalid line: ${line}" >&2
                continue
            fi
            listen="$(normalize_listen "${listen}")"
            upstream="$(normalize_upstream "${upstream}")"
            if [ -z "${listen}" ] || [ -z "${upstream}" ]; then
                echo "Ignoring invalid mapping: ${line}" >&2
                continue
            fi
            cat <<EOF
server {
    listen ${listen};
    proxy_pass ${upstream};
}

EOF
        done < "${MAPPING_FILE}"
    else
        local listen upstream
        listen="$(normalize_listen "${LISTEN}")"
        upstream="$(normalize_upstream "${DEFAULT_UPSTREAM}")"
        cat <<EOF
server {
    listen ${listen};
    proxy_pass ${upstream};
}
EOF
    fi
}

ensure_nginx
ensure_stream_include
ensure_mapping_file

cat <<EOF | ${SUDO} tee "${STREAM_CONF}" >/dev/null
$(stream_servers)
EOF

${SUDO} nginx -t
if command -v systemctl >/dev/null 2>&1; then
    ${SUDO} systemctl reload nginx
else
    ${SUDO} nginx -s reload
fi

echo "Nginx stream configured: ${STREAM_CONF} (mappings from ${MAPPING_FILE})"
