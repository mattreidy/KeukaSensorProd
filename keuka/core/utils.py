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

def get_system_fqdn() -> str:
    """
    Get the system's FQDN, preferring DuckDNS domain if configured,
    otherwise falling back to system hostname.
    """
    # Try to read DuckDNS configuration
    duckdns_conf = Path("/home/pi/KeukaSensorProd/configuration/services/duckdns.conf")
    try:
        if duckdns_conf.exists():
            conf_text = duckdns_conf.read_text(errors="replace")
            for line in conf_text.splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                m = re.match(r"domains\s*=\s*(.*)$", s, re.IGNORECASE)
                if m:
                    domains = m.group(1).strip().strip('"').strip("'")
                    if domains:
                        # Take first domain if multiple are listed
                        first_domain = domains.split(",")[0].strip()
                        if first_domain:
                            # Add .duckdns.org if not already present
                            if not first_domain.endswith(".duckdns.org"):
                                first_domain += ".duckdns.org"
                            return first_domain
    except Exception:
        pass
    
    # Fallback to system hostname
    try:
        hostname = subprocess.getoutput("hostname").strip()
        if hostname:
            return hostname
    except Exception:
        pass
    
    # Last resort
    return "unknown"
