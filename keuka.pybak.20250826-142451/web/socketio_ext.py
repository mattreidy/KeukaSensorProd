# keuka/socketio_ext.py
import os
from flask_socketio import SocketIO

# Force a safe default so we don't change the app's runtime model just by installing eventlet.
# You can override later by setting KS_SOCKETIO_ASYNC=eventlet (or gevent) when you switch workers.
ASYNC_MODE = os.environ.get("KS_SOCKETIO_ASYNC", "threading")

# One shared Socket.IO instance for the whole app; initialized in app.py
socketio = SocketIO(cors_allowed_origins="*", async_mode=ASYNC_MODE)
