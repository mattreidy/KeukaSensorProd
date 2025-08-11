#!/usr/bin/env bash
set -euo pipefail

# Active interface and gateway from default route
IFACE=$(ip route | awk '/default/ {print $5; exit}')
GATEWAY=$(ip route | awk '/default/ {print $3; exit}')
[[ -z "${IFACE:-}" ]] && IFACE=$(iw dev | awk '/Interface/ {print $2; exit}')
[[ -z "${GATEWAY:-}" ]] && exit 0

# Ensure Wi-Fi not blocked
rfkill unblock wifi || true

# Test connectivity via the Wi-Fi interface
if ! ping -I "$IFACE" -c 2 -W 2 "$GATEWAY" >/dev/null 2>&1; then
  logger -t wifi-watchdog "Gateway unreachable via $IFACE, restarting Wi-Fi"
  wpa_cli -i "$IFACE" reconfigure >/dev/null 2>&1 || true
  ip link set "$IFACE" down || true
  sleep 1
  ip link set "$IFACE" up || true
  dhcpcd -n "$IFACE" >/dev/null 2>&1 || true
fi
