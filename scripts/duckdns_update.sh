#!/usr/bin/env bash
# duckdns_update.sh (debug build, stale-lock safe)
# ------------------------------------------------
# DuckDNS updater with production niceties + detailed debug logging:
# - Bash strict mode; optional xtrace to the log (DUCKDNS_XTRACE=true) with safe fallback to stderr.
# - curl/wget fallback (or override via $CURL).
# - Lock with stale detection (PID + mtime TTL) to avoid permanent "another run is active".
# - CRLF-safe config parsing.
# - Optional IPv6 update via $DUCKDNS_IPV6=true.
# - Env overrides for CONF/LOG via $DUCKDNS_CONF/$DUCKDNS_LOG.
# - Rich logging: DNS, HTTP status/headers, curl metrics, stderr, timings.
#
# Exit codes:
#   0 = success (OK for all requested families)
#   1 = remote KO or transport no-response
#   2 = local/script error (missing config, no HTTP client, etc.)

set -Eeuo pipefail

# ------------------------ config selection -------------------------
timestamp() { date -Is 2>/dev/null || date '+%Y-%m-%dT%H:%M:%S%z'; }

choose_conf() {
  printf '%s' "/home/pi/KeukaSensorProd/config/duckdns.conf"
}

choose_log() {
  printf '%s' "/home/pi/KeukaSensorProd/logs/duckdns_last.txt"
}

CONF="$(choose_conf)"
LOG="$(choose_log)"


# Ensure log dir exists early
mkdir -p "$(dirname "$LOG")" || true
touch "$LOG" 2>/dev/null || true

# ------------------------ debug controls --------------------------
DEBUG="${DUCKDNS_DEBUG:-false}"
XTRACE="${DUCKDNS_XTRACE:-false}"

SESSION_ID="$(date +%s)-$$"
# Base log writer (file)
_log_file() { printf "%s [%s] %s\n" "$(timestamp)" "$SESSION_ID" "$*" >> "$LOG" 2>/dev/null || true; }
# Also to stderr when DEBUG=true
log() { _log_file "$*"; if [[ "$DEBUG" == "true" ]]; then printf "%s [%s] %s\n" "$(timestamp)" "$SESSION_ID" "$*" >&2; fi; }
# Multiline append (indented)
log_block() { while IFS= read -r __l; do _log_file "    ${__l}"; [[ "$DEBUG" == "true" ]] && printf "%s [%s]    %s\n" "$(timestamp)" "$SESSION_ID" "$__l" >&2; done; }

# Optional bash xtrace: try FD 9 to the log; if that fails, fall back to stderr
if [[ "$XTRACE" == "true" ]]; then
  if exec 9>>"$LOG" 2>/dev/null; then
    export BASH_XTRACEFD=9
    export PS4='+ $(date -Is 2>/dev/null || date "+%Y-%m-%dT%H:%M:%S%z") ['"$SESSION_ID"'] ${FUNCNAME[0]:-main}() $LINENO: '
    set -x
  else
    log "[warn] could not open $LOG for xtrace; falling back to stderr"
    export PS4='+ $(date -Is 2>/dev/null || date "+%Y-%m-%dT%H:%M:%S%z") ['"$SESSION_ID"'] ${FUNCNAME[0]:-main}() $LINENO: '
    set -x
  fi
fi

start_epoch_ns() { date +%s%N 2>/dev/null || { date +%s; printf '000000000'; }; }
elapsed_ms() {
  local end; end="$(date +%s%N 2>/dev/null || { date +%s; printf '000000000'; })"
  awk -v s="$1" -v e="$end" 'BEGIN{d=(e-s)/1000000; if (d<0) d=0; printf "%.0f", d}'
}

# ------------------------ lock with stale detection ----------------
LOCKBASE="${DUCKDNS_LOCKBASE:-/home/pi/KeukaSensorProd/logs}"
mkdir -p "${LOCKBASE}" || true
LOCKDIR="${LOCKBASE}/.duckdns.lock"
LOCKPID="${LOCKDIR}/pid"
LOCK_TTL_MIN="${DUCKDNS_LOCK_TTL_MIN:-10}"  # consider stale after 10 minutes

