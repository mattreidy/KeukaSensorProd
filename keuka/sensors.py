# sensors.py
# -----------------------------------------------------------------------------
# Physical sensor access:
#  - Ultrasonic (JSN-SR04T) distance in inches (median of N samples).
#  - DS18B20 temperature in °F via w1thermsensor or /sys fallback.
#  - Optional GPS (NEO-6M / UART NMEA) for latitude, longitude, elevation.
#  - GPIO import is optional; a dummy GPIO is used on dev machines.
# -----------------------------------------------------------------------------

import os
import time
from typing import Optional, Tuple, Dict, Any

# --- Hard-coded GPIO pins (BCM numbering) ---
TRIG_PIN = 23            # Ultrasonic trigger pin
ECHO_PIN = 24            # Ultrasonic echo pin
TEMP_PIN = 6             # DS18B20 data pin (requires 1-Wire enabled in /boot/config.txt)

# GPS hardware UART pins (BCM numbering):
#   GPS TX → Pi RXD0 (GPIO15, physical pin 10)
#   GPS RX → Pi TXD0 (GPIO14, physical pin 8)
# These are documented for wiring clarity only; the code talks to /dev/serial0.
GPS_PI_RX_PIN = 15       # Pi RX (connects to GPS TX)
GPS_PI_TX_PIN = 14       # Pi TX (connects to GPS RX)

ULTRASONIC_TIMEOUT_S = 0.04
SAMPLES = 11

# --- GPS defaults (override via env if desired) ---
# On Raspberry Pi, /dev/serial0 points to the primary UART (ttyAMA0/ttyS0 depending on model).
GPS_PORT = os.environ.get("GPS_PORT", "/dev/serial0")
GPS_BAUD = int(os.environ.get("GPS_BAUD", "9600"))  # NEO-6M default is 9600
GPS_READ_TIMEOUT_S = float(os.environ.get("GPS_READ_TIMEOUT_S", "0.5"))  # serial read timeout
GPS_SESSION_DURATION_S = float(os.environ.get("GPS_SESSION_DURATION_S", "2.0"))  # how long to listen per call

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

# Serial (pyserial) for GPS if present
try:
    import serial  # type: ignore
except Exception:
    serial = None  # type: ignore

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

# -----------------------------------------------------------------------------
# GPS (NEO-6M / NMEA over UART)
# -----------------------------------------------------------------------------
# This module does NOT spawn background threads; it reads a short burst
# of NMEA lines whenever you call read_gps_snapshot() or read_gps_lat_lon_elev().
#
# Wiring (3.3V logic; powering GPS at 3.3V as you specified):
#   GPS VCC → Pi 3.3V (physical pin 1 or 17)
#   GPS GND → Pi GND (e.g., pin 6/9/14/20/25/30/34/39)
#   GPS TX  → Pi RXD0 (GPIO15, physical pin 10)
#   GPS RX  → Pi TXD0 (GPIO14, physical pin 8)
#
# Ensure Linux serial is enabled and console is disabled on the UART you use.
# On Raspberry Pi OS, /dev/serial0 usually points to the primary UART.

_gps_ser = None  # type: ignore
_gps_last_fix: Optional[Dict[str, Any]] = None  # cache last good fix in this process

def _gps_open() -> None:
    """Lazy-open the GPS serial port."""
    global _gps_ser
    if _gps_ser is not None:
        return
    if serial is None:
        # pyserial not available; leave _gps_ser as None
        return
    try:
        _gps_ser = serial.Serial(
            GPS_PORT,
            GPS_BAUD,
            timeout=GPS_READ_TIMEOUT_S
        )
        # Give module a brief moment after opening the port
        time.sleep(0.1)
    except Exception:
        _gps_ser = None

def _nmea_checksum_ok(line: str) -> bool:
    """
    Validate NMEA checksum. Input should be the whole line without trailing CR/LF.
    Example: '$GPGGA,...*47'
    """
    try:
        if not line.startswith("$") or "*" not in line:
            return False
        data, cks = line[1:].split("*", 1)
        calc = 0
        for ch in data:
            calc ^= ord(ch)
        return int(cks.strip(), 16) == calc
    except Exception:
        return False

