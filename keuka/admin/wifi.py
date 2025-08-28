# keuka/admin/wifi.py
# -----------------------------------------------------------------------------
# Wi-Fi admin page + JSON APIs
#
# Endpoints:
#   - GET  /admin/wifi         -> HTML page for scanning/connecting & IP mode
#   - GET  /api/wifi/scan      -> list SSIDs with RSSI/Freq
#   - POST /api/wifi/connect   -> {ssid, psk} DHCP connect
#   - POST /api/wifi/ip        -> {mode, ip_cidr?, router?, dns_csv?} DHCP/Static
#   - GET  /api/wifi/status    -> live status for STA/AP
#   - GET  /api/ap_ssid        -> lightweight helper returning AP SSID
#
# Notes:
#   - Uses the same HTML/JS template you already had (unchanged), with the
#     placeholder substitutions for ifaces & SSID exactly as before.
#   - Depends on: ui.render_page, config, and wifi_net helpers.
# -----------------------------------------------------------------------------

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..ui import render_page
from ..config import WLAN_STA_IFACE, WLAN_AP_IFACE
from ..core.utils import get_device_name, set_device_name
from ..wifi_net import (
    wifi_scan, wifi_connect, wifi_status_sta,
    ip_addr4, gw4, dns_servers, dhcpcd_current_mode, apply_network,
    ap_ssid_current,
)

