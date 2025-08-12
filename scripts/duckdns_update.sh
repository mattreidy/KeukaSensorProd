#!/usr/bin/env bash
set -euo pipefail

CONF="/opt/keuka-sensor/duckdns.conf"
LOG="/opt/keuka-sensor/duckdns_last.txt"

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

# Empty ip= means "use my current public IP"
OUT="$(curl -s "https://www.duckdns.org/update?domains=${domains}&token=${token}&ip=" || true)"
echo "$(date -Is) ${OUT}" >> "$LOG"
tail -n 200 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
