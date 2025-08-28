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
import uuid
import json
from functools import wraps

from flask import Blueprint, request, render_template_string, jsonify
import paramiko

from ..core.utils import get_system_fqdn

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
  <link rel="stylesheet" href="../static/css/xterm.min.css">
  <script src="../static/js/xterm.min.js"></script>
  <script src="../static/js/xterm-addon-fit.min.js"></script>
  <style>
    body { margin: 0; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; background: #111; color: #eee; }
    .wrap { max-width: 1000px; margin: 0 auto; padding: 16px; }
    .device-name { font-weight: 700; font-size: 1.1rem; text-align: center; margin: 0.8rem 0; }
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
    <div class="device-name">{{ device_fqdn }}</div>
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

    // Universal HTTP-based terminal communication variables
    let sessionId = null;
    let pollInterval = null;

    function connectSSH() {
      setStatus('connecting…');

      // Clean up any existing session
      if (sessionId) {
        stopPolling();
      }

      const payload = {
        host: document.getElementById('host').value.trim(),
        port: parseInt(document.getElementById('port').value.trim(), 10),
        username: document.getElementById('username').value.trim(),
        password: document.getElementById('password').value
      };
      
      function connectHTTP() {
        setStatus('connecting via HTTP...');
        console.log('[terminal] Using HTTP-based terminal communication for universal proxy support');
        
        // Determine the correct base URL for API calls
        const isProxy = window.location.pathname.includes('/proxy/');
        const baseUrl = isProxy ? window.location.pathname.split('/admin/terminal')[0] : '';
        const startUrl = baseUrl + '/admin/terminal/start';
        
        console.log('[terminal] HTTP start URL:', startUrl);
        
        // Start SSH session via HTTP
        fetch(startUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        })
        .then(response => response.json())
        .then(data => {
          if (data.success) {
            sessionId = data.sessionId;
            setStatus('connected, starting ssh via HTTP...');
            console.log('[terminal] HTTP session started:', sessionId);
            startPolling();
          } else {
            setStatus('HTTP connection error: ' + data.error);
          }
        })
        .catch(err => {
          console.log('[terminal] HTTP connection failed:', err);
          setStatus('HTTP connection failed: ' + err.message);
        });
      }
      
      function startPolling() {
        // Determine base URL for polling
        const isProxy = window.location.pathname.includes('/proxy/');
        const baseUrl = isProxy ? window.location.pathname.split('/admin/terminal')[0] : '';
        
        // Poll for SSH output
        pollInterval = setInterval(() => {
          if (!sessionId) return;
          
          const pollUrl = baseUrl + `/admin/terminal/poll/${sessionId}`;
          fetch(pollUrl)
            .then(response => response.json())
            .then(data => {
              if (data.data) {
                term.write(data.data);
              }
              if (data.closed) {
                setStatus('SSH session closed');
                stopPolling();
              }
            })
            .catch(err => {
              console.log('[terminal] Poll error:', err);
              stopPolling();
            });
        }, 100);  // Poll every 100ms for responsive terminal
      }
      
      function stopPolling() {
        if (pollInterval) {
          clearInterval(pollInterval);
          pollInterval = null;
        }
        if (sessionId) {
          const isProxy = window.location.pathname.includes('/proxy/');
          const baseUrl = isProxy ? window.location.pathname.split('/admin/terminal')[0] : '';
          const closeUrl = baseUrl + `/admin/terminal/close/${sessionId}`;
          fetch(closeUrl, { method: 'POST' });
          sessionId = null;
        }
      }
      
      // Handle terminal input for HTTP-based communication with debouncing
      let inputQueue = [];
      let inputTimeout = null;
      
      term.onData(function(data) {
        if (!sessionId) return;
        
        // Add input to queue and debounce to prevent duplicates
        inputQueue.push(data);
        
        if (inputTimeout) {
          clearTimeout(inputTimeout);
        }
        
        inputTimeout = setTimeout(() => {
          if (inputQueue.length > 0) {
            const combinedInput = inputQueue.join('');
            inputQueue = [];
            
            const isProxy = window.location.pathname.includes('/proxy/');
            const baseUrl = isProxy ? window.location.pathname.split('/admin/terminal')[0] : '';
            const inputUrl = baseUrl + `/admin/terminal/input/${sessionId}`;
            
            fetch(inputUrl, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ data: combinedInput })
            }).catch(err => {
              console.log('[terminal] Input error:', err);
            });
          }
        }, 10);  // 10ms debounce to batch rapid keystrokes
      });
      
      // Use HTTP-based communication instead of Socket.IO
      connectHTTP();
      
      // Clean up on page unload
      window.addEventListener('beforeunload', stopPolling);
    }

    document.getElementById('connectBtn').addEventListener('click', () => connectSSH());
  </script>
  <!-- Socket.IO client (local) -->
  <script src="../static/js/socket.io.min.js"></script>
