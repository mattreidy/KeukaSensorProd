#!/bin/bash
# Universal USB WiFi Adapter (wlan1) Reliability Fix Script
# Works with any USB WiFi adapter regardless of vendor/driver

LOG_FILE="/var/log/usb_wifi_fix.log"

log_message() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

log_message "Starting universal USB WiFi adapter fix for wlan1..."

# 1. Disable USB autosuspend for all USB WiFi devices
log_message "Disabling USB autosuspend for all USB WiFi devices"
for device_path in /sys/class/net/wlan*/device; do
    if [ -L "$device_path" ]; then
        # Follow the symlink to get the actual USB device path
        usb_device=$(readlink -f "$device_path")
        if [[ "$usb_device" =~ /sys/devices/.*/usb[0-9]+/[0-9-.]+ ]]; then
            control_file="$usb_device/power/control"
            if [ -f "$control_file" ]; then
                echo "on" > "$control_file" 2>/dev/null
                interface=$(basename "$(dirname "$device_path")")
                log_message "Disabled autosuspend for $interface at $usb_device"
            fi
        fi
    fi
done

# 2. Reload USB WiFi interface (generic approach)
if ip link show wlan1 >/dev/null 2>&1; then
    log_message "wlan1 exists, performing soft reset"
    
    # Get the USB device path for wlan1
    WLAN1_USB_PATH=$(readlink -f /sys/class/net/wlan1/device 2>/dev/null)
    
    if [ -n "$WLAN1_USB_PATH" ] && [[ "$WLAN1_USB_PATH" =~ /sys/devices/.*/usb[0-9]+/[0-9-.]+ ]]; then
        USB_DEVICE_ID=$(basename "$WLAN1_USB_PATH")
        
        # Find the driver being used
        DRIVER_NAME=""
        for driver_link in "$WLAN1_USB_PATH"/driver; do
            if [ -L "$driver_link" ]; then
                DRIVER_NAME=$(basename "$(readlink "$driver_link")")
                break
            fi
        done
        
        if [ -n "$DRIVER_NAME" ]; then
            log_message "Found driver: $DRIVER_NAME for device: $USB_DEVICE_ID"
            
            # Unbind and rebind the device
            echo "$USB_DEVICE_ID" > "/sys/bus/usb/drivers/$DRIVER_NAME/unbind" 2>/dev/null
            sleep 3
            echo "$USB_DEVICE_ID" > "/sys/bus/usb/drivers/$DRIVER_NAME/bind" 2>/dev/null
            log_message "Performed unbind/bind cycle for $USB_DEVICE_ID"
        fi
    fi
else
    log_message "wlan1 not found, attempting to trigger USB re-enumeration"
    
    # Try to find and reset any USB WiFi devices
    for usb_dev in /sys/bus/usb/devices/*/; do
        if [ -f "$usb_dev/idVendor" ] && [ -f "$usb_dev/idProduct" ]; then
            # Check if this USB device has a wireless interface
            for net_dev in "$usb_dev"/net/*; do
                if [ -d "$net_dev" ] && [[ "$(basename "$net_dev")" =~ ^wlan[0-9]+$ ]]; then
                    device_name=$(basename "$usb_dev")
                    # Find driver
                    if [ -L "$usb_dev/driver" ]; then
                        driver_name=$(basename "$(readlink "$usb_dev/driver")")
                        log_message "Resetting USB WiFi device: $device_name (driver: $driver_name)"
                        echo "$device_name" > "/sys/bus/usb/drivers/$driver_name/unbind" 2>/dev/null
                        sleep 3
                        echo "$device_name" > "/sys/bus/usb/drivers/$driver_name/bind" 2>/dev/null
                    fi
                fi
            done
        fi
    done
fi

# 3. Wait for wlan1 interface to be available
log_message "Waiting for wlan1 interface..."
for i in {1..30}; do
    if ip link show wlan1 >/dev/null 2>&1; then
        log_message "wlan1 interface found after $i seconds"
        break
    fi
    sleep 1
done

# 4. Configure and start the interface
if ip link show wlan1 >/dev/null 2>&1; then
    log_message "Configuring wlan1 interface"
    
    # Ensure interface is up
    ip link set wlan1 up
    
    # Kill any existing wpa_supplicant processes for wlan1
    pkill -f "wpa_supplicant.*wlan1" 2>/dev/null
    
    # Wait a moment for cleanup
    sleep 2
    
    # Restart dhcpcd to handle the interface
    log_message "Restarting dhcpcd service"
    systemctl restart dhcpcd.service
    
    # Wait for dhcpcd to start managing wlan1
    sleep 5
    
else
    log_message "ERROR: wlan1 interface not found after 30 seconds"
    exit 1
fi

# 5. Verify connection
log_message "Verifying WiFi connection..."
for i in {1..60}; do
    # Check if we have an IP address and are connected
    if ip addr show wlan1 | grep -q "inet.*192.168" && iwconfig wlan1 2>/dev/null | grep -q "Access Point.*[0-9A-Fa-f]"; then
        SSID=$(iwconfig wlan1 2>/dev/null | grep ESSID | cut -d'"' -f2)
        IP=$(ip addr show wlan1 | grep "inet " | awk '{print $2}' | cut -d'/' -f1)
        log_message "SUCCESS: Connected to WiFi network: $SSID with IP: $IP"
        exit 0
    fi
    sleep 1
done

log_message "WARNING: WiFi connection not fully established within 60 seconds"
# Still exit 0 as the interface may come up later
exit 0