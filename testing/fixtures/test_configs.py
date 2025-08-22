#!/usr/bin/env python3
"""
Test configuration fixtures and utilities
"""

import os
import tempfile
import shutil
from pathlib import Path
from typing import Dict, Any
import json

class TestConfigManager:
    """Manages test configurations safely"""
    
    def __init__(self):
        self.temp_dirs = []
        self.original_env = os.environ.copy()
        
    def create_temp_config_dir(self) -> Path:
        """Create a temporary configuration directory"""
        temp_dir = Path(tempfile.mkdtemp(prefix="keuka_test_config_"))
        self.temp_dirs.append(temp_dir)
        return temp_dir
        
    def cleanup_temp_dirs(self):
        """Clean up all temporary directories"""
        for temp_dir in self.temp_dirs:
            if temp_dir.exists():
                try:
                    shutil.rmtree(temp_dir)
                except Exception as e:
                    print(f"Warning: Could not clean up {temp_dir}: {e}")
        self.temp_dirs.clear()
        
    def create_test_sensor_config(self, temp_dir: Path) -> Path:
        """Create a test sensor configuration file"""
        config_content = """
# Test sensor configuration
[ultrasonic]
TRIG_PIN=18
ECHO_PIN=24
TIMEOUT_SECONDS=3.0
SAMPLES=5

[temperature]
SENSOR_ID=auto
RETRY_COUNT=3
RETRY_DELAY=1.0

[gps]
SERIAL_PORT=/dev/ttyAMA0
BAUDRATE=9600
TIMEOUT=1.0
"""
        config_file = temp_dir / "sensors.conf"
        config_file.write_text(config_content.strip())
        return config_file
        
    def create_test_camera_config(self, temp_dir: Path) -> Path:
        """Create a test camera configuration file"""
        config_content = """
# Test camera configuration
[camera]
DEVICE_INDEX=0
WIDTH=640
HEIGHT=480
FPS=30
QUALITY=85
ROTATION=0
"""
        config_file = temp_dir / "camera.conf"
        config_file.write_text(config_content.strip())
        return config_file
        
    def create_test_duckdns_config(self, temp_dir: Path) -> Path:
        """Create a test DuckDNS configuration file"""
        config_content = """token=test-token-12345
domains=test-domain1,test-domain2
"""
        config_file = temp_dir / "duckdns.conf"
        config_file.write_text(config_content.strip())
        return config_file
        
    def create_test_environment_file(self, temp_dir: Path) -> Path:
        """Create a test environment file"""
        env_content = """# Test environment file
KEUKA_TEST_MODE=1
KEUKA_MOCK_HARDWARE=1
KEUKA_SAFE_MODE=1
ADMIN_USER=testuser
ADMIN_PASS=testpass
WLAN_STA_IFACE=wlan0
WLAN_AP_IFACE=wlan1
"""
        env_file = temp_dir / "test.env"
        env_file.write_text(env_content.strip())
        return env_file
        
    def set_test_environment(self, config_dir: Path):
        """Set up test environment variables"""
        test_env = {
            "KEUKA_TEST_MODE": "1",
            "KEUKA_MOCK_HARDWARE": "1", 
            "KEUKA_SAFE_MODE": "1",
            "KEUKA_CONFIG_DIR": str(config_dir),
            "ADMIN_USER": "testuser",
            "ADMIN_PASS": "testpass",
            "WLAN_STA_IFACE": "wlan0",
            "WLAN_AP_IFACE": "wlan1",
        }
        
        for key, value in test_env.items():
            os.environ[key] = value
            
    def restore_environment(self):
        """Restore original environment"""
        os.environ.clear()
        os.environ.update(self.original_env)


