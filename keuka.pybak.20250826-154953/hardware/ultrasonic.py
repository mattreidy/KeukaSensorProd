# ultrasonic.py
# JSN-SR04T waterproof ultrasonic distance sensor interface

import time
import logging
from typing import Optional

from .base_sensor import NumericSensor

logger = logging.getLogger(__name__)

# Default pin configuration (BCM numbering)
TRIG_PIN = 23
ECHO_PIN = 24
ULTRASONIC_TIMEOUT_S = 0.04
DEFAULT_SAMPLES = 11

# GPIO (allow import on dev machines without raising)
try:
    import RPi.GPIO as GPIO  # type: ignore
    _GPIO_AVAILABLE = True
except Exception:
    class _DummyGPIO:
        BCM = BOARD = IN = OUT = LOW = HIGH = PUD_DOWN = PUD_UP = None
        def setmode(self, *a, **k): pass
        def setwarnings(self, *a, **k): pass
        def setup(self, *a, **k): pass
        def output(self, *a, **k): pass
        def input(self, *a, **k): return 0
        def cleanup(self): pass
    GPIO = _DummyGPIO()
    _GPIO_AVAILABLE = False

class UltrasonicSensor(NumericSensor):
    """
    JSN-SR04T waterproof ultrasonic distance sensor with proper error handling.
    """
    
    def __init__(self, trig_pin: int = TRIG_PIN, echo_pin: int = ECHO_PIN, 
                 timeout_s: float = ULTRASONIC_TIMEOUT_S):
        super().__init__(name="JSN-SR04T Ultrasonic", retry_attempts=2, retry_delay=0.1)
        self.trig_pin = trig_pin
        self.echo_pin = echo_pin
        self.timeout_s = timeout_s
        self._gpio_initialized = False
    
    def _initialize_hardware(self) -> bool:
        """Initialize GPIO pins for ultrasonic sensor."""
        if not _GPIO_AVAILABLE:
            logger.warning("RPi.GPIO not available (development mode)")
            return False
            
        try:
            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.trig_pin, GPIO.OUT)
            GPIO.setup(self.echo_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            GPIO.output(self.trig_pin, GPIO.LOW)
            time.sleep(0.1)  # Settle time
            self._gpio_initialized = True
            logger.info(f"Ultrasonic sensor initialized (TRIG={self.trig_pin}, ECHO={self.echo_pin})")
            return True
        except Exception as e:
            logger.error(f"Ultrasonic GPIO initialization failed: {e}")
            return False
    
    def _read_raw_data(self) -> float:
        """Perform single ultrasonic distance measurement."""
        if not self._gpio_initialized:
            raise RuntimeError("GPIO not initialized")
        
        # Send trigger pulse
        GPIO.output(self.trig_pin, GPIO.LOW)
        time.sleep(0.000002)  # 2μs
        GPIO.output(self.trig_pin, GPIO.HIGH)
        time.sleep(0.000010)  # 10μs
        GPIO.output(self.trig_pin, GPIO.LOW)

        # Wait for echo start
        start = time.time()
        while GPIO.input(self.echo_pin) == 0:
            if time.time() - start > self.timeout_s:
                raise TimeoutError("Echo start timeout")

        # Measure echo duration
        echo_start = time.time()
        while GPIO.input(self.echo_pin) == 1:
            if time.time() - echo_start > self.timeout_s:
                raise TimeoutError("Echo end timeout")

        echo_end = time.time()
        duration = echo_end - echo_start
        
        # Convert to distance (speed of sound = 343 m/s, round trip)
        distance_inches = (duration * 13503.9) / 2.0
        return distance_inches
    
    def _process_raw_data(self, raw_data: float) -> float:
        """Process and validate distance reading."""
        # Validate reasonable distance range (2cm to 4m for JSN-SR04T)
        if not (0.8 <= raw_data <= 157.5):  # inches
            raise ValueError(f"Distance reading {raw_data} inches out of valid range (0.8-157.5)")
        return raw_data
    
    def read_distance_inches(self) -> float:
        """Read distance in inches."""
        return self.read_with_validation(min_value=0.8, max_value=157.5)
    
    async def read_distance_inches_async(self) -> float:
        """Read distance in inches asynchronously."""
        value = await self.read_async()
        # Apply validation
        if not (0.8 <= value <= 157.5):
            return float('nan')
        return value
    
    def read_median_distance(self, samples: int = DEFAULT_SAMPLES) -> float:
        """Read median of multiple distance measurements to reduce outliers."""
        values = []
        for _ in range(samples):
            try:
                value = self.read_distance_inches()
                if value == value and value != float('inf'):  # Not NaN or inf
                    values.append(value)
                time.sleep(0.075)  # Brief delay between samples
            except Exception as e:
                logger.debug(f"Sample read failed: {e}")
                continue
        
        if not values:
            logger.warning("No valid ultrasonic samples obtained")
            return float('nan')
        
        values.sort()
        median = values[len(values) // 2]
        logger.debug(f"Ultrasonic median: {median} inches from {len(values)} samples")
        return median
    
    async def read_median_distance_async(self, samples: int = DEFAULT_SAMPLES) -> float:
        """Read median distance asynchronously."""
        import asyncio
        
        tasks = []
        for _ in range(samples):
            task = asyncio.create_task(self.read_distance_inches_async())
            tasks.append(task)
        
        values = []
        for task in tasks:
            try:
                value = await task
                if value == value and value != float('inf'):  # Not NaN or inf
                    values.append(value)
            except Exception as e:
                logger.debug(f"Async sample failed: {e}")
        
        if not values:
            return float('nan')
        
        values.sort()
        return values[len(values) // 2]

# Create global sensor instance
_ultrasonic_sensor = UltrasonicSensor()

# Legacy functions - now handled by UltrasonicSensor class

def read_distance_inches(timeout_s: float = ULTRASONIC_TIMEOUT_S) -> float:
    """
    Single ultrasonic distance measurement in inches.
    
    Legacy function for backward compatibility.
    
    Args:
        timeout_s: Maximum time to wait for echo response
        
    Returns:
        Distance in inches, or NaN on timeout/errors
    """
    return _ultrasonic_sensor.read_distance_inches()

def median_distance_inches(samples: int = DEFAULT_SAMPLES) -> float:
    """
    Median of multiple ultrasonic readings to reduce outliers.
    
    Legacy function for backward compatibility.
    
    Args:
        samples: Number of readings to take for median calculation
        
    Returns:
        Median distance in inches, or NaN if no valid samples
    """
    return _ultrasonic_sensor.read_median_distance(samples)

def is_available() -> bool:
    """Check if ultrasonic sensor hardware is available."""
    return _ultrasonic_sensor.is_available()

# Async functions for non-blocking operations
async def read_distance_inches_async() -> float:
    """Read distance in inches asynchronously."""
    return await _ultrasonic_sensor.read_distance_inches_async()

async def median_distance_inches_async(samples: int = DEFAULT_SAMPLES) -> float:
    """Read median distance asynchronously."""
    return await _ultrasonic_sensor.read_median_distance_async(samples)