#!/usr/bin/env python3
"""
Hardware mocking utilities for safe testing
"""

import os
import time
import random
from unittest.mock import MagicMock, Mock
from typing import Optional, Tuple, Dict, Any

class MockGPIO:
    """Mock RPi.GPIO for testing"""
    
    # GPIO Constants
    BCM = "BCM"
    OUT = "OUT" 
    IN = "IN"
    HIGH = True
    LOW = False
    
    @staticmethod
    def setmode(mode):
        pass
        
    @staticmethod
    def setup(pin, mode):
        pass
        
    @staticmethod
    def output(pin, value):
        pass
        
    @staticmethod
    def input(pin):
        # Return random values to simulate sensor readings
        return random.choice([True, False])
        
    @staticmethod
    def cleanup():
        pass


class MockW1ThermSensor:
    """Mock W1ThermSensor for testing"""
    
    def __init__(self, sensor_type=None, sensor_id=None):
        self.sensor_type = sensor_type
        self.sensor_id = sensor_id
        self._base_temp = 20.0  # Base temperature in Celsius
    
    def get_temperature(self, unit='celsius'):
        """Return mock temperature reading"""
        if os.environ.get("KEUKA_TEST_MODE") == "1":
            # Return realistic but fake temperature readings
            temp_c = self._base_temp + random.uniform(-5, 15)
            if unit.lower() == 'fahrenheit':
                return temp_c * 9/5 + 32
            return temp_c
        else:
            raise Exception("W1ThermSensor not available in test mode")


class MockCV2:
    """Mock OpenCV for testing"""
    
    class VideoCapture:
        def __init__(self, device=0):
            self.device = device
            self.is_opened = True
            
        def isOpened(self):
            return self.is_opened
            
        def read(self):
            # Return mock frame data
            if os.environ.get("KEUKA_TEST_MODE") == "1":
                # Return success=True and mock image array
                import numpy as np
                # Create a simple test image (100x100 pixels, 3 channels)
                fake_frame = np.zeros((100, 100, 3), dtype=np.uint8)
                return True, fake_frame
            return False, None
            
        def release(self):
            self.is_opened = False
            
        def set(self, prop, value):
            return True
            
        def get(self, prop):
            return 30.0  # Mock FPS
    
    @staticmethod
    def imencode(ext, img, params=None):
        """Mock image encoding"""
        if os.environ.get("KEUKA_TEST_MODE") == "1":
            # Return mock JPEG data
            fake_jpeg = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00'
            return True, fake_jpeg
        return False, None
    
    # Constants
    CAP_PROP_FRAME_WIDTH = 3
    CAP_PROP_FRAME_HEIGHT = 4
    CAP_PROP_FPS = 5


class MockSerial:
    """Mock serial connection for GPS testing"""
    
    def __init__(self, port, baudrate=9600, timeout=1):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.is_open = True
        
        # Mock NMEA sentences
        self.mock_sentences = [
            b"$GPGGA,123456.78,4123.1234,N,08456.5678,W,1,08,0.9,545.4,M,46.9,M,,*47\r\n",
            b"$GPRMC,123456.78,A,4123.1234,N,08456.5678,W,000.0,360.0,310821,,,*78\r\n"
        ]
        self.sentence_index = 0
    
    def readline(self):
        """Return mock NMEA sentence"""
        if os.environ.get("KEUKA_TEST_MODE") == "1":
            sentence = self.mock_sentences[self.sentence_index % len(self.mock_sentences)]
            self.sentence_index += 1
            time.sleep(0.1)  # Simulate reading delay
            return sentence
        return b""
    
    def close(self):
        self.is_open = False


