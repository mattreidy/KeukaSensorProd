# -----------------------------------------------------------------------------
# Admin pages and APIs (MERGED):
#  - /admin               -> redirects to /admin/wifi
#  - /admin/wifi          simple UI to scan/connect wlan1 and set DHCP/static
#  - /api/wifi/scan       GET -> list of SSIDs with RSSI/Freq
#  - /api/wifi/connect    POST json {ssid, psk} -> connect via DHCP, wait for IP
#  - /api/wifi/ip         POST json {mode, ip_cidr?, router?, dns_csv?} -> static/DHCP
#  - /api/wifi/status     GET -> current status + IP/GW/DNS for both ifaces
#  - /api/wanip           GET -> current public IP and last-change timestamp (tracked)
#  - /admin/update        Code-only updater for keuka/ + version compare (local vs remote)
#  - /admin/version       returns local/remote commit SHAs (+ source + error)
#  - /admin/start_update  starts code-only update
#  - /admin/cancel_update cancels an in-flight update
#  - /admin/status        updater state + ONLY last attempt logs
#
# NOTE: This file enforces HTTP Basic Auth for:
#   * /admin/**
#   * /api/duckdns/**
# Credentials come from config.ADMIN_USER / config.ADMIN_PASS (env: KS_ADMIN_USER / KS_ADMIN_PASS).
# -----------------------------------------------------------------------------

from __future__ import annotations
from pathlib import Path
import json
import re
from datetime import datetime, timezone
from urllib.request import urlopen
from flask import Blueprint, request, jsonify, redirect, Response
from ui import render_page
from config import WLAN_STA_IFACE, WLAN_AP_IFACE, ADMIN_USER, ADMIN_PASS
from wifi_net import (
    wifi_scan, wifi_connect, wifi_status_sta,
    ip_addr4, gw4, dns_servers, dhcpcd_current_mode, apply_network,
)

# --- update feature imports ---
from updater import updater, APP_ROOT, REPO_URL, SERVICE_NAME
from version import get_local_commit_with_source, get_remote_commit, short_sha

# Track last seen public IP + when it changed (stored under keuka/)
WAN_TRACK = Path(APP_ROOT) / "wan_ip.json"

admin_bp = Blueprint("admin", __name__)

# ---- BASIC AUTH GUARD --------------------------------------------------------

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

def _is_duckdns_api(path: str) -> bool:
    return path.startswith("/api/duckdns")

@admin_bp.before_app_request
def _protect_admin_and_duckdns():
    """
    Enforce HTTP Basic Auth for /admin/** and /api/duckdns/** using ADMIN_USER/ADMIN_PASS.
    For API calls under /api/duckdns/** return JSON 401 so frontend fetch() never receives HTML.
    """
    path = request.path or ""
    if not (_is_admin_path(path) or _is_duckdns_api(path)):
        return  # not protected

    # If creds aren't configured, fail closed.
    if not (ADMIN_USER and ADMIN_PASS):
        return _unauthorized_json() if _is_duckdns_api(path) else _unauthorized_text()

    auth = request.authorization
    if not auth or auth.type.lower() != "basic":
        return _unauthorized_json() if _is_duckdns_api(path) else _unauthorized_text()

    if auth.username != ADMIN_USER or auth.password != ADMIN_PASS:
        return _unauthorized_json() if _is_duckdns_api(path) else _unauthorized_text()
    # otherwise OK — request continues


@admin_bp.route("/admin")
def admin_index():
    return redirect("/admin/wifi", code=302)


# --- Public WAN IP helper -----------------------------------------------------

def _fetch_public_ip() -> str | None:
    """Fast external check for IPv4; returns dotted quad or None."""
    try:
        with urlopen("https://api.ipify.org", timeout=4) as f:
            ip = f.read().decode("utf-8", "ignore").strip()
            if re.match(r"^\\d{1,3}(?:\\.\\d{1,3}){3}$", ip):
                return ip
    except Exception:
        pass
    return None


