# async_sensor_manager.py
# Async sensor manager for non-blocking sensor operations

import asyncio
import time
import logging
from typing import Dict, Any, Optional, List, Tuple
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .base_sensor import BaseSensor

logger = logging.getLogger(__name__)

@dataclass
class SensorReading:
    """Container for sensor reading with metadata"""
    sensor_name: str
    value: Any
    timestamp: float
    success: bool
    error: Optional[str] = None
    duration_ms: int = 0

class AsyncSensorManager:
    """
    Manages multiple sensors with async operations and batched reading.
    Prevents blocking web requests during sensor operations.
    """
    
    def __init__(self, max_workers: int = 4, default_timeout: float = 5.0):
        """
        Initialize async sensor manager.
        
        Args:
            max_workers: Maximum number of worker threads
            default_timeout: Default timeout for sensor operations
        """
        self.sensors: Dict[str, BaseSensor] = {}
        self.max_workers = max_workers
        self.default_timeout = default_timeout
        self._executor: Optional[ThreadPoolExecutor] = None
        self._reading_cache: Dict[str, SensorReading] = {}
        self._cache_ttl = 1.0  # Cache readings for 1 second
    
    def register_sensor(self, name: str, sensor: BaseSensor) -> None:
        """
        Register a sensor with the manager.
        
        Args:
            name: Unique sensor identifier
            sensor: Sensor instance
        """
        self.sensors[name] = sensor
        logger.info(f"Registered sensor: {name}")
    
    def _get_executor(self) -> ThreadPoolExecutor:
        """Get or create the thread pool executor."""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=self.max_workers,
                                              thread_name_prefix="SensorThread")
        return self._executor
    
    async def read_sensor_async(self, sensor_name: str, 
                               timeout: Optional[float] = None,
                               use_cache: bool = True) -> SensorReading:
        """
        Read a single sensor asynchronously.
        
        Args:
            sensor_name: Name of sensor to read
            timeout: Optional timeout override
            use_cache: Whether to use cached readings
            
        Returns:
            SensorReading with result and metadata
        """
        if sensor_name not in self.sensors:
            return SensorReading(
                sensor_name=sensor_name,
                value=None,
                timestamp=time.time(),
                success=False,
                error=f"Sensor '{sensor_name}' not registered"
            )
        
        # Check cache first
        if use_cache and sensor_name in self._reading_cache:
            cached = self._reading_cache[sensor_name]
            if time.time() - cached.timestamp < self._cache_ttl:
                logger.debug(f"Returning cached reading for {sensor_name}")
                return cached
        
        sensor = self.sensors[sensor_name]
        timeout = timeout or self.default_timeout
        start_time = time.time()
        
        try:
            # Use asyncio.to_thread for non-blocking execution
            value = await asyncio.wait_for(
                asyncio.to_thread(sensor.read_with_retry, timeout),
                timeout=timeout + 1.0  # Add buffer to asyncio timeout
            )
            
            duration_ms = int((time.time() - start_time) * 1000)
            reading = SensorReading(
                sensor_name=sensor_name,
                value=value,
                timestamp=time.time(),
                success=True,
                duration_ms=duration_ms
            )
            
            # Cache successful reading
            self._reading_cache[sensor_name] = reading
            logger.debug(f"Async read {sensor_name}: {value} ({duration_ms}ms)")
            return reading
            
        except asyncio.TimeoutError:
            error_msg = f"Timeout after {timeout}s"
            logger.warning(f"Sensor {sensor_name} timeout")
            reading = SensorReading(
                sensor_name=sensor_name,
                value=sensor._get_fallback_value(),
                timestamp=time.time(),
                success=False,
                error=error_msg,
                duration_ms=int((time.time() - start_time) * 1000)
            )
            self._reading_cache[sensor_name] = reading
            return reading
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Sensor {sensor_name} error: {error_msg}")
            reading = SensorReading(
                sensor_name=sensor_name,
                value=sensor._get_fallback_value(),
                timestamp=time.time(),
                success=False,
                error=error_msg,
                duration_ms=int((time.time() - start_time) * 1000)
            )
            self._reading_cache[sensor_name] = reading
            return reading
    
    async def read_all_sensors_async(self, timeout: Optional[float] = None,
                                    use_cache: bool = True) -> Dict[str, SensorReading]:
        """
        Read all registered sensors concurrently.
        
        Args:
            timeout: Optional timeout for each sensor
            use_cache: Whether to use cached readings
            
        Returns:
            Dictionary of sensor readings
        """
        if not self.sensors:
            return {}
        
        logger.debug(f"Reading {len(self.sensors)} sensors concurrently")
        tasks = []
        
        for sensor_name in self.sensors:
            task = asyncio.create_task(
                self.read_sensor_async(sensor_name, timeout, use_cache)
            )
            tasks.append((sensor_name, task))
        
        results = {}
        for sensor_name, task in tasks:
            try:
                results[sensor_name] = await task
            except Exception as e:
                logger.error(f"Failed to read sensor {sensor_name}: {e}")
                results[sensor_name] = SensorReading(
                    sensor_name=sensor_name,
                    value=None,
                    timestamp=time.time(),
                    success=False,
                    error=str(e)
                )
        
        return results
    
    def get_cached_reading(self, sensor_name: str) -> Optional[SensorReading]:
        """
        Get cached sensor reading without performing new read.
        
        Args:
            sensor_name: Name of sensor
            
        Returns:
            Cached reading or None if not available/expired
        """
        if sensor_name not in self._reading_cache:
            return None
            
        cached = self._reading_cache[sensor_name]
        if time.time() - cached.timestamp > self._cache_ttl:
            return None
            
        return cached
    
    def get_sensor_health(self, sensor_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Get health information for sensors.
        
        Args:
            sensor_name: Specific sensor name, or None for all sensors
            
        Returns:
            Dictionary with health information
        """
        if sensor_name:
            if sensor_name not in self.sensors:
                return {"error": f"Sensor '{sensor_name}' not found"}
            return self.sensors[sensor_name].get_health_info()
        
        # Return health for all sensors
        health_info = {}
        for name, sensor in self.sensors.items():
            health_info[name] = sensor.get_health_info()
        
        return health_info
    
    def invalidate_cache(self, sensor_name: Optional[str] = None) -> None:
        """
        Invalidate cached readings.
        
        Args:
            sensor_name: Specific sensor to invalidate, or None for all
        """
        if sensor_name:
            self._reading_cache.pop(sensor_name, None)
        else:
            self._reading_cache.clear()
        logger.debug(f"Invalidated cache for {sensor_name or 'all sensors'}")
    
    async def health_check(self) -> Dict[str, Any]:
        """
        Perform comprehensive health check of all sensors.
        
        Returns:
            Health check results
        """
        start_time = time.time()
        readings = await self.read_all_sensors_async(timeout=2.0, use_cache=False)
        
        total_sensors = len(self.sensors)
        healthy_sensors = sum(1 for r in readings.values() if r.success)
        failed_sensors = total_sensors - healthy_sensors
        
        avg_response_time = 0.0
        if readings:
            avg_response_time = sum(r.duration_ms for r in readings.values()) / len(readings)
        
        return {
            "timestamp": time.time(),
            "total_sensors": total_sensors,
            "healthy_sensors": healthy_sensors,
            "failed_sensors": failed_sensors,
            "health_rate": round(healthy_sensors / total_sensors, 3) if total_sensors > 0 else 0.0,
            "avg_response_time_ms": round(avg_response_time, 1),
            "health_check_duration_s": round(time.time() - start_time, 3),
            "sensor_details": {name: reading.success for name, reading in readings.items()}
        }
    
    def shutdown(self) -> None:
        """Clean up resources."""
        if self._executor:
            self._executor.shutdown(wait=True)
            self._executor = None
        logger.info("AsyncSensorManager shutdown complete")
    
    def __del__(self):
        """Cleanup on deletion."""
        self.shutdown()

# Global sensor manager instance
sensor_manager = AsyncSensorManager()