class HardwareMockManager:
    """Manages hardware mocking for tests"""
    
    def __init__(self):
        self.original_modules = {}
        self.mock_active = False
    
    def activate_mocks(self):
        """Activate hardware mocks"""
        if self.mock_active:
            return
            
        import sys
        
        # Mock RPi.GPIO
        if 'RPi.GPIO' not in sys.modules:
            sys.modules['RPi'] = MagicMock()
            sys.modules['RPi.GPIO'] = MockGPIO()
        
        # Mock W1ThermSensor
        if 'w1thermsensor' not in sys.modules:
            mock_w1 = MagicMock()
            mock_w1.W1ThermSensor = MockW1ThermSensor
            sys.modules['w1thermsensor'] = mock_w1
        
        # Mock OpenCV
        if 'cv2' not in sys.modules:
            sys.modules['cv2'] = MockCV2()
        
        # Mock serial
        if 'serial' not in sys.modules:
            mock_serial_module = MagicMock()
            mock_serial_module.Serial = MockSerial
            sys.modules['serial'] = mock_serial_module
        
        self.mock_active = True
    
    def deactivate_mocks(self):
        """Deactivate hardware mocks"""
        if not self.mock_active:
            return
        
        import sys
        
        # Remove mocked modules
        mock_modules = ['RPi', 'RPi.GPIO', 'w1thermsensor', 'cv2', 'serial']
        for module in mock_modules:
            if module in sys.modules and hasattr(sys.modules[module], '_mock_name'):
                del sys.modules[module]
        
        self.mock_active = False


# Global mock manager instance
mock_manager = HardwareMockManager()

def setup_hardware_mocks():
    """Convenience function to set up all hardware mocks"""
    if os.environ.get("KEUKA_TEST_MODE") == "1":
        mock_manager.activate_mocks()

def teardown_hardware_mocks():
    """Convenience function to tear down all hardware mocks"""
    mock_manager.deactivate_mocks()

# Auto-activate mocks if in test mode
if os.environ.get("KEUKA_TEST_MODE") == "1":
    setup_hardware_mocks()


# Utility functions for generating realistic mock data
def mock_temperature_reading() -> float:
    """Generate realistic temperature reading in Celsius"""
    base_temp = 22.0  # Room temperature baseline
    variation = random.uniform(-5, 10)  # Some variation
    return base_temp + variation

def mock_distance_reading() -> float:
    """Generate realistic ultrasonic distance reading in inches"""
    # Simulate distances between 2 inches and 120 inches
    return random.uniform(2.0, 120.0)

def mock_gps_reading() -> Tuple[float, float, float]:
    """Generate realistic GPS coordinates"""
    # Mock coordinates around Keuka Lake, NY
    base_lat = 42.5  # Latitude
    base_lon = -77.1  # Longitude
    base_elev = 200.0  # Elevation in meters
    
    lat = base_lat + random.uniform(-0.1, 0.1)
    lon = base_lon + random.uniform(-0.1, 0.1) 
    elev = base_elev + random.uniform(-50, 100)
    
    return lat, lon, elev

def mock_wifi_scan() -> list:
    """Generate mock WiFi scan results"""
    mock_networks = [
        {"ssid": "TestNetwork1", "signal_dbm": -45, "freq_mhz": 2437},
        {"ssid": "TestNetwork2", "signal_dbm": -67, "freq_mhz": 2462},
        {"ssid": "TestNetwork3", "signal_dbm": -72, "freq_mhz": 5180},
    ]
    return mock_networks


if __name__ == "__main__":
    # Test the mocks
    setup_hardware_mocks()
    
    print("Testing hardware mocks...")
    
    # Test GPIO mock
    try:
        import RPi.GPIO as GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(18, GPIO.OUT)
        print("✅ GPIO mock working")
    except Exception as e:
        print(f"❌ GPIO mock failed: {e}")
    
    # Test temperature sensor mock  
    try:
        from w1thermsensor import W1ThermSensor
        sensor = W1ThermSensor()
        temp = sensor.get_temperature()
        print(f"✅ Temperature mock working: {temp}°C")
    except Exception as e:
        print(f"❌ Temperature mock failed: {e}")
    
    # Test OpenCV mock
    try:
        import cv2
        cap = cv2.VideoCapture(0)
        ret, frame = cap.read()
        print(f"✅ OpenCV mock working: {ret}")
    except Exception as e:
        print(f"❌ OpenCV mock failed: {e}")
    
    teardown_hardware_mocks()