#!/usr/bin/env bash
set -euo pipefail

# Detect default route interface and gateway
IFACE="$(ip route | awk '/default/ {print $5; exit}')"
GATEWAY="$(ip route | awk '/default/ {print $3; exit}')"

# Fall back to wlan0 if detection fails
IFACE="${IFACE:-wlan0}"

# Ensure Wi-Fi not blocked
rfkill unblock wifi || true

# If we can’t ping the gateway via the Wi-Fi iface, bounce it
if [[ -n "${GATEWAY:-}" ]]; then
  if ! ping -I "$IFACE" -c 2 -W 2 "$GATEWAY" >/dev/null 2>&1; then
    logger -t wifi-watchdog "Gateway unreachable via $IFACE — restarting Wi-Fi"
    wpa_cli -i "$IFACE" reconfigure >/dev/null 2>&1 || true
    ip link set "$IFACE" down || true
    sleep 1
    ip link set "$IFACE" up || true
    dhcpcd -n "$IFACE" >/dev/null 2>&1 || true
  fi
fi
