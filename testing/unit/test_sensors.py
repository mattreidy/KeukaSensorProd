#!/usr/bin/env python3
"""
Unit tests for sensor modules with hardware mocking
"""

import pytest
import os
import sys
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

class TestSensorModules:
    """Test sensor modules with mocked hardware"""
    
    def setup_method(self):
        """Set up test environment with hardware mocking"""
        self.test_env = {
            "KEUKA_TEST_MODE": "1",
            "KEUKA_MOCK_HARDWARE": "1"
        }
        for key, value in self.test_env.items():
            os.environ[key] = value
    
    def teardown_method(self):
        """Clean up test environment"""
        for key in self.test_env:
            if key in os.environ:
                del os.environ[key]
    
    def test_temperature_module_import(self):
        """Test temperature module can be imported"""
        from keuka.hardware import temperature
        assert temperature is not None
    
    def test_temperature_functions_exist(self):
        """Test temperature module has required functions"""
        from keuka.hardware import temperature
        
        required_functions = ['read_temp_fahrenheit', 'read_temp_celsius']
        for func_name in required_functions:
            assert hasattr(temperature, func_name), f"Missing function: {func_name}"
    
    @patch('keuka.hardware.temperature.W1ThermSensor', autospec=True)
    def test_temperature_reading_mock(self, mock_w1_sensor):
        """Test temperature reading with mocked W1ThermSensor"""
        # Mock the sensor to return a test value
        mock_sensor_instance = MagicMock()
        mock_sensor_instance.get_temperature.return_value = 25.0  # 25°C
        mock_w1_sensor.return_value = mock_sensor_instance
        
        from keuka.hardware import temperature
        
        # This should work even if we don't have the actual hardware
        try:
            temp_c = temperature.read_temp_celsius()
            temp_f = temperature.read_temp_fahrenheit()
            
            # In mock mode, should return reasonable test values or NaN
            assert isinstance(temp_c, (float, type(float('nan'))))
            assert isinstance(temp_f, (float, type(float('nan'))))
        except Exception as e:
            # If it raises an exception, it should be handled gracefully
            assert "test mode" in str(e).lower() or "mock" in str(e).lower()
    
    def test_ultrasonic_module_import(self):
        """Test ultrasonic module can be imported"""
        from keuka.hardware import ultrasonic
        assert ultrasonic is not None
    
    def test_ultrasonic_functions_exist(self):
        """Test ultrasonic module has required functions"""
        from keuka.hardware import ultrasonic
        
        required_functions = ['read_distance_inches', 'median_distance_inches']
        for func_name in required_functions:
            assert hasattr(ultrasonic, func_name), f"Missing function: {func_name}"
    
    def test_ultrasonic_constants_exist(self):
        """Test ultrasonic module has required constants"""
        from keuka.hardware import ultrasonic
        
        required_constants = ['TRIG_PIN', 'ECHO_PIN', 'DEFAULT_SAMPLES']
        for const_name in required_constants:
            assert hasattr(ultrasonic, const_name), f"Missing constant: {const_name}"
    
    @patch('keuka.hardware.ultrasonic.GPIO', autospec=True)
    def test_ultrasonic_reading_mock(self, mock_gpio):
        """Test ultrasonic reading with mocked GPIO"""
        # Mock GPIO operations
        mock_gpio.input.return_value = False
        mock_gpio.output = MagicMock()
        mock_gpio.setup = MagicMock()
        
        from keuka.hardware import ultrasonic
        
        try:
            distance = ultrasonic.read_distance_inches()
            median_distance = ultrasonic.median_distance_inches()
            
            # Should return float or NaN
            assert isinstance(distance, (float, type(float('nan'))))
            assert isinstance(median_distance, (float, type(float('nan'))))
        except Exception as e:
            # Exception should indicate test/mock mode
            assert "test mode" in str(e).lower() or "mock" in str(e).lower() or "gpio" in str(e).lower()
    
    def test_gps_module_import(self):
        """Test GPS module can be imported"""
        from keuka.hardware import gps
        assert gps is not None
    
    def test_gps_functions_exist(self):
        """Test GPS module has required functions"""
        from keuka.hardware import gps
        
        required_functions = ['read_gps_lat_lon_elev']
        for func_name in required_functions:
            assert hasattr(gps, func_name), f"Missing function: {func_name}"
    
    def test_gps_reading_mock(self):
        """Test GPS reading returns expected format"""
        from keuka.hardware import gps
        
        try:
            lat, lon, elev = gps.read_gps_lat_lon_elev()
            
            # Should return three values (even if NaN)
            assert isinstance(lat, (float, type(float('nan'))))
            assert isinstance(lon, (float, type(float('nan'))))
            assert isinstance(elev, (float, type(float('nan'))))
        except Exception as e:
            # Should handle gracefully in test mode
            assert "gps" in str(e).lower() or "serial" in str(e).lower()
    
    def test_sensors_wrapper_import(self):
        """Test main sensors wrapper module"""
        from keuka import sensors
        assert sensors is not None
    
    def test_sensors_wrapper_functions(self):
        """Test sensors wrapper has all expected functions"""
        from keuka import sensors
        
        expected_functions = [
            'read_temp_fahrenheit',
            'read_temp_celsius', 
            'read_distance_inches',
            'median_distance_inches',
            'read_gps_lat_lon_elev'
        ]
        
        for func_name in expected_functions:
            assert hasattr(sensors, func_name), f"Missing function in sensors wrapper: {func_name}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])