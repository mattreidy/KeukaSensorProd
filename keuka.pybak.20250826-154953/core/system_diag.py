# system_diag.py
# -----------------------------------------------------------------------------
# System diagnostics:
#  - CPU temperature (thermal zone or vcgencmd).
#  - Uptime (seconds).
#  - Root filesystem disk usage (total/used/free/%).
#  - Memory usage from /proc/meminfo (total/used/free/%).
# -----------------------------------------------------------------------------

import shutil
from datetime import timedelta, datetime

from .utils import sh

def cpu_temp_c() -> float:
    """CPU temperature in °C (NaN on error)."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            v = f.read().strip()
            return float(v) / 1000.0
    except Exception:
        pass
    code, out = sh(["/usr/bin/vcgencmd", "measure_temp"])
    if code == 0 and "temp=" in out:
        try:
            return float(out.split("temp=")[1].split("'")[0])
        except Exception:
            pass
    return float('nan')

def uptime_seconds() -> float:
    """Seconds since boot."""
    try:
        with open("/proc/uptime", "r") as f:
            return float(f.read().split()[0])
    except Exception:
        return 0.0

def disk_usage_root() -> dict:
    """Disk usage for '/'. Returns bytes and percent."""
    try:
        total, used, free = shutil.disk_usage("/")
        pct = (used / total * 100.0) if total > 0 else 0.0
        return {"total": total, "used": used, "free": free, "percent": round(pct, 1)}
    except Exception:
        return {"total": 0, "used": 0, "free": 0, "percent": 0.0}

def mem_usage() -> dict:
    """Parse /proc/meminfo to estimate used/available memory."""
    try:
        m = {}
        with open("/proc/meminfo", "r") as f:
            for ln in f:
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    m[k.strip()] = v.strip()
        def kib_to_bytes(s: str) -> int:
            try:
                return int(s.split()[0]) * 1024
            except Exception:
                return 0
        total = kib_to_bytes(m.get("MemTotal", "0 kB"))
        avail = kib_to_bytes(m.get("MemAvailable", "0 kB"))
        used = total - avail
        pct = (used / total * 100.0) if total > 0 else 0.0
        return {"total": total, "used": used, "free": avail, "percent": round(pct, 1)}
    except Exception:
        return {"total": 0, "used": 0, "free": 0, "percent": 0.0}
