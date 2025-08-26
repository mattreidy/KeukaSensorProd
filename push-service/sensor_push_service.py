#!/usr/bin/env python3
"""
Sensor push service that collects data locally and uploads to keuka.org server
Handles network outages gracefully with local storage buffering
"""

import requests
import json
import time
import logging
import socket
import sys
import os
from datetime import datetime
import pytz

# Add the current directory to Python path for importing
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Add the parent directory to import keuka sensors
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from local_storage import LocalSensorStorage

# Import coordinate normalization utility
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'keuka'))
from keuka.utils.coordinate_parser import normalize_gps_coordinates, is_valid_coordinate_pair

class SensorPushService:
    def __init__(self, config_file="/opt/keuka/sensor_config.json"):
        self.storage = LocalSensorStorage()
        self.config = self.load_config(config_file)
        self.server_url = self.config.get('server_url', 'https://keuka.org/api/sensors/data')
        # Always detect sensor name dynamically, ignore any hardcoded value in config
        self.sensor_name = self.detect_sensor_name()
        self.timeout = self.config.get('upload_timeout', 30)
        
        logging.info(f"Initialized sensor push service for {self.sensor_name}")
        logging.info(f"Server URL: {self.server_url}")
    
    def load_config(self, config_file):
        """Load configuration from JSON file with fallback defaults"""
        default_config = {
            'server_url': 'https://keuka.org/api/sensors/data',
            'sensor_name': None,
            'upload_timeout': 30,
            'max_upload_batch': 50
        }
        
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r') as f:
                    config = json.load(f)
                logging.info(f"Loaded configuration from {config_file}")
                return {**default_config, **config}
            except Exception as e:
                logging.error(f"Error loading config file {config_file}: {e}")
        else:
            logging.info(f"Config file {config_file} not found, using defaults")
        
        return default_config
    
    def detect_sensor_name(self):
        """Determine sensor name from device config or fallbacks"""
        # First try to get from device configuration
        device_conf_path = "/home/pi/KeukaSensorProd/configuration/services/device.conf"
        try:
            if os.path.exists(device_conf_path):
                with open(device_conf_path, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith('device_name='):
                            device_name = line.split('=', 1)[1].strip()
                            if device_name:
                                logging.info(f"Using device name from device config: {device_name}")
                                return device_name
        except Exception as e:
            logging.warning(f"Failed to read device config: {e}")
        
        # Fallback to sensor config (for backward compatibility)
        device_name = self.config.get('device_name')
        if device_name:
            logging.info(f"Using device name from sensor config: {device_name}")
            return device_name
        
        # Fallback to hostname
        hostname = socket.gethostname().lower()
        if 'sensor' in hostname or 'keuka' in hostname:
            logging.info(f"Using sensor name from hostname: {hostname}")
            return hostname
        
        # Final fallback: use default
        logging.warning("Using fallback sensor name")
        return "sensor1"

    def get_public_ip(self):
        """Get the public IP address of this device"""
        try:
            # Try multiple services for reliability
            services = [
                'https://api.ipify.org?format=text',
                'https://checkip.amazonaws.com',
                'https://icanhazip.com'
            ]
            
            for service in services:
                try:
                    response = requests.get(service, timeout=10)
                    if response.status_code == 200:
                        ip = response.text.strip()
                        # Basic IP validation
                        if ip and len(ip.split('.')) == 4:
                            logging.debug(f"Retrieved public IP: {ip}")
                            return ip
                except Exception as e:
                    logging.debug(f"Failed to get IP from {service}: {e}")
                    continue
            
            logging.warning("Failed to retrieve public IP from all services")
            return None
        except Exception as e:
            logging.error(f"Error getting public IP: {e}")
            return None
    
    def collect_sensor_data(self):
        """
        Collect current sensor readings from hardware
        Returns dict with sensor data or None on error
        """
        try:
            # Import sensor modules from the existing keuka codebase
            from keuka.sensors import read_temp_fahrenheit, median_distance_inches
            from keuka.hardware.gps import read_gps_lat_lon_elev
            
            ny_tz = pytz.timezone('America/New_York')
            current_time = datetime.now(ny_tz)
            
            # Get public IP address
            public_ip = self.get_public_ip()
            
            # Initialize sensor data structure with fallback coordinates
            sensor_data = {
                "waterTempF": None,
                "waterLevelInches": None,
                "turbidityNTU": None,  # Placeholder for future turbidity sensor
                "latitude": self.config.get('fallback_latitude', 42.606),
                "longitude": self.config.get('fallback_longitude', -77.091),
                "elevationFeet": self.config.get('fallback_elevation', 710),
                "publicIP": public_ip
            }
            
            # Read temperature sensor (DS18B20)
            try:
                temperature_f = read_temp_fahrenheit()
                if temperature_f == temperature_f:  # Check for NaN
                    sensor_data["waterTempF"] = round(temperature_f, 1)
                    logging.debug(f"Temperature reading: {temperature_f}°F")
                else:
                    logging.warning("Temperature sensor returned NaN")
            except Exception as e:
                logging.error(f"Error reading temperature sensor: {e}")
            
            # Read ultrasonic distance sensor (JSN-SR04T)
            try:
                distance_inches = median_distance_inches(samples=11)  # Use median for stability
                if distance_inches == distance_inches:  # Check for NaN
                    sensor_data["waterLevelInches"] = round(distance_inches, 1)
                    logging.debug(f"Distance reading: {distance_inches} inches")
                else:
                    logging.warning("Ultrasonic sensor returned NaN")
            except Exception as e:
                logging.error(f"Error reading ultrasonic sensor: {e}")
            
            # Read GPS coordinates if available (NEO-6M)
            try:
                gps_lat, gps_lon, gps_alt = read_gps_lat_lon_elev(duration_s=2.0)
                
                # Normalize GPS coordinates to handle various formats
                norm_lat, norm_lon = normalize_gps_coordinates(gps_lat, gps_lon)
                
                if is_valid_coordinate_pair(norm_lat, norm_lon):
                    sensor_data["latitude"] = round(norm_lat, 6)
                    sensor_data["longitude"] = round(norm_lon, 6)
                    if gps_alt == gps_alt:  # Check altitude for NaN
                        # Convert meters to feet
                        sensor_data["elevationFeet"] = round(gps_alt * 3.28084, 1)
                    logging.info(f"Using live GPS reading: {norm_lat}, {norm_lon}, {gps_alt}m")
                else:
                    logging.info("GPS reading not available, using fallback coordinates from config")
            except Exception as e:
                logging.debug(f"GPS reading failed, using fallback coordinates: {e}")
            
            logging.info(f"Collected sensor data: {sensor_data}")
            return sensor_data
            
        except ImportError as e:
            logging.error(f"Failed to import sensor modules: {e}")
            return None
        except Exception as e:
            logging.error(f"Failed to collect sensor data: {e}")
            return None
    
    def store_reading_locally(self):
        """
        Collect sensor data and store it locally
        Returns the local storage ID or None on failure
        """
        sensor_data = self.collect_sensor_data()
        if sensor_data is None:
            return None
        
        try:
            reading_id = self.storage.store_reading(sensor_data)
            logging.info(f"Stored reading locally with ID {reading_id}")
            return reading_id
        except Exception as e:
            logging.error(f"Failed to store reading locally: {e}")
            return None
    
    def upload_pending_readings(self):
        """
        Upload all pending readings to the server
        Returns tuple (success_count, error_count)
        """
        max_batch = self.config.get('max_upload_batch', 50)
        unuploaded = self.storage.get_unuploaded(limit=max_batch)
        
        if not unuploaded:
            logging.debug("No pending readings to upload")
            return 0, 0
        
        success_count = 0
        error_count = 0
        
        logging.info(f"Attempting to upload {len(unuploaded)} pending readings")
        
        for reading_id, timestamp_ny, data_json in unuploaded:
            try:
                data = json.loads(data_json)
                
                payload = {
                    "sensorName": self.sensor_name,
                    "timestampNY": timestamp_ny,
                    "data": data,
                    "metadata": {
                        "deviceName": self.sensor_name,
                        "localId": reading_id,
                        "publicIP": data.get("publicIP")
                    }
                }
                
                response = requests.post(
                    self.server_url,
                    json=payload,
                    timeout=self.timeout,
                    headers={"Content-Type": "application/json"}
                )
                
                if response.status_code == 200:
                    self.storage.mark_uploaded(reading_id)
                    success_count += 1
                    logging.debug(f"Uploaded reading {reading_id} successfully")
                else:
                    error_count += 1
                    logging.error(f"Failed to upload reading {reading_id}: HTTP {response.status_code} - {response.text}")
                    # Stop on first failure to maintain chronological order
                    break
                    
            except requests.RequestException as e:
                error_count += 1
                logging.error(f"Network error uploading reading {reading_id}: {e}")
                # Stop on network errors to avoid wasting time/battery
                break
            except Exception as e:
                error_count += 1
                logging.error(f"Unexpected error uploading reading {reading_id}: {e}")
                # Stop on unexpected errors
                break
        
        if success_count > 0:
            logging.info(f"Successfully uploaded {success_count} readings")
        if error_count > 0:
            logging.warning(f"Failed to upload {error_count} readings")
        
        return success_count, error_count
    
    def cleanup_old_data(self):
        """Clean up old uploaded data to save disk space"""
        try:
            deleted_count = self.storage.cleanup_old(days=7)  # Keep 7 days of uploaded data
            if deleted_count > 0:
                self.storage.vacuum_db()  # Reclaim disk space
        except Exception as e:
            logging.error(f"Error during cleanup: {e}")
    
    def get_status(self):
        """Get current service status"""
        stats = self.storage.get_stats()
        return {
            'sensor_name': self.sensor_name,
            'server_url': self.server_url,
            'storage_stats': stats,
            'timestamp': datetime.now().isoformat()
        }
    
    def run_cycle(self):
        """
        Complete sensor cycle: collect data, store locally, upload pending readings
        This is the main function called by the systemd timer
        """
        logging.info("Starting sensor data collection and upload cycle")
        
        # Always collect and store current reading
        reading_id = self.store_reading_locally()
        if reading_id is None:
            logging.error("Failed to collect/store sensor data")
        
        # Try to upload all pending readings
        success_count, error_count = self.upload_pending_readings()
        
        # Periodic cleanup (only if we had successful uploads)
        if success_count > 0:
            self.cleanup_old_data()
        
        # Log summary
        stats = self.storage.get_stats()
        logging.info(f"Cycle complete. Stored: {reading_id is not None}, "
                    f"Uploaded: {success_count}, Errors: {error_count}, "
                    f"Pending: {stats['pending']}")
        
        return {
            'reading_stored': reading_id is not None,
            'uploads_successful': success_count,
            'upload_errors': error_count,
            'pending_readings': stats['pending']
        }


def main():
    """Main entry point for the sensor push service"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Keuka Sensor Push Service')
    parser.add_argument('--config', default='/opt/keuka/sensor_config.json',
                       help='Path to configuration file')
    parser.add_argument('--log-level', default='INFO',
                       choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                       help='Logging level')
    parser.add_argument('--status', action='store_true',
                       help='Show service status and exit')
    
    args = parser.parse_args()
    
    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    try:
        service = SensorPushService(args.config)
        
        if args.status:
            status = service.get_status()
            print(json.dumps(status, indent=2))
            return
        
        # Run the main cycle
        result = service.run_cycle()
        
        # Exit with error code if there were problems
        if not result['reading_stored'] and result['upload_errors'] > 0:
            sys.exit(1)
        
    except Exception as e:
        logging.error(f"Fatal error in sensor push service: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()