try_acquire_lock() {
  # First attempt
  if mkdir "${LOCKDIR}" 2>/dev/null; then
    echo "$$" > "$LOCKPID" 2>/dev/null || true
    return 0
  fi

  # If exists, check PID
  if [[ -f "$LOCKPID" ]]; then
    local p; p="$(cat "$LOCKPID" 2>/dev/null || true)"
    if [[ -n "$p" ]] && ! kill -0 "$p" 2>/dev/null; then
      # stale by dead PID
      rm -rf "${LOCKDIR}" 2>/dev/null || true
      if mkdir "${LOCKDIR}" 2>/dev/null; then echo "$$" > "$LOCKPID" 2>/dev/null || true; return 0; fi
    fi
  fi

  # Also consider mtime-based TTL
  if command -v find >/dev/null 2>&1; then
    if find "${LOCKDIR}" -maxdepth 0 -mmin +"$LOCK_TTL_MIN" >/dev/null 2>&1; then
      rm -rf "${LOCKDIR}" 2>/dev/null || true
      if mkdir "${LOCKDIR}" 2>/dev/null; then echo "$$" > "$LOCKPID" 2>/dev/null || true; return 0; fi
    fi
  fi

  return 1
}

if ! try_acquire_lock; then
  log "[lock] another run is active; exiting without work"
  exit 0
fi
cleanup() { rm -rf "${LOCKDIR}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

# ------------------------ environment summary ---------------------
log "[start] duckdns updater"
log "[paths] CONF=${CONF}"
log "[paths] LOG=${LOG}"
log "[env]   DEBUG=${DEBUG} XTRACE=${XTRACE} USER=${USER:-$(id -un 2>/dev/null || echo '?')} UID=$(id -u 2>/dev/null || echo '?')"
log "[env]   PATH=${PATH}"

# ------------------------ HTTP client selection -------------------
find_http_client() {
  if [[ -n "${CURL:-}" && -x "$CURL" ]]; then printf '%s' "$CURL"; return; fi
  if command -v curl >/dev/null 2>&1; then command -v curl; return; fi
  if command -v wget >/dev/null 2>&1; then printf '%s' "wget"; return; fi
  printf '%s' ""
}

HTTP_BIN="$(find_http_client)"
if [[ -z "$HTTP_BIN" ]]; then
  log "[error] no curl/wget found"
  exit 2
fi

if [[ "$HTTP_BIN" == *curl* ]]; then
  log "[http] using curl: $("$HTTP_BIN" --version 2>/dev/null | head -n1 || echo '?')"
else
  log "[http] using wget: $("$HTTP_BIN" --version 2>/dev/null | head -n1 || echo '?')"
fi

# ------------------------ DNS/Network debug -----------------------
dns_debug() {
  local host="$1"
  if command -v getent >/dev/null 2>&1; then
    local a; a="$(getent hosts "$host" 2>/dev/null || true)"
    if [[ -n "$a" ]]; then
      log "[dns] getent hosts ${host}:"; printf "%s\n" "$a" | log_block
    else
      log "[dns] getent hosts ${host}: (no result)"
    fi
  fi
  if command -v host >/dev/null 2>&1; then
    local h; h="$(host "$host" 2>&1 || true)"
    log "[dns] host ${host}:"; printf "%s\n" "$h" | log_block
  elif command -v nslookup >/dev/null 2>&1; then
    local n; n="$(nslookup "$host" 2>&1 || true)"
    log "[dns] nslookup ${host}:"; printf "%s\n" "$n" | log_block
  fi
}

if [[ "$DEBUG" == "true" ]]; then
  log "[sys] uname: $(uname -a 2>/dev/null || echo '?')"
  log "[sys] hostname -I: $(hostname -I 2>/dev/null || echo '?')"
  log "[sys] default route: $(ip route get 1.1.1.1 2>/dev/null | head -n1 || echo '?')"
  dns_debug "www.duckdns.org"
fi

# ------------------------ load config (CRLF safe) -----------------
TOKEN=""; DOMAINS=""
if [[ -f "$CONF" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "${line: -1}" == $'\r' ]] && line="${line%$'\r'}"
    [[ -z "$line" || "${line:0:1}" == "#" ]] && continue
    key="${line%%=*}"; val="${line#*=}"
    if [[ "${val:0:1}" == '"' && "${val: -1}" == '"' ]]; then val="${val:1:-1}"; fi
    case "${key,,}" in
      token)   TOKEN="$val" ;;
      domains) DOMAINS="$val" ;;
    esac
  done < "$CONF"
fi

# env overrides (lower/upper-case)
[[ -n "${token:-}"   ]] && TOKEN="$token"
[[ -n "${domains:-}" ]] && DOMAINS="$domains"

# redact token for logs but show length
tok_len="${#TOKEN}"
log "[cfg] domains=${DOMAINS:-<unset>} token_len=${tok_len}"
if [[ -z "$TOKEN" || -z "$DOMAINS" ]]; then
  log "[error] missing token/domains ($CONF)"
  exit 2
fi

