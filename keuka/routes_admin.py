# routes_admin.py
# -----------------------------------------------------------------------------
# Admin pages and APIs:
#  - /admin              -> redirects to /admin/wifi
#  - /admin/wifi         simple UI to scan/connect wlan1 and set DHCP/static
#  - /api/wifi/scan      GET -> list of SSIDs with RSSI/Freq
#  - /api/wifi/connect   POST json {ssid, psk} -> connect via DHCP, wait for IP
#  - /api/wifi/ip        POST json {mode, ip_cidr?, router?, dns_csv?} -> static/DHCP
#  - /api/wifi/status    GET -> current status + IP/GW/DNS for both ifaces
# -----------------------------------------------------------------------------

import json
from flask import Blueprint, request, jsonify, redirect, url_for

from ui import render_page
from config import WLAN_STA_IFACE, WLAN_AP_IFACE
from wifi_net import (
    wifi_scan, wifi_connect, wifi_status_sta,
    ip_addr4, gw4, dns_servers, dhcpcd_current_mode, apply_network,
)

admin_bp = Blueprint("admin", __name__)

# -------- redirect: /admin -> /admin/wifi --------
@admin_bp.route("/admin")
def admin_index():
    return redirect(url_for("admin.admin_wifi"), code=302)

# -------- HTML page --------
@admin_bp.route("/admin/wifi")
def admin_wifi():
    body = f"""
      <div class="flex" style="gap:.8rem;align-items:center;margin-bottom:.3rem">
        <h1 style="margin:0">Wi-Fi Setup</h1>
        <span class="muted">STA = {WLAN_STA_IFACE} (LAN), AP = {WLAN_AP_IFACE} (KeukaSensor)</span>
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
            <label>SSID <input id="ssid" name="ssid" required></label>
            <label>Password (leave blank for open) <input id="psk" name="psk" type="password"></label>
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
              <label>IPv4/CIDR (e.g., 192.168.2.50/24)<input id="ip_cidr" name="ip_cidr"></label>
              <label>Gateway <input id="router" name="router"></label>
              <label>DNS (comma separated) <input id="dns_csv" name="dns_csv" placeholder="8.8.8.8,1.1.1.1"></label>
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
            li.title = "Click to fill SSID below";
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
            q('#staticFields').style.display = "";
            q('#ip_cidr').value = j.dhcpcd.ip || "";
            q('#router').value = j.dhcpcd.router || "";
            q('#dns_csv').value = (j.dhcpcd.dns||[]).join(", ");
          }} else {{
            q('#mode').value = "dhcp";
            q('#staticFields').style.display = "none";
          }}
        }}

        q('#btnScan').onclick = async () => {{
          q('#scanNote').textContent = "Scanning…";
          try {{
            const r = await fetch('/api/wifi/scan', {{cache:'no-store'}});
            const j = await r.json();
            renderScan(j.networks||[]);
            q('#scanNote').textContent = "Done.";
          }} catch(e) {{
            q('#scanNote').textContent = "Scan failed.";
          }}
        }};

        q('#mode').onchange = () => {{
          q('#staticFields').style.display = (q('#mode').value === "static") ? "" : "none";
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

# -------- JSON APIs --------
@admin_bp.route("/api/wifi/scan")
def api_wifi_scan():
    nets = wifi_scan()
    return jsonify({"ok": True, "networks": nets})

@admin_bp.route("/api/wifi/connect", methods=["POST"])
def api_wifi_connect():
    data = request.get_json(silent=True) or {}
    ssid = (data.get("ssid") or "").strip()
    psk  = data.get("psk") or ""

    ok, msg, details = wifi_connect(ssid, psk)  # returns (ok,msg,{"ip":..., "iface":...})
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
