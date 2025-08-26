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

import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Tuple

from .config import ADMIN_USER, ADMIN_PASS

def now() -> str:
    """Local server time string."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def utcnow_str() -> str:
    """UTC time string (used by health payload, converted to browser local)."""
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def basic_auth_ok(req) -> bool:
    """
    Validate HTTP Basic Auth credentials against env-configured admin user/pass.
    Call as: if not basic_auth_ok(request): return 401...
    """
    a = req.authorization
    return bool(a and a.username == ADMIN_USER and a.password == ADMIN_PASS)

def sh(cmd: list[str]) -> Tuple[int, str]:
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

def get_device_name() -> str:
    """
    Get the device name from configuration, with fallbacks.
    """
    # Try to read device configuration
    device_conf = Path("/home/pi/KeukaSensorProd/configuration/services/device.conf")
    try:
        if device_conf.exists():
            conf_text = device_conf.read_text(errors="replace")
            for line in conf_text.splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                m = re.match(r"device_name\s*=\s*(.*)$", s, re.IGNORECASE)
                if m:
                    device_name = m.group(1).strip().strip('"').strip("'")
                    if device_name:
                        return device_name
    except Exception:
        pass
    
    # Fallback to system hostname
    try:
        hostname = subprocess.getoutput("hostname").strip()
        if hostname and 'sensor' in hostname.lower():
            return hostname
    except Exception:
        pass
    
    # Last resort
    return "sensor1"

def get_system_fqdn() -> str:
    """
    Get the device name for display purposes.
    """
    return get_device_name()

def set_device_name(name: str) -> bool:
    """
    Set the device name in configuration file.
    """
    device_conf = Path("/home/pi/KeukaSensorProd/configuration/services/device.conf")
    try:
        # Validate device name (simple alphanumeric + underscore/dash)
        if not re.match(r'^[a-zA-Z0-9_-]+$', name):
            return False
        
        content = f"device_name={name}\n"
        device_conf.parent.mkdir(parents=True, exist_ok=True)
        device_conf.write_text(content, encoding="utf-8")
        return True
    except Exception:
        return False
