# routes_root.py
# -----------------------------------------------------------------------------
# Root ("/") route that provides sensor data in plaintext format:
#   "<tempF>,<distanceInches>,<latitude>,<longitude>,<elevationFeet>,<fqdn>"
# NaNs are reported as 0.00 for legacy compatibility.
# FQDN is determined from DuckDNS configuration or system hostname.
# -----------------------------------------------------------------------------

import re
import subprocess
from pathlib import Path
from flask import Blueprint, make_response
from ...sensors import read_temp_fahrenheit, median_distance_inches, read_gps_lat_lon_elev

root_bp = Blueprint("root", __name__)

def _get_fqdn() -> str:
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

@root_bp.route("/")
def root_plaintext():
    tF = read_temp_fahrenheit()
    dIn = median_distance_inches()
    lat, lon, elev_m = read_gps_lat_lon_elev()
    fqdn = _get_fqdn()
    
    # Convert elevation from meters to feet (1 meter = 3.28084 feet)
    elev_ft = elev_m * 3.28084 if (elev_m == elev_m) else elev_m
    
    # Convert NaN to 0.0 for legacy compatibility
    tF_out = 0.0 if (tF != tF) else tF
    dIn_out = 0.0 if (dIn != dIn) else dIn
    lat_out = 0.0 if (lat != lat) else lat
    lon_out = 0.0 if (lon != lon) else lon
    elev_out = 0.0 if (elev_ft != elev_ft) else elev_ft
    
    resp = make_response(f"{tF_out:.2f},{dIn_out:.2f},{lat_out:.6f},{lon_out:.6f},{elev_out:.2f},{fqdn}")
    resp.mimetype = "text/plain"
    return resp
