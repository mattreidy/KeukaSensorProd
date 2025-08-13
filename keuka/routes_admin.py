# routes_admin.py
# -----------------------------------------------------------------------------
# Admin UI:
#   /admin                - overview + actions (update/restart/reboot)
#   /admin/wifi*          - scan/connect Wi-Fi
#   /admin/network*       - DHCP/Static IPv4 config for WLAN_STA_IFACE
#   /admin/duckdns*       - DuckDNS token/domains + timer controls
#
# All admin routes require HTTP Basic Auth using ADMIN_USER/PASS.
# -----------------------------------------------------------------------------

import os
import time
import subprocess
from flask import Blueprint, Response, request, redirect, url_for

from ui import render_page
from utils import basic_auth_ok, utcnow_str
from config import (
    APP_DIR,
    WLAN_STA_IFACE, WLAN_AP_IFACE,
    DUCKDNS_CONF, DUCKDNS_LAST,
)
from wifi_net import (
    wifi_status, wifi_scan, wifi_connect,
    dhcpcd_current_mode, apply_network,
    ip_addr4, gw4, dns_servers
)

admin_bp = Blueprint("admin", __name__)

def _require_auth():
    if not basic_auth_ok(request):
        return Response('Auth required', 401, {"WWW-Authenticate": 'Basic realm="KeukaSensor"'})
    return None

@admin_bp.route("/admin", methods=["GET"])
@admin_bp.route("/admin/<action>", methods=["POST"])
def admin(action=None):
    # Guard
    auth = _require_auth()
    if auth:
        return auth

    if request.method == 'POST':
        if action == 'restart':
            subprocess.Popen(['sudo', '/usr/bin/systemctl', 'restart', 'keuka-sensor.service'])
            return redirect(url_for('admin.admin'))
        if action == 'reboot':
            subprocess.Popen(['sudo', '/usr/sbin/reboot'])
            return 'Rebooting...', 202
        if action == 'update':
            script = str(APP_DIR / 'update.sh')
            if os.path.exists(script):
                subprocess.Popen(['bash', script], cwd=str(APP_DIR))
                return redirect(url_for('admin.admin'))
            return 'No update.sh found', 404

    ip = subprocess.getoutput("hostname -I | awk '{print $1}'")
    host = subprocess.getoutput('hostname')
    st = wifi_status()
    html = f"""
      <div class='grid'>
        <div class='card'>
          <div><b>Server Time (UTC):</b> {utcnow_str()}</div>
          <div><b>Host:</b> {host}</div>
          <div><b>IP (primary):</b> {ip}</div>
          <div><b>Wi-Fi (STA {WLAN_STA_IFACE}):</b> SSID {st.get('ssid') or '(n/a)'} | RSSI {st.get('signal_dbm') or '(n/a)'} dBm | Freq {st.get('freq_mhz') or '(n/a)'} MHz</div>
        </div>
        <div class='card'>
          <form method='post' action='/admin/update' style='display:inline'><button class="btn">Update Code</button></form>
          <form method='post' action='/admin/restart' style='display:inline;margin-left:.5rem'><button class="btn">Restart Service</button></form>
          <form method='post' action='/admin/reboot' style='display:inline;margin-left:.5rem' onsubmit="return confirm('Reboot now?')"><button class="btn">Reboot Pi</button></form>
          <div style='margin-top:.6rem'>
            <a class="btn" href='/admin/wifi'>Wi-Fi Setup</a>
            <a class="btn" style='margin-left:.4rem' href='/admin/network'>Network (DHCP/Static)</a>
            <a class="btn" style='margin-left:.4rem' href='/admin/duckdns'>DuckDNS</a>
          </div>
        </div>
      </div>
      <p class="muted" style="margin-top:.6rem">Times on admin pages are server UTC; Health shows local browser time.</p>
    """
    return render_page("Keuka Sensor – Admin", html)

