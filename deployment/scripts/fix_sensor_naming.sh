#!/bin/bash
# fix_sensor_naming.sh - Sync sensor naming across web interface and push service

echo "=== Keuka Sensor Naming Diagnostic ==="

# Check what the web interface is using
echo "1. Web interface sensor name:"
cd /home/pi/KeukaSensorProd/keuka
python3 -c "from core.config import SENSOR_NAME; print(f'  Web: {SENSOR_NAME}')" 2>/dev/null || echo "  Error reading web config"

# Check what the push service is using  
echo "2. Push service sensor name:"
cd /home/pi/KeukaSensorProd/push-service
python3 sensor_push_service.py --status 2>/dev/null | grep '"sensor_name"' | cut -d'"' -f4 | sed 's/^/  Push: /' || echo "  Error reading push service"

# Check if old device.conf exists
echo "3. Old config file status:"
if [ -f "/home/pi/KeukaSensorProd/configuration/services/device.conf" ]; then
    echo "  device.conf EXISTS: $(cat /home/pi/KeukaSensorProd/configuration/services/device.conf)"
    echo "  This file should be deleted - it's obsolete"
else
    echo "  device.conf DELETED: ✓ Good"
fi

# Check current push service version
echo "4. Push service code version:"
if grep -q "generate_hardware_sensor_id" /home/pi/KeukaSensorProd/push-service/sensor_push_service.py; then
    echo "  Push service UPDATED: ✓ Uses hardware naming"
else
    echo "  Push service OUTDATED: ✗ Still uses old naming"
    echo "  Run 'git pull' to update the code"
fi

echo "=== Fix Recommendations ==="
echo "If names don't match:"
echo "1. cd /home/pi/KeukaSensorProd && git pull"
echo "2. sudo systemctl restart keuka-sensor"
echo "3. sudo systemctl restart keuka-sensor-push.timer"
echo "4. Re-run this script to verify"