# HTML template copied from the original routes_admin.py (unchanged).
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
    <span class="muted">
      STA = %%STA%% (LAN), AP = %%AP%% (<span id="hdrApSsid">%%APSSID%%</span>)
    </span>
  </div>

  <div class="topnav" style="margin:.4rem 0 .8rem 0;">
    <a href="/admin/wifi"><strong>Wi-Fi</strong></a>
    <a href="/admin/update">Update Code</a>
    <a href="/admin/terminal" target="_blank" rel="noopener">Open SSH Terminal</a>
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

    <!-- Device Name Configuration -->
    <div class="card">
      <h3 style="margin-top:0">Device Configuration</h3>
      
      <div style="max-width:420px;margin:.3rem 0 .6rem 0">
        <label>Device Name <span class="muted">(used for sensor data identification)</span></label>
        <input id="device_name" type="text" placeholder="e.g. sensor1, sensor2" maxlength="32">
        <div style="margin:.3rem 0">
          <button class="btn" id="device_name_save">Save Device Name</button>
          <span id="device_name_status" class="muted"></span>
        </div>
      </div>
    </div>

    <!-- Public IP card -->
    <div class="card">
      <h3 style="margin-top:0">Public IP</h3>
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.6rem;margin-top:.4rem">
        <div>Current Public IP: <span id="wan_ip" class="mono">—</span></div>
        <div>Last Public IP change: <span id="wan_changed" class="muted">—</span></div>
      </div>
    </div>
  </div>

  <script>
    const q = (s)=>document.querySelector(s);

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


    function renderScan(list) {
      const ul = q('#scanList'); ul.innerHTML = "";
      if (!list || !list.length) { ul.innerHTML = "<li>(none found)</li>"; return; }
      list.forEach(n => {
        const li = document.createElement('li');
        li.innerHTML = `<strong>${n.ssid}</strong> <span class="muted">(${n.signal_dbm ?? "(n/a)"} dBm)</span>`;
        li.style.cursor="pointer";
        li.onclick = () => q('#ssid').value = n.ssid;
        ul.appendChild(li);
      });
    }

    async function refreshStatus() {
      const url = window.getProxyAwareUrl ? window.getProxyAwareUrl('/api/wifi/status') : '/api/wifi/status';
      const r = await fetch(url, {cache:'no-store'});
      const j = await r.json();
      q('#status').textContent = JSON.stringify(j, null, 2);
      q('#curIp').textContent = "STA ip: " + (j.ip['%%STA%%'] || "(none)") + "   |   GW: " + (j.gateway_sta || "(none)");
      if (j.ap_ssid) {
        const el = document.getElementById('hdrApSsid');
        if (el) el.textContent = j.ap_ssid;
      }
    }

    q('#btnScan').onclick = async () => {
      q('#scanNote').textContent = "Scanning…";
      try {
        const url = window.getProxyAwareUrl ? window.getProxyAwareUrl('/api/wifi/scan') : '/api/wifi/scan';
        const r = await fetch(url, {cache:'no-store'});
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
      const payload = { ssid: q('#ssid').value.trim(), psk: q('#psk').value };
      try {
        const url = window.getProxyAwareUrl ? window.getProxyAwareUrl('/api/wifi/connect') : '/api/wifi/connect';
        const r = await fetch(url, {
          method: 'POST', headers: {'Content-Type':'application/json'},
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
        const url = window.getProxyAwareUrl ? window.getProxyAwareUrl('/api/wifi/ip') : '/api/wifi/ip';
        const r = await fetch(url, {
          method: 'POST', headers: { 'Content-Type': 'application/json'},
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


    // --- Device Name functions ---
    async function device_name_load() {
      try {
        const url = window.getProxyAwareUrl ? window.getProxyAwareUrl('/api/device/name') : '/api/device/name';
        const r = await fetch(url, { cache: 'no-store' });
        const j = await r.json();
        if (j.ok && j.device_name) {
          document.getElementById('device_name').value = j.device_name;
        }
      } catch (e) {
        console.debug('device name load:', e.message);
      }
    }

    async function device_name_save() {
      const name = document.getElementById('device_name').value.trim();
      if (!name) {
        document.getElementById('device_name_status').textContent = 'Device name cannot be empty';
        return;
      }
      
      document.getElementById('device_name_status').textContent = 'Saving...';
      try {
        const url = window.getProxyAwareUrl ? window.getProxyAwareUrl('/api/device/name') : '/api/device/name';
        const r = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ device_name: name })
        });
        const j = await r.json();
        if (j.ok) {
          document.getElementById('device_name_status').innerHTML = '<span style="color: var(--ok)">Saved</span>';
          // Refresh page header to show new name
          setTimeout(() => location.reload(), 1000);
        } else {
          document.getElementById('device_name_status').innerHTML = '<span style="color: var(--crit)">Error: ' + (j.error || 'save failed') + '</span>';
        }
      } catch (e) {
        document.getElementById('device_name_status').innerHTML = '<span style="color: var(--crit)">Error: ' + e.message + '</span>';
      }
    }

    // wire buttons
    document.getElementById('device_name_save').onclick = device_name_save;

    // initial
    refreshStatus();
    
    // Wait for proxy-aware system to initialize before loading device name
    setTimeout(() => {
      device_name_load();
    }, 100);
    
    (function wan_loop(){
      const url = window.getProxyAwareUrl ? window.getProxyAwareUrl('/api/wanip') : '/api/wanip';
      fetch(url,{cache:'no-store'}).then(r=>r.json()).then(j=>{
        document.getElementById('wan_ip').textContent = j.ip || '—';
        setTimeField('wan_changed', j.changed_at || null);
      }).catch(()=>{
        document.getElementById('wan_ip').textContent='(unavailable)';
        document.getElementById('wan_changed').textContent='(unavailable)';
      });
      setTimeout(wan_loop, 60000);
    })();
  </script>
"""

def attach(bp: Blueprint) -> None:
    # HTML page
    @bp.route("/admin/wifi")
    def admin_wifi():
        body = (_WIFI_HTML_TMPL
                .replace("%%STA%%", WLAN_STA_IFACE)
                .replace("%%AP%%", WLAN_AP_IFACE)
                .replace("%%APSSID%%", ap_ssid_current()))
        return render_page("Keuka Sensor – Wi-Fi", body)

    # JSON APIs
    @bp.route("/api/wifi/scan")
    def api_wifi_scan():
        nets = wifi_scan()
        return jsonify({"ok": True, "networks": nets})

    @bp.route("/api/wifi/connect", methods=["POST"])
    def api_wifi_connect():
        data = request.get_json(silent=True) or {}
        ssid = (data.get("ssid") or "").strip()
        psk  = data.get("psk") or ""
        ok, msg, details = wifi_connect(ssid, psk)
        status = 200 if ok else 400
        return jsonify({"ok": ok, "message": msg, **details}), status

    @bp.route("/api/wifi/ip", methods=["POST"])
    def api_wifi_ip():
        data = request.get_json(silent=True) or {}
        mode = data.get("mode")
        ip_cidr = data.get("ip_cidr") or ""
        router = data.get("router") or ""
        dns_csv = data.get("dns_csv") or ""
        ok, msg = apply_network(mode, ip_cidr, router, dns_csv)
        status = 200 if ok else 400
        return jsonify({"ok": ok, "message": msg}), status

    @bp.route("/api/wifi/status")
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
            "ap_ssid": ap_ssid_current(),
        })

    @bp.route("/api/ap_ssid")
    def api_ap_ssid():
        return jsonify({"iface": WLAN_AP_IFACE, "ssid": ap_ssid_current()})

    @bp.route("/api/device/name", methods=["GET"])
    def api_device_name_get():
        device_name = get_device_name()
        return jsonify({"ok": True, "device_name": device_name})

    @bp.route("/api/device/name", methods=["POST"])
    def api_device_name_set():
        data = request.get_json(silent=True) or {}
        device_name = (data.get("device_name") or "").strip()
        
        if not device_name:
            return jsonify({"ok": False, "error": "Device name cannot be empty"}), 400
        
        if len(device_name) > 32:
            return jsonify({"ok": False, "error": "Device name too long (max 32 characters)"}), 400
        
        success = set_device_name(device_name)
        if success:
            return jsonify({"ok": True})
        else:
            return jsonify({"ok": False, "error": "Invalid device name format (use only letters, numbers, dash, underscore)"}), 400
