# keuka/routes_duckdns.py
# -----------------------------------------------------------------------------
# DuckDNS admin page + JSON API (Flask Blueprint)
# - Stores token & subdomain list in keuka/duckdns.conf (same file your config.py points to)
# - Triggers the existing duckdns-update.service
# - Controls the existing duckdns-update.timer
# - Shows last update log from keuka/duckdns_last.txt
# -----------------------------------------------------------------------------

from __future__ import annotations
from flask import Blueprint, request, jsonify, Response
from pathlib import Path
import base64
import html
import json
import re
import subprocess

from config import (
    ADMIN_USER, ADMIN_PASS,
    DUCKDNS_CONF, DUCKDNS_LAST,
)

duckdns_bp = Blueprint("duckdns", __name__)

CONF: Path = DUCKDNS_CONF           # e.g. /home/pi/KeukaSensorProd/keuka/duckdns.conf
LAST: Path = DUCKDNS_LAST           # e.g. /home/pi/KeukaSensorProd/keuka/duckdns_last.txt
SERVICE = "duckdns-update.service"
TIMER   = "duckdns-update.timer"


# ----------------------- tiny helpers -----------------------

def _sh(cmd: list[str] | str, timeout: int = 20) -> tuple[int, str]:
    """Run a shell command and return (rc, stdout+stderr)."""
    try:
        if isinstance(cmd, str):
            p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        else:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out.strip()
    except Exception as e:
        return 1, f"exec error: {e}"

def _require_admin(req) -> bool:
    """Basic auth check using ADMIN_USER/ADMIN_PASS from config.py."""
    auth = req.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return False
    try:
        raw = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8", "replace")
        user, pw = raw.split(":", 1)
    except Exception:
        return False
    return (user == ADMIN_USER) and (pw == ADMIN_PASS)

