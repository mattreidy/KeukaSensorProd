# config.py
# -----------------------------------------------------------------------------
# Centralized configuration and constants for the Keuka Sensor app.
# - Environment variables can override sensible defaults.
# - Paths for system files and app data live here.
# - UI thresholds are here (warn/crit bands).
# -----------------------------------------------------------------------------

import os
from pathlib import Path

# Hardware config
TRIG_PIN = 23
ECHO_PIN = 24
ULTRASONIC_TIMEOUT_S = 0.03
SAMPLES = 7

# Camera config
CAMERA_INDEX = 0
FRAME_W, FRAME_H = 640, 480
MJPEG_JPEG_QUALITY = 70

# Auth config
ADMIN_USER = os.environ.get("KS_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("KS_ADMIN_PASS", "password")

# Paths / network interfaces
APP_DIR = Path(__file__).resolve().parent
WLAN_STA_IFACE = os.environ.get("KS_STA_IFACE", "wlan1")  # USB adapter (external antenna)
WLAN_AP_IFACE  = os.environ.get("KS_AP_IFACE",  "wlan0")  # onboard AP
WPA_SUP_CONF = Path("/etc/wpa_supplicant/wpa_supplicant-" + WLAN_STA_IFACE + ".conf")

DHCPCD_CONF = Path("/etc/dhcpcd.conf")
DHCPCD_MARK_BEGIN = "# KS-STATIC-BEGIN"
DHCPCD_MARK_END   = "# KS-STATIC-END"

# UI thresholds
TEMP_WARN_F = float(os.environ.get("KS_TEMP_WARN_F", "85"))
TEMP_CRIT_F = float(os.environ.get("KS_TEMP_CRIT_F", "95"))
RSSI_WARN_DBM = int(os.environ.get("KS_RSSI_WARN_DBM", "-70"))
RSSI_CRIT_DBM = int(os.environ.get("KS_RSSI_CRIT_DBM", "-80"))
CPU_TEMP_WARN_C = float(os.environ.get("KS_CPU_WARN_C", "75"))
CPU_TEMP_CRIT_C = float(os.environ.get("KS_CPU_CRIT_C", "85"))

# Tunnel configuration
KEUKA_SERVER_URL = os.environ.get('KEUKA_SERVER_URL', 'https://keuka.org')
TUNNEL_ENABLED = os.environ.get('TUNNEL_ENABLED', 'true').lower() == 'true'

# Always use hardware-generated sensor name (ignoring any environment SENSOR_NAME)
try:
    from .utils import generate_hardware_sensor_id
    SENSOR_NAME = generate_hardware_sensor_id()
except Exception:
    # Fallback to environment variable if hardware naming fails
    SENSOR_NAME = os.environ.get('SENSOR_NAME')
    if not SENSOR_NAME:
        # Ultimate fallback
        SENSOR_NAME = "sensor-unknown"

# App
VERSION = "V5.12"


