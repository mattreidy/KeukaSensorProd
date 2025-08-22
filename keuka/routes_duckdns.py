# -----------------------------------------------------------------------------
# DuckDNS admin page + JSON API (Flask Blueprint)
# - Stores token & subdomain list in config/duckdns.conf (same file your config.py points to)
# - Triggers the existing duckdns-update.service
# - Controls the existing duckdns-update.timer
# - Shows last update log from logs/duckdns_last.txt
# - Enhanced health: next run, last result, service SubState/ExecMainStatus/start/exit
# -----------------------------------------------------------------------------

from __future__ import annotations
from flask import Blueprint, request, jsonify, Response
from pathlib import Path
import base64
import html
import json
import re
import subprocess
from typing import Dict, Any

from config import (
    ADMIN_USER, ADMIN_PASS,
)

duckdns_bp = Blueprint("duckdns", __name__)

from pathlib import Path

# Hardcoded config/log paths and systemd units
CONF: Path = Path("/home/pi/KeukaSensorProd/config/duckdns.conf")
LAST: Path = Path("/home/pi/KeukaSensorProd/logs/duckdns_last.txt")
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
    """
    Write DuckDNS config as pi:pi (0600) so the duckdns-update.service (User=pi)
    can read/update it without sudo. Atomic replace via os.replace().
    """
    import os
    from shutil import chown as _chown

    # Normalize CSV (strip optional ".duckdns.org")
    parts = [p.strip() for p in (domains or "").split(",") if p.strip()]
    clean = []
    for p in parts:
        if p.endswith(".duckdns.org"):
            p = p[:-len(".duckdns.org")]
        clean.append(p)

    content = "token={}\ndomains={}\n".format((token or "").strip(), ",".join(clean))

    # Write to a temp file next to the target, then atomically replace
    tmp = CONF.with_suffix(".tmp")
    tmp.write_text(content)

    # Ensure ownership/mode (running as pi, so chown is either a no-op or already correct)
    try:
        _chown(str(tmp), user="pi", group="pi")  # safe when running as pi
    except Exception:
        # Not fatal if chown isn't needed/allowed; keep going.
        pass
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass

    # Atomic rename into place
    os.replace(str(tmp), str(CONF))


def _systemctl(args: str) -> tuple[int, str]:
    # -n prevents prompting for password; make sure 'pi' can sudo systemctl without password for these units.
    return _sh(f"sudo -n systemctl {args} --no-pager", timeout=20)

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
            m = re.search(r"(\d{4}-\d{2}-\d{2}T[0-9:+-]{5,})", txt)
            data["when"] = m.group(1) if m else None
    except Exception:
        pass
    return data

# ---- Option C helpers --------------------------------------------------------