# ---------- Wi-Fi ----------
@admin_bp.route('/admin/wifi')
def admin_wifi():
    auth = _require_auth()
    if auth:
        return auth
    body = f"""
      <h1 style="margin-top:.2rem">Wi-Fi Setup (STA {WLAN_STA_IFACE})</h1>
      <div class="card">
        <button class="btn" onclick='doScan()'>Scan Networks</button>
        <table style='margin-top:1rem'><thead><tr><th>SSID</th><th>RSSI (dBm)</th><th>Freq (MHz)</th><th></th></tr></thead><tbody id='nets'></tbody></table>
      </div>
      <div class="card">
        <h3 style="margin-top:0">Add/Connect</h3>
        <form method='post' action='/admin/wifi/connect' class="flex" style="flex-wrap:wrap">
          <label>SSID <input id='ssid' name='ssid' required style="margin-left:.4rem"></label>
          <label style='margin-left:1rem'>Password <input name='psk' type='password' required style="margin-left:.4rem"></label>
          <button type='submit' class="btn" style='margin-left:1rem'>Connect</button>
        </form>
        <p class="muted" style="margin-top:.4rem">Tip: If scan shows nothing (adapter missing), you can still enter SSID and password manually.</p>
      </div>
      <script>
        async function doScan(){{
          const r = await fetch('/admin/wifi/scan'); const data = await r.json();
          const tb = document.getElementById('nets'); tb.innerHTML='';
          data.forEach(n=>{{
            const tr=document.createElement('tr');
            tr.innerHTML=`<td>${{n.ssid||'(hidden)'}} </td><td>${{n.signal_dbm||''}}</td><td>${{n.freq_mhz||''}}</td>
                           <td><button class="btn" onclick="sel('${{(n.ssid||'').replace(/'/g, "\\'")}}')">Select</button></td>`;
            tb.appendChild(tr);
          }});
        }}
        function sel(ssid){{ document.getElementById('ssid').value = ssid; window.scrollTo(0,document.body.scrollHeight); }}
      </script>
    """
    return render_page("Keuka Sensor – Wi-Fi", body)

@admin_bp.route('/admin/wifi/scan')
def wifi_scan_api():
    auth = _require_auth()
    if auth:
        return auth
    from flask import json, Response
    return Response(json.dumps(wifi_scan()), mimetype='application/json')

@admin_bp.route('/admin/wifi/connect', methods=['POST'])
def wifi_connect_api():
    auth = _require_auth()
    if auth:
        return auth
    ssid = request.form.get('ssid', '').strip()
    psk = request.form.get('psk', '').strip()
    if not ssid or not psk:
        return 'Missing ssid/psk', 400
    wifi_connect(ssid, psk)
    time.sleep(1)
    return redirect(url_for('admin.admin'))

# ---------- Network ----------
@admin_bp.route('/admin/network', methods=['GET'])
def network_page():
    auth = _require_auth()
    if auth:
        return auth
    mode = dhcpcd_current_mode()
    st = wifi_status()
    ip_sta = ip_addr4(WLAN_STA_IFACE) or "(none)"
    gw = gw4(WLAN_STA_IFACE) or "(none)"
    dns = ", ".join(dns_servers()) or "(none)"
    ip_ap = ip_addr4(WLAN_AP_IFACE) or "(none)"
    body = f"""
      <h1 style="margin-top:.2rem">Network (IPv4)</h1>
      <div class="grid">
        <div class="card">
          <h3 style="margin-top:0">Current (STA {WLAN_STA_IFACE})</h3>
          <div>SSID: <b>{st.get('ssid') or '(n/a)'}</b></div>
          <div>RSSI: <b>{st.get('signal_dbm') or '(n/a)'} dBm</b> | Freq: <b>{st.get('freq_mhz') or '(n/a)'} MHz</b></div>
          <div>IP: <b>{ip_sta}</b> | Gateway: <b>{gw}</b> | DNS: <b>{dns}</b></div>
        </div>
        <div class="card">
          <h3 style="margin-top:0">Provisioning AP (wlan0)</h3>
          <div>IP: <b>{ip_ap}</b> (AP stays available for recovery)</div>
        </div>
      </div>
      <div class="card" style="margin-top:1rem">
        <h3 style="margin-top:0">Change IPv4 mode for {WLAN_STA_IFACE}</h3>
        <form method='post' action='/admin/network/apply'>
          <p>
            <label>Mode</label>
            <select name='mode'>
              <option value='dhcp' {"selected" if mode.get("mode")!="static" else ""}>DHCP (default)</option>
              <option value='static' {"selected" if mode.get("mode")=="static" else ""}>Static</option>
            </select>
          </p>
          <p><label>Static IP (CIDR)</label><br><input name='ip' placeholder='192.168.1.50/24' value="{mode.get('ip','') or ''}" style="width:260px"></p>
          <p><label>Gateway</label><br><input name='gw' placeholder='192.168.1.1' value="{mode.get('router','') or ''}" style="width:260px"></p>
          <p><label>DNS (comma list)</label><br><input name='dns' placeholder='1.1.1.1,8.8.8.8' value="{",".join(mode.get('dns',[]))}" style="width:260px"></p>
          <p><button type='submit' class="btn">Apply</button>
           <span class='muted' style='margin-left:.6rem'>Note: you may lose this session if IP changes—use the AP at <b>http://192.168.50.1/admin</b> if needed.</span></p>
        </form>
      </div>
    """
    return render_page("Keuka Sensor – Network", body)

