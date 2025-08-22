# ultrasonic.py
# JSN-SR04T waterproof ultrasonic distance sensor interface

import time
from typing import Optional

# Default pin configuration (BCM numbering)
TRIG_PIN = 23
ECHO_PIN = 24
ULTRASONIC_TIMEOUT_S = 0.04
DEFAULT_SAMPLES = 11

# GPIO (allow import on dev machines without raising)
try:
    import RPi.GPIO as GPIO  # type: ignore
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

# Global setup state
_ultra_ready = False

def _ultra_setup():
    """Configure GPIO pins for ultrasonic sensor."""
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(TRIG_PIN, GPIO.OUT)
    GPIO.setup(ECHO_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    GPIO.output(TRIG_PIN, GPIO.LOW)
    time.sleep(0.1)

def _ensure_ultra():
    """Ensure ultrasonic sensor is set up (lazy initialization)."""
    global _ultra_ready
    if not _ultra_ready:
        _ultra_setup()
        _ultra_ready = True

def read_distance_inches(timeout_s: float = ULTRASONIC_TIMEOUT_S) -> float:
    """
    Single ultrasonic distance measurement in inches.
    
    Args:
        timeout_s: Maximum time to wait for echo response
        
    Returns:
        Distance in inches, or NaN on timeout/errors
    """
    _ensure_ultra()
    GPIO.output(TRIG_PIN, GPIO.LOW)
    time.sleep(0.000002)
    GPIO.output(TRIG_PIN, GPIO.HIGH)
    time.sleep(0.000010)
    GPIO.output(TRIG_PIN, GPIO.LOW)

    start = time.time()
    while GPIO.input(ECHO_PIN) == 0:
        if time.time() - start > timeout_s:
            return float('nan')

    t0 = time.time()
    while GPIO.input(ECHO_PIN) == 1:
        if time.time() - t0 > timeout_s:
            return float('nan')

    t1 = time.time()
    dt = t1 - t0
    # Speed of sound conversion constant (inches/sec) * delta time / 2 (round-trip)
    return (dt * 13503.9) / 2.0

def median_distance_inches(samples: int = DEFAULT_SAMPLES) -> float:
    """
    Median of multiple ultrasonic readings to reduce outliers.
    
    Args:
        samples: Number of readings to take for median calculation
        
    Returns:
        Median distance in inches, or NaN if no valid samples
    """
    vals = []
    for _ in range(samples):
        v = read_distance_inches()
        if not (v != v or v == float('inf')):  # filter NaN/inf
            vals.append(v)
        time.sleep(0.075)
    if not vals:
        return float('nan')
    vals.sort()
    return vals[len(vals)//2]

def is_available() -> bool:
    """Check if ultrasonic sensor hardware is available."""
    try:
        _ensure_ultra()
        return True
    except Exception:
        return False