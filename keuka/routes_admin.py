# routes_admin.py
# -----------------------------------------------------------------------------
# Admin pages and APIs (MERGED):
#  - /admin               -> redirects to /admin/wifi
#  - /admin/wifi          simple UI to scan/connect wlan1 and set DHCP/static
#  - /api/wifi/scan       GET -> list of SSIDs with RSSI/Freq
#  - /api/wifi/connect    POST json {ssid, psk} -> connect via DHCP, wait for IP
#  - /api/wifi/ip         POST json {mode, ip_cidr?, router?, dns_csv?} -> static/DHCP
#  - /api/wifi/status     GET -> current status + IP/GW/DNS for both ifaces
#  - /admin/update        Code-only updater for keuka/ + version compare (local vs remote)
#  - /admin/version       returns local/remote commit SHAs (+ source + error)
#  - /admin/start_update  starts code-only update
#  - /admin/cancel_update cancels an in-flight update
#  - /admin/status        updater state + ONLY last attempt logs
# -----------------------------------------------------------------------------

from __future__ import annotations

import json
from flask import Blueprint, request, jsonify, redirect, Response

from ui import render_page
from config import WLAN_STA_IFACE, WLAN_AP_IFACE
from wifi_net import (
    wifi_scan, wifi_connect, wifi_status_sta,
    ip_addr4, gw4, dns_servers, dhcpcd_current_mode, apply_network,
)

# --- update feature imports ---
from updater import updater, APP_ROOT, REPO_URL, SERVICE_NAME
from version import get_local_commit_with_source, get_remote_commit, short_sha

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/admin")
def admin_index():
    return redirect("/admin/wifi", code=302)


# -------- HTML page: Wi-Fi --------

