# sensors.py
# Unified sensor interface with async support and backward compatibility

import asyncio
import logging
from typing import Optional, Dict, Any, Tuple

from .hardware.async_sensor_manager import sensor_manager, SensorReading
from .hardware.temperature import _temp_sensor, read_temp_fahrenheit, read_temp_celsius
from .hardware.ultrasonic import _ultrasonic_sensor, median_distance_inches
from .hardware.gps import read_gps_lat_lon_elev, read_gps_snapshot

logger = logging.getLogger(__name__)

# Register sensors with the async manager
sensor_manager.register_sensor("temperature", _temp_sensor)
sensor_manager.register_sensor("ultrasonic", _ultrasonic_sensor)

# ============= LEGACY COMPATIBILITY FUNCTIONS =============
# These maintain backward compatibility with existing code

def read_temp_fahrenheit() -> float:
    """Read temperature in Fahrenheit (legacy compatibility)."""
    return _temp_sensor.read_fahrenheit()

def read_temp_celsius() -> float:
    """Read temperature in Celsius (legacy compatibility)."""
    return _temp_sensor.read_celsius()

def median_distance_inches(samples: int = 11) -> float:
    """Read median ultrasonic distance (legacy compatibility)."""
    return _ultrasonic_sensor.read_median_distance(samples)

# GPS functions remain unchanged since they don't follow the same pattern
# and are already reasonably optimized

# ============= NEW ASYNC INTERFACE =============
# These provide non-blocking sensor operations for web routes

async def read_all_sensors_async(use_cache: bool = True, timeout: float = 3.0) -> Dict[str, Any]:
    """
    Read all sensors asynchronously and return formatted data.
    
    Args:
        use_cache: Whether to use cached readings (recommended)
        timeout: Timeout for each sensor operation
        
    Returns:
        Dictionary with sensor readings
    """
    try:
        # Read sensors concurrently
        readings = await sensor_manager.read_all_sensors_async(timeout=timeout, use_cache=use_cache)
        
        # Format results for backward compatibility
        result = {}
        
        # Temperature
        if "temperature" in readings:
            temp_reading = readings["temperature"]
            if temp_reading.success:
                result["temperature_f"] = temp_reading.value
                result["temperature_c"] = (temp_reading.value - 32.0) * 5.0 / 9.0 if temp_reading.value == temp_reading.value else float('nan')
            else:
                result["temperature_f"] = float('nan')
                result["temperature_c"] = float('nan')
        
        # Ultrasonic distance
        if "ultrasonic" in readings:
            dist_reading = readings["ultrasonic"]
            result["distance_inches"] = dist_reading.value if dist_reading.success else float('nan')
        
        # Add sensor health information
        result["sensor_health"] = {
            name: {
                "success": reading.success,
                "error": reading.error,
                "duration_ms": reading.duration_ms,
                "timestamp": reading.timestamp
            } for name, reading in readings.items()
        }
        
        return result
        
    except Exception as e:
        logger.error(f"Error reading sensors asynchronously: {e}")
        return {
            "temperature_f": float('nan'),
            "temperature_c": float('nan'),
            "distance_inches": float('nan'),
            "sensor_health": {},
            "error": str(e)
        }

async def read_temperature_async() -> Tuple[float, float]:
    """
    Read temperature asynchronously.
    
    Returns:
        Tuple of (fahrenheit, celsius)
    """
    reading = await sensor_manager.read_sensor_async("temperature")
    if reading.success:
        fahrenheit = reading.value
        celsius = (fahrenheit - 32.0) * 5.0 / 9.0 if fahrenheit == fahrenheit else float('nan')
        return fahrenheit, celsius
    else:
        return float('nan'), float('nan')

async def read_distance_async() -> float:
    """
    Read ultrasonic distance asynchronously.
    
    Returns:
        Distance in inches or NaN
    """
    reading = await sensor_manager.read_sensor_async("ultrasonic")
    return reading.value if reading.success else float('nan')

def get_sensor_health() -> Dict[str, Any]:
    """
    Get comprehensive health information for all sensors.
    
    Returns:
        Dictionary with health metrics
    """
    return sensor_manager.get_sensor_health()

async def sensor_health_check() -> Dict[str, Any]:
    """
    Perform comprehensive async health check of all sensors.
    
    Returns:
        Health check results
    """
    return await sensor_manager.health_check()

def get_cached_sensor_data() -> Dict[str, Any]:
    """
    Get cached sensor readings without triggering new reads.
    Useful for high-frequency access like SSE streams.
    
    Returns:
        Dictionary with cached sensor data
    """
    result = {
        "temperature_f": float('nan'),
        "temperature_c": float('nan'),
        "distance_inches": float('nan'),
        "cache_status": {}
    }
    
    # Temperature
    temp_cache = sensor_manager.get_cached_reading("temperature")
    if temp_cache and temp_cache.success:
        result["temperature_f"] = temp_cache.value
        result["temperature_c"] = (temp_cache.value - 32.0) * 5.0 / 9.0
        result["cache_status"]["temperature"] = {
            "age_s": temp_cache.timestamp - temp_cache.timestamp if hasattr(temp_cache, 'timestamp') else 0,
            "success": True
        }
    else:
        result["cache_status"]["temperature"] = {"success": False, "reason": "no_cache"}
    
    # Distance
    dist_cache = sensor_manager.get_cached_reading("ultrasonic")
    if dist_cache and dist_cache.success:
        result["distance_inches"] = dist_cache.value
        result["cache_status"]["ultrasonic"] = {
            "age_s": dist_cache.timestamp - dist_cache.timestamp if hasattr(dist_cache, 'timestamp') else 0,
            "success": True
        }
    else:
        result["cache_status"]["ultrasonic"] = {"success": False, "reason": "no_cache"}
    
    return result

# ============= SENSOR INITIALIZATION =============
# Ensure sensors are ready for use

def initialize_sensors() -> Dict[str, bool]:
    """
    Initialize all sensors and return their availability status.
    
    Returns:
        Dictionary with sensor availability
    """
    status = {}
    
    try:
        status["temperature"] = _temp_sensor.is_available()
    except Exception as e:
        logger.error(f"Temperature sensor initialization failed: {e}")
        status["temperature"] = False
    
    try:
        status["ultrasonic"] = _ultrasonic_sensor.is_available()
    except Exception as e:
        logger.error(f"Ultrasonic sensor initialization failed: {e}")
        status["ultrasonic"] = False
    
    # Log initialization results
    available_sensors = [name for name, available in status.items() if available]
    failed_sensors = [name for name, available in status.items() if not available]
    
    if available_sensors:
        logger.info(f"Sensors initialized: {', '.join(available_sensors)}")
    if failed_sensors:
        logger.warning(f"Sensors failed to initialize: {', '.join(failed_sensors)}")
    
    return status

# Initialize sensors on module import
_sensor_status = initialize_sensors()
logger.info(f"Sensor module loaded with status: {_sensor_status}")