#!/usr/bin/with-contenv bashio
set -euo pipefail

readonly DB_DIR="/config/vnstat"
readonly CONFIG_FILE="/data/vnstat.conf"
readonly APP_DIR="/app"
readonly WEB_PORT="8099"

detect_interface() {
    local detected

    detected="$(ip route show default 2>/dev/null | awk '/default/ {print $5; exit}')"
    if [[ -n "${detected}" ]]; then
        printf '%s\n' "${detected}"
        return 0
    fi

    detected="$(ip -o link show 2>/dev/null | awk -F': ' '$2 != "lo" {print $2; exit}')"
    if [[ -n "${detected}" ]]; then
        printf '%s\n' "${detected}"
        return 0
    fi

    return 1
}

ensure_interface_exists() {
    local iface="${1}"
    ip link show "${iface}" >/dev/null 2>&1
}

mkdir -p "${DB_DIR}"

CONFIGURED_INTERFACE="$(bashio::config 'interface')"
if [[ -n "${CONFIGURED_INTERFACE}" ]]; then
    INTERFACE="${CONFIGURED_INTERFACE}"
else
    INTERFACE="$(detect_interface)"
fi

if [[ -z "${INTERFACE:-}" ]] || ! ensure_interface_exists "${INTERFACE}"; then
    bashio::log.fatal "Unable to determine a valid network interface. Set the 'interface' option explicitly."
    exit 1
fi

export VNSTAT_DB_DIR="${DB_DIR}"
export VNSTAT_INTERFACE="${INTERFACE}"
export VNSTAT_CONFIG="${CONFIG_FILE}"
export APP_PORT="${WEB_PORT}"

bashio::log.info "Using interface: ${VNSTAT_INTERFACE}"
bashio::log.info "Using database directory: ${VNSTAT_DB_DIR}"

cat > "${CONFIG_FILE}" <<EOF
DatabaseDir "${VNSTAT_DB_DIR}"
UpdateInterval 30
PollInterval 5
SaveInterval 5
MonthlyMonths 48
CreateDirs 1
UseLogging 0
EOF

vnstat --config "${VNSTAT_CONFIG}" --add -i "${VNSTAT_INTERFACE}" >/dev/null 2>&1 || true

python3 "${APP_DIR}/server.py" &
WEB_PID=$!

vnstatd -n --config "${VNSTAT_CONFIG}" &
VNSTATD_PID=$!

cleanup() {
    kill "${WEB_PID}" "${VNSTATD_PID}" >/dev/null 2>&1 || true
    wait "${WEB_PID}" "${VNSTATD_PID}" 2>/dev/null || true
}

trap cleanup EXIT HUP INT TERM

wait -n "${WEB_PID}" "${VNSTATD_PID}"