@admin_bp.route("/admin/wifi")
def admin_wifi():
    body = f"""
      <style>
        /* Make form controls clearly visible on mobile & dark mode */
        form.stack {{ display: block; }}
        label {{ display: block; margin: .6rem 0; }}
        input[type="text"], input[type="password"], select {{
          width: 100%;
          padding: .5rem .6rem;
          border: 1px solid #4a4a4a;
          border-radius: .45rem;
          background: #fff;     /* light background */
          color: #111;          /* dark text */
          outline: none;
        }}
        @media (prefers-color-scheme: dark) {{
          input[type="text"], input[type="password"], select {{
            background: #1f1f1f;
            color: #f1f1f1;
            border-color: #666;
          }}
          input::placeholder {{ color: #bbb; }}
        }}
        input::placeholder {{ color: #666; }}
        .topnav a {{ margin-right:.8rem; text-decoration:none; }}
      </style>

      <div class="flex" style="gap:.8rem;align-items:center;margin-bottom:.3rem">
        <h1 style="margin:0">Wi-Fi Setup</h1>
        <span class="muted">STA = {WLAN_STA_IFACE} (LAN), AP = {WLAN_AP_IFACE} (KeukaSensor)</span>
      </div>

      <div class="topnav" style="margin:.4rem 0 .8rem 0;">
        <a href="/admin/wifi"><strong>Wi-Fi</strong></a>
        <a href="/admin/update">Update Code</a>
      </div>

      <div class="grid" style="margin-top:.6rem">
        <div class="card">
          <h3 style="margin-top:0">Scan & Connect (STA {WLAN_STA_IFACE})</h3>
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
          <h3 style="margin-top:0">IP on STA ({WLAN_STA_IFACE})</h3>
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
      </div>

      <script>
        const q = (s)=>document.querySelector(s);

        function renderScan(list) {{
          const ul = q('#scanList'); ul.innerHTML = "";
          if (!list || !list.length) {{ ul.innerHTML = "<li>(none found)</li>"; return; }}
          list.forEach(n => {{
            const li = document.createElement('li');
            li.innerHTML = `<strong>${{n.ssid}}</strong> <span class="muted">(${{n.signal_dbm??"(n/a)"}} dBm @ ${{n.freq_mhz??"(n/a)"}} MHz)</span>`;
            li.style.cursor="pointer";
            li.onclick = () => q('#ssid').value = n.ssid;
            ul.appendChild(li);
          }});
        }}

        async function refreshStatus() {{
          const r = await fetch('/api/wifi/status', {{cache:'no-store'}});
          const j = await r.json();
          q('#status').textContent = JSON.stringify(j, null, 2);
          q('#curIp').textContent = "STA ip: " + (j.ip['{WLAN_STA_IFACE}'] || "(none)") + "   |   GW: " + (j.gateway_sta || "(none)");

          if (j.dhcpcd && j.dhcpcd.mode === "static") {{
            q('#mode').value = "static";
            q('#staticFields').style.display = "block";
            q('#ip_cidr').value = j.dhcpcd.ip || "";
            q('#router').value = j.dhcpcd.router || "";
            q('#dns_csv').value = (j.dhcpcd.dns||[]).join(", ");
          }} else {{
            q('#mode').value = "dhcp";
            q('#staticFields').style.display = "none";
            const cur = j.ip['{WLAN_STA_IFACE}'];
            if (cur && cur.includes('/')) {{
              const ipOnly = cur.split('/')[0];
              q('#ip_cidr').placeholder = ipOnly.replace(/\\d+$/, '50') + "/24";
            }}
          }}
        }}

        q('#btnScan').onclick = async () => {{
          q('#scanNote').textContent = "Scanning…";
          try {{
            const r = await fetch('/api/wifi/scan', {{cache:'no-store'}});
            const j = await r.json();
            renderScan(j.networks||[]);
            q('#scanNote').textContent = (j.networks && j.networks.length) ? "Done." : "No networks found.";
          }} catch(e) {{
            q('#scanNote').textContent = "Scan failed.";
          }}
        }};

        q('#mode').onchange = () => {{
          q('#staticFields').style.display = (q('#mode').value === "static") ? "block" : "none";
        }};

        q('#connectForm').onsubmit = async (ev) => {{
          ev.preventDefault();
          q('#connectNote').textContent = "Connecting…";
          const payload = {{
            ssid: q('#ssid').value.trim(),
            psk: q('#psk').value,
          }};
          try {{
            const r = await fetch('/api/wifi/connect', {{
              method: 'POST',
              headers: {{'Content-Type':'application/json'}},
              body: JSON.stringify(payload)
            }});
            const j = await r.json();
            if (!r.ok || !j.ok) throw new Error(j.message||"failed");
            q('#connectNote').textContent = "Connected: " + (j.ip || "(no ip yet)");
            await refreshStatus();
          }} catch(e) {{
            q('#connectNote').textContent = "Error: " + e.message;
          }}
        }};

        q('#ipForm').onsubmit = async (ev) => {{
          ev.preventDefault();
          q('#ipNote').textContent = "Applying…";
          const payload = {{
            mode: q('#mode').value,
            ip_cidr: q('#ip_cidr').value.trim(),
            router: q('#router').value.trim(),
            dns_csv: q('#dns_csv').value.trim()
          }};
          try {{
            const r = await fetch('/api/wifi/ip', {{
              method: 'POST',
              headers: {{'Content-Type':'application/json'}},
              body: JSON.stringify(payload)
            }});
            const j = await r.json();
            if (!r.ok || !j.ok) throw new Error(j.message||"failed");
            q('#ipNote').textContent = "Applied.";
            await new Promise(res => setTimeout(res, 1000));
            await refreshStatus();
          }} catch(e) {{
            q('#ipNote').textContent = "Error: " + e.message;
          }}
        }};

        // initial
        refreshStatus();
      </script>
    """
    return render_page("Keuka Sensor – Wi-Fi", body)


# -------- HTML page: Update Code --------

