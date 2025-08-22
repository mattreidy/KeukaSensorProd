# run.py
# -----------------------------------------------------------------------------
# Tiny launcher so you can run the app with:
#     python3 run.py
# This calls the application factory from app.py.
# -----------------------------------------------------------------------------

from .app import create_app

if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
