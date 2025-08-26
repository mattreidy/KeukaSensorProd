# base_sensor.py
# Base classes for hardware sensors with proper error handling and logging

import time
import logging
import asyncio
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Union, Callable, TypeVar, Generic
from dataclasses import dataclass
from enum import Enum

# Configure logging
logger = logging.getLogger(__name__)

T = TypeVar('T')

class SensorStatus(Enum):
    """Sensor health status enumeration"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"
    UNKNOWN = "unknown"

@dataclass
class SensorHealth:
    """Sensor health information"""
    status: SensorStatus
    last_success_time: Optional[float] = None
    last_error_time: Optional[float] = None
    last_error: Optional[str] = None
    consecutive_failures: int = 0
    total_readings: int = 0
    successful_readings: int = 0

class BaseSensor(ABC, Generic[T]):
    """
    Base class for all hardware sensors with standardized error handling,
    health tracking, and async operation support.
    """
    
    def __init__(self, name: str, retry_attempts: int = 3, retry_delay: float = 0.1):
        """
        Initialize base sensor.
        
        Args:
            name: Human-readable sensor name
            retry_attempts: Number of retry attempts on failure
            retry_delay: Delay between retry attempts in seconds
        """
        self.name = name
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        self.health = SensorHealth(status=SensorStatus.UNKNOWN)
        self._initialization_attempted = False
        
    @abstractmethod
    def _initialize_hardware(self) -> bool:
        """
        Initialize hardware-specific components.
        
        Returns:
            True if initialization successful, False otherwise
        """
        pass
    
    @abstractmethod
    def _read_raw_data(self) -> Any:
        """
        Read raw data from the sensor hardware.
        
        Returns:
            Raw sensor data (implementation specific)
            
        Raises:
            Exception: Any sensor-specific exceptions
        """
        pass
    
    @abstractmethod
    def _process_raw_data(self, raw_data: Any) -> T:
        """
        Process raw sensor data into final format.
        
        Args:
            raw_data: Raw data from _read_raw_data()
            
        Returns:
            Processed sensor data
        """
        pass
    
    def _get_fallback_value(self) -> T:
        """
        Get fallback value when sensor reading fails.
        
        Returns:
            Fallback value (typically NaN for numeric sensors)
        """
        return float('nan')  # type: ignore
    
    def _ensure_initialized(self) -> bool:
        """
        Ensure sensor is initialized (lazy initialization).
        
        Returns:
            True if initialization successful, False otherwise
        """
        if not self._initialization_attempted:
            try:
                success = self._initialize_hardware()
                self._initialization_attempted = True
                if success:
                    logger.info(f"{self.name} sensor initialized successfully")
                else:
                    logger.warning(f"{self.name} sensor initialization failed")
                return success
            except Exception as e:
                logger.error(f"{self.name} sensor initialization error: {e}")
                self._initialization_attempted = True
                return False
        return True
    
    def _update_health_success(self, reading_time: float) -> None:
        """Update health tracking after successful reading."""
        self.health.last_success_time = reading_time
        self.health.consecutive_failures = 0
        self.health.successful_readings += 1
        self.health.total_readings += 1
        
        # Determine status based on success rate
        if self.health.total_readings > 10:
            success_rate = self.health.successful_readings / self.health.total_readings
            if success_rate > 0.95:
                self.health.status = SensorStatus.HEALTHY
            elif success_rate > 0.7:
                self.health.status = SensorStatus.DEGRADED
            else:
                self.health.status = SensorStatus.FAILED
        else:
            self.health.status = SensorStatus.HEALTHY
    
    def _update_health_failure(self, error: str, error_time: float) -> None:
        """Update health tracking after failed reading."""
        self.health.last_error = error
        self.health.last_error_time = error_time
        self.health.consecutive_failures += 1
        self.health.total_readings += 1
        
        # Determine status based on consecutive failures
        if self.health.consecutive_failures >= 5:
            self.health.status = SensorStatus.FAILED
        elif self.health.consecutive_failures >= 2:
            self.health.status = SensorStatus.DEGRADED
        else:
            self.health.status = SensorStatus.HEALTHY
    
    def read_with_retry(self, timeout: Optional[float] = None) -> T:
        """
        Read sensor data with retry logic and error handling.
        
        Args:
            timeout: Optional timeout for the entire operation
            
        Returns:
            Sensor reading or fallback value on failure
        """
        if not self._ensure_initialized():
            error_msg = f"{self.name} sensor not available (initialization failed)"
            logger.warning(error_msg)
            self._update_health_failure(error_msg, time.time())
            return self._get_fallback_value()
        
        start_time = time.time()
        last_exception = None
        
        for attempt in range(self.retry_attempts + 1):
            try:
                # Check timeout
                if timeout and (time.time() - start_time) > timeout:
                    raise TimeoutError(f"Sensor read timeout after {timeout}s")
                
                raw_data = self._read_raw_data()
                processed_data = self._process_raw_data(raw_data)
                
                # Success - update health and return
                self._update_health_success(time.time())
                logger.debug(f"{self.name} sensor read successful (attempt {attempt + 1})")
                return processed_data
                
            except Exception as e:
                last_exception = e
                logger.debug(f"{self.name} sensor read failed (attempt {attempt + 1}): {e}")
                
                # If not the last attempt, wait before retry
                if attempt < self.retry_attempts:
                    time.sleep(self.retry_delay * (attempt + 1))  # Exponential backoff
        
        # All attempts failed
        error_msg = f"{self.name} sensor failed after {self.retry_attempts + 1} attempts: {last_exception}"
        logger.error(error_msg)
        self._update_health_failure(str(last_exception), time.time())
        return self._get_fallback_value()
    
    async def read_async(self, timeout: Optional[float] = None) -> T:
        """
        Async version of sensor reading (non-blocking).
        
        Args:
            timeout: Optional timeout for the operation
            
        Returns:
            Sensor reading or fallback value on failure
        """
        return await asyncio.to_thread(self.read_with_retry, timeout)
    
    def is_available(self) -> bool:
        """
        Check if sensor is available and responsive.
        
        Returns:
            True if sensor is available, False otherwise
        """
        return self._ensure_initialized() and self.health.status != SensorStatus.FAILED
    
    def is_healthy(self) -> bool:
        """
        Check if sensor is in healthy state.
        
        Returns:
            True if sensor is healthy, False otherwise
        """
        return self.health.status == SensorStatus.HEALTHY
    
    def get_health_info(self) -> Dict[str, Any]:
        """
        Get comprehensive sensor health information.
        
        Returns:
            Dictionary with health metrics
        """
        uptime = None
        if self.health.last_success_time:
            uptime = time.time() - self.health.last_success_time
            
        error_age = None
        if self.health.last_error_time:
            error_age = time.time() - self.health.last_error_time
        
        success_rate = 0.0
        if self.health.total_readings > 0:
            success_rate = self.health.successful_readings / self.health.total_readings
        
        return {
            "name": self.name,
            "status": self.health.status.value,
            "available": self.is_available(),
            "last_success_ago_s": uptime,
            "last_error": self.health.last_error,
            "last_error_ago_s": error_age,
            "consecutive_failures": self.health.consecutive_failures,
            "success_rate": round(success_rate, 3),
            "total_readings": self.health.total_readings,
            "successful_readings": self.health.successful_readings
        }
    
    def reset_health(self) -> None:
        """Reset health tracking counters."""
        self.health = SensorHealth(status=SensorStatus.UNKNOWN)
        self._initialization_attempted = False
        logger.info(f"{self.name} sensor health reset")

class NumericSensor(BaseSensor[float]):
    """Base class for sensors that return numeric values."""
    
    def _get_fallback_value(self) -> float:
        """Return NaN for failed numeric readings."""
        return float('nan')
    
    def read_with_validation(self, min_value: Optional[float] = None, 
                           max_value: Optional[float] = None, 
                           timeout: Optional[float] = None) -> float:
        """
        Read sensor with value validation.
        
        Args:
            min_value: Minimum acceptable value
            max_value: Maximum acceptable value
            timeout: Optional timeout
            
        Returns:
            Validated sensor reading or NaN
        """
        value = self.read_with_retry(timeout)
        
        # Skip validation if value is already NaN/inf
        if not (value == value and value != float('inf') and value != float('-inf')):
            return value
        
        # Validate range
        if min_value is not None and value < min_value:
            logger.warning(f"{self.name} reading {value} below minimum {min_value}")
            return float('nan')
            
        if max_value is not None and value > max_value:
            logger.warning(f"{self.name} reading {value} above maximum {max_value}")
            return float('nan')
        
        return value

class BooleanSensor(BaseSensor[bool]):
    """Base class for sensors that return boolean values."""
    
    def _get_fallback_value(self) -> bool:
        """Return False for failed boolean readings."""
        return False