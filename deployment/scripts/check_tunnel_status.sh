#!/bin/bash
# check_tunnel_status.sh - Diagnose tunnel connection issues

echo "=== Keuka Tunnel Connection Diagnostic ==="

echo "1. Tunnel service status:"
systemctl is-active keuka-tunnel 2>/dev/null || echo "  keuka-tunnel service not running"
systemctl is-enabled keuka-tunnel 2>/dev/null || echo "  keuka-tunnel service not enabled"

echo "2. Tunnel service logs (last 20 lines):"
journalctl -u keuka-tunnel -n 20 --no-pager 2>/dev/null || echo "  No tunnel service logs found"

echo "3. Main service status:"
systemctl is-active keuka-sensor 2>/dev/null || echo "  keuka-sensor service not running"

echo "4. Network connectivity:"
ping -c 2 keuka.org >/dev/null 2>&1 && echo "  ✓ Can reach keuka.org" || echo "  ✗ Cannot reach keuka.org"

echo "5. Checking tunnel client in main service logs:"
journalctl -u keuka-sensor -n 50 --no-pager | grep -i tunnel || echo "  No tunnel messages in main service logs"

echo "6. Current sensor name being used:"
cd /home/pi/KeukaSensorProd/keuka
python3 -c "from core.config import SENSOR_NAME; print(f'  SENSOR_NAME: {SENSOR_NAME}')" 2>/dev/null || echo "  Error reading sensor name"

echo "7. Tunnel process check:"
ps aux | grep -i tunnel | grep -v grep || echo "  No tunnel processes found"

echo "=== Recommendations ==="
echo "If tunnel is not running:"
echo "1. sudo systemctl enable keuka-tunnel"
echo "2. sudo systemctl start keuka-tunnel"
echo "3. sudo systemctl status keuka-tunnel"
echo ""
echo "If tunnel is failing to connect:"
echo "1. Check network connectivity"
echo "2. Verify sensor name matches on keuka.org"
echo "3. Check tunnel logs for error details"