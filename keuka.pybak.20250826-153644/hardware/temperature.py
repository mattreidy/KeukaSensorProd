# temperature.py
# DS18B20 temperature sensor interface via 1-Wire

import os
import logging
from typing import Optional

from .base_sensor import NumericSensor

logger = logging.getLogger(__name__)

# Default pin configuration
TEMP_PIN = 6  # BCM numbering (requires 1-Wire enabled in /boot/config.txt)

# DS18B20 via w1thermsensor if present
try:
    from w1thermsensor import W1ThermSensor  # type: ignore
except Exception:
    W1ThermSensor = None

class TemperatureSensor(NumericSensor):
    """
    DS18B20 temperature sensor with proper error handling and async support.
    """
    
    def __init__(self, pin: int = TEMP_PIN):
        super().__init__(name="DS18B20 Temperature", retry_attempts=2, retry_delay=0.2)
        self.pin = pin
        self._w1_sensors = None
    
    def _initialize_hardware(self) -> bool:
        """Initialize temperature sensor hardware."""
        try:
            if W1ThermSensor is not None:
                self._w1_sensors = W1ThermSensor.get_available_sensors()
                if self._w1_sensors:
                    logger.info(f"Found {len(self._w1_sensors)} DS18B20 sensors via w1thermsensor")
                    return True
            
            # Check /sys interface as fallback
            if self._check_sys_interface():
                logger.info("DS18B20 sensor available via /sys interface")
                return True
                
            logger.warning("No DS18B20 temperature sensors found")
            return False
            
        except Exception as e:
            logger.error(f"Temperature sensor initialization failed: {e}")
            return False
    
    def _check_sys_interface(self) -> bool:
        """Check if DS18B20 is available via /sys interface."""
        try:
            base = '/sys/bus/w1/devices'
            if not os.path.exists(base):
                return False
            devices = [d for d in os.listdir(base) if d.startswith('28-')]
            return len(devices) > 0
        except Exception:
            return False
    
    def _read_raw_data(self) -> float:
        """Read raw temperature data from sensor."""
        # Try w1thermsensor library first
        if self._w1_sensors:
            try:
                celsius = self._w1_sensors[0].get_temperature()
                return celsius * 9.0 / 5.0 + 32.0  # Convert to Fahrenheit
            except Exception as e:
                logger.debug(f"w1thermsensor read failed: {e}")
        
        # Fall back to direct /sys interface
        return self._read_sys_fallback()
    
    def _process_raw_data(self, raw_data: float) -> float:
        """Process raw temperature reading."""
        # Validate temperature range (reasonable for DS18B20)
        if not (-67 <= raw_data <= 257):  # -55°C to 125°C in Fahrenheit
            raise ValueError(f"Temperature reading {raw_data}°F out of valid range")
        return raw_data
    
    def _read_sys_fallback(self) -> float:
        """Read temperature directly from /sys/bus/w1/devices interface."""
        base = '/sys/bus/w1/devices'
        devices = [d for d in os.listdir(base) if d.startswith('28-')]
        if not devices:
            raise RuntimeError("No DS18B20 devices found in /sys interface")
            
        device_path = os.path.join(base, devices[0], 'w1_slave')
        with open(device_path, 'r') as f:
            data = f.read()
            
        # Check for valid reading (YES indicates successful reading)
        if 'YES' not in data:
            raise RuntimeError("DS18B20 sensor reading not ready (CRC error)")
            
        # Extract temperature value (in millidegrees Celsius)
        if 't=' not in data:
            raise RuntimeError("Invalid DS18B20 data format")
            
        temp_str = data.strip().split('t=')[-1]
        celsius = float(temp_str) / 1000.0
        return celsius * 9.0 / 5.0 + 32.0  # Convert to Fahrenheit
    
    def read_celsius(self) -> float:
        """Read temperature in Celsius."""
        fahrenheit = self.read_with_retry()
        if fahrenheit != fahrenheit:  # Check for NaN
            return float('nan')
        return (fahrenheit - 32.0) * 5.0 / 9.0
    
    def read_fahrenheit(self) -> float:
        """Read temperature in Fahrenheit."""
        return self.read_with_retry()
    
    async def read_celsius_async(self) -> float:
        """Read temperature in Celsius asynchronously."""
        fahrenheit = await self.read_async()
        if fahrenheit != fahrenheit:  # Check for NaN
            return float('nan')
        return (fahrenheit - 32.0) * 5.0 / 9.0
    
    async def read_fahrenheit_async(self) -> float:
        """Read temperature in Fahrenheit asynchronously."""
        return await self.read_async()
    
    def get_sensor_info(self) -> Optional[dict]:
        """Get information about detected temperature sensors."""
        try:
            if self._w1_sensors:
                sensor = self._w1_sensors[0]
                return {
                    'type': 'DS18B20',
                    'id': sensor.id,
                    'interface': 'w1thermsensor',
                    'pin': self.pin
                }
        except Exception:
            pass
        
        # Check /sys interface
        try:
            base = '/sys/bus/w1/devices'
            devices = [d for d in os.listdir(base) if d.startswith('28-')]
            if devices:
                return {
                    'type': 'DS18B20',
                    'id': devices[0],
                    'interface': 'sys_fallback',
                    'pin': self.pin
                }
        except Exception:
            pass
        
        return None

# Create global sensor instance
_temp_sensor = TemperatureSensor()

def read_temp_fahrenheit() -> float:
    """
    Read DS18B20 temperature in Fahrenheit.
    
    Legacy function for backward compatibility.
    
    Returns:
        Temperature in Fahrenheit, or NaN if no sensor found
    """
    return _temp_sensor.read_fahrenheit()

def read_temp_celsius() -> float:
    """
    Read DS18B20 temperature in Celsius.
    
    Legacy function for backward compatibility.
    
    Returns:
        Temperature in Celsius, or NaN if no sensor found
    """
    return _temp_sensor.read_celsius()

# Legacy function - now handled by TemperatureSensor class

def is_available() -> bool:
    """
    Check if DS18B20 temperature sensor is available.
    
    Legacy function for backward compatibility.
    
    Returns:
        True if sensor is detected, False otherwise
    """
    return _temp_sensor.is_available()

def get_sensor_info() -> Optional[dict]:
    """
    Get information about detected temperature sensors.
    
    Legacy function for backward compatibility.
    
    Returns:
        Dictionary with sensor information, or None if no sensors
    """
    return _temp_sensor.get_sensor_info()

# Async functions for non-blocking operations
async def read_temp_fahrenheit_async() -> float:
    """Read temperature in Fahrenheit asynchronously."""
    return await _temp_sensor.read_fahrenheit_async()

async def read_temp_celsius_async() -> float:
    """Read temperature in Celsius asynchronously."""
    return await _temp_sensor.read_celsius_async()