def _parse_lat_lon(lat: str, lat_hemi: str, lon: str, lon_hemi: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Convert NMEA lat/lon to decimal degrees.
    lat is ddmm.mmmm, lon is dddmm.mmmm.
    """
    def _to_deg(raw: str, is_lon: bool) -> Optional[float]:
        if not raw or raw == "0" or raw == "0.0":
            return None
        try:
            if is_lon:
                deg = int(raw[0:3])
                mins = float(raw[3:])
            else:
                deg = int(raw[0:2])
                mins = float(raw[2:])
            return deg + mins / 60.0
        except Exception:
            return None

    lat_dd = _to_deg(lat, is_lon=False)
    lon_dd = _to_deg(lon, is_lon=True)

    if lat_dd is not None and lat_hemi in ("S", "s"):
        lat_dd = -lat_dd
    if lon_dd is not None and lon_hemi in ("W", "w"):
        lon_dd = -lon_dd

    return lat_dd, lon_dd

def _parse_gga(fields: list) -> Optional[Dict[str, Any]]:
    """
    Parse GGA:
    $xxGGA,1:UTC,2:lat,3:N/S,4:lon,5:E/W,6:fixq,7:num_sats,8:HDOP,9:alt,10:M,11:geoid,12:M,13:age,14:station*CS
    """
    try:
        # Ensure we have required fields
        if len(fields) < 15:
            return None
        utc = fields[1]
        lat, lat_h = fields[2], fields[3]
        lon, lon_h = fields[4], fields[5]
        fixq = int(fields[6]) if fields[6] else 0
        num_sats = int(fields[7]) if fields[7] else 0
        hdop = float(fields[8]) if fields[8] else float('nan')
        alt = float(fields[9]) if fields[9] else float('nan')

        if fixq == 0:
            # no fix
            return None

        lat_dd, lon_dd = _parse_lat_lon(lat, lat_h, lon, lon_h)
        if lat_dd is None or lon_dd is None:
            return None

        return {
            "type": "GGA",
            "utc": utc,
            "lat": lat_dd,
            "lon": lon_dd,
            "alt_m": alt,     # meters
            "fix_quality": fixq,
            "num_sats": num_sats,
            "hdop": hdop,
        }
    except Exception:
        return None

def _parse_rmc(fields: list) -> Optional[Dict[str, Any]]:
    """
    Parse RMC:
    $xxRMC,1:UTC,2:status(A/V),3:lat,4:N/S,5:lon,6:E/W,7:speed(knots),8:track,9:date,10:magvar,11:E/W*CS
    """
    try:
        if len(fields) < 12:
            return None
        status = fields[2]
        if status != "A":
            return None  # not valid
        utc = fields[1]
        lat, lat_h = fields[3], fields[4]
        lon, lon_h = fields[5], fields[6]
        spd_knots = float(fields[7]) if fields[7] else 0.0
        track = float(fields[8]) if fields[8] else float('nan')
        date = fields[9]

        lat_dd, lon_dd = _parse_lat_lon(lat, lat_h, lon, lon_h)
        if lat_dd is None or lon_dd is None:
            return None

        return {
            "type": "RMC",
            "utc": utc,
            "date": date,
            "lat": lat_dd,
            "lon": lon_dd,
            "speed_knots": spd_knots,
            "track_deg": track,
        }
    except Exception:
        return None

def _read_nmea_lines(duration_s: float) -> Dict[str, Dict[str, Any]]:
    """
    Read NMEA lines for up to duration_s and return the latest parsed
    GGA and RMC dicts we saw (if any). Requires pyserial and working UART.
    """
    _gps_open()
    results: Dict[str, Dict[str, Any]] = {}
    if _gps_ser is None:
        return results

    end = time.time() + duration_s
    buf = b""

    while time.time() < end:
        try:
            chunk = _gps_ser.read(128)
            if not chunk:
                continue
            buf += chunk
            # split on CR/LF
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip().decode(errors="ignore")
                if not line or not line.startswith("$"):
                    continue
                # Validate checksum
                # Strip trailing CR if present
                if line.endswith("\r"):
                    line = line[:-1]
                if not _nmea_checksum_ok(line):
                    continue
                # Remove leading '$' and split out fields (exclude checksum part)
                core = line[1:].split("*", 1)[0]
                parts = core.split(",")
                talker = parts[0]  # e.g., GPGGA, GPRMC, GNGGA ...
                fields = parts

                if talker.endswith("GGA"):
                    gga = _parse_gga(fields)
                    if gga:
                        results["GGA"] = gga  # keep latest
                elif talker.endswith("RMC"):
                    rmc = _parse_rmc(fields)
                    if rmc:
                        results["RMC"] = rmc  # keep latest
        except Exception:
            # swallow serial errors and continue trying
            pass

    return results

def read_gps_snapshot(duration_s: float = GPS_SESSION_DURATION_S) -> Dict[str, Any]:
    """
    Listen to the GPS for up to duration_s seconds and return a snapshot dict.
    Keys may include:
      - 'lat', 'lon', 'alt_m', 'fix_quality', 'num_sats', 'hdop'  (from GGA)
      - 'speed_knots', 'track_deg', 'utc', 'date'                 (from RMC)
    If no valid fix is found, returns {}.
    Caches the last good fix in this process as _gps_last_fix.
    """
    global _gps_last_fix
    res = _read_nmea_lines(duration_s)
    snapshot: Dict[str, Any] = {}

    # Prefer GGA for elevation/fix quality; merge RMC if present
    gga = res.get("GGA")
    rmc = res.get("RMC")

    if gga:
        snapshot.update(gga)
    if rmc:
        # Do not overwrite lat/lon with RMC if we already have from GGA,
        # but if GGA missing, use RMC lat/lon.
        for k, v in rmc.items():
            if k in ("lat", "lon"):
                if "lat" not in snapshot or "lon" not in snapshot:
                    snapshot[k] = v
            else:
                snapshot[k] = v

    # Update cache if we have at least lat/lon
    if "lat" in snapshot and "lon" in snapshot:
        _gps_last_fix = snapshot
        return snapshot

    # If no fresh fix, return the last known fix (if any)
    return _gps_last_fix or {}

def read_gps_lat_lon_elev(duration_s: float = GPS_SESSION_DURATION_S) -> Tuple[float, float, float]:
    """
    Convenience: returns (lat_dd, lon_dd, alt_m). If any are unavailable,
    returns NaN for that value. Uses a short listen window per call, then
    falls back to last known fix if needed.
    """
    snap = read_gps_snapshot(duration_s)
    lat = float('nan')
    lon = float('nan')
    alt = float('nan')
    try:
        if "lat" in snap:
            lat = float(snap["lat"])
        if "lon" in snap:
            lon = float(snap["lon"])
        if "alt_m" in snap:
            alt = float(snap["alt_m"])
    except Exception:
        pass
    return (lat, lon, alt)

def gps_available() -> bool:
    """
    Quick check: returns True if pyserial is available and the GPS port can be opened.
    Note: This opens the port lazily and keeps it open for subsequent calls.
    """
    _gps_open()
    return _gps_ser is not None
