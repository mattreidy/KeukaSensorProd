# sensors.py
# Backward compatibility wrapper for hardware modules
# This file provides the same interface as the original sensors.py
# but delegates to the new modular hardware components.

from .hardware.ultrasonic import (
    read_distance_inches,
    median_distance_inches,
    TRIG_PIN,
    ECHO_PIN,
    ULTRASONIC_TIMEOUT_S,
    DEFAULT_SAMPLES as SAMPLES
)

from .hardware.temperature import (
    read_temp_fahrenheit,
    read_temp_celsius
)

from .hardware.gps import (
    read_gps_snapshot,
    read_gps_lat_lon_elev,
    is_available as gps_available,
    GPS_PORT,
    GPS_BAUD,
    GPS_READ_TIMEOUT_S,
    GPS_SESSION_DURATION_S,
    GPS_PI_RX_PIN,
    GPS_PI_TX_PIN
)

# Maintain original pin constant for legacy code
TEMP_PIN = 6

# Re-export functions with original names for backward compatibility
__all__ = [
    'read_distance_inches',
    'median_distance_inches', 
    'read_temp_fahrenheit',
    'read_gps_snapshot',
    'read_gps_lat_lon_elev',
    'gps_available',
    'TRIG_PIN',
    'ECHO_PIN', 
    'TEMP_PIN',
    'GPS_PI_RX_PIN',
    'GPS_PI_TX_PIN',
    'ULTRASONIC_TIMEOUT_S',
    'GPS_PORT',
    'GPS_BAUD',
    'GPS_READ_TIMEOUT_S', 
    'GPS_SESSION_DURATION_S',
    'SAMPLES'
]