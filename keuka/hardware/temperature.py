# temperature.py
# DS18B20 temperature sensor interface via 1-Wire

import os
from typing import Optional

# Default pin configuration
TEMP_PIN = 6  # BCM numbering (requires 1-Wire enabled in /boot/config.txt)

# DS18B20 via w1thermsensor if present
try:
    from w1thermsensor import W1ThermSensor  # type: ignore
except Exception:
    W1ThermSensor = None

def read_temp_fahrenheit() -> float:
    """
    Read DS18B20 temperature in Fahrenheit.
    
    Tries w1thermsensor library first, then falls back to direct
    /sys/bus/w1/devices/*/w1_slave reading.
    
    Returns:
        Temperature in Fahrenheit, or NaN if no sensor found
    """
    # Try w1thermsensor library first
    try:
        if W1ThermSensor is not None:
            sensors = W1ThermSensor.get_available_sensors()
            if sensors:
                celsius = sensors[0].get_temperature()
                return celsius * 9.0 / 5.0 + 32.0
    except Exception:
        pass

    # Fall back to direct /sys interface
    return _read_temp_sys_fallback()

def read_temp_celsius() -> float:
    """
    Read DS18B20 temperature in Celsius.
    
    Returns:
        Temperature in Celsius, or NaN if no sensor found
    """
    fahrenheit = read_temp_fahrenheit()
    if fahrenheit != fahrenheit:  # Check for NaN
        return float('nan')
    return (fahrenheit - 32.0) * 5.0 / 9.0

def _read_temp_sys_fallback() -> float:
    """
    Read temperature directly from /sys/bus/w1/devices interface.
    
    Returns:
        Temperature in Fahrenheit, or NaN if no sensor found
    """
    base = '/sys/bus/w1/devices'
    try:
        # Find first DS18B20 device (starts with '28-')
        devices = [d for d in os.listdir(base) if d.startswith('28-')]
        if not devices:
            return float('nan')
            
        device_path = os.path.join(base, devices[0], 'w1_slave')
        with open(device_path, 'r') as f:
            data = f.read()
            
        # Check for valid reading (YES indicates successful reading)
        if 'YES' not in data:
            return float('nan')
            
        # Extract temperature value (in millidegrees Celsius)
        temp_str = data.strip().split('t=')[-1]
        celsius = float(temp_str) / 1000.0
        return celsius * 9.0 / 5.0 + 32.0
        
    except Exception:
        return float('nan')

def is_available() -> bool:
    """
    Check if DS18B20 temperature sensor is available.
    
    Returns:
        True if sensor is detected, False otherwise
    """
    # Check w1thermsensor library
    try:
        if W1ThermSensor is not None:
            sensors = W1ThermSensor.get_available_sensors()
            if sensors:
                return True
    except Exception:
        pass
    
    # Check /sys interface
    try:
        base = '/sys/bus/w1/devices'
        devices = [d for d in os.listdir(base) if d.startswith('28-')]
        return len(devices) > 0
    except Exception:
        return False

def get_sensor_info() -> Optional[dict]:
    """
    Get information about detected temperature sensors.
    
    Returns:
        Dictionary with sensor information, or None if no sensors
    """
    try:
        if W1ThermSensor is not None:
            sensors = W1ThermSensor.get_available_sensors()
            if sensors:
                sensor = sensors[0]
                return {
                    'type': 'DS18B20',
                    'id': sensor.id,
                    'interface': 'w1thermsensor',
                    'pin': TEMP_PIN
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
                'pin': TEMP_PIN
            }
    except Exception:
        pass
    
    return None