</body>
</html>
"""

terminal_bp = Blueprint("terminal_bp", __name__, static_folder="../static", static_url_path="/admin/static")

@terminal_bp.route(TERMINAL_ROUTE, methods=["GET"])
@gateway_auth_required
def terminal_page():
    device_fqdn = get_system_fqdn()
    return render_template_string(
        PAGE_HTML, host=DEFAULT_SSH_HOST, port=DEFAULT_SSH_PORT, ns_path=TERMINAL_NS, device_fqdn=device_fqdn
    )

# HTTP-based terminal endpoints for proxy compatibility
@terminal_bp.route("/admin/terminal/start", methods=["POST"])
@gateway_auth_required
def start_http_session():
    try:
        data = request.get_json()
        host = str(data.get("host") or DEFAULT_SSH_HOST)
        port = int(data.get("port") or DEFAULT_SSH_PORT)
        username = str(data["username"]).strip()
        password = str(data["password"])
        
        if not username or not password:
            return jsonify({"success": False, "error": "Username and password are required"})
        
        session_id = str(uuid.uuid4())
        session = HTTPSSHSession(session_id, host, port, username, password)
        session.connect()
        
        _http_sessions[session_id] = session
        threading.Thread(target=session.read_loop, daemon=True).start()
        
        return jsonify({"success": True, "sessionId": session_id})
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@terminal_bp.route("/admin/terminal/poll/<session_id>", methods=["GET"])
@gateway_auth_required
def poll_http_session(session_id):
    session = _http_sessions.get(session_id)
    if not session:
        return jsonify({"error": "Session not found"})
    
    data = session.get_output()
    return jsonify({"data": data, "closed": session.is_closed()})

@terminal_bp.route("/admin/terminal/input/<session_id>", methods=["POST"])
@gateway_auth_required
def send_http_input(session_id):
    session = _http_sessions.get(session_id)
    if not session:
        return jsonify({"error": "Session not found"})
    
    data = request.get_json().get("data", "")
    session.write(data)
    return jsonify({"success": True})

@terminal_bp.route("/admin/terminal/close/<session_id>", methods=["POST"])
@gateway_auth_required
def close_http_session(session_id):
    session = _http_sessions.pop(session_id, None)
    if session:
        session.close()
    return jsonify({"success": True})

# Legacy SocketIO-based SSH session class removed - using HTTP-based communication
_sessions_by_sid = {}  # Kept for compatibility
_http_sessions = {}  # For HTTP-based sessions

class HTTPSSHSession:
    def __init__(self, session_id, host, port, username, password):
        self.session_id = session_id
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.client = None
        self.channel = None
        self.output_buffer = []
        self._closed = False
        self._lock = threading.Lock()
        self.last_activity = time.time()
        
    def connect(self):
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(
            hostname=self.host, 
            port=self.port, 
            username=self.username, 
            password=self.password,
            timeout=10
        )
        self.channel = self.client.invoke_shell(term="xterm", width=120, height=30)
        self.channel.settimeout(0.0)
        
    def write(self, data):
        with self._lock:
            if self.channel and not self._closed:
                # Add small delay to prevent input flooding
                current_time = time.time()
                if hasattr(self, '_last_write_time'):
                    time_since_last = current_time - self._last_write_time
                    if time_since_last < 0.005:  # 5ms minimum between writes
                        time.sleep(0.005 - time_since_last)
                
                self.channel.send(data)
                self._last_write_time = time.time()
                self.last_activity = time.time()
                
    def read_loop(self):
        try:
            while not self._closed:
                if self.channel and self.channel.recv_ready():
                    chunk = self.channel.recv(4096)
                    if not chunk:
                        break
                    with self._lock:
                        self.output_buffer.append(chunk.decode("utf-8", errors="ignore"))
                    self.last_activity = time.time()
                else:
                    if time.time() - self.last_activity > IDLE_TIMEOUT:
                        break
                    time.sleep(0.03)
        finally:
            self.close()
            
    def get_output(self):
        with self._lock:
            if self.output_buffer:
                output = ''.join(self.output_buffer)
                self.output_buffer.clear()
                return output
            return ""
    
    def is_closed(self):
        return self._closed
        
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

# Legacy SocketIO namespace class removed - using HTTP-based communication

def register_terminal_blueprint(app):
    app.register_blueprint(terminal_bp)

def register_terminal_namespace():
    # Socket.IO namespace registration removed - using HTTP-based communication
    pass