@admin_bp.route("/api/wanip")
def api_wanip():
    """
    Returns {"ok": True, "ip": "...", "changed_at": ISO8601, "checked_at": ISO8601}
    Updates WAN_TRACK if the IP changed.
    """
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    prev = {}
    try:
        if WAN_TRACK.exists():
            prev = json.loads(WAN_TRACK.read_text())
    except Exception:
        prev = {}

    prev_ip = prev.get("ip")
    prev_changed = prev.get("changed_at")

    ip = _fetch_public_ip()
    if ip and ip != prev_ip:
        prev_ip = ip
        prev_changed = now_iso
        try:
            WAN_TRACK.write_text(json.dumps({
                "ip": ip,
                "changed_at": prev_changed,
                "checked_at": now_iso,
            }))
        except Exception:
            pass
    else:
        try:
            if ip:
                WAN_TRACK.write_text(json.dumps({
                    "ip": prev_ip or ip,
                    "changed_at": prev_changed,
                    "checked_at": now_iso,
                }))
        except Exception:
            pass

    return jsonify({
        "ok": True,
        "ip": ip or (prev_ip or None),
        "changed_at": prev_changed,
        "checked_at": now_iso,
    })


# -------- HTML templates (plain strings; placeholders replaced) ---------------

_WIFI_HTML_TMPL = """
  <style>
    /* Make form controls clearly visible on mobile & dark mode */
    form.stack { display: block; }
    label { display: block; margin: .6rem 0; }
    input[type="text"], input[type="password"], select {
      width: 100%;
      padding: .5rem .6rem;
      border: 1px solid #4a4a4a;
      border-radius: .45rem;
      background: #fff;     /* light background */
      color: #111;          /* dark text */
      outline: none;
    }
    @media (prefers-color-scheme: dark) {
      input[type="text"], input[type="password"], select {
        background: #1f1f1f;
        color: #f1f1f1;
        border-color: #666;
      }
      input::placeholder { color: #bbb; }
    }
    input::placeholder { color: #666; }
    .topnav a { margin-right:.8rem; text-decoration:none; }
    .ok { color:#0a7d00; font-weight:600; }
    .bad { color:#b00020; font-weight:600; }
    .muted { color:#666; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    .card { border: 1px solid #ddd; border-radius: 8px; padding: 16px; margin: .6rem 0; }
  </style>

  <div class="flex" style="gap:.8rem;align-items:center;margin-bottom:.3rem">
    <h1 style="margin:0">Wi-Fi Setup</h1>
    <span class="muted">STA = %%STA%% (LAN), AP = %%AP%% (KeukaSensor)</span>
  </div>

  <div class="topnav" style="margin:.4rem 0 .8rem 0;">
    <a href="/admin/wifi"><strong>Wi-Fi</strong></a>
    <a href="/admin/update">Update Code</a>
    <a href="/admin/duckdns">DuckDNS</a>
  </div>

  <div class="grid" style="margin-top:.6rem">
    <div class="card">
      <h3 style="margin-top:0">Scan & Connect (STA %%STA%%)</h3>
      <div class="flex" style="gap:.5rem">
        <button class="btn" id="btnScan">Scan</button>
        <span id="scanNote" class="muted"></span>
      </div>
      <ul id="scanList" style="margin:.5rem 0 1rem 0;padding-left:1rem"></ul>

      <form id="connectForm" class="stack" style="max-width:420px">
        <label>SSID
          <input id="ssid" name="ssid" type="text" required placeholder="Network name">
        </label>
        <label>Password (leave blank for open)
          <input id="psk" name="psk" type="password" placeholder="••••••••">
        </label>
        <button class="btn" type="submit">Connect (DHCP)</button>
        <div id="connectNote" class="muted"></div>
      </form>
    </div>

    <div class="card">
      <h3 style="margin-top:0">IP on STA (%%STA%%)</h3>
      <div id="curIp" class="mono" style="margin:.3rem 0"></div>
      <form id="ipForm" class="stack" style="max-width:420px">
        <label>
          Mode
          <select id="mode" name="mode">
            <option value="dhcp">DHCP (default)</option>
            <option value="static">Static</option>
          </select>
        </label>
        <div id="staticFields" style="display:none">
          <label>IPv4/CIDR (e.g., 192.168.2.50/24)
            <input id="ip_cidr" name="ip_cidr" type="text" placeholder="192.168.2.50/24">
          </label>
          <label>Gateway
            <input id="router" name="router" type="text" placeholder="192.168.2.1">
          </label>
          <label>DNS (comma separated)
            <input id="dns_csv" name="dns_csv" type="text" placeholder="8.8.8.8,1.1.1.1">
          </label>
        </div>
        <button class="btn" type="submit">Apply</button>
        <div id="ipNote" class="muted"></div>
      </form>
    </div>

    <div class="card">
      <h3 style="margin-top:0">Status (both ifaces)</h3>
      <pre id="status" class="mono" style="white-space:pre-wrap;margin-top:.4rem"></pre>
    </div>

    <!-- DuckDNS + Public IP card -->
    <div class="card">
      <h3 style="margin-top:0">DuckDNS &amp; Public IP</h3>

      <div class="flex" style="gap:.5rem;flex-wrap:wrap;margin:.3rem 0 .6rem 0">
        <div style="min-width:260px;flex:1">
          <label>DuckDNS Subdomain(s) <span class="muted">(comma separated)</span></label>
          <input id="dd_domains" type="text" placeholder="e.g. keukasensor1,backupname">
        </div>
        <div style="min-width:260px;flex:1">
          <label>DuckDNS Token</label>
          <input id="dd_token" type="password" placeholder="DuckDNS account token">
        </div>
      </div>

      <div style="display:flex;gap:.5rem;flex-wrap:wrap;margin:.2rem 0 .6rem 0">
        <button class="btn" id="dd_btn_save">Save</button>
        <button class="btn" id="dd_btn_run">Run update now</button>
        <button class="btn" id="dd_btn_toggle">Toggle hourly timer</button>
        <span class="muted" id="dd_busy" style="display:none">Working…</span>
      </div>

      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.6rem">
        <div>Status (service, oneshot): <span id="dd_svc" class="muted">—</span></div>
        <div>Timer: <span id="dd_tmr" class="muted">—</span></div>
        <div>Next run: <span id="dd_next" class="muted">—</span></div>
        <div>Last result: <span id="dd_res" class="muted">—</span></div>
      </div>

      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.6rem;margin-top:.4rem">
        <div>Last DuckDNS update: <span id="dd_last" class="muted">—</span></div>
        <div>SubState: <span id="dd_sub" class="muted">—</span></div>
        <div>ExecMainStatus: <span id="dd_code" class="muted">—</span></div>
        <div>Last start: <span id="dd_start" class="muted">—</span></div>
        <div>Last exit: <span id="dd_exit" class="muted">—</span></div>
      </div>

      <hr style="margin:.8rem 0">

      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.6rem">
        <div>Current Public IP: <span id="wan_ip" class="mono">—</span></div>
        <div>Last Public IP change: <span id="wan_changed" class="muted">—</span></div>
      </div>
    </div>
  </div>

  <script>
    const q = (s)=>document.querySelector(s);
    function fmtLocal(iso) {
      if (!iso) return '—';   // nothing to format

      try {
        const d = new Date(iso);
        if (isNaN(d)) {
          return iso;
        }
        return new Intl.DateTimeFormat(undefined, {
          dateStyle: 'medium',
          timeStyle: 'short'
        }).format(d);
      } catch {
        return iso;
      }
    }

    let dd_state = { timer_enabled: false, timer_active: false };

    function renderScan(list) {
      const ul = q('#scanList'); ul.innerHTML = "";
      if (!list || !list.length) { ul.innerHTML = "<li>(none found)</li>"; return; }
      list.forEach(n => {
        const li = document.createElement('li');
        li.innerHTML = `<strong>${n.ssid}</strong> <span class="muted">(${n.signal_dbm??"(n/a)"} dBm @ ${n.freq_mhz??"(n/a)"} MHz)</span>`;
        li.style.cursor="pointer";
        li.onclick = () => q('#ssid').value = n.ssid;
        ul.appendChild(li);
      });
    }

    async function refreshStatus() {
      const r = await fetch('/api/wifi/status', {cache:'no-store'});
      const j = await r.json();
      q('#status').textContent = JSON.stringify(j, null, 2);
      q('#curIp').textContent = "STA ip: " + (j.ip['%%STA%%'] || "(none)") + "   |   GW: " + (j.gateway_sta || "(none)");

      if (j.dhcpcd && j.dhcpcd.mode === "static") {
        q('#mode').value = "static";
        q('#staticFields').style.display = "block";
        q('#ip_cidr').value = j.dhcpcd.ip || "";
        q('#router').value = j.dhcpcd.router || "";
        q('#dns_csv').value = (j.dhcpcd.dns||[]).join(", ");
      } else {
        q('#mode').value = "dhcp";
        q('#staticFields').style.display = "none";
        const cur = j.ip['%%STA%%'];
        if (cur && cur.includes('/')) {
          const ipOnly = cur.split('/')[0];
          q('#ip_cidr').placeholder = ipOnly.replace(/\d+$/, '50') + "/24";
        }
      }
    }

    q('#btnScan').onclick = async () => {
      q('#scanNote').textContent = "Scanning…";
      try {
        const r = await fetch('/api/wifi/scan', {cache:'no-store'});
        const j = await r.json();
        renderScan(j.networks||[]);
        q('#scanNote').textContent = (j.networks && j.networks.length) ? "Done." : "No networks found.";
      } catch(e) {
        q('#scanNote').textContent = "Scan failed.";
      }
    };

    q('#mode').onchange = () => {
      q('#staticFields').style.display = (q('#mode').value === "static") ? "block" : "none";
    };

    q('#connectForm').onsubmit = async (ev) => {
      ev.preventDefault();
      q('#connectNote').textContent = "Connecting…";
      const payload = {
        ssid: q('#ssid').value.trim(),
        psk: q('#psk').value,
      };
      try {
        const r = await fetch('/api/wifi/connect', {
          method: 'POST',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify(payload)
        });
        const j = await r.json();
        if (!r.ok || !j.ok) throw new Error(j.message||"failed");
        q('#connectNote').textContent = "Connected: " + (j.ip || "(no ip yet)");
        await refreshStatus();
      } catch(e) {
        q('#connectNote').textContent = "Error: " + e.message;
      }
    };

    q('#ipForm').onsubmit = async (ev) => {
      ev.preventDefault();
      q('#ipNote').textContent = "Applying…";
      const payload = {
        mode: q('#mode').value,
        ip_cidr: q('#ip_cidr').value.trim(),
        router: q('#router').value.trim(),
        dns_csv: q('#dns_csv').value.trim()
      };
      try {
        const r = await fetch('/api/wifi/ip', {
          method: 'POST',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify(payload)
        });
        const j = await r.json();
        if (!r.ok || !j.ok) throw new Error(j.message||'failed');
        q('#ipNote').textContent = "Applied.";
        await new Promise(res => setTimeout(res, 1000));
        await refreshStatus();
      } catch(e) {
        q('#ipNote').textContent = "Error: " + e.message;
      }
    };

    // --- DuckDNS helpers on this page (reuses /api/duckdns/* endpoints) ---
    async function dd_load() {
      try {
        const r = await fetch('/api/duckdns/status', { cache: 'no-store' });
        if (r.status === 401) throw new Error('auth required: open /admin/duckdns once');
        const j = await r.json();
        if (!j.ok) throw new Error(j.error || 'status failed');

        q('#dd_svc').innerHTML = j.service_active ? '<span class="ok">running</span>' : '<span class="muted">inactive</span>';
        q('#dd_tmr').textContent = (j.timer_enabled ? 'enabled' : 'disabled') + ' / ' + (j.timer_active ? 'active' : 'inactive');
        q('#dd_next').textContent = j.timer_next || '—';

        if (j.last_result === 'OK') {
          q('#dd_res').innerHTML = '<span class="ok">OK</span>';
        } else if (j.last_result === 'KO') {
          q('#dd_res').innerHTML = '<span class="bad">KO</span>';
        } else {
          q('#dd_res').textContent = '—';
        }

        q('#dd_last').textContent = (j.last && j.last.when) ? j.last.when : '—';
        q('#dd_sub').textContent = j.service_substate || '—';
        q('#dd_code').textContent = (j.service_exec_status === null || j.service_exec_status === undefined) ? '—' : String(j.service_exec_status);
        q('#dd_start').textContent = j.service_started_at || '—';
        q('#dd_exit').textContent = j.service_exited_at || '—';

        dd_state.timer_enabled = !!j.timer_enabled;
        dd_state.timer_active  = !!j.timer_active;
        updateToggleLabel();
      } catch (e) {
        q('#dd_svc').textContent = '(unavailable)';
        q('#dd_tmr').textContent = '(unavailable)';
        q('#dd_next').textContent = '—';
        q('#dd_res').textContent = '—';
        q('#dd_last').textContent = '—';
        q('#dd_sub').textContent = '—';
        q('#dd_code').textContent = '—';
        q('#dd_start').textContent = '—';
        q('#dd_exit').textContent = '—';
        console.debug('duckdns status:', e.message);
      }
    }

    function updateToggleLabel() {
      const b = document.getElementById('dd_btn_toggle');
      b.textContent = dd_state.timer_enabled ? 'Disable hourly timer' : 'Enable hourly timer';
    }

    async function dd_save() {
      const body = {
        domains: document.getElementById('dd_domains').value.trim(),
        token:   document.getElementById('dd_token').value.trim()
      };
      document.getElementById('dd_busy').style.display = 'inline';
      try {
        const r = await fetch('/api/duckdns/save', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });
        if (r.status === 401) throw new Error('auth required');
        const j = await r.json();
        if (!j.ok) throw new Error(j.error || 'save failed');
        await dd_load();
      } catch (e) {
        alert('DuckDNS save failed: ' + e.message);
      } finally {
        document.getElementById('dd_busy').style.display = 'none';
      }
    }

    async function dd_run() {
      document.getElementById('dd_busy').style.display = 'inline';
      try {
        const r = await fetch('/api/duckdns/run', { method: 'POST' });
        if (r.status === 401) throw new Error('auth required');
        const j = await r.json();
        if (!j.ok) throw new Error(j.error || 'run failed');
        await new Promise(res => setTimeout(res, 700));
        await dd_load();
      } catch (e) {
        alert('DuckDNS run failed: ' + e.message);
      } finally {
        document.getElementById('dd_busy').style.display = 'none';
      }
    }

    async function dd_toggle() {
      document.getElementById('dd_busy').style.display = 'inline';
      try {
        const want = !dd_state.timer_enabled;
        const r = await fetch('/api/duckdns/timer', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: want })
        });
        if (r.status === 401) throw new Error('auth required');
        const j = await r.json();
        if (!j.ok) throw new Error(j.error || 'timer change failed');
        dd_state.timer_enabled = want;
        updateToggleLabel();
        await dd_load();
      } catch (e) {
        alert('DuckDNS timer change failed: ' + e.message);
      } finally {
        document.getElementById('dd_busy').style.display = 'none';
      }
    }

    // Bind DuckDNS buttons
    document.getElementById('dd_btn_save').onclick = dd_save;
    document.getElementById('dd_btn_run').onclick = dd_run;
    document.getElementById('dd_btn_toggle').onclick = dd_toggle;

    // --- WAN IP (public) ---
    async function wan_refresh() {
      try {
        const r = await fetch('/api/wanip', { cache: 'no-store' });
        const j = await r.json();
        document.getElementById('wan_ip').textContent = j.ip || '—';
        const changedEl = document.getElementById('wan_changed');
        changedEl.textContent = fmtLocal(j.changed_at);
        changedEl.title = j.changed_at || '';
      } catch (e) {
        document.getElementById('wan_ip').textContent = '(unavailable)';
        document.getElementById('wan_changed').textContent = '(unavailable)';
      }
    }

    // initial
    refreshStatus();
    dd_load();
    wan_refresh();
    setInterval(wan_refresh, 60_000);
  </script>
"""

