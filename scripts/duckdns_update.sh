#!/bin/sh
# duckdns_update.sh
# -----------------
# Minimal DuckDNS updater for low-memory Raspberry Pi.
# - POSIX /bin/sh (works with dash); no arrays or bashisms.
# - Handles Windows CRLF config files safely.
# - Uses curl if available, else wget. Single HTTP call per v4/v6.
# - Simple lock to prevent overlapping runs.
#
# Config file (default):
#   /home/pi/KeukaSensorProd/config/duckdns.conf
#     token=YOUR_DUCKDNS_TOKEN
#     domains=example1,example2
#
# Optional overrides (e.g. via /etc/default/duckdns or service Environment=):
#   DUCKDNS_CONF=/custom/path/duckdns.conf
#   DUCKDNS_LOG=/custom/path/duckdns_last.txt
#   DUCKDNS_IPV6=true           # also update AAAA
#   CURL=/usr/bin/curl          # path to curl
#
# Exit codes: 0 on success, 1 on logical failure.

set -eu

BASE="/home/pi/KeukaSensorProd"
CONF="${DUCKDNS_CONF:-$BASE/config/duckdns.conf}"
LOG="${DUCKDNS_LOG:-$BASE/config/duckdns_last.txt}"
LOCKDIR="$BASE/config/.duckdns.lock"

# ---- tiny lock (mkdir is atomic) --------------------------------------------
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  # Another run is active; avoid overlap
  exit 0
fi
cleanup() { rmdir "$LOCKDIR" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

# ---- logging ----------------------------------------------------------------
log_line() {
  if command -v date >/dev/null 2>&1; then
    # -Is is ISO-8601 seconds; fall back if not supported
    TS="$(date -Is 2>/dev/null || date '+%Y-%m-%dT%H:%M:%S%z')"
    printf "%s %s\n" "$TS" "$*" >> "$LOG"
  else
    printf "%s\n" "$*" >> "$LOG"
  fi
}

# ---- find HTTP client -------------------------------------------------------
find_http_client() {
  if [ -n "${CURL:-}" ] && [ -x "$CURL" ]; then
    printf "%s" "$CURL"; return
  fi
  if command -v curl >/dev/null 2>&1; then
    printf "%s" "$(command -v curl)"; return
  fi
  if command -v wget >/dev/null 2>&1; then
    printf "%s" "wget"; return
  fi
  printf "%s" ""
}

HTTP_BIN="$(find_http_client)"
[ -n "$HTTP_BIN" ] || { log_line "curl/wget not found"; exit 1; }

http_get() {
  # $1 = URL ; echoes body, returns non-zero on transport error
  if [ "$HTTP_BIN" = "wget" ]; then
    wget -q -O - "$1"
  else
    "$HTTP_BIN" -fsS --max-time 15 "$1"
  fi
}

# ---- load config (safe with CRLF) -------------------------------------------
TOKEN=""; DOMAINS=""
if [ -f "$CONF" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    # strip trailing CR if file is CRLF
    line=${line%$(printf '\r')}
    # skip blanks/comments
    case "$line" in ''|'#'*) continue;; esac
    key=${line%%=*}; val=${line#*=}
    # trim possible surrounding quotes
    case "$val" in \"*\") val=${val#\"}; val=${val%\"};; esac
    case "$key" in
      token)   TOKEN=$val ;;
      domains) DOMAINS=$val ;;
    esac
  done < "$CONF"
fi

# env overrides win
[ -n "${token:-}" ]   && TOKEN=$token
[ -n "${domains:-}" ] && DOMAINS=$domains

# ensure log directory exists
mkdir -p "$(dirname "$LOG")"

# validate
if [ -z "${TOKEN:-}" ] || [ -z "${DOMAINS:-}" ]; then
  log_line "MISSING token/domains ($CONF)"
  exit 1
fi

EXIT=0

# ---- IPv4 update ------------------------------------------------------------
URL4="https://www.duckdns.org/update?domains=${DOMAINS}&token=${TOKEN}&ip="
RESP4="$(http_get "$URL4" 2>/dev/null || true)"; [ -n "${RESP4:-}" ] || RESP4="(noresp)"
log_line "v4 $RESP4 $DOMAINS"
[ "$RESP4" = "OK" ] || EXIT=1

# ---- Optional IPv6 update ---------------------------------------------------
if [ "${DUCKDNS_IPV6:-false}" = "true" ]; then
  URL6="https://www.duckdns.org/update?domains=${DOMAINS}&token=${TOKEN}&ipv6="
  RESP6="$(http_get "$URL6" 2>/dev/null || true)"; [ -n "${RESP6:-}" ] || RESP6="(noresp)"
  log_line "v6 $RESP6 $DOMAINS"
  [ "$RESP6" = "OK" ] || EXIT=1
fi

exit "$EXIT"
