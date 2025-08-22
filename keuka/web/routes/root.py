# routes_root.py
# -----------------------------------------------------------------------------
# Root ("/") route that keeps compatibility with the original plaintext output:
#   "<tempF>,<distanceInches>" where NaNs are reported as 0.00/0.00 for legacy.
# -----------------------------------------------------------------------------

from flask import Blueprint, make_response
from sensors import read_temp_fahrenheit, median_distance_inches

root_bp = Blueprint("root", __name__)

@root_bp.route("/")
def root_plaintext():
    tF = read_temp_fahrenheit()
    dIn = median_distance_inches()
    tF_out = 0.0 if (tF != tF) else tF
    dIn_out = 0.0 if (dIn != dIn) else dIn
    resp = make_response(f"{tF_out:.2f},{dIn_out:.2f}")
    resp.mimetype = "text/plain"
    return resp