# -------- HTML page: Wi-Fi --------
@admin_bp.route("/admin/wifi")
def admin_wifi():
    body = _WIFI_HTML_TMPL.replace("%%STA%%", WLAN_STA_IFACE).replace("%%AP%%", WLAN_AP_IFACE)
    return render_page("Keuka Sensor – Wi-Fi", body)


# -------- HTML page: Update Code --------
@admin_bp.route("/admin/update")
def admin_update():
    body = """
      <style>
        .topnav a { margin-right:.8rem; text-decoration:none; }
        .badge { display:inline-block;padding:.15rem .45rem;border-radius:.4rem;background:#444;color:#fff; }
        .badge.ok { background:#184; color:#fff; }
        .badge.warn { background:#a60; color:#fff; }
      </style>

      <div class="flex" style="gap:.8rem;align-items:center;margin-bottom:.3rem">
        <h1 style="margin:0">Update Code (keuka/ only)</h1>
        <span class="muted">Repo: {REPO_URL}</span>
      </div>

      <div class="topnav" style="margin:.4rem 0 .8rem 0;">
        <a href="/admin/wifi">Wi-Fi</a>
        <a href="/admin/update"><strong>Update Code</strong></a>
        <a href="/admin/duckdns">DuckDNS</a>
      </div>

      <div class="card">
        <h3>Version status</h3>
        <div id="versionRow" style="display:flex;gap:1rem;align-items:center;">
          <div>Local: <code id="localSha">-</code> <span class="muted" id="localSrc"></span></div>
          <div>Remote: <code id="remoteSha">-</code></div>
          <span id="verBadge" class="badge">checking...</span>
          <button id="btnRefreshVer" class="btn btn-secondary" onclick="refreshVersion()">Refresh</button>
          <span id="verErr" class="muted" style="margin-left:1rem;"></span>
        </div>
      </div>

      <div class="card">
        <h3>Code-only update (keuka/ folder)</h3>
        <p>This pulls the latest code from <code>{REPO_URL}</code> (shallow clone),
        stages only the <code>keuka/</code> directory, backs up the current code on this Pi,
        applies the update, and restarts <code>{SERVICE_NAME}</code>.</p>

        <div style="display:flex;gap:.5rem;align-items:center;margin:.6rem 0;">
          <button id="btnStart" onclick="startUpdate()" class="btn">Start Update</button>
          <button id="btnCancel" onclick="cancelUpdate()" class="btn btn-secondary" disabled>Cancel</button>
          <span id="stateBadge" class="badge">state: <span id="stateText">-</span></span>
        </div>

        <div>
          <strong>Status log</strong>
          <pre id="logbox" style="height:300px;overflow:auto;background:#111;color:#ddd;border:1px solid #333;padding:.5rem;border-radius:.4rem;"></pre>
        </div>
      </div>

      <script>
      const logbox = document.getElementById('logbox');
      const stateText = document.getElementById('stateText');
      const btnStart = document.getElementById('btnStart');
      const btnCancel = document.getElementById('btnCancel');
      const localSha = document.getElementById('localSha');
      const localSrc = document.getElementById('localSrc');
      const remoteSha = document.getElementById('remoteSha');
      const verBadge = document.getElementById('verBadge');
      const verErr = document.getElementById('verErr');
      let pollTimer = null;

      function setButtons(state) {
        if (state === 'running') {
          btnStart.disabled = true;
          btnCancel.disabled = false;
        } else {
          btnStart.disabled = false;
          btnCancel.disabled = true;
        }
      }

      function setBadge(local, remote, err) {
        if (err) {
          verBadge.textContent = 'error';
          verBadge.className = 'badge';
          verErr.textContent = err;
          return;
        }
        verErr.textContent = '';
        if (local && remote && local !== remote) {
          verBadge.textContent = 'Update available';
          verBadge.className = 'badge warn';
        } else if (local && remote) {
          verBadge.textContent = 'Up to date';
          verBadge.className = 'badge ok';
        } else {
          verBadge.textContent = 'Unknown';
          verBadge.className = 'badge';
        }
      }

      async function refreshVersion() {
        verBadge.textContent = 'checking...';
        verErr.textContent = '';
        try {
          const r = await fetch('/admin/version?cb=' + Date.now(), {
            headers: { 'Accept': 'application/json' }
          });
          const txt = await r.text();
          let v;
          try { v = JSON.parse(txt); } catch (e) { throw new Error(txt.slice(0,200)); }
          localSha.textContent = v.local_short || '-';
          localSrc.textContent = v.local_source ? '(' + v.local_source + ')' : '';
          remoteSha.textContent = v.remote_short || '-';
          setBadge(v.local, v.remote, v.error);
        } catch (e) {
          setBadge(null, null, e.message || 'fetch failed');
        }
      }

      async function startUpdate() {
        btnStart.disabled = true;
        try {
          await fetch('/admin/start_update', { method: 'POST' });
        } catch (e) {
          appendLog('Failed to start: ' + e.message);
        } finally {
          setTimeout(pollStatus, 200);
        }
      }

      async function cancelUpdate() {
        try {
          await fetch('/admin/cancel_update', { method: 'POST' });
        } catch (e) {
          appendLog('Failed to cancel: ' + e.message);
        }
      }

      function appendLog(line) {
        const atBottom = (logbox.scrollTop + logbox.clientHeight + 8) >= logbox.scrollHeight;
        logbox.textContent = line ? (logbox.textContent + (line.endsWith('\\n') ? line : (line + '\\n'))) : logbox.textContent;
        if (atBottom) logbox.scrollTop = logbox.scrollHeight;
      }

      async function pollStatus() {
        try {
          const r = await fetch('/admin/status?cb=' + Date.now(), { headers: { 'Accept':'application/json' } });
          const s = await r.json();
          stateText.textContent = s.state;
          setButtons(s.state);
          if (Array.isArray(s.logs) && s.logs.length) {
            logbox.textContent = s.logs.join('\\n');
          }
          if (s.state === 'running') {
            pollTimer = setTimeout(pollStatus, 600);
          } else {
            await refreshVersion();
            let tries = 8;
            const tick = async () => {
              await new Promise(res => setTimeout(res, 1500));
              await refreshVersion();
              if (--tries > 0) tick();
            };
            tick();
          }
        } catch (e) {
          appendLog('[note] status temporarily unavailable...');
          pollTimer = setTimeout(pollStatus, 1200);
        }
      }

      refreshVersion();
      pollStatus();
      </script>
    """
    return render_page("Keuka Sensor – Update Code", body)


