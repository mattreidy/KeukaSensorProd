#!/usr/bin/env python3
# keuka/admin/ssh_web_terminal.py
#
# Integrated web SSH terminal:
# - Page:        GET /admin/terminal
# - Socket.IO:   namespace = /admin/terminal
# - SSH target:  localhost (configurable via env)
# - Protected by your existing /admin Basic Auth.
#
# Security notes:
# - This page is behind your /admin Basic Auth.
# - Optionally, add a second gate via KS_TERM_USER/KS_TERM_PASS env vars.
# - For production-hardening consider SSH keys-only.

import os
import time
import threading
from functools import wraps

from flask import Blueprint, request, render_template_string
from flask_socketio import Namespace, emit
import paramiko

from ..socketio_ext import socketio

DEFAULT_SSH_HOST = os.environ.get("KS_TERM_SSH_HOST", "127.0.0.1")
DEFAULT_SSH_PORT = int(os.environ.get("KS_TERM_SSH_PORT", "22"))
IDLE_TIMEOUT     = int(os.environ.get("KS_TERM_IDLE_SECS", "900"))  # 15 min

GATE_USER = os.environ.get("KS_TERM_USER")
GATE_PASS = os.environ.get("KS_TERM_PASS")

TERMINAL_ROUTE = "/admin/terminal"
TERMINAL_NS    = "/admin/terminal"

def _bad_auth_response():
    return ("Unauthorized", 401, {"WWW-Authenticate": 'Basic realm="Keuka Terminal"'})

def gateway_auth_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not GATE_USER or not GATE_PASS:
            return f(*args, **kwargs)
        auth = request.authorization
        if not auth or auth.username != GATE_USER or auth.password != GATE_PASS:
            return _bad_auth_response()
        return f(*args, **kwargs)
    return wrapper