# ------------------------ HTTP wrappers ---------------------------
http_get_curl() {
  local url="$1"
  local tmp_body tmp_hdr tmp_err
  tmp_body="$(mktemp -t duckdns_body.XXXXXX)" || return 2
  tmp_hdr="$(mktemp -t duckdns_hdr.XXXXXX)" || { rm -f "$tmp_body"; return 2; }
  tmp_err="$(mktemp -t duckdns_err.XXXXXX)" || { rm -f "$tmp_body" "$tmp_hdr"; return 2; }

  local start_ns; start_ns="$(start_epoch_ns)"
  local metrics=""
  if metrics="$(
    "$HTTP_BIN" \
      -fsS --max-time "${DUCKDNS_HTTP_TIMEOUT:-15}" \
      ${DUCKDNS_CURL_OPTS:-} \
      -H 'User-Agent: keuka-duckdns/1.0' \
      -D "$tmp_hdr" \
      -o "$tmp_body" \
      -w 'http_code=%{http_code} time_total=%{time_total} namelookup=%{time_namelookup} connect=%{time_connect} appconnect=%{time_appconnect} starttransfer=%{time_starttransfer} remote_ip=%{remote_ip} ssl_verify=%{ssl_verify_result}' \
      "$url" \
    2>"$tmp_err"
  )"; then :; else :; fi
  local rc=$?
  local ms; ms="$(elapsed_ms "$start_ns")"

  log "[curl] GET ${url%%token=*}token=REDACTED"
  if [[ -s "$tmp_hdr" ]]; then
    log "[curl] response headers:"; sed 's/^/HTTP> /' "$tmp_hdr" | log_block
  else
    log "[curl] response headers: (none)"
  fi
  if [[ -n "$metrics" ]]; then
    log "[curl] metrics: ${metrics} elapsed_ms=${ms}"
  else
    log "[curl] metrics: (none) elapsed_ms=${ms}"
  fi
  if [[ -s "$tmp_err" ]]; then
    log "[curl] stderr:"; sed 's/^/ERR> /' "$tmp_err" | log_block
  else
    log "[curl] stderr: (empty)"
  fi

  cat "$tmp_body"
  rm -f "$tmp_body" "$tmp_hdr" "$tmp_err"
  return "$rc"
}

http_get_wget() {
  local url="$1"
  local start_ns; start_ns="$(start_epoch_ns)"
  local out; out="$(wget -q -O - --timeout="${DUCKDNS_HTTP_TIMEOUT:-15}" --server-response "$url" 2> >(sed 's/^/ERR> /' | while read -r l; do _log_file "$l"; [[ "$DEBUG" == "true" ]] && printf "%s [%s] %s\n" "$(timestamp)" "$SESSION_ID" "$l" >&2; done))" || true
  local rc=$?
  local ms; ms="$(elapsed_ms "$start_ns")"
  log "[wget] GET ${url%%token=*}token=REDACTED elapsed_ms=${ms} rc=${rc}"
  printf "%s" "$out"
  return "$rc"
}

http_get() {
  local url="$1"
  if [[ "$HTTP_BIN" == *curl* ]]; then
    http_get_curl "$url"; return $?
  else
    http_get_wget "$url"; return $?
  fi
}

# ------------------------ perform updates -------------------------
exit_code=0

# IPv4 update
url4="https://www.duckdns.org/update?domains=${DOMAINS}&token=${TOKEN}&ip="
resp4=""
if resp4="$(http_get "$url4")"; then :; else :; fi

resp4="${resp4//$'\r'/}"
resp4_trim="$(printf "%s" "$resp4" | awk 'BEGIN{ORS="";} {print} END{}')"

if [[ -z "$resp4_trim" ]]; then
  log "[duckdns] v4 (noresp) ${DOMAINS}"
  exit_code=1
elif [[ "$resp4_trim" == "OK" ]]; then
  log "[duckdns] v4 OK ${DOMAINS}"
else
  log "[duckdns] v4 ${resp4_trim} ${DOMAINS}"
  exit_code=1
fi

# Optional IPv6
if [[ "${DUCKDNS_IPV6:-false}" == "true" ]]; then
  url6="https://www.duckdns.org/update?domains=${DOMAINS}&token=${TOKEN}&ipv6="
  resp6=""
  if resp6="$(http_get "$url6")"; then :; else :; fi
  resp6="${resp6//$'\r'/}"
  resp6_trim="$(printf "%s" "$resp6" | awk 'BEGIN{ORS="";} {print} END{}')"
  if [[ -z "$resp6_trim" ]]; then
    log "[duckdns] v6 (noresp) ${DOMAINS}"
    exit_code=1
  elif [[ "$resp6_trim" == "OK" ]]; then
    log "[duckdns] v6 OK ${DOMAINS}"
  else
    log "[duckdns] v6 ${resp6_trim} ${DOMAINS}"
    exit_code=1
  fi
fi

log "[end] exit_code=${exit_code}"
exit "$exit_code"