# -------- JSON APIs: Wi-Fi --------

@admin_bp.route("/api/wifi/scan")
def api_wifi_scan():
    nets = wifi_scan()
    return jsonify({"ok": True, "networks": nets})


@admin_bp.route("/api/wifi/connect", methods=["POST"])
def api_wifi_connect():
    data = request.get_json(silent=True) or {}
    ssid = (data.get("ssid") or "").strip()
    psk  = data.get("psk") or ""
    ok, msg, details = wifi_connect(ssid, psk)
    status = 200 if ok else 400
    return jsonify({"ok": ok, "message": msg, **details}), status


@admin_bp.route("/api/wifi/ip", methods=["POST"])
def api_wifi_ip():
    data = request.get_json(silent=True) or {}
    mode = data.get("mode")
    ip_cidr = data.get("ip_cidr") or ""
    router = data.get("router") or ""
    dns_csv = data.get("dns_csv") or ""
    ok, msg = apply_network(mode, ip_cidr, router, dns_csv)
    status = 200 if ok else 400
    return jsonify({"ok": ok, "message": msg}), status


@admin_bp.route("/api/wifi/status")
def api_wifi_status():
    st = wifi_status_sta()
    return jsonify({
        "sta": st,
        "ip": {
            WLAN_STA_IFACE: ip_addr4(WLAN_STA_IFACE),
            WLAN_AP_IFACE:  ip_addr4(WLAN_AP_IFACE),
        },
        "gateway_sta": gw4(WLAN_STA_IFACE),
        "gateway_ap":  gw4(WLAN_AP_IFACE),
        "dns": dns_servers(),
        "dhcpcd": dhcpcd_current_mode(),
        "ifaces": {"sta": WLAN_STA_IFACE, "ap": WLAN_AP_IFACE},
    })