PAGE_HTML = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Keuka SSH Terminal</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <!-- xterm.js (local) -->
  <link rel="stylesheet" href="{{ url_for('static', filename='css/xterm.min.css') }}">
  <script src="{{ url_for('static', filename='js/xterm.min.js') }}"></script>
  <script src="{{ url_for('static', filename='js/xterm-addon-fit.min.js') }}"></script>
  <style>
    body { margin: 0; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; background: #111; color: #eee; }
    .wrap { max-width: 1000px; margin: 0 auto; padding: 16px; }
    h1 { font-size: 18px; font-weight: 600; margin: 0 0 8px; }
    form { background:#1c1c1c; padding:12px; border-radius:8px; display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
    label { font-size: 13px; opacity: .9; }
    input { background:#000; color:#eee; border:1px solid #333; padding:8px 10px; border-radius:6px; }
    button { background:#2b6; border:0; color:#fff; padding:8px 12px; border-radius:6px; cursor:pointer; }
    button:disabled { opacity:.6; cursor:not-allowed; }
    #term { height: 70vh; background:#000; border-radius:8px; margin-top:12px; }
    .row { display:flex; gap:8px; align-items:center; }
    #status { margin-top:8px; font-size:12px; color:#8fd; }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Keuka SSH Terminal (localhost)</h1>
    <form id="sshForm" onsubmit="return false;">
      <div class="row">
        <label>Host</label>
        <input id="host" value="{{ host }}" size="16" />
        <label>Port</label>
        <input id="port" value="{{ port }}" size="6" />
        <label>Username</label>
        <input id="username" placeholder="os user (e.g., pi)" size="14" />
        <label>Password</label>
        <input id="password" type="password" placeholder="os password" size="16" />
        <button id="connectBtn">Connect</button>
      </div>
    </form>
    <div id="status">status: idle</div>
    <div id="term"></div>
  </div>

  <script>
    let socket = null;
    const statusEl = document.getElementById('status');
    function setStatus(s){ statusEl.textContent = 'status: ' + s; console.log('[terminal]', s); }

    const term = new window.Terminal({ cursorBlink: true, fontSize: 14 });
    const fitAddon = new window.FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open(document.getElementById('term'));
    fitAddon.fit();
    window.addEventListener('resize', () => fitAddon.fit());

    function connectSSH() {
      setStatus('connecting…');

      if (socket) { try { socket.close(); } catch(e){} }

      const payload = {
        host: document.getElementById('host').value.trim(),
        port: parseInt(document.getElementById('port').value.trim(), 10),
        username: document.getElementById('username').value.trim(),
        password: document.getElementById('password').value
      };

      // Connect to our namespaced endpoint
      socket = io("{{ ns_path }}");
      socket.on('connect', function() {
        setStatus('connected, starting ssh…');
        socket.emit('ssh_start', payload);
      });
      socket.on('connect_error', (err) => {
        setStatus('connect_error: ' + err.message);
      });
      socket.on('reconnect_error', (err) => {
        setStatus('reconnect_error: ' + err.message);
      });

      socket.on('ssh_data', function(data) {
        term.write(data);
      });

      socket.on('ssh_error', function(msg) {
        setStatus('ssh_error: ' + msg);
        term.writeln('\\r\\n[error] ' + msg + '\\r\\n');
      });

      socket.on('ssh_closed', function() {
        setStatus('ssh session closed');
        term.writeln('\\r\\n[session closed]\\r\\n');
      });

      term.onData(function(data) {
        if (socket) socket.emit('ssh_input', data);
      });
    }

    document.getElementById('connectBtn').addEventListener('click', () => connectSSH());
  </script>
  <!-- Socket.IO client (local) -->
  <script src="{{ url_for('static', filename='js/socket.io.min.js') }}"></script>
</body>
</html>
"""

terminal_bp = Blueprint("terminal_bp", __name__)

@terminal_bp.route(TERMINAL_ROUTE, methods=["GET"])
@gateway_auth_required
def terminal_page():
    return render_template_string(
        PAGE_HTML, host=DEFAULT_SSH_HOST, port=DEFAULT_SSH_PORT, ns_path=TERMINAL_NS
    )

class SSHSession:
    def __init__(self, sid, host, port, username, password, sio):
        self.sid = sid
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.client = None
        self.channel = None
        self.last_activity = time.time()
        self._closed = False
        self._lock = threading.Lock()
        self.sio = sio

    def open(self):
        print(f"[ssh] open sid={self.sid} {self.username}@{self.host}:{self.port}")
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(
            self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            look_for_keys=False,
            allow_agent=False,
            timeout=10,
            banner_timeout=10,
            auth_timeout=10,
        )
        self.channel = self.client.invoke_shell(term="xterm", width=120, height=30)
        self.channel.settimeout(0.0)

    def write(self, data: str):
        with self._lock:
            if self.channel and not self._closed:
                self.channel.send(data)
                self.last_activity = time.time()

    def read_loop(self):
        try:
            while not self._closed:
                if self.channel and self.channel.recv_ready():
                    chunk = self.channel.recv(4096)
                    if not chunk:
                        break
                    self.sio.emit("ssh_data", chunk.decode("utf-8", errors="ignore"),
                                  room=self.sid, namespace=TERMINAL_NS)
                    self.last_activity = time.time()
                else:
                    if time.time() - self.last_activity > IDLE_TIMEOUT:
                        break
                    time.sleep(0.03)
        finally:
            self.close()
            self.sio.emit("ssh_closed", room=self.sid, namespace=TERMINAL_NS)
            print(f"[ssh] closed sid={self.sid}")

    def close(self):
        with self._lock:
            self._closed = True
            try:
                if self.channel:
                    self.channel.close()
            except Exception:
                pass
            try:
                if self.client:
                    self.client.close()
            except Exception:
                pass

_sessions_by_sid = {}

class TerminalNamespace(Namespace):
    def __init__(self, namespace, sio):
        super().__init__(namespace)
        self.sio = sio

    def on_connect(self):
        print(f"[ns] client connected to {TERMINAL_NS}")

    def on_ssh_start(self, payload):
        from flask import request
        sid = request.sid
        print(f"[ns] ssh_start from sid={sid} payload_keys={list(payload.keys())}")
        try:
            host = str(payload.get("host") or DEFAULT_SSH_HOST)
            port = int(payload.get("port") or DEFAULT_SSH_PORT)
            username = str(payload["username"]).strip()
            password = str(payload["password"])
            if not username or not password:
                emit("ssh_error", "Username and password are required.")
                return
        except Exception as e:
            emit("ssh_error", f"Invalid parameters: {e}")
            return

        old = _sessions_by_sid.pop(sid, None)
        if old:
            try: old.close()
            except Exception: pass

        sess = SSHSession(sid, host, port, username, password, self.sio)
        try:
            sess.open()
        except Exception as e:
            emit("ssh_error", f"SSH connect failed: {e}")
            return

        _sessions_by_sid[sid] = sess
        t = threading.Thread(target=sess.read_loop, daemon=True)
        t.start()

    def on_ssh_input(self, data):
        from flask import request
        sid = request.sid
        sess = _sessions_by_sid.get(sid)
        if not sess:
            emit("ssh_error", "No active session.")
            return
        try:
            sess.write(data)
        except Exception as e:
            emit("ssh_error", f"Write failed: {e}")

    def on_disconnect(self):
        from flask import request
        sid = request.sid
        print(f"[ns] client disconnected sid={sid}")
        sess = _sessions_by_sid.pop(sid, None)
        if sess:
            try: sess.close()
            except Exception: pass

def register_terminal_blueprint(app):
    app.register_blueprint(terminal_bp)

def register_terminal_namespace():
    socketio.on_namespace(TerminalNamespace(TERMINAL_NS, socketio))
