# Keuka Sensor Installation Guide

## Overview

This guide covers the complete installation of the Keuka Sensor system on a Raspberry Pi, including hardware setup, software installation, and service configuration.

## Prerequisites

### Hardware Requirements
- Raspberry Pi 3B+ or newer
- MicroSD card (32GB or larger, Class 10)
- JSN-SR04T waterproof ultrasonic sensor
- DS18B20 temperature sensor
- USB Wi-Fi adapter with external antenna
- Camera module (CSI or USB)
- Appropriate wiring and resistors (see hardware guide)

### Software Requirements
- Raspberry Pi OS (Bullseye or newer)
- Python 3.9 or newer
- Git
- Systemd

## Installation Steps

### 1. System Preparation

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install required system packages
sudo apt install -y python3-pip python3-venv git curl wget

# Enable required interfaces
sudo raspi-config
# Enable: Camera, I2C, SPI, 1-Wire, Serial (disable console)
```

### 2. Repository Setup

```bash
# Clone repository
cd /home/pi
git clone https://github.com/mattreidy/KeukaSensorProd.git
cd KeukaSensorProd

# Create Python virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt
```

### 3. Hardware Configuration

```bash
# Enable 1-Wire for temperature sensor
echo 'dtoverlay=w1-gpio,gpiopin=6' | sudo tee -a /boot/config.txt

# Enable camera (if using CSI camera)
echo 'dtoverlay=ov5647' | sudo tee -a /boot/config.txt

# Reboot to apply hardware changes
sudo reboot
```

### 4. Service Installation

```bash
# Run the service installation script
cd /home/pi/KeukaSensorProd
sudo ./deployment/scripts/install_services.sh

# Set up environment configuration
sudo ./deployment/environment/setup_environment.sh --defaults
```

### 5. Configuration

```bash
# Edit service configuration
sudo nano /etc/default/keuka-sensor

# Edit global configuration
sudo nano /etc/keuka.env
```

### 6. Start Services

```bash
# Start the main application
sudo systemctl start keuka-sensor.service

# Check service status
sudo systemctl status keuka-sensor.service

# View logs
sudo journalctl -u keuka-sensor.service -f
```

## Post-Installation

### Verification

1. **Web Interface**: Visit `http://raspberry-pi-ip:5000`
2. **Health Dashboard**: Visit `http://raspberry-pi-ip:5000/health`
3. **Admin Interface**: Visit `http://raspberry-pi-ip:5000/admin` (admin/password)

### Network Configuration

1. Connect to the setup AP: `KeukaSensorSetup` (password: `keuka1234`)
2. Navigate to `http://192.168.50.1/admin/wifi`
3. Configure Wi-Fi connection to your network

## Troubleshooting

### Common Issues

**Service won't start:**
```bash
# Check service status
sudo systemctl status keuka-sensor.service

# Check logs for errors
sudo journalctl -u keuka-sensor.service -n 50
```

**Camera not working:**
```bash
# Check camera detection
vcgencmd get_camera

# Check camera permissions
ls -la /dev/video*
```

**Sensors not responding:**
```bash
# Check 1-Wire devices
ls /sys/bus/w1/devices/

# Check GPIO permissions
sudo usermod -a -G gpio pi
```

## Next Steps

- Set up monitoring and alerts
- Customize sensor thresholds
- Install in weatherproof enclosure

For detailed hardware setup, see the [Hardware Setup Guide](../../documentation/hardware_setup.md).
For operational information, see the [Operation Manual](../../documentation/operation_manual.md).