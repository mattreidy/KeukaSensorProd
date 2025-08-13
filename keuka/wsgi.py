# wsgi.py
# -------
# WSGI entrypoint for Gunicorn.
# Uses the same application factory your modular app exposes.

from app import create_app

# Gunicorn will import "app" from this module:  wsgi:app
app = create_app()