@admin_bp.route('/admin/network/apply', methods=['POST'])
def network_apply():
    auth = _require_auth()
    if auth:
        return auth
    mode = request.form.get('mode', 'dhcp')
    ip_cidr = request.form.get('ip', '').strip()
    gw = request.form.get('gw', '').strip()
    dns = request.form.get('dns', '').strip()
    ok, msg = apply_network(mode, ip_cidr, gw, dns)
    if not ok:
        return f"Error: {msg}", 400
    return redirect(url_for('admin.network_page'))

# ---------- DuckDNS ----------
def duckdns_read() -> dict:
    """Read DuckDNS token/domains + timer enabled state + recent log tail."""
    data = {"token": "", "domains": "", "enabled": False, "last": ""}
    if DUCKDNS_CONF.exists():
        for ln in DUCKDNS_CONF.read_text(encoding="utf-8").splitlines():
            if ln.startswith("token="):
                data["token"] = ln.split("=", 1)[1].strip()
            if ln.startswith("domains="):
                data["domains"] = ln.split("=", 1)[1].strip()
    # enabled?
    code, _ = sh(["systemctl", "is-enabled", "duckdns-update.timer"])
    data["enabled"] = (code == 0)
    if DUCKDNS_LAST.exists():
        data["last"] = DUCKDNS_LAST.read_text(encoding="utf-8")[-200:]
    return data

from utils import sh  # imported here to avoid circular top-level imports

def duckdns_write(token: str, domains: str) -> None:
    """Write DuckDNS config and protect it with chmod 600."""
    txt = f"token={token.strip()}\n" + f"domains={domains.strip()}\n"
    DUCKDNS_CONF.write_text(txt, encoding="utf-8")
    sh(["sudo", "/bin/chmod", "600", str(DUCKDNS_CONF)])

@admin_bp.route('/admin/duckdns', methods=['GET'])
def duckdns_page():
    auth = _require_auth()
    if auth:
        return auth
    dd = duckdns_read()
    body = f"""
      <h1 style="margin-top:.2rem">DuckDNS updater</h1>
      <div class="card">
        <p>Get your <b>token</b> and create subdomain(s) at <b>duckdns.org</b>. This device will update your public IP automatically.</p>
        <form method='post' action='/admin/duckdns/save'>
          <p><label>Token</label><br><input name='token' value="{dd['token']}" style='width:360px'></p>
          <p><label>Domains</label><br><input name='domains' value="{dd['domains']}" style='width:360px'> <span class='muted'>(comma separated, e.g. <i>keukasensor1,keukasensor2</i>)</span></p>
          <p>
            <button type='submit' class="btn">Save</button>
            <a class="btn" href='/admin/duckdns/update' style='margin-left:.4rem'>Update Now</a>
            <a class="btn" href='/admin/duckdns/enable' style='margin-left:.4rem'>Enable Timer</a>
            <a class="btn" href='/admin/duckdns/disable' style='margin-left:.4rem'>Disable Timer</a>
          </p>
        </form>
      </div>
      <div class="card" style="margin-top:1rem">
        <h3 style="margin-top:0">Status</h3>
        <div>Timer enabled: <span class="badge {('b-ok' if dd['enabled'] else 'b-warn')}">{'yes' if dd['enabled'] else 'no'}</span></div>
        <pre style='background:var(--bg);border:1px solid var(--border);padding:8px'>{dd['last'] or '(no recent log)'}</pre>
      </div>
    """
    return render_page("Keuka Sensor – DuckDNS", body)

@admin_bp.route('/admin/duckdns/save', methods=['POST'])
def duckdns_save():
    auth = _require_auth()
    if auth:
        return auth
    duckdns_write(request.form.get('token',''), request.form.get('domains',''))
    return redirect(url_for('admin.duckdns_page'))

@admin_bp.route('/admin/duckdns/update')
def duckdns_update_now():
    auth = _require_auth()
    if auth:
        return auth
    sh(["sudo", "/usr/bin/systemctl", "start", "duckdns-update.service"])
    time.sleep(0.5)
    return redirect(url_for('admin.duckdns_page'))

@admin_bp.route('/admin/duckdns/enable')
def duckdns_enable():
    auth = _require_auth()
    if auth:
        return auth
    sh(["sudo", "/usr/bin/systemctl", "enable", "--now", "duckdns-update.timer"])
    return redirect(url_for('admin.duckdns_page'))

@admin_bp.route('/admin/duckdns/disable')
def duckdns_disable():
    auth = _require_auth()
    if auth:
        return auth
    sh(["sudo", "/usr/bin/systemctl", "disable", "--now", "duckdns-update.timer"])
    return redirect(url_for('admin.duckdns_page'))
