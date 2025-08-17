# app.py v5
# -----------------------------------------------------------------------------
# Application factory for the Keuka Sensor web app.
# - Creates a Flask app instance.
# - Registers all route blueprints (root, webcam, admin, health).
# - Keeps configuration centralized in config.py.
# -----------------------------------------------------------------------------

from flask import Flask
from config import VERSION  # example: can be shown in templates if needed

# Import blueprints
from routes_root import root_bp
from routes_webcam import webcam_bp
from routes_admin import admin_bp
from routes_health import health_bp
from routes_duckdns import duckdns_bp


def create_app() -> Flask:
    """
    App factory so tests or alternative servers (gunicorn) can import the app
    without side effects.
    """
    app = Flask(__name__)

    # Register all feature blueprints at their natural routes
    app.register_blueprint(root_bp)
    app.register_blueprint(webcam_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(duckdns_bp)

    # You can stash global config if desired
    app.config["VERSION"] = VERSION
    return app

# Allow `python app.py` to run directly (handy during development)
if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
