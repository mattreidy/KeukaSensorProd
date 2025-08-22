# keuka/routes_admin.py
# -----------------------------------------------------------------------------
# Compatibility shim.
#
# Historically the app imported `admin_bp` from this module. We’ve split the
# implementation across `keuka/admin/*`, but we keep this file so no other
# part of the application needs to change. It simply re-exports the composed
# blueprint from the `admin` package using an **absolute** import, because
# the app loads this module as a top-level module (PYTHONPATH points at keuka/),
# so there is no parent package for relative imports.
# -----------------------------------------------------------------------------

from __future__ import annotations

# IMPORTANT: absolute import, NOT relative.
# With PYTHONPATH=/home/pi/KeukaSensorProd/keuka, `admin` resolves to the
# directory keuka/admin (which must contain __init__.py).
from ...admin import admin_bp  # re-export (public API unchanged)
