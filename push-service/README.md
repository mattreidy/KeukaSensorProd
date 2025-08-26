# Keuka Sensor Push Service

This directory contains the push-based sensor service that replaces the old pull-based HTTP server architecture.

## Architecture Overview

The new system works as follows:
1. **Local Collection**: Sensors collect data every 5 minutes via systemd timer
2. **Local Storage**: Data is stored in local SQLite database for network outage resilience
3. **Push Upload**: Pending readings are uploaded to keuka.org/api/sensors/data
4. **Database Storage**: Server stores data in MongoDB with NY timezone handling

## Files Included

### Core Python Files
- `local_storage.py` - SQLite storage system for sensor readings
- `sensor_push_service.py` - Main service that collects and uploads sensor data
- `sensor_config.json` - Configuration file for sensor settings

### Systemd Files
- `keuka-sensor-push.service` - Systemd service definition
- `keuka-sensor-push.timer` - Systemd timer (runs every 5 minutes)

### Installation
- `install.sh` - Automated installation script
- `README.md` - This documentation file

## Installation Steps

1. **Copy files to sensor**:
   ```bash
   scp -r sensor-files/* pi@keukasensor1:/home/pi/keuka-push/
   ```

2. **Run installation**:
   ```bash
   ssh pi@keukasensor1
   cd /home/pi/keuka-push
   sudo ./install.sh
   ```

3. **Configure sensor**:
   ```bash
   sudo nano /opt/keuka/sensor_config.json
   # Update sensor_name, coordinates, etc.
   ```

4. **Integrate with existing sensor code**:
   - Edit `/opt/keuka/sensor_push_service.py`
   - Replace placeholder sensor reading functions with actual hardware interfaces
   - Update the `collect_sensor_data()` method

## Configuration

### sensor_config.json
```json
{
  "server_url": "https://keuka.org/api/sensors/data",
  "sensor_name": "keukasensor1",
  "upload_timeout": 30,
  "max_upload_batch": 50,
  "fixed_latitude": 42.606,
  "fixed_longitude": -77.091,
  "fixed_elevation": 710
}
```

### Key Settings
- `sensor_name`: Must match the sensor identifier (keukasensor1, keukasensor2, etc.)
- `server_url`: Endpoint for uploading sensor data
- `upload_timeout`: HTTP timeout in seconds
- `max_upload_batch`: Maximum readings to upload per cycle
- `fixed_*`: GPS coordinates and elevation if GPS module not available

## Data Format

The service uploads data in this JSON format:
```json
{
  "sensorName": "keukasensor1",
  "timestampNY": "2025-01-15T10:30:00-05:00",
  "data": {
    "waterTempF": 72.5,
    "waterLevelInches": 24.2,
    "turbidityNTU": 15.3,
    "latitude": 42.606,
    "longitude": -77.091,
    "elevationFeet": 710
  },
  "metadata": {
    "fqdn": "keukasensor1.local",
    "localId": 12345
  }
}
```

## Management Commands

### Check Service Status
```bash
# Timer status
sudo systemctl status keuka-sensor-push.timer

# Service logs
sudo journalctl -u keuka-sensor-push.service -f

# Storage statistics
/opt/keuka/sensor_push_service.py --status
```

### Manual Operations
```bash
# Run service manually
sudo systemctl start keuka-sensor-push.service

# Stop timer
sudo systemctl stop keuka-sensor-push.timer

# Restart timer
sudo systemctl restart keuka-sensor-push.timer
```

## Database Schema

Local SQLite storage (`/opt/keuka/sensor_data.db`):
```sql
CREATE TABLE sensor_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ny TEXT NOT NULL,           -- NY timezone timestamp
    data TEXT NOT NULL,                   -- JSON sensor data
    uploaded INTEGER DEFAULT 0,          -- Upload status (0=pending, 1=uploaded)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Troubleshooting

### Service Not Running
```bash
# Check timer status
sudo systemctl status keuka-sensor-push.timer

# Check for errors
sudo journalctl -u keuka-sensor-push.service --since "1 hour ago"
```

### Network Issues
- Service will buffer readings locally during network outages
- Readings are uploaded in chronological order when connectivity returns
- Check `/opt/keuka/sensor_data.db` for pending readings

### Storage Issues
- Old uploaded readings are cleaned up automatically (7 days retention)
- Database is vacuumed after cleanup to reclaim space
- Monitor disk usage: `df -h /opt/keuka/`

### Integration with Existing Code
1. **Find current sensor reading functions** in existing KeukaSensorProd code
2. **Update `collect_sensor_data()`** in `sensor_push_service.py`
3. **Replace placeholders** with actual sensor readings:
   - Temperature: DS18B20 sensor
   - Water level: JSN-SR04T ultrasonic sensor
   - Turbidity: Future turbidity sensor
   - GPS: GY-NEO6MV2 module

## Migration from Pull-Based System

1. **Deploy push service** to all sensors
2. **Test data flow** to ensure readings arrive at server
3. **Remove old HTTP server** from sensor code
4. **Update firewall/router** - no more port forwarding needed
5. **Monitor for missing data** during transition

## Monitoring

The system provides several monitoring capabilities:

### Server-Side
- MongoDB queries for missing sensors
- API endpoints for sensor status
- Database indexes for efficient queries

### Sensor-Side  
- Local storage statistics
- Upload success/failure logging
- Systemd journal integration
- Status command for diagnostics

This new architecture eliminates network configuration complexity while providing better data reliability and storage efficiency.