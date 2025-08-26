# Keuka Sensor Implementation Guide
## Migration from HTTP Server to Push-Based Data Collection

### Overview
This guide provides step-by-step instructions for migrating the KeukaSensorProd Raspberry Pi sensors from the current HTTP server model to a push-based data collection system that uploads to keuka.org every 5 minutes.

---

## What We're Changing

### **Current Architecture (OLD)**
- Raspberry Pi runs Flask HTTP server on ports 5001-5005
- Server polls sensors via HTTP GET requests every 15 seconds
- Requires port forwarding configuration at each location
- Data lost during network outages

### **New Architecture (PUSH-BASED)**
- Raspberry Pi collects sensor data locally every 5 minutes
- Data stored in local SQLite database for network resilience
- Automatic upload to keuka.org/api/sensors/data
- No port forwarding required
- Network outage recovery with buffered data upload

---

## Files to Add to KeukaSensorProd Repository

### 1. **local_storage.py** - Local Data Storage System
```python
# SQLite-based storage for sensor readings with network outage resilience
# Provides methods to store readings locally and track upload status
# Handles automatic cleanup of old data to prevent disk space issues
```

### 2. **sensor_push_service.py** - Main Push Service
```python
# Main service that:
# - Collects sensor readings from existing hardware interfaces
# - Stores readings in local SQLite database
# - Uploads pending readings to keuka.org server
# - Handles network failures gracefully with retry logic
```

### 3. **sensor_config.json** - Configuration File
```json
# Configuration for each sensor including:
# - Sensor name (keukasensor1, keukasensor2, etc.)
# - Server URL for uploads
# - GPS coordinates and elevation
# - Upload timeout and batch size settings
```

### 4. **keuka-sensor-push.service** - Systemd Service Definition
```ini
# Systemd service file for running the push service
# Handles proper user permissions and working directory
# Provides logging and error handling
```

### 5. **keuka-sensor-push.timer** - Systemd Timer (5-minute interval)
```ini
# Systemd timer that triggers the service every 5 minutes
# Includes boot delay and timing accuracy settings
# Ensures persistent operation across reboots
```

### 6. **install.sh** - Installation Script
```bash
# Automated installation script that:
# - Creates required directories (/opt/keuka/)
# - Installs Python dependencies (requests, pytz)
# - Sets up systemd service and timer
# - Configures proper file permissions
```

---

## Integration with Existing KeukaSensorProd Code

### **Step 1: Locate Current Sensor Reading Code**

In your existing KeukaSensorProd repository, find these files:
- `keuka/sensors.py` - Current sensor interface code
- `keuka/routes_root.py` or similar - Current HTTP endpoint handlers
- Any hardware interface modules for:
  - DS18B20 temperature sensor
  - JSN-SR04T ultrasonic distance sensor
  - GY-NEO6MV2 GPS module

### **Step 2: Extract Sensor Reading Functions**

From `sensors.py` or similar files, identify functions that:
- Read temperature from DS18B20 sensor
- Read water level from ultrasonic sensor  
- Get GPS coordinates (if available)
- Return sensor data in a structured format

Example current code pattern:
```python
def get_current_readings():
    return {
        "temperature_f": read_temperature_sensor(),
        "distance_inches": read_distance_sensor(),
        "latitude": get_gps_latitude(),
        "longitude": get_gps_longitude()
    }
```

### **Step 3: Modify sensor_push_service.py**

In the `collect_sensor_data()` method, replace the placeholder code with your actual sensor readings:

**REPLACE THIS PLACEHOLDER:**
```python
def collect_sensor_data(self):
    # TODO: Replace with actual sensor reading code
    sensor_data = {
        "waterTempF": None,      # Will be filled by actual sensor reading
        "waterLevelInches": None, # Will be filled by actual sensor reading
        "turbidityNTU": None,    # Placeholder for future turbidity sensor
        "latitude": self.config.get('fixed_latitude', 42.606),
        "longitude": self.config.get('fixed_longitude', -77.091),
        "elevationFeet": self.config.get('fixed_elevation', 710)
    }
```

**WITH YOUR ACTUAL SENSOR CODE:**
```python
def collect_sensor_data(self):
    try:
        # Import your existing sensor modules
        from sensors import SensorManager  # or whatever your module is called
        
        # Create sensor manager (adapt to your existing code structure)
        sensor_mgr = SensorManager()  # or however you initialize sensors
        
        # Get readings using your existing functions
        current_readings = sensor_mgr.get_current_readings()  # your method name
        
        # Map your existing data structure to the new format
        sensor_data = {
            "waterTempF": current_readings.get("temperature_f"),
            "waterLevelInches": current_readings.get("distance_inches"), 
            "turbidityNTU": None,  # Add when turbidity sensor is installed
            "latitude": current_readings.get("latitude", self.config.get('fixed_latitude', 42.606)),
            "longitude": current_readings.get("longitude", self.config.get('fixed_longitude', -77.091)),
            "elevationFeet": current_readings.get("elevation_feet", self.config.get('fixed_elevation', 710))
        }
        
        logging.info(f"Collected sensor data: {sensor_data}")
        return sensor_data
        
    except Exception as e:
        logging.error(f"Failed to collect sensor data: {e}")
        return None
```

---

## Installation and Deployment