def _parse_conf(text: str) -> dict:
    """Parse key=value lines, ignoring comments/blanks."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"([A-Za-z0-9_]+)\s*=\s*(.*)$", s)
        if not m:
            continue
        k, v = m.group(1).lower(), m.group(2).strip()
        out[k] = v.strip('"').strip("'")
    return out

def _read_conf() -> dict:
    try:
        return _parse_conf(CONF.read_text(errors="replace")) if CONF.exists() else {}
    except Exception:
        return {}

def _write_conf(domains: str, token: str) -> None:
    # Normalize a CSV of subdomains (no .duckdns.org suffix)
    parts = [p.strip() for p in (domains or "").split(",") if p.strip()]
    clean = []
    for p in parts:
        # allow user to paste full host like foo.duckdns.org; store just "foo"
        if p.endswith(".duckdns.org"):
            p = p[:-len(".duckdns.org")]
        clean.append(p)
    content = "token={}\ndomains={}\n".format(token.strip(), ",".join(clean))
    # root-owned secret with 0600
    tmp = CONF.with_suffix(".tmp")
    tmp.write_text(content)
    _sh(["sudo", "chown", "root:root", str(tmp)])
    _sh(["sudo", "chmod", "600", str(tmp)])
    _sh(["sudo", "mv", str(tmp), str(CONF)])

def _systemctl(args: str) -> tuple[int, str]:
    # -n prevents prompting for password; make sure 'pi' can sudo systemctl without password for these units.
    return _sh(f"sudo -n systemctl {args}", timeout=20)

def _unit_active(name: str) -> bool:
    rc, _ = _systemctl(f"is-active {name}")
    return rc == 0

def _unit_enabled(name: str) -> bool:
    rc, _ = _systemctl(f"is-enabled {name}")
    return rc == 0

def _last_update() -> dict:
    data = {"when": None, "text": None}
    try:
        if LAST.exists():
            txt = LAST.read_text(errors="replace")
            data["text"] = txt[-4000:]  # last ~4k chars
            # Try to extract an ISO timestamp if present in your log lines
            m = re.search(r"(\d{4}-\d{2}-\d{2}T[0-9:+-]{5,})", txt)
            data["when"] = m.group(1) if m else None
    except Exception:
        pass
    return data


# ----------------------- HTML page -----------------------

@duckdns_bp.route("/admin/duckdns", methods=["GET"])
def admin_duckdns():
    if not _require_admin(request):
        return Response(
            "Unauthorized",
            401,
            {"WWW-Authenticate": 'Basic realm="Keuka Admin"'}
        )

    cfg = _read_conf()
    dom = html.escape(cfg.get("domains", ""))
    tok = html.escape(cfg.get("token", ""))

    html_page = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>KeukaSensor • DuckDNS</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 18px; }}
  .card {{ max-width: 860px; border: 1px solid #ddd; border-radius: 8px; padding: 16px; margin: 0 auto; }}
  .row {{ display: flex; gap: 12px; flex-wrap: wrap; }}
  .row > div {{ flex: 1 1 260px; }}
  label {{ display:block; font-weight:600; margin: 8px 0 4px; }}
  input[type=text], input[type=password] {{ width:100%; padding:8px; border:1px solid #ccc; border-radius:6px; }}
  button {{ padding:8px 14px; border:1px solid #888; border-radius:6px; cursor:pointer; background:#f5f5f5; }}
  button.primary {{ background:#1e90ff; color:#fff; border-color:#1e90ff; }}
  .muted {{ color:#666; font-size: 0.9em; }}
  pre {{ background:#fafafa; border:1px solid #eee; border-radius:6px; padding:10px; overflow:auto; max-height: 360px; }}
  .ok {{ color:#0a7d00; font-weight:600; }}
  .bad {{ color:#b00020; font-weight:600; }}
</style>
</head>
<body>
<div class="card">
  <h2>DuckDNS configuration</h2>
  <p class="muted">Set your subdomain(s) and token. This device will update DuckDNS regularly via systemd timer.</p>
  <div class="row">
    <div>
      <label>Subdomain(s) <span class="muted">(comma-separated; enter just the name, e.g. <b>keukasensor1</b>)</span></label>
      <input id="domains" type="text" value="{dom}" placeholder="keukasensor1,secondname">
    </div>
    <div>
      <label>Token</label>
      <input id="token" type="password" value="{tok}" placeholder="DuckDNS account token">
    </div>
  </div>

  <div style="margin-top:12px; display:flex; gap:8px; flex-wrap:wrap;">
    <button class="primary" onclick="save()">Save</button>
    <button onclick="runNow()">Run update now</button>
    <button onclick="timer(true)">Enable periodic updates</button>
    <button onclick="timer(false)">Disable periodic updates</button>
    <span id="busy" class="muted" style="display:none;">Working…</span>
  </div>

  <h3 style="margin-top:20px;">Status</h3>
  <div class="row">
    <div>Service: <span id="svc" class="muted">…</span></div>
    <div>Timer: <span id="tmr" class="muted">…</span></div>
  </div>

  <h3 style="margin-top:16px;">Last update</h3>
  <div class="muted" id="when">—</div>
  <pre id="log">(fetching…)</pre>
  <p class="muted">Tip: you can also manage Wi-Fi at <a href="/admin/wifi">/admin/wifi</a>.</p>
</div>

<script>
async function loadStatus() {{
  const r = await fetch('/api/duckdns/status', {{headers: {{'Authorization': '{request.headers.get("Authorization","")}'}}}});
  const j = await r.json();
  document.getElementById('svc').innerHTML = j.service_active ? '<span class="ok">active</span>' : '<span class="bad">inactive</span>';
  document.getElementById('tmr').innerHTML = (j.timer_enabled ? 'enabled' : 'disabled') + ' / ' + (j.timer_active ? 'active' : 'inactive');
  document.getElementById('when').textContent = j.last.when ? ('Last: ' + j.last.when) : '—';
  document.getElementById('log').textContent = j.last.text || '—';
  if (j.conf) {{
    if (j.conf.domains !== undefined) document.getElementById('domains').value = j.conf.domains || '';
    if (j.conf.token   !== undefined) document.getElementById('token').value   = j.conf.token || '';
  }}
}}
async function save() {{
  const body = {{
    domains: document.getElementById('domains').value.trim(),
    token:   document.getElementById('token').value.trim()
  }};
  document.getElementById('busy').style.display = 'inline';
  try {{
    const r = await fetch('/api/duckdns/save', {{
      method:'POST',
      headers: {{
        'Content-Type':'application/json',
        'Authorization': '{request.headers.get("Authorization","")}'
      }},
      body: JSON.stringify(body)
    }});
    const j = await r.json();
    if (!j.ok) alert('Save failed: ' + (j.error||'unknown error'));
    await loadStatus();
  }} finally {{
    document.getElementById('busy').style.display = 'none';
  }}
}}
async function runNow() {{
  document.getElementById('busy').style.display = 'inline';
  try {{
    const r = await fetch('/api/duckdns/run', {{method:'POST', headers: {{'Authorization': '{request.headers.get("Authorization","")}'}}}});
    const j = await r.json();
    if (!j.ok) alert('Run failed: ' + (j.error||'unknown error'));
    setTimeout(loadStatus, 1000);
  }} finally {{
    document.getElementById('busy').style.display = 'none';
  }}
}}
async function timer(enable) {{
  document.getElementById('busy').style.display = 'inline';
  try {{
    const r = await fetch('/api/duckdns/timer', {{
      method:'POST',
      headers: {{'Content-Type':'application/json', 'Authorization': '{request.headers.get("Authorization","")}' }},
      body: JSON.stringify({{enabled: !!enable}})
    }});
    const j = await r.json();
    if (!j.ok) alert('Timer change failed: ' + (j.error||'unknown error'));
    await loadStatus();
  }} finally {{
    document.getElementById('busy').style.display = 'none';
  }}
}}
loadStatus();
</script>
</body></html>"""
    return Response(html_page, mimetype="text/html")


