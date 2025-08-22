#!/bin/bash
# WiFi Connection Monitor - runs periodically to check wlan1 connectivity
# If connection is lost, runs the USB WiFi fix script

LOG_FILE="/var/log/wifi_monitor.log"

log_message() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> "$LOG_FILE"
}

# Check if wlan1 exists and has an IP address
if ip link show wlan1 >/dev/null 2>&1; then
    # Check if wlan1 has an IP address and can reach the gateway
    if ip addr show wlan1 | grep -q "inet.*192.168" && iwconfig wlan1 2>/dev/null | grep -q "Access Point.*[0-9A-Fa-f]"; then
        # Try to ping the gateway to verify connectivity
        GATEWAY=$(ip route show dev wlan1 | grep default | awk '{print $3}' | head -1)
        if [ -n "$GATEWAY" ] && ping -c 1 -W 2 "$GATEWAY" >/dev/null 2>&1; then
            # Connection is good
            exit 0
        else
            log_message "Gateway ping failed, triggering WiFi fix"
        fi
    else
        log_message "wlan1 not properly connected, triggering WiFi fix"
    fi
else
    log_message "wlan1 interface not found, triggering WiFi fix"
fi

# Run the fix script
log_message "Running USB WiFi fix script"
/home/pi/KeukaSensorProd/scripts/usb_wifi_fix.sh