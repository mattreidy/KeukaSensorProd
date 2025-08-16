#!/usr/bin/env bash
# duckdns_update.sh
# -----------------
# DuckDNS updater with production niceties:
# - Bash strict mode.
# - curl/wget fallback (or override via $CURL).
# - Simple lock to avoid overlapping runs.
# - CRLF-safe config parsing.
# - Optional IPv6 update via $DUCKDNS_IPV6=true.
# - Env overrides for CONF/LOG via $DUCKDNS_CONF/$DUCKDNS_LOG.
#
# Exit codes:
#   0 = success (OK from DuckDNS for all requested families)
#   1 = remote KO from DuckDNS (bad token/domains). Treat as SUCCESS in systemd.
#   2 = local/script error (missing config, no HTTP client, etc.) -> real failure.
#
# Default search order for config/log (overridden by env):
#   1) $DUCKDNS_CONF / $DUCKDNS_LOG if set
#   2) /opt/keuka-sensor/duckdns.conf (and duckdns_last.txt) if present
#   3) /home/pi/KeukaSensorProd/keuka/duckdns.conf (and duckdns_last.txt)
#
# Config file format:
#   token=YOUR_DUCKDNS_TOKEN
#   domains=sub1,sub2

set -Eeuo pipefail

timestamp() { date -Is 2>/dev/null || date '+%Y-%m-%dT%H:%M:%S%z'; }

# ---- pick defaults -----------------------------------------------------------
choose_conf() {
  if [[ -n "${DUCKDNS_CONF:-}" ]]; then
    printf '%s' "$DUCKDNS_CONF"; return
  fi
  if [[ -f "/opt/keuka-sensor/duckdns.conf" ]]; then
    printf '%s' "/opt/keuka-sensor/duckdns.conf"; return
  fi
  if [[ -f "/home/pi/KeukaSensorProd/keuka/duckdns.conf" ]]; then
    printf '%s' "/home/pi/KeukaSensorProd/keuka/duckdns.conf"; return
  fi
  printf '%s' "/opt/keuka-sensor/duckdns.conf"
}

choose_log() {
  if [[ -n "${DUCKDNS_LOG:-}" ]]; then
    printf '%s' "$DUCKDNS_LOG"; return
  fi
  if [[ -f "/opt/keuka-sensor/duckdns_last.txt" ]]; then
    printf '%s' "/opt/keuka-sensor/duckdns_last.txt"; return
  fi
  if [[ -f "/home/pi/KeukaSensorProd/keuka/duckdns_last.txt" ]]; then
    printf '%s' "/home/pi/KeukaSensorProd/keuka/duckdns_last.txt"; return
  fi
  printf '%s' "/opt/keuka-sensor/duckdns_last.txt"
}

CONF="$(choose_conf)"
LOG="$(choose_log)"
LOCKDIR="$(dirname "$CONF")/.duckdns.lock"

# ---- tiny lock ---------------------------------------------------------------
mkdir -p "$(dirname "$LOCKDIR")"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  # Another run is active; don't overlap
  exit 0
fi
cleanup() { rmdir "$LOCKDIR" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

# ---- logging -----------------------------------------------------------------
log_line() {
  printf "%s %s\n" "$(timestamp)" "$*" >> "$LOG"
}

# ---- http client -------------------------------------------------------------
find_http_client() {
  if [[ -n "${CURL:-}" && -x "$CURL" ]]; then
    printf '%s' "$CURL"; return
  fi
  if command -v curl >/dev/null 2>&1; then
    command -v curl; return
  fi
  if command -v wget >/dev/null 2>&1; then
    printf '%s' "wget"; return
  fi
  printf '%s' ""
}

HTTP_BIN="$(find_http_client)"
if [[ -z "$HTTP_BIN" ]]; then
  log_line "[duckdns] curl/wget not found"
  exit 2
fi

http_get() {
  # $1=url; echoes body or nothing on transport error
  if [[ "$HTTP_BIN" == "wget" ]]; then
    wget -q -O - --timeout=15 "$1"
  else
    "$HTTP_BIN" -fsS --max-time 15 "$1"
  fi
}

# ---- load config (CRLF safe) -------------------------------------------------
TOKEN=""; DOMAINS=""
if [[ -f "$CONF" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "${line: -1}" == $'\r' ]] && line="${line%$'\r'}"
    [[ -z "$line" || "${line:0:1}" == "#" ]] && continue
    key="${line%%=*}"; val="${line#*=}"
    if [[ "${val:0:1}" == '"' && "${val: -1}" == '"' ]]; then
      val="${val:1:-1}"
    fi
    case "$key" in
      token)   TOKEN="$val" ;;
      domains) DOMAINS="$val" ;;
    esac
  done < "$CONF"
fi

# env overrides (support lower-case for compatibility)
[[ -n "${token:-}"   ]] && TOKEN="$token"
[[ -n "${domains:-}" ]] && DOMAINS="$domains"

mkdir -p "$(dirname "$LOG")"

if [[ -z "$TOKEN" || -z "$DOMAINS" ]]; then
  log_line "[duckdns] missing token/domains ($CONF)"
  exit 2
fi

# ---- perform updates ---------------------------------------------------------
exit_code=0

# IPv4: let server detect our WAN IP with ip=
url4="https://www.duckdns.org/update?domains=${DOMAINS}&token=${TOKEN}&ip="
resp4="$(http_get "$url4" 2>/dev/null || true)"; [[ -n "$resp4" ]] || resp4="(noresp)"
log_line "[duckdns] v4 ${resp4} ${DOMAINS}"
if [[ "$resp4" == "OK" || "$resp4"$'\n' == *$'\nOK' ]]; then
  : # success
else
  exit_code=1
fi

# Optional IPv6
if [[ "${DUCKDNS_IPV6:-false}" == "true" ]]; then
  url6="https://www.duckdns.org/update?domains=${DOMAINS}&token=${TOKEN}&ipv6="
  resp6="$(http_get "$url6" 2>/dev/null || true)"; [[ -n "$resp6" ]] || resp6="(noresp)"
  log_line "[duckdns] v6 ${resp6} ${DOMAINS}"
  if [[ "$resp6" == "OK" || "$resp6"$'\n' == *$'\nOK' ]]; then
    : # success
  else
    exit_code=1
  fi
fi

exit "$exit_code"
