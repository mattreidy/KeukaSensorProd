#!/bin/bash
# Installation script for Keuka Sensor Push Service

set -e

echo "Installing Keuka Sensor Push Service..."

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (use sudo)"
    exit 1
fi

# Create directories
echo "Creating directories..."
mkdir -p /opt/keuka
chown pi:pi /opt/keuka

# Install Python dependencies
echo "Installing Python dependencies..."
pip3 install requests pytz

# Copy files
echo "Copying service files..."
cp local_storage.py /opt/keuka/
cp sensor_push_service.py /opt/keuka/
cp sensor_config.json /opt/keuka/

# Make scripts executable
chmod +x /opt/keuka/sensor_push_service.py
chmod +x /opt/keuka/local_storage.py

# Set ownership
chown pi:pi /opt/keuka/*

# Install systemd files
echo "Installing systemd service..."
cp keuka-sensor-push.service /etc/systemd/system/
cp keuka-sensor-push.timer /etc/systemd/system/

# Reload systemd and enable timer
echo "Enabling systemd timer..."
systemctl daemon-reload
systemctl enable keuka-sensor-push.timer
systemctl start keuka-sensor-push.timer

# Show status
echo "Installation complete!"
echo ""
echo "Service status:"
systemctl status keuka-sensor-push.timer --no-pager -l

echo ""
echo "To check logs:"
echo "  sudo journalctl -u keuka-sensor-push.service -f"
echo ""
echo "To manually run the service:"
echo "  sudo systemctl start keuka-sensor-push.service"
echo ""
echo "To check service status:"
echo "  /opt/keuka/sensor_push_service.py --status"
echo ""

echo "Next steps:"
echo "1. Edit /opt/keuka/sensor_config.json to set correct sensor_name and coordinates"
echo "2. Integrate actual sensor reading code in sensor_push_service.py"
echo "3. Test the service: sudo systemctl start keuka-sensor-push.service"