# ----------------------- JSON API -----------------------

@duckdns_bp.route("/api/duckdns/status", methods=["GET"])
def api_status():
    if not _require_admin(request):
        return jsonify(ok=False, error="unauthorized"), 401
    conf = _read_conf()
    last = _last_update()
    return jsonify(
        ok=True,
        conf={"domains": conf.get("domains",""), "token": conf.get("token","")},
        service_active=_unit_active(SERVICE),
        timer_active=_unit_active(TIMER),
        timer_enabled=_unit_enabled(TIMER),
        last=last,
    )

@duckdns_bp.route("/api/duckdns/save", methods=["POST"])
def api_save():
    if not _require_admin(request):
        return jsonify(ok=False, error="unauthorized"), 401
    j = request.get_json(silent=True) or {}
    domains = (j.get("domains") or "").strip()
    token   = (j.get("token") or "").strip()
    if not domains or not token:
        return jsonify(ok=False, error="domains and token are required"), 400
    try:
        _write_conf(domains, token)
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=f"write failed: {e}"), 500

@duckdns_bp.route("/api/duckdns/run", methods=["POST"])
def api_run_now():
    if not _require_admin(request):
        return jsonify(ok=False, error="unauthorized"), 401
    rc, out = _systemctl(f"start {SERVICE}")
    if rc != 0:
        return jsonify(ok=False, error=out or "systemctl start failed"), 500
    return jsonify(ok=True, note="started")

@duckdns_bp.route("/api/duckdns/timer", methods=["POST"])
def api_timer():
    if not _require_admin(request):
        return jsonify(ok=False, error="unauthorized"), 401
    j = request.get_json(silent=True) or {}
    enable = bool(j.get("enabled"))
    if enable:
        rc1, o1 = _systemctl(f"enable {TIMER}")
        rc2, o2 = _systemctl(f"start {TIMER}")
        if rc1 != 0 or rc2 != 0:
            return jsonify(ok=False, error=(o1 or o2 or "enable/start failed")), 500
    else:
        _systemctl(f"stop {TIMER}")
        rc, o = _systemctl(f"disable {TIMER}")
        if rc != 0:
            return jsonify(ok=False, error=o or "disable failed"), 500
    return jsonify(ok=True)
