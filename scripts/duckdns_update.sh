#!/usr/bin/env bash
set -euo pipefail

# Config file created by the web UI (token/domains) or edit manually
CONF="/home/pi/KeukaSensorProd/duckdns.conf"
LOG="/home/pi/KeukaSensorProd/duckdns_last.txt"

if [[ ! -f "$CONF" ]]; then
  echo "$(date -Is) no conf" >> "$LOG"
  exit 0
fi

# shellcheck disable=SC1090
source "$CONF"

if [[ -z "${token:-}" || -z "${domains:-}" ]]; then
  echo "$(date -Is) missing token/domains" >> "$LOG"
  exit 0
fi

OUT="$(curl -s "https://www.duckdns.org/update?domains=${domains}&token=${token}&ip=" || true)"
echo "$(date -Is) ${OUT}" >> "$LOG"

# Keep log to last 200 lines
tail -n 200 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
