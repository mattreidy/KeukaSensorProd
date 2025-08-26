# routes_root.py
# -----------------------------------------------------------------------------
# Root ("/") route that provides sensor data in plaintext format:
#   "<tempF>,<distanceInches>,<latitude>,<longitude>,<elevationFeet>,<fqdn>"
# NaNs are reported as 0.00 for legacy compatibility.
# FQDN is determined from DuckDNS configuration or system hostname.
# -----------------------------------------------------------------------------

from flask import Blueprint, make_response
from ...sensors import read_temp_fahrenheit, median_distance_inches, read_gps_lat_lon_elev
from ...core.utils import get_system_fqdn

root_bp = Blueprint("root", __name__)

@root_bp.route("/")
def root_plaintext():
    tF = read_temp_fahrenheit()
    dIn = median_distance_inches()
    lat, lon, elev_m = read_gps_lat_lon_elev()
    fqdn = get_system_fqdn()
    
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
