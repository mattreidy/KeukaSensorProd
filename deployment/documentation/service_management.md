# Keuka Sensor Service Management Guide

## Service Overview

The Keuka Sensor system runs as multiple systemd services:

- **keuka-sensor.service**: Main Flask application
- **duckdns-update.service**: DuckDNS IP update (oneshot)
- **duckdns-update.timer**: Periodic DuckDNS updates (5 minutes)
- **log-cleanup.service**: Log cleanup (oneshot)
- **log-cleanup.timer**: Daily log cleanup

## Service Operations

### Basic Commands

```bash
# Check all service status
sudo systemctl status keuka-sensor.service
sudo systemctl status duckdns-update.timer
sudo systemctl status log-cleanup.timer

# Start/Stop/Restart services
sudo systemctl start keuka-sensor.service
sudo systemctl stop keuka-sensor.service
sudo systemctl restart keuka-sensor.service

# Enable/Disable services
sudo systemctl enable keuka-sensor.service
sudo systemctl disable keuka-sensor.service

# View logs
sudo journalctl -u keuka-sensor.service -f
sudo journalctl -u duckdns-update.service -f
```

### Configuration Updates

```bash
# After changing environment files
sudo systemctl daemon-reload
sudo systemctl restart keuka-sensor.service

# After updating service files
sudo ./deployment/scripts/install_services.sh --force
sudo systemctl daemon-reload
sudo systemctl restart keuka-sensor.service
```

## Environment Configuration

### Service Environment (/etc/default/keuka-sensor)

Contains service-specific variables:
- Network interface settings
- Camera configuration
- Authentication credentials
- Performance tuning

### Global Environment (/etc/keuka.env)

Contains system-wide variables:
- Application paths
- DuckDNS configuration
- Logging settings
- Backup retention

### Configuration Templates

Templates are available in `deployment/environment/`:
- `keuka-sensor.env.template`
- `keuka.env.template`

## Log Management

### Log Locations

```bash
# Application logs
/home/pi/KeukaSensorProd/data/logs/

# System logs
sudo journalctl -u keuka-sensor.service
sudo journalctl -u duckdns-update.service
```

### Log Cleanup

Automatic cleanup runs daily via `log-cleanup.timer`:
- Removes old temporary files
- Truncates large log files
- Cleans backup directories
- Manages journal retention

Manual cleanup:
```bash
sudo systemctl start log-cleanup.service
```

## Update Procedures

### Code Updates

```bash
# Using the update API (preferred)
curl -X POST http://localhost:5000/api/update/start

# Manual update
cd /home/pi/KeukaSensorProd
git pull
sudo systemctl restart keuka-sensor.service
```

### Service Updates

```bash
# Pull latest service files
cd /home/pi/KeukaSensorProd
git pull

# Reinstall services
sudo ./deployment/scripts/install_services.sh --force

# Reload and restart
sudo systemctl daemon-reload
sudo systemctl restart keuka-sensor.service
```

## Performance Monitoring

### Resource Usage

```bash
# Check service resource usage
sudo systemctl status keuka-sensor.service
ps aux | grep gunicorn

# Monitor system resources
htop
df -h
free -h
```

### Performance Tuning

Edit `/etc/default/keuka-sensor`:
```bash
# Adjust worker/thread counts
KS_GUNICORN_WORKERS=1
KS_GUNICORN_THREADS=8

# Adjust timeouts
KS_GUNICORN_TIMEOUT=120

# Camera performance
CAM_FRAME_W=640
CAM_FRAME_H=480
CAM_JPEG_QUALITY=80
```

## Troubleshooting

### Service Won't Start

```bash
# Check service status
sudo systemctl status keuka-sensor.service

# Check logs
sudo journalctl -u keuka-sensor.service -n 50

# Check environment
sudo systemctl show keuka-sensor.service --property=Environment

# Test manually
cd /home/pi/KeukaSensorProd/keuka
source ../venv/bin/activate
python run.py
```

### High Resource Usage

```bash
# Check resource usage
sudo systemctl status keuka-sensor.service
top -p $(pgrep -f gunicorn)

# Reduce camera quality
# Edit /etc/default/keuka-sensor
CAM_FRAME_W=320
CAM_FRAME_H=240
CAM_JPEG_QUALITY=60

sudo systemctl restart keuka-sensor.service
```

### Network Issues

```bash
# Check network configuration
ip addr show
ip route show

# Test DuckDNS manually
sudo -u pi /home/pi/KeukaSensorProd/deployment/scripts/duckdns_update.sh

# Check Wi-Fi configuration
iwconfig
wpa_cli status
```

## Backup and Recovery

### Configuration Backup

```bash
# Backup configuration
sudo cp -r /etc/default/keuka-sensor /home/pi/KeukaSensorProd/data/backups/
sudo cp /etc/keuka.env /home/pi/KeukaSensorProd/data/backups/

# Restore configuration
sudo cp /home/pi/KeukaSensorProd/data/backups/keuka-sensor /etc/default/
sudo cp /home/pi/KeukaSensorProd/data/backups/keuka.env /etc/
sudo systemctl restart keuka-sensor.service
```

### Service Recovery

```bash
# Reinstall all services
sudo ./deployment/scripts/install_services.sh --force

# Reset failed services
sudo systemctl reset-failed

# Restart all services
sudo systemctl restart keuka-sensor.service
sudo systemctl restart duckdns-update.timer
sudo systemctl restart log-cleanup.timer
```