# routes_root.py
# -----------------------------------------------------------------------------
# Root ("/") route that provides sensor data in plaintext format:
#   "<tempF>,<distanceInches>,<latitude>,<longitude>,<elevationFeet>,<fqdn>"
# NaNs are reported as 0.00 for legacy compatibility.
# FQDN is determined from system hostname.
# -----------------------------------------------------------------------------

from __future__ import annotations
from flask import Blueprint, make_response, Response
from ...sensors import read_temp_fahrenheit, median_distance_inches, read_gps_lat_lon_elev
from ...core.utils import get_system_fqdn
from ..common import safe_float_conversion, log_request

root_bp = Blueprint("root", __name__)

@root_bp.route("/")
def root_plaintext() -> Response:
    """Return sensor data in plaintext CSV format for legacy compatibility."""
    log_request("debug")
    
    try:
        # Read sensor data
        tF = read_temp_fahrenheit()
        dIn = median_distance_inches()
        lat, lon, elev_m = read_gps_lat_lon_elev()
        fqdn = get_system_fqdn()
        
        # Convert elevation from meters to feet (1 meter = 3.28084 feet)
        elev_ft = elev_m * 3.28084 if (elev_m == elev_m) else elev_m
        
        # Convert NaN to 0.0 for legacy compatibility using common utility
        tF_out = safe_float_conversion(tF)
        dIn_out = safe_float_conversion(dIn)
        lat_out = safe_float_conversion(lat)
        lon_out = safe_float_conversion(lon)
        elev_out = safe_float_conversion(elev_ft)
        
        # Create response
        data = f"{tF_out:.2f},{dIn_out:.2f},{lat_out:.6f},{lon_out:.6f},{elev_out:.2f},{fqdn}"
        resp = make_response(data)
        resp.mimetype = "text/plain"
        return resp
    
    except Exception as e:
        # Return error data in same format for legacy compatibility
        resp = make_response("0.00,0.00,0.000000,0.000000,0.00,error")
        resp.mimetype = "text/plain"
        return resp
