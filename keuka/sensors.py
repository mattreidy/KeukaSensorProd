# sensors.py
# -----------------------------------------------------------------------------
# Physical sensor access:
#  - Ultrasonic (JSN-SR04T) distance in inches (median of N samples).
#  - DS18B20 temperature in °F via w1thermsensor or /sys fallback.
#  - GPIO import is optional; a dummy GPIO is used on dev machines.
# -----------------------------------------------------------------------------

import os
import time

# --- Hard-coded GPIO pins (BCM numbering) ---
TRIG_PIN = 23            # Ultrasonic trigger pin
ECHO_PIN = 24            # Ultrasonic echo pin
TEMP_PIN = 6             # DS18B20 data pin (requires 1-Wire enabled in /boot/config.txt)
ULTRASONIC_TIMEOUT_S = 0.04
SAMPLES = 5

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

# DS18B20 via w1thermsensor if present
try:
    from w1thermsensor import W1ThermSensor  # type: ignore
except Exception:
    W1ThermSensor = None

# --- Ultrasonic setup (lazy) ---
_ultra_ready = False

def _ultra_setup():
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(TRIG_PIN, GPIO.OUT)
    GPIO.setup(ECHO_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    GPIO.output(TRIG_PIN, GPIO.LOW)
    time.sleep(0.1)

def _ensure_ultra():
    global _ultra_ready
    if not _ultra_ready:
        _ultra_setup()
        _ultra_ready = True

def read_distance_inches(timeout_s: float = ULTRASONIC_TIMEOUT_S) -> float:
    """Single ultrasonic read; returns NaN on timeout/errors."""
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

def median_distance_inches(samples: int = SAMPLES) -> float:
    """Median of N ultrasonic reads to reduce outliers; NaN if no valid samples."""
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

def read_temp_fahrenheit() -> float:
    """
    Read DS18B20 temperature (°F).
    Tries w1thermsensor first; falls back to /sys/bus/w1/devices/*/w1_slave.
    Returns NaN if no sensor found.
    """
    try:
        if W1ThermSensor is not None:
            ss = W1ThermSensor.get_available_sensors()
            if ss:
                c = ss[0].get_temperature()
                return c * 9.0 / 5.0 + 32.0
    except Exception:
        pass

    # /sys fallback
    base = '/sys/bus/w1/devices'
    try:
        dev = next((d for d in os.listdir(base) if d.startswith('28-')))
        with open(os.path.join(base, dev, 'w1_slave'), 'r') as f:
            data = f.read()
        if 'YES' in data:
            c = float(data.strip().split('t=')[-1]) / 1000.0
            return c * 9.0 / 5.0 + 32.0
    except Exception:
        pass

    return float('nan')
