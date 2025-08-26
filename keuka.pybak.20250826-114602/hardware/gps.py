# gps.py
# NEO-6M GPS module interface via UART/NMEA

import os
import time
from typing import Optional, Tuple, Dict, Any

# GPS hardware UART pins (BCM numbering):
#   GPS TX → Pi RXD0 (GPIO15, physical pin 10)
#   GPS RX → Pi TXD0 (GPIO14, physical pin 8)
GPS_PI_RX_PIN = 15       # Pi RX (connects to GPS TX)
GPS_PI_TX_PIN = 14       # Pi TX (connects to GPS RX)

# GPS configuration (override via environment variables)
GPS_PORT = os.environ.get("GPS_PORT", "/dev/serial0")
GPS_BAUD = int(os.environ.get("GPS_BAUD", "9600"))  # NEO-6M default
GPS_READ_TIMEOUT_S = float(os.environ.get("GPS_READ_TIMEOUT_S", "0.5"))
GPS_SESSION_DURATION_S = float(os.environ.get("GPS_SESSION_DURATION_S", "2.0"))

# Serial (pyserial) for GPS if present
try:
    import serial  # type: ignore
except Exception:
    serial = None  # type: ignore

# Global state
_gps_ser = None  # type: ignore
_gps_last_fix: Optional[Dict[str, Any]] = None  # cache last good fix

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
    Validate NMEA checksum.
    
    Args:
        line: NMEA sentence (e.g., '$GPGGA,...*47')
        
    Returns:
        True if checksum is valid, False otherwise
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
    
    Args:
        lat: Latitude in ddmm.mmmm format
        lat_hemi: Hemisphere (N/S)
        lon: Longitude in dddmm.mmmm format  
        lon_hemi: Hemisphere (E/W)
        
    Returns:
        Tuple of (latitude_dd, longitude_dd) or (None, None) if invalid
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
    Parse GGA NMEA sentence.
    
    Format: $xxGGA,1:UTC,2:lat,3:N/S,4:lon,5:E/W,6:fixq,7:num_sats,8:HDOP,9:alt,10:M,11:geoid,12:M,13:age,14:station*CS
    
    Returns:
        Dictionary with parsed data or None if invalid
    """
    try:
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
    Parse RMC NMEA sentence.
    
    Format: $xxRMC,1:UTC,2:status(A/V),3:lat,4:N/S,5:lon,6:E/W,7:speed(knots),8:track,9:date,10:magvar,11:E/W*CS
    
    Returns:
        Dictionary with parsed data or None if invalid
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
    Read NMEA lines for specified duration and return parsed results.
    
    Args:
        duration_s: How long to listen for NMEA data
        
    Returns:
        Dictionary with latest parsed GGA and RMC data
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
    Listen to GPS for specified duration and return a snapshot.
    
    Args:
        duration_s: How long to listen for GPS data
        
    Returns:
        Dictionary with GPS data including lat, lon, alt_m, fix_quality,
        num_sats, hdop, speed_knots, track_deg, utc, date
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
    Convenience function to get GPS coordinates and elevation.
    
    Args:
        duration_s: How long to listen for GPS data
        
    Returns:
        Tuple of (latitude_dd, longitude_dd, altitude_m). NaN for unavailable values.
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

def is_available() -> bool:
    """
    Check if GPS module is available.
    
    Returns:
        True if pyserial is available and GPS port can be opened
    """
    _gps_open()
    return _gps_ser is not None

def get_last_fix() -> Optional[Dict[str, Any]]:
    """
    Get the last cached GPS fix without attempting new reading.
    
    Returns:
        Last GPS fix dictionary or None if no fix cached
    """
    return _gps_last_fix

def clear_last_fix() -> None:
    """Clear the cached GPS fix."""
    global _gps_last_fix
    _gps_last_fix = None