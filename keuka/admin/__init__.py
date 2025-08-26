# keuka/admin/__init__.py
# -----------------------------------------------------------------------------
# Admin package entrypoint.
#
# Goals:
#   - Keep the existing public interface stable:
#       * Blueprint is still named "admin"
#       * All routes/paths/HTML/JS remain the same
#       * routes_admin.py continues to expose `admin_bp`
#   - Split the big module into smaller concerns:
#       * auth guard (before_app_request) that protects /admin/**
#       * Wi-Fi pages & APIs
#       * Update page & code-only updater APIs
#       * WAN IP helper API
#
# Implementation notes:
#   - We use a SINGLE blueprint (`admin_bp = Blueprint("admin", __name__)`).
#     Each submodule exposes `attach(bp)` to register its routes on this bp.
#   - The before_app_request auth guard lives here, so it also protects
#   - Nothing else in the app needs to change. routes_admin.py will import
#     `admin_bp` from here and re-export it.
# -----------------------------------------------------------------------------

from __future__ import annotations

from flask import Blueprint, request, Response
import json

from ..config import ADMIN_USER, ADMIN_PASS

# Create the single blueprint that keeps the original name.
admin_bp = Blueprint("admin", __name__)

# ---- BASIC AUTH GUARD --------------------------------------------------------
# We enforce HTTP Basic Auth for /admin/** exactly like the old monolithic 
# file did.

def _unauthorized_json() -> Response:
    return Response(
        json.dumps({"ok": False, "error": "unauthorized"}),
        401,
        {
            "WWW-Authenticate": 'Basic realm="Keuka Admin", charset="UTF-8"',
            "Content-Type": "application/json; charset=utf-8",
        },
    )

def _unauthorized_text() -> Response:
    return Response(
        "Authentication required.\n",
        401,
        {
            "WWW-Authenticate": 'Basic realm="Keuka Admin", charset="UTF-8"',
            "Content-Type": "text/plain; charset=utf-8",
        },
    )

def _is_admin_path(path: str) -> bool:
    return path.startswith("/admin")

@admin_bp.before_app_request
def _protect_admin():
    """
    Enforce HTTP Basic Auth for /admin/** using ADMIN_USER/ADMIN_PASS.
    """
    path = request.path or ""
    if not _is_admin_path(path):
        return  # not protected

    # If creds aren't configured, fail closed.
    if not (ADMIN_USER and ADMIN_PASS):
        return _unauthorized_text()

    auth = request.authorization
    if not auth or auth.type.lower() != "basic":
        return _unauthorized_text()

    if auth.username != ADMIN_USER or auth.password != ADMIN_PASS:
        return _unauthorized_text()
    # otherwise OK — request continues

# ---- ROUTE GROUPS ------------------------------------------------------------
# Import & attach the submodules that register their handlers on this blueprint.
# (Imports are local to avoid import cycles during app startup.)

from . import wifi as _wifi
from . import update as _update
from . import wan as _wan

_wifi.attach(admin_bp)
_update.attach(admin_bp)
_wan.attach(admin_bp)

# Keep /admin -> /admin/wifi redirect here so it’s obvious where the entry is.
@admin_bp.route("/admin")
def _admin_index_redirect():
    from flask import redirect
    return redirect("/admin/wifi", code=302)