class TestDataGenerator:
    """Generates test data for various components"""
    
    @staticmethod
    def generate_health_data() -> Dict[str, Any]:
        """Generate mock health/status data"""
        return {
            "time_utc": "2023-08-22 10:30:00",
            "tempF": 72.5,
            "distanceInches": 24.7,
            "gps": {
                "lat": 42.5123,
                "lon": -77.1456,
                "elevation_ft": 656.2
            },
            "camera": "running",
            "wifi_sta": {
                "ssid": "TestNetwork",
                "signal_dbm": -45,
                "freq_mhz": 2437,
                "bssid": "aa:bb:cc:dd:ee:ff"
            },
            "wifi_ap": {
                "ssid": "KeukaSensor-AP",
                "channel": "6",
                "hw_mode": "g"
            },
            "ip": {
                "wlan0": "192.168.1.100",
                "wlan1": "192.168.4.1"
            },
            "gateway_sta": "192.168.1.1",
            "gateway_ap": None,
            "dns": ["192.168.1.1", "8.8.8.8"],
            "system": {
                "cpu_temp_c": 45.2,
                "cpu_util_pct": 12.5,
                "uptime_seconds": 86400,
                "boot_time_utc": "2023-08-21 10:30:00",
                "disk": {
                    "percent": 45,
                    "used": 14000000000,
                    "total": 32000000000
                },
                "mem": {
                    "percent": 35,
                    "total": 4000000000,
                    "used": 1400000000,
                    "free": 2600000000
                },
                "hostname": "keukasensor"
            },
            "thresholds": {
                "temp_warn_f": 85.0,
                "temp_crit_f": 95.0,
                "rssi_warn_dbm": -70,
                "rssi_crit_dbm": -80,
                "cpu_warn_c": 70.0,
                "cpu_crit_c": 80.0
            },
            "version": "2.0.0-test",
            "contact": {
                "name": "Test User",
                "address": "123 Test St\nTest City, NY 12345",
                "phone": "+1-555-123-4567",
                "email": "test@example.com",
                "notes": "This is a test configuration"
            }
        }
        
    @staticmethod
    def generate_wifi_scan_data() -> Dict[str, Any]:
        """Generate mock WiFi scan data"""
        return {
            "ok": True,
            "networks": [
                {
                    "ssid": "TestNetwork1",
                    "signal_dbm": -45,
                    "freq_mhz": 2437,
                    "security": "WPA2"
                },
                {
                    "ssid": "TestNetwork2", 
                    "signal_dbm": -67,
                    "freq_mhz": 2462,
                    "security": "WPA2"
                },
                {
                    "ssid": "OpenNetwork",
                    "signal_dbm": -72,
                    "freq_mhz": 2412,
                    "security": "Open"
                }
            ]
        }
        
    @staticmethod
    def generate_duckdns_status_data() -> Dict[str, Any]:
        """Generate mock DuckDNS status data"""
        return {
            "ok": True,
            "conf": {
                "domains": "test-domain1,test-domain2",
                "token": "test-token-12345"
            },
            "service_active": False,
            "timer_active": False,
            "timer_enabled": False,
            "timer_next": None,
            "last_result": "OK",
            "last": {
                "when": "2023-08-22 09:00:00",
                "text": "[INFO] DuckDNS update successful"
            },
            "service_substate": "dead",
            "service_exec_status": 0,
            "service_started_at": None,
            "service_exited_at": "2023-08-22 09:00:05"
        }


def create_full_test_environment() -> TestConfigManager:
    """Create a complete test environment with all necessary configs"""
    config_manager = TestConfigManager()
    
    # Create temporary config directory
    temp_dir = config_manager.create_temp_config_dir()
    
    # Create all test configuration files
    config_manager.create_test_sensor_config(temp_dir)
    config_manager.create_test_camera_config(temp_dir)
    config_manager.create_test_duckdns_config(temp_dir)
    config_manager.create_test_environment_file(temp_dir)
    
    # Set up test environment
    config_manager.set_test_environment(temp_dir)
    
    return config_manager


# Test configuration templates
SENSOR_CONFIG_TEMPLATE = """
# Sensor configuration template for testing
[ultrasonic]
TRIG_PIN={trig_pin}
ECHO_PIN={echo_pin}
TIMEOUT_SECONDS={timeout}
SAMPLES={samples}

[temperature]
SENSOR_ID={sensor_id}
RETRY_COUNT={retry_count}
RETRY_DELAY={retry_delay}

[gps]
SERIAL_PORT={serial_port}
BAUDRATE={baudrate}
TIMEOUT={gps_timeout}
"""

CAMERA_CONFIG_TEMPLATE = """
# Camera configuration template for testing
[camera]
DEVICE_INDEX={device_index}
WIDTH={width}
HEIGHT={height}
FPS={fps}
QUALITY={quality}
ROTATION={rotation}
"""


if __name__ == "__main__":
    # Test the configuration manager
    print("Testing configuration manager...")
    
    config_manager = create_full_test_environment()
    
    try:
        print("✅ Test environment created successfully")
        print(f"   Config directory: {os.environ.get('KEUKA_CONFIG_DIR')}")
        print(f"   Test mode: {os.environ.get('KEUKA_TEST_MODE')}")
        
        # Test data generation
        health_data = TestDataGenerator.generate_health_data()
        print(f"✅ Health data generated: {len(health_data)} fields")
        
        wifi_data = TestDataGenerator.generate_wifi_scan_data()
        print(f"✅ WiFi data generated: {len(wifi_data['networks'])} networks")
        
    finally:
        config_manager.cleanup_temp_dirs()
        config_manager.restore_environment()
        print("✅ Test environment cleaned up")