@admin_bp.route("/admin/update")
def admin_update():
    body = f"""
      <style>
        .topnav a {{ margin-right:.8rem; text-decoration:none; }}
        .badge {{ display:inline-block;padding:.15rem .45rem;border-radius:.4rem;background:#444;color:#fff; }}
        .badge.ok {{ background:#184; color:#fff; }}
        .badge.warn {{ background:#a60; color:#fff; }}
      </style>

      <div class="flex" style="gap:.8rem;align-items:center;margin-bottom:.3rem">
        <h1 style="margin:0">Update Code (keuka/ only)</h1>
        <span class="muted">Repo: {REPO_URL}</span>
      </div>

      <div class="topnav" style="margin:.4rem 0 .8rem 0;">
        <a href="/admin/wifi">Wi-Fi</a>
        <a href="/admin/update"><strong>Update Code</strong></a>
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

      function setButtons(state) {{
        if (state === 'running') {{
          btnStart.disabled = true;
          btnCancel.disabled = false;
        }} else {{
          btnStart.disabled = false;
          btnCancel.disabled = true;
        }}
      }}

      function setBadge(local, remote, err) {{
        if (err) {{
          verBadge.textContent = 'error';
          verBadge.className = 'badge';
          verErr.textContent = err;
          return;
        }}
        verErr.textContent = '';
        if (local && remote && local !== remote) {{
          verBadge.textContent = 'Update available';
          verBadge.className = 'badge warn';
        }} else if (local && remote) {{
          verBadge.textContent = 'Up to date';
          verBadge.className = 'badge ok';
        }} else {{
          verBadge.textContent = 'Unknown';
          verBadge.className = 'badge';
        }}
      }}

      async function refreshVersion() {{
        verBadge.textContent = 'checking...';
        verErr.textContent = '';
        try {{
          const r = await fetch('/admin/version?cb=' + Date.now(), {{
            headers: {{ 'Accept': 'application/json' }}
          }});
          const txt = await r.text();
          let v;
          try {{ v = JSON.parse(txt); }} catch (e) {{ throw new Error(txt.slice(0,200)); }}
          localSha.textContent = v.local_short || '-';
          localSrc.textContent = v.local_source ? '(' + v.local_source + ')' : '';
          remoteSha.textContent = v.remote_short || '-';
          setBadge(v.local, v.remote, v.error);
        }} catch (e) {{
          setBadge(null, null, e.message || 'fetch failed');
        }}
      }}

      async function startUpdate() {{
        btnStart.disabled = true;
        try {{
          await fetch('/admin/start_update', {{ method: 'POST' }});
        }} catch (e) {{
          appendLog('Failed to start: ' + e.message);
        }} finally {{
          setTimeout(pollStatus, 200);
        }}
      }}

      async function cancelUpdate() {{
        try {{
          await fetch('/admin/cancel_update', {{ method: 'POST' }});
        }} catch (e) {{
          appendLog('Failed to cancel: ' + e.message);
        }}
      }}

      function appendLog(line) {{
        const atBottom = (logbox.scrollTop + logbox.clientHeight + 8) >= logbox.scrollHeight;
        logbox.textContent += (line.endsWith('\\n') ? line : (line + '\\n'));
        if (atBottom) logbox.scrollTop = logbox.scrollHeight;
      }}

      async function pollStatus() {{
        try {{
          const r = await fetch('/admin/status?cb=' + Date.now(), {{ headers: {{ 'Accept':'application/json' }} }});
          const s = await r.json();
          stateText.textContent = s.state;
          setButtons(s.state);
          if (Array.isArray(s.logs) && s.logs.length) {{
            logbox.textContent = s.logs.join('\\n');
          }}
          if (s.state === 'running') {{
            pollTimer = setTimeout(pollStatus, 600);
          }} else {{
            await refreshVersion();
            // Retry version a few times after finish (detached apply)
            let tries = 8;
            const tick = async () => {{
              await new Promise(res => setTimeout(res, 1500));
              await refreshVersion();
              if (--tries > 0) tick();
            }};
            tick();
          }}
        }} catch (e) {{
          appendLog('[note] status temporarily unavailable...');
          pollTimer = setTimeout(pollStatus, 1200);
        }}
      }}

      // initial
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
    # Return explicit error info and source of the local value
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
        "local_source": local_source,  # "marker-keuka" | "marker-root" | "git" | "none"
        "up_to_date": (bool(local) and bool(remote) and local == remote),
        "error": err
    }), mimetype="application/json")
