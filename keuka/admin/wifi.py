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

from ui import render_page
from config import WLAN_STA_IFACE, WLAN_AP_IFACE
from wifi_net import (
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
    <a href="/admin/duckdns">DuckDNS</a>
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
      if (j.ap_ssid) {
        const el = document.getElementById('hdrApSsid');
        if (el) el.textContent = j.ap_ssid;
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
      const payload = { ssid: q('#ssid').value.trim(), psk: q('#psk').value };
      try {
        const r = await fetch('/api/wifi/connect', {
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
        const r = await fetch('/api/wifi/ip', {
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

    // --- DuckDNS helpers on this page (reuses /api/duckdns/* endpoints) ---
    async function dd_load() {
      try {
        const r = await fetch('/api/duckdns/status', { cache: 'no-store' });
        if (r.status === 401) throw new Error('auth required: open /admin/duckdns once');
        const j = await r.json();
        if (!j.ok) throw new Error(j.error || 'status failed');

        q('#dd_svc').innerHTML = j.service_active ? '<span class="ok">running</span>' : '<span class="muted">inactive</span>';
        q('#dd_tmr').textContent = (j.timer_enabled ? 'enabled' : 'disabled') + ' / ' + (j.timer_active ? 'active' : 'inactive') ;

        setTimeField('dd_next', j.timer_next || null);

        if (j.last_result === 'OK') q('#dd_res').innerHTML = '<span class="ok">OK</span>';
        else if (j.last_result === 'KO') q('#dd_res').innerHTML = '<span class="bad">KO</span>';
        else q('#dd_res').textContent = '—';

        setTimeField('dd_last', (j.last && j.last.when) ? j.last.when : null);
        q('#dd_sub').textContent = j.service_substate || '—';
        q('#dd_code').textContent = (j.service_exec_status == null) ? '—' : String(j.service_exec_status);
        setTimeField('dd_start', j.service_started_at || null);
        setTimeField('dd_exit',  j.service_exited_at  || null);

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
      if (!b) return;
      const cur = b.textContent || '';
      // keep existing label if already set by prior state
      if (cur.includes('Enable') || cur.includes('Disable')) return;
      b.textContent = 'Toggle hourly timer';
    }

    async function dd_save() {
      const body = {
        domains: document.getElementById('dd_domains').value.trim(),
        token:   document.getElementById('dd_token').value.trim()
      };
      document.getElementById('dd_busy').style.display = 'inline';
      try {
        const r = await fetch('/api/duckdns/save', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
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
        await new Promise(res => setTimeout(res, 900));
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
        // we don't actually know current enabled state reliably here; just flip on server
        const r = await fetch('/api/duckdns/timer', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: true })
        });
        if (r.status === 401) throw new Error('auth required');
        const j = await r.json();
        if (!j.ok) throw new Error(j.error || 'timer change failed');
        await dd_load();
      } catch (e) {
        alert('DuckDNS timer change failed: ' + e.message);
      } finally {
        document.getElementById('dd_busy').style.display = 'none';
      }
    }

    // wire buttons
    document.getElementById('dd_btn_save').onclick = dd_save;
    document.getElementById('dd_btn_run').onclick = dd_run;
    document.getElementById('dd_btn_toggle').onclick = dd_toggle;

    // initial
    refreshStatus();
    dd_load();
    (function wan_loop(){
      fetch('/api/wanip',{cache:'no-store'}).then(r=>r.json()).then(j=>{
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
