#!/bin/sh
# duckdns_update.sh
# -----------------
# Minimal DuckDNS updater for low-memory Raspberry Pi.
# - POSIX /bin/sh; no bashisms.
# - Safe with CRLF in *config file* (not in this script).
# - Uses curl if available, else wget. One HTTP call per v4/v6.
# - Simple lock to avoid overlapping runs.
#
# Config file default (matches keuka/config.py):
#   /home/pi/KeukaSensorProd/keuka/duckdns.conf
#     token=YOUR_DUCKDNS_TOKEN
#     domains=sub1,sub2
#
# Optional env overrides (/etc/default/duckdns or service Environment=):
#   DUCKDNS_CONF=/custom/path/duckdns.conf
#   DUCKDNS_LOG=/custom/path/duckdns_last.txt
#   DUCKDNS_IPV6=true
#   CURL=/usr/bin/curl

set -eu

BASE="/home/pi/KeukaSensorProd"
CONF="${DUCKDNS_CONF:-$BASE/keuka/duckdns.conf}"
LOG="${DUCKDNS_LOG:-$BASE/keuka/duckdns_last.txt}"
LOCKDIR="$BASE/keuka/.duckdns.lock"

# ---- tiny lock --------------------------------------------------------------
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  # Another run is active; don't overlap
  exit 0
fi
cleanup() { rmdir "$LOCKDIR" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

# ---- logging ----------------------------------------------------------------
log_line() {
  # ISO-ish timestamp
  TS="$(date -Is 2>/dev/null || date '+%Y-%m-%dT%H:%M:%S%z')"
  printf "%s %s\n" "$TS" "$*" >> "$LOG"
}

# ---- http client ------------------------------------------------------------
find_http_client() {
  if [ -n "${CURL:-}" ] && [ -x "$CURL" ]; then printf "%s" "$CURL"; return; fi
  if command -v curl >/dev/null 2>&1; then printf "%s" "$(command -v curl)"; return; fi
  if command -v wget >/dev/null 2>&1; then printf "%s" "wget"; return; fi
  printf "%s" ""
}
HTTP_BIN="$(find_http_client)"
[ -n "$HTTP_BIN" ] || { log_line "[duckdns] curl/wget not found"; exit 1; }

http_get() {
  # $1=url; echo body or nothing on transport error
  if [ "$HTTP_BIN" = "wget" ]; then
    wget -q -O - "$1"
  else
    "$HTTP_BIN" -fsS --max-time 15 "$1"
  fi
}

# ---- load config (safe with CRLF) -------------------------------------------
TOKEN=""; DOMAINS=""
if [ -f "$CONF" ]; then
  # shellcheck disable=SC2039
  while IFS= read -r line || [ -n "$line" ]; do
    # strip trailing CR if present
    case "$line" in *"$(
      printf '\r'
    )") line=$(printf "%s" "$line" | tr -d '\r');; esac
    case "$line" in ''|'#'*) continue;; esac
    key=${line%%=*}; val=${line#*=}
    case "$val" in \"*\") val=${val#\"}; val=${val%\"};; esac
    case "$key" in
      token)   TOKEN=$val ;;
      domains) DOMAINS=$val ;;
    esac
  done < "$CONF"
fi

# Allow env to override
[ -n "${token:-}" ]   && TOKEN=$token
[ -n "${domains:-}" ] && DOMAINS=$domains

mkdir -p "$(dirname "$LOG")"

if [ -z "${TOKEN:-}" ] || [ -z "${DOMAINS:-}" ]; then
  log_line "[duckdns] missing token/domains ($CONF)"; exit 1
fi

EXIT=0

# IPv4 update
URL4="https://www.duckdns.org/update?domains=${DOMAINS}&token=${TOKEN}&ip="
RESP4="$(http_get "$URL4" 2>/dev/null || true)"; [ -n "${RESP4:-}" ] || RESP4="(noresp)"
log_line "[duckdns] v4 ${RESP4} ${DOMAINS}"
[ "$RESP4" = "OK" ] || EXIT=1

# Optional IPv6
if [ "${DUCKDNS_IPV6:-false}" = "true" ]; then
  URL6="https://www.duckdns.org/update?domains=${DOMAINS}&token=${TOKEN}&ipv6="
  RESP6="$(http_get "$URL6" 2>/dev/null || true)"; [ -n "${RESP6:-}" ] || RESP6="(noresp)"
  log_line "[duckdns] v6 ${RESP6} ${DOMAINS}"
  [ "$RESP6" = "OK" ] || EXIT=1
fi

exit "$EXIT"
