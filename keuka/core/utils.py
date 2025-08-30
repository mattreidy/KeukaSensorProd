# utils.py
# -----------------------------------------------------------------------------
# General-purpose utilities used across modules:
#  - time helpers
#  - shell execution wrapper
#  - atomic file writes
#  - basic auth check (request-agnostic: pass Flask request)
#  - text file read
#  - FQDN determination
# -----------------------------------------------------------------------------

from __future__ import annotations
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Tuple, List, Any

from .config import ADMIN_USER, ADMIN_PASS

def now() -> str:
    """Local server time string."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def utcnow_str() -> str:
    """UTC time string (used by health payload, converted to browser local)."""
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def basic_auth_ok(req: Any) -> bool:
    """
    Validate HTTP Basic Auth credentials against env-configured admin user/pass.
    Call as: if not basic_auth_ok(request): return 401...
    """
    a = req.authorization
    return bool(a and a.username == ADMIN_USER and a.password == ADMIN_PASS)

def sh(cmd: List[str]) -> Tuple[int, str]:
    """
    Run a shell command and return (exit_code, output).
    Captures both stdout and stderr into text.
    """
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        return 0, out
    except subprocess.CalledProcessError as e:
        return e.returncode, e.output

def read_text(path: Path) -> str:
    """Safe text file read; returns empty string on error."""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""

def write_text_atomic(path: Path, content: str, sudo_mv: bool = False) -> bool:
    """
    Write content to a temp file and move into place atomically.
    If sudo_mv is True, uses 'sudo mv' (for root-owned files).
    """
    tmp = Path("/tmp") / (path.name + ".new")
    tmp.write_text(content, encoding="utf-8")
    if sudo_mv:
        code, _ = sh(["sudo", "/bin/mv", str(tmp), str(path)])
        return code == 0
    else:
        tmp.replace(path)
        return True

def generate_hardware_sensor_id() -> str:
    """
    Generate hardware-based sensor ID that survives SD card cloning.
    ID is generated dynamically each time and is unique per hardware.
    """
    # Try Raspberry Pi CPU serial first (most reliable)
    try:
        with open('/proc/cpuinfo', 'r') as f:
            for line in f:
                if 'Serial' in line and ':' in line:
                    serial = line.split(':', 1)[1].strip()
                    if serial and len(serial) >= 8 and serial != '0000000000000000':
                        return f"sensor-{serial[-8:].lower()}"
    except Exception:
        pass
    
    # Try ethernet MAC address
    try:
        with open('/sys/class/net/eth0/address', 'r') as f:
            mac = f.read().strip()
            if mac and len(mac) >= 6:
                mac_clean = mac.replace(':', '').lower()
                return f"sensor-{mac_clean[-8:]}"
    except Exception:
        pass
    
    # Try first WiFi MAC address
    try:
        with open('/sys/class/net/wlan0/address', 'r') as f:
            mac = f.read().strip()
            if mac and len(mac) >= 6:
                mac_clean = mac.replace(':', '').lower()
                return f"sensor-{mac_clean[-8:]}"
    except Exception:
        pass
    
    # Final fallback - hash of hostname + warning
    try:
        import hashlib
        hostname = subprocess.getoutput("hostname").strip()
        if hostname:
            hash_id = hashlib.md5(hostname.encode()).hexdigest()[:8]
            return f"sensor-{hash_id}"
    except Exception:
        pass
    
    # Last resort with random component
    import time
    fallback_id = f"{int(time.time()) % 100000000:08x}"
    return f"sensor-{fallback_id}"

def get_device_name() -> str:
    """
    Get the hardware-generated device name. Always returns a unique sensor ID.
    """
    return generate_hardware_sensor_id()

def get_system_fqdn() -> str:
    """
    Get the device name for display purposes.
    """
    return get_device_name()

# set_device_name() removed - sensor names are now hardware-generated automatically