### **Phase 1: Backup Current System**
```bash
# SSH to sensor
ssh pi@keukasensor1

# Backup current code
cp -r /path/to/current/keuka /home/pi/keuka-backup-$(date +%Y%m%d)

# Stop current HTTP server if running
sudo systemctl stop keuka-sensor  # or whatever your service is named
```

### **Phase 2: Deploy New Files**
```bash
# Copy new files to sensor
scp -r sensor-files/* pi@keukasensor1:/home/pi/keuka-push/

# SSH to sensor and install
ssh pi@keukasensor1
cd /home/pi/keuka-push
sudo ./install.sh
```

### **Phase 3: Configure Sensor**
```bash
# Edit configuration file
sudo nano /opt/keuka/sensor_config.json

# Update these fields for each sensor:
{
  "sensor_name": "keukasensor1",  # Change for each sensor
  "fixed_latitude": 42.606,      # Update with actual GPS coordinates
  "fixed_longitude": -77.091,    # Update with actual GPS coordinates  
  "fixed_elevation": 710         # Update with actual elevation
}
```

### **Phase 4: Integration Testing**
```bash
# Test manual collection (without uploading)
/opt/keuka/sensor_push_service.py --status

# Test full cycle (collect + upload)
sudo systemctl start keuka-sensor-push.service

# Check logs
sudo journalctl -u keuka-sensor-push.service -f

# Verify data appears on keuka.org/#sensors
```

### **Phase 5: Remove Old System**
```bash
# Only after confirming new system works!

# Disable old HTTP server
sudo systemctl disable old-keuka-service  # whatever your old service was
sudo systemctl stop old-keuka-service

# Remove old port forwarding from router (external step)
# No more need for ports 5001-5005 to be forwarded
```

---

## Data Format and Server Integration

### **Upload Format**
The new system uploads data in this JSON format to `https://keuka.org/api/sensors/data`:

```json
{
  "sensorName": "keukasensor1",
  "timestampNY": "2025-08-26T10:30:00-04:00",
  "data": {
    "waterTempF": 72.5,
    "waterLevelInches": 24.2,
    "turbidityNTU": null,
    "latitude": 42.606,
    "longitude": -77.091,
    "elevationFeet": 710
  },
  "metadata": {
    "fqdn": "keukasensor1.duckdns.org",
    "localId": 12345
  }
}
```

### **Server Response**
Successful uploads return:
```json
{
  "success": true,
  "timestamp": "2025-08-26T14:30:00.000Z"
}
```

---

## Troubleshooting Guide

### **Service Not Starting**
```bash
# Check service status
sudo systemctl status keuka-sensor-push.timer
sudo systemctl status keuka-sensor-push.service

# Check logs for errors
sudo journalctl -u keuka-sensor-push.service --since "1 hour ago"

# Verify files exist
ls -la /opt/keuka/
```

### **Sensor Reading Failures**
```bash
# Test sensor integration manually
cd /opt/keuka
python3 -c "
from sensor_push_service import SensorPushService
svc = SensorPushService()
data = svc.collect_sensor_data()
print(data)
"
```

### **Network Upload Issues**
```bash
# Check storage statistics  
/opt/keuka/sensor_push_service.py --status

# Test network connectivity
curl -I https://keuka.org/api/sensors/data

# Check local database
sqlite3 /opt/keuka/sensor_data.db "SELECT COUNT(*) FROM sensor_readings WHERE uploaded = 0;"
```

### **Storage Issues**
```bash
# Check disk space
df -h /opt/keuka/

# Manual cleanup if needed
cd /opt/keuka
python3 -c "
from local_storage import LocalSensorStorage
storage = LocalSensorStorage()
deleted = storage.cleanup_old(days=1)  # Keep only 1 day
storage.vacuum_db()
print(f'Deleted {deleted} old records')
"
```

---

## Future Turbidity Sensor Integration

When the turbidity sensor is added:

### **Hardware Integration**
1. Connect turbidity sensor to appropriate GPIO pins
2. Install sensor-specific libraries
3. Create reading function similar to existing sensors

### **Code Integration**
Update the `collect_sensor_data()` method:
```python
# Add turbidity reading
turbidity_reading = read_turbidity_sensor()  # your new function
sensor_data["turbidityNTU"] = turbidity_reading
```

### **Server-Side**
The server already supports turbidity data - no changes needed on keuka.org.

---

## Support and Monitoring

### **Log Monitoring**
```bash
# Real-time logs
sudo journalctl -u keuka-sensor-push.service -f

# Recent errors only
sudo journalctl -u keuka-sensor-push.service --since "1 hour ago" --grep "ERROR"
```

### **Status Checks**
```bash
# Service status
sudo systemctl is-active keuka-sensor-push.timer

# Storage statistics
/opt/keuka/sensor_push_service.py --status

# Database info
sqlite3 /opt/keuka/sensor_data.db "SELECT 
  COUNT(*) as total,
  SUM(uploaded = 0) as pending,
  SUM(uploaded = 1) as uploaded
FROM sensor_readings;"
```

### **Performance Monitoring**
- **Upload success rate**: Should be >95% under normal conditions
- **Storage growth**: Should stabilize after 7-day cleanup cycle
- **Network usage**: ~1KB per reading, ~288KB per day per sensor
- **CPU usage**: Minimal impact, <1% during collection

This new system provides better reliability, eliminates network configuration complexity, and prepares the infrastructure for future sensor additions while maintaining all existing functionality.