# -------- JSON APIs: Update feature --------

@admin_bp.route("/admin/start_update", methods=["POST"])
def admin_start_update():
    started = updater.start()
    return Response(json.dumps({"started": started}), mimetype="application/json")


@admin_bp.route("/admin/cancel_update", methods=["POST"])
def admin_cancel_update():
    updater.cancel()
    return Response(json.dumps({"canceled": True}), mimetype="application/json")


@admin_bp.route("/admin/status")
def admin_status():
    state, logs, t0, t1 = updater.get_logs()
    return Response(json.dumps({
        "state": state,
        "logs": logs[-1000:],
        "started_at": t0,
        "finished_at": t1,
    }), mimetype="application/json")


@admin_bp.route("/admin/version")
def admin_version():
    err = None
    local = None
    local_source = "none"
    remote = None
    try:
        local, local_source = get_local_commit_with_source(APP_ROOT)
    except Exception as e:
        err = f"local: {e}"
    try:
        remote = get_remote_commit(REPO_URL)
    except Exception as e:
        err = (err + "; " if err else "") + f"remote: {e}"
    return Response(json.dumps({
        "local": local,
        "remote": remote,
        "local_short": short_sha(local),
        "remote_short": short_sha(remote),
        "local_source": local_source,
        "up_to_date": (bool(local) and bool(remote) and local == remote),
        "error": err
    }), mimetype="application/json")