def _parse_systemd_show(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out

def _service_details() -> Dict[str, Any]:
    rc, out = _systemctl("show {} -p SubState -p ExecMainStatus -p ExecMainStartTimestamp -p ExecMainExitTimestamp".format(SERVICE))
    if rc != 0 or not out:
        return {}
    m = _parse_systemd_show(out)
    return {
        "service_substate": m.get("SubState"),
        "service_exec_status": (int(m["ExecMainStatus"]) if m.get("ExecMainStatus", "").isdigit() else None),
        "service_started_at": m.get("ExecMainStartTimestamp"),
        "service_exited_at": m.get("ExecMainExitTimestamp"),
    }

def _timer_details() -> Dict[str, Any]:
    rc, out = _systemctl("show {} -p NextElapseUSecRealtime -p LastTriggerUSecRealtime".format(TIMER))
    if rc != 0 or not out:
        return {"timer_next": None, "timer_last_trigger": None}
    m = _parse_systemd_show(out)
    return {
        "timer_next": m.get("NextElapseUSecRealtime") or None,
        "timer_last_trigger": m.get("LastTriggerUSecRealtime") or None,
    }

def _last_result() -> Dict[str, Any]:
    """
    Parse the last ' [duckdns] v4 ' line from LAST and map to OK/KO.
    Returns {'last_result':'OK'|'KO'|None, 'last_result_line':str|None}
    """
    try:
        if not LAST.exists():
            return {"last_result": None, "last_result_line": None}
        txt = LAST.read_text(errors="replace")
        lines = [ln for ln in txt.splitlines() if " [duckdns] v4 " in ln]
        if not lines:
            return {"last_result": None, "last_result_line": None}
        last_line = lines[-1]
        status = "OK" if (" v4 OK " in last_line or last_line.rstrip().endswith(" v4 OK")) else "KO"
        return {"last_result": status, "last_result_line": last_line[-400:]}
    except Exception:
        return {"last_result": None, "last_result_line": None}


# ----------------------- HTML template (no f-strings) ------------------------

_DUCKDNS_HTML_TMPL = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>KeukaSensor • DuckDNS</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 18px; }
  .card { max-width: 980px; border: 1px solid #ddd; border-radius: 8px; padding: 16px; margin: 0 auto; }
  .row { display: flex; gap: 12px; flex-wrap: wrap; }
  .row > div { flex: 1 1 260px; }
  label { display:block; font-weight:600; margin: 8px 0 4px; }
  input[type=text], input[type=password] { width:100%; padding:8px; border:1px solid #ccc; border-radius:6px; }
  button { padding:8px 14px; border:1px solid #888; border-radius:6px; cursor:pointer; background:#f5f5f5; }
  button.primary { background:#1e90ff; color:#fff; border-color:#1e90ff; }
  .muted { color:#666; font-size: 0.9em; }
  .ok { color:#0a7d00; font-weight:600; }
  .bad { color:#b00020; font-weight:600; }
  pre { background:#fafafa; border:1px solid #eee; border-radius:6px; padding:10px; overflow:auto; max-height: 360px; }
</style>
</head>
<body>
<div class="card">
  <h2>DuckDNS configuration</h2>
  <p class="muted">Set your subdomain(s) and token. This device updates DuckDNS via a systemd timer.</p>

  <div class="row">
    <div>
      <label>Subdomain(s) <span class="muted">(comma-separated; just the name, e.g. <b>keukasensor1</b>)</span></label>
      <input id="domains" type="text" value="%%DOMAINS%%" placeholder="keukasensor1,secondname">
    </div>
    <div>
      <label>Token</label>
      <input id="token" type="password" value="%%TOKEN%%" placeholder="DuckDNS account token">
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
    <div>Service (oneshot): <span id="svc" class="muted">…</span></div>
    <div>Timer: <span id="tmr" class="muted">…</span></div>
    <div>Next run: <span id="next" class="muted">—</span></div>
  </div>

  <div class="row" style="margin-top:8px">
    <div>Last result: <span id="last_res" class="muted">—</span></div>
    <div>Last update: <span id="when" class="muted">—</span></div>
  </div>

  <div class="row" style="margin-top:8px">
    <div>Service substate: <span id="svc_sub" class="muted">—</span></div>
    <div>ExecMainStatus: <span id="svc_code" class="muted">—</span></div>
    <div>Last start: <span id="svc_start" class="muted">—</span></div>
    <div>Last exit: <span id="svc_exit" class="muted">—</span></div>
  </div>

  <h3 style="margin-top:16px;">Update log</h3>
  <pre id="log">(fetching…)</pre>
</div>

<script>
// --- robust local time formatter (handles ISO and systemd-ish strings) ---
function tzAbbrevToOffset(abbr) {
  const map = {
    // zero-offset
    UTC: "+0000", GMT: "+0000",
    // UK/EU
    BST: "+0100", CET: "+0100", CEST: "+0200",
    // US
    EST: "-0500", EDT: "-0400",
    CST: "-0600", CDT: "-0500",
    MST: "-0700", MDT: "-0600",
    PST: "-0800", PDT: "-0700"
  };
  return map[abbr] || null;
}

function parseToDate(raw) {
  if (raw == null) return null;
  const s = String(raw).trim();

  // 1) ISO 8601? Let the browser parse it.
  const d1 = new Date(s);
  if (!isNaN(d1)) return d1;

  // 2) systemd-style:
  //    "Sun 2025-08-17 18:57:43 BST"  (weekday optional)
  //    "2025-08-17 18:57:43 BST"
  const m = s.match(/^(?:[A-Za-z]{3,9}\s+)?(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\s+([A-Za-z]{2,5})$/);
  if (m) {
    const off = tzAbbrevToOffset(m[3]);
    if (off) {
      const iso = `${m[1]}T${m[2]}${off.slice(0,3)}:${off.slice(3)}`; // RFC3339
      const d2 = new Date(iso);
      if (!isNaN(d2)) return d2;
    }
  }
  return null;
}

function fmtLocal(raw) {
  const d = parseToDate(raw);
  if (!d) return raw || "—";
  try {
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(d);
  } catch {
    return d.toString();
  }
}

function setTimeField(id, raw) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = fmtLocal(raw);
  el.title = raw || '';
}


async function readJSONOrThrow(r) {
  const text = await r.text();
  try { return JSON.parse(text); }
  catch (e) { throw new Error(text.slice(0, 200)); }
}

async function loadStatus() {
  const r = await fetch('/api/duckdns/status', {headers: {'Authorization': '%%AUTH%%', 'Accept':'application/json'}});
  const j = await readJSONOrThrow(r);

  document.getElementById('svc').innerHTML = j.service_active ? '<span class="ok">running</span>' : '<span class="muted">inactive</span>';
  document.getElementById('tmr').innerHTML = (j.timer_enabled ? 'enabled' : 'disabled') + ' / ' + (j.timer_active ? 'active' : 'inactive');

  setTimeField('next', j.timer_next || null);

  if (j.last_result === 'OK') {
    document.getElementById('last_res').innerHTML = '<span class="ok">OK</span>';
  } else if (j.last_result === 'KO') {
    document.getElementById('last_res').innerHTML = '<span class="bad">KO</span>';
  } else {
    document.getElementById('last_res').textContent = '—';
  }

  setTimeField('when', (j.last && j.last.when) ? j.last.when : null);

  document.getElementById('svc_sub').textContent = j.service_substate || '—';
  document.getElementById('svc_code').textContent = (j.service_exec_status === null || j.service_exec_status === undefined) ? '—' : String(j.service_exec_status);

  setTimeField('svc_start', j.service_started_at || null);
  setTimeField('svc_exit',  j.service_exited_at  || null);

  document.getElementById('log').textContent = (j.last && j.last.text) ? j.last.text : '—';

  if (j.conf) {
    if (j.conf.domains !== undefined) document.getElementById('domains').value = j.conf.domains || '';
    if (j.conf.token   !== undefined) document.getElementById('token').value   = j.conf.token || '';
  }
}

async function save() {
  const body = {
    domains: document.getElementById('domains').value.trim(),
    token:   document.getElementById('token').value.trim()
  };
  document.getElementById('busy').style.display = 'inline';
  try {
    const r = await fetch('/api/duckdns/save', {
      method:'POST',
      headers: {
        'Content-Type':'application/json',
        'Authorization': '%%AUTH%%',
        'Accept':'application/json'
      },
      body: JSON.stringify(body)
    });
    const j = await readJSONOrThrow(r);
    if (!r.ok || !j.ok) throw new Error(j.error || ('HTTP ' + r.status));
    await loadStatus();
  } catch (e) {
    alert('DuckDNS save failed: ' + e.message);
  } finally {
    document.getElementById('busy').style.display = 'none';
  }
}

async function runNow() {
  document.getElementById('busy').style.display = 'inline';
  try {
    const r = await fetch('/api/duckdns/run', {method:'POST', headers: {'Authorization': '%%AUTH%%', 'Accept':'application/json'}});
    const j = await readJSONOrThrow(r);
    if (!r.ok || !j.ok) throw new Error(j.error || ('HTTP ' + r.status));
    setTimeout(loadStatus, 900);
  } catch (e) {
    alert('DuckDNS run failed: ' + e.message);
  } finally {
    document.getElementById('busy').style.display = 'none';
  }
}

async function timer(enable) {
  document.getElementById('busy').style.display = 'inline';
  try {
    const r = await fetch('/api/duckdns/timer', {
      method:'POST',
      headers: {'Content-Type':'application/json', 'Authorization': '%%AUTH%%', 'Accept':'application/json' },
      body: JSON.stringify({enabled: !!enable})
    });
    const j = await readJSONOrThrow(r);
    if (!r.ok || !j.ok) throw new Error(j.error || ('HTTP ' + r.status));
    await loadStatus();
  } catch (e) {
    alert('DuckDNS timer change failed: ' + e.message);
  } finally {
    document.getElementById('busy').style.display = 'none';
  }
}
loadStatus();
</script>
</body></html>
"""

# ----------------------- HTML route -----------------------

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
    auth_hdr = request.headers.get("Authorization", "")

    html_page = (_DUCKDNS_HTML_TMPL
                 .replace("%%DOMAINS%%", dom)
                 .replace("%%TOKEN%%", tok)
                 .replace("%%AUTH%%", auth_hdr))

    return Response(html_page, mimetype="text/html")


# ----------------------- JSON API -----------------------

@duckdns_bp.route("/api/duckdns/status", methods=["GET"])
def api_status():
    if not _require_admin(request):
        return jsonify(ok=False, error="unauthorized"), 401

    conf = _read_conf()
    last = _last_update()
    svc = _service_details()
    tmr = _timer_details()
    last_res = _last_result()

    return jsonify(
        ok=True,
        conf={"domains": conf.get("domains",""), "token": conf.get("token","")},
        service_active=_unit_active(SERVICE),
        timer_active=_unit_active(TIMER),
        timer_enabled=_unit_enabled(TIMER),
        last=last,
        timer_next=tmr.get("timer_next"),
        timer_last_trigger=tmr.get("timer_last_trigger"),
        last_result=last_res.get("last_result"),
        last_result_line=last_res.get("last_result_line"),
        service_substate=svc.get("service_substate"),
        service_exec_status=svc.get("service_exec_status"),
        service_started_at=svc.get("service_started_at"),
        service_exited_at=svc.get("service_exited_at"),
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
