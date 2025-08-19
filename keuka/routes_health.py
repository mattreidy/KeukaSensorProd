# routes_health.py
# -----------------------------------------------------------------------------
# Health dashboard + data:
#   /health      - Responsive HTML page (dark-mode aware, mobile-friendly)
#   /health.json - JSON payload (programmatic or fallback polling)
#   /health.sse  - Server-Sent Events stream that pushes a fresh payload ~5s
# -----------------------------------------------------------------------------

import json
import time
import re
from pathlib import Path
from datetime import datetime, timedelta
from flask import Blueprint, Response, request

from ui import render_page
from utils import utcnow_str, read_text
from config import (
    APP_DIR,
    WLAN_STA_IFACE, WLAN_AP_IFACE, VERSION,
    TEMP_WARN_F, TEMP_CRIT_F, RSSI_WARN_DBM, RSSI_CRIT_DBM,
    CPU_TEMP_WARN_C, CPU_TEMP_CRIT_C,
)
from camera import camera
from sensors import read_temp_fahrenheit, median_distance_inches
from wifi_net import wifi_status, ip_addr4, gw4, dns_servers
from system_diag import cpu_temp_c, uptime_seconds, disk_usage_root, mem_usage

health_bp = Blueprint("health", __name__)

# ---- contact persistence (file: contact.txt in APP_DIR) ----------------------
CONTACT_FILE = APP_DIR / "contact.txt"

def _contact_defaults() -> dict:
    return {"name": "", "address": "", "phone": "", "email": "", "notes": ""}

def contact_get() -> dict:
    try:
        txt = CONTACT_FILE.read_text(encoding="utf-8")
        data = json.loads(txt) if txt.strip() else {}
    except Exception:
        data = {}
    # Ensure all keys exist and are strings
    out = _contact_defaults()
    for k in out.keys():
        v = data.get(k, "")
        try:
            out[k] = str(v) if v is not None else ""
        except Exception:
            out[k] = ""
    return out

def contact_set(info: dict) -> None:
    def _s(val: object, maxlen: int) -> str:
        try:
            s = str(val) if val is not None else ""
        except Exception:
            s = ""
        s = s.replace("\r\n", "\n").replace("\r", "\n")
        return s[:maxlen]

    payload = {
        "name":    _s(info.get("name"),    200),
        "address": _s(info.get("address"), 2000),
        "phone":   _s(info.get("phone"),   100),
        "email":   _s(info.get("email"),   320),
        "notes":   _s(info.get("notes"),   5000),
    }

    CONTACT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONTACT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CONTACT_FILE)  # atomic on POSIX

def hostapd_info(conf_path: str = "/etc/hostapd/hostapd.conf") -> dict:
    """Best-effort parse of hostapd.conf so we can show AP SSID/channel."""
    try:
        txt = read_text(Path(conf_path))
    except Exception:
        return {"ssid": None, "channel": None, "hw_mode": None}
    def get(k):
        m = re.search(rf"^\s*{re.escape(k)}\s*=\s*(.+)\s*$", txt, re.M)
        return m.group(1).strip() if m else None
    return {
        "ssid": get("ssid"),
        "channel": get("channel"),
        "hw_mode": get("hw_mode"),
    }

# -------- Shared payload builder --------
def build_health_payload() -> dict:
    # Sensor readings (gracefully handle missing hardware)
    tF = read_temp_fahrenheit()
    dIn = median_distance_inches()

    # Wi-Fi
    st = wifi_status() or {}               # STA link info (on WLAN_STA_IFACE)
    ap = hostapd_info()                    # AP broadcast info (on WLAN_AP_IFACE)

    # System stats
    cpu_c = cpu_temp_c()
    up_s = uptime_seconds()

    # CPU utilization from /proc/stat deltas
    cpu_util = None
    try:
        with open("/proc/stat", "r") as f:
            parts = f.readline().split()[1:]
        nums = list(map(int, parts[:8]))
        idle = nums[3] + nums[4]
        total = sum(nums)
        prev = getattr(build_health_payload, "_prev_stat", None)
        if prev is not None:
            idle_d = idle - prev[0]
            total_d = total - prev[1]
            if total_d > 0:
                cpu_util = round((1.0 - (idle_d / total_d)) * 100.0, 1)
        build_health_payload._prev_stat = (idle, total)
    except Exception:
        pass

    boot_utc = (datetime.utcnow() - timedelta(seconds=up_s)).strftime("%Y-%m-%d %H:%M:%S")

    # IPs per interface
    ip_map = {}
    for iface in (WLAN_STA_IFACE, WLAN_AP_IFACE):
        val = ip_addr4(iface)
        if val:
            ip_map[iface] = val

    return {
        "time_utc": utcnow_str(),
        "tempF": None if (tF != tF) else round(tF, 2),
        "distanceInches": None if (dIn != dIn) else round(dIn, 2),
        "camera": "running" if camera.running else "idle",
        "wifi_sta": st,
        "wifi_ap": ap,
        "ifaces": {"sta": WLAN_STA_IFACE, "ap": WLAN_AP_IFACE},
        "ip": ip_map,
        "gateway_sta": gw4(WLAN_STA_IFACE),
        "gateway_ap":  gw4(WLAN_AP_IFACE),
        "dns": dns_servers(),
        "app": "keuka-sensor",
        "version": VERSION,
        "system": {
            "cpu_temp_c": None if (cpu_c != cpu_c) else round(cpu_c, 1),
            "cpu_util_pct": cpu_util,
            "uptime_seconds": int(up_s),
            "boot_time_utc": boot_utc,
            "disk": disk_usage_root(),
            "mem": mem_usage(),
            "hostname": __import__("subprocess").getoutput("hostname"),
        },
        "thresholds": {
            "temp_warn_f": TEMP_WARN_F,
            "temp_crit_f": TEMP_CRIT_F,
            "rssi_warn_dbm": RSSI_WARN_DBM,
            "rssi_crit_dbm": RSSI_CRIT_DBM,
            "cpu_warn_c": CPU_TEMP_WARN_C,
            "cpu_crit_c": CPU_TEMP_CRIT_C,
        },
        "contact": contact_get(),
    }

# -------- HTML dashboard --------
@health_bp.route("/health")
def health():
    info = build_health_payload()
    extra_head = f"""
    <script id="seed" type="application/json">{json.dumps(info)}</script>
    """
    # NOTE: This is an f-string. ALL JS braces are doubled {{ }} to avoid f-string parsing.
    body = f"""
      <div class="flex" style="gap:.8rem;align-items:center;margin-bottom:.3rem">
        <h1 style="margin:0">Health</h1>
        <span id="connDot" class="dot" title="connection status"></span>
        <span id="lastUpdated" class="muted">—</span>
        <span class="right muted">Local Time: <span id="localTime"></span></span>
      </div>

      <div class="grid" style="margin-top:.6rem">
        <div class="card">
          <h3 style="margin-top:0">Environment</h3>
          <table>
            <tr><th>Temperature</th><td><span id="tempF"></span> °F <span id="tempBadge" class="badge"></span></td></tr>
            <tr><th>Distance</th><td><span id="distanceInches"></span> in</td></tr>
            <tr><th>Camera</th><td><span id="cameraBadge" class="badge"></span></td></tr>
          </table>
        </div>

        <div class="card">
          <h3 style="margin-top:0">Wi-Fi (STA <span id="sta_if"></span>)</h3>
          <table>
            <tr><th>Status</th><td><span id="wifiStatus" class="badge"></span></td></tr>
            <tr><th>SSID</th><td id="ssid"></td></tr>
            <tr><th>Signal</th><td><span id="rssiBars" class="flex"></span></td></tr>
            <tr><th>Frequency</th><td><span id="freq"></span> MHz</td></tr>
            <tr><th>BSSID</th><td id="bssid"></td></tr>
          </table>
        </div>

        <div class="card">
          <h3 style="margin-top:0">Wi-Fi AP (<span id="ap_if"></span>)</h3>
          <table>
            <tr><th>SSID</th><td id="ap_ssid"></td></tr>
            <tr><th>Channel</th><td id="ap_chan"></td></tr>
            <tr><th>Mode</th><td id="ap_mode"></td></tr>
          </table>
        </div>

        <div class="card">
          <h3 style="margin-top:0">Networking</h3>
          <table>
            <tr><th>IPv4 (STA)</th><td class="mono" id="ip_sta"></td></tr>
            <tr><th>IPv4 (AP)</th><td class="mono" id="ip_ap"></td></tr>
            <tr><th>Gateway (STA)</th><td class="mono" id="gw_sta"></td></tr>
            <tr><th>Gateway (AP)</th><td class="mono" id="gw_ap"></td></tr>
            <tr><th>DNS Servers</th><td class="mono" id="dns"></td></tr>
          </table>
        </div>

        <div class="card">
          <h3 style="margin-top:0">System</h3>
          <table>
            <tr><th>Hostname</th><td id="hostname"></td></tr>
            <tr><th>CPU Temp</th><td><span id="cpuTemp"></span> °C <span id="cpuBadge" class="badge"></span></td></tr>
            <tr><th>CPU Utilization</th><td><span id="cpuUtil"></span>%</td></tr>
            <tr><th>Uptime</th><td><span id="uptime"></span> (<span id="bootLocal"></span> boot)</td></tr>
            <tr><th>Disk</th><td><span id="diskPct"></span>% used <span class="mono" id="diskSizes"></span></td></tr>
            <tr><th>Memory</th>
                <td>
                  <span id="memPct"></span>% used —
                  <span class="mono" id="memUsed"></span> /
                  <span class="mono" id="memTotal"></span>
                  (free <span class="mono" id="memFree"></span>)
                </td></tr>
          </table>
        </div>

        <div class="card">
          <h3 style="margin-top:0">Webcam</h3>
          <a href="/webcam" title="Open live stream">
            <img id="thumb" class="thumb" src="/snapshot?cb=0" alt="Webcam snapshot (click to open)" onerror="this.style.display='none'">
          </a>
          <div class="muted" style="margin-top:.4rem">Click thumbnail to open live stream.</div>
        </div>
      </div>

      <div class="card" style="margin-top:1rem; max-width:500px">
        <h3 style="margin-top:0">Contact / Notes</h3>
        <form onsubmit="saveContact(event)" class="flex" style="flex-direction:column; gap:.8rem">
          
          <div>
            <label for="c_name"><b>Name</b></label>
            <input id="c_name" type="text" placeholder="Name" style="width:100%" />
          </div>
          
          <div>
            <label for="c_address"><b>Address</b></label>
            <textarea id="c_address" rows="2" placeholder="Street, City, State, ZIP" style="width:100%"></textarea>
          </div>
          
          <div>
            <label for="c_phone"><b>Phone</b></label>
            <input id="c_phone" type="text" placeholder="+1 ..." style="width:100%" />
          </div>
          
          <div>
            <label for="c_email"><b>Email</b></label>
            <input id="c_email" type="email" placeholder="you@example.com" style="width:100%" />
          </div>
          
          <div>
            <label for="c_notes"><b>Notes</b></label>
            <textarea id="c_notes" rows="5" placeholder="Free-form notes..." style="width:100%"></textarea>
          </div>
          
          <div class="flex" style="gap:.6rem; align-items:center">
            <button id="c_save" class="btn" type="submit">Save</button>
            <span id="c_status" class="muted"></span>
          </div>
        
        </form>
      </div>

      <div class="card" style="margin-top:1rem">
        <div class="flex"><strong>Raw JSON</strong><button class="btn" onclick="copyJSON()">Copy</button><span id="copynote" class="muted"></span></div>
        <pre id="rawjson" class="mono" style="white-space:pre-wrap;margin-top:.4rem"></pre>
      </div>

      <script>
        // ---- helpers ----
        function fmt(v, fallback="(n/a)") {{ return (v===null||v===undefined||v==="") ? fallback : v; }}
        function bytes(n) {{
          if (n===0) return "0 B";
          const u=["B","KB","MB","GB","TB","PB"]; let i = Math.floor(Math.log(n)/Math.log(1024));
          return (n/Math.pow(1024,i)).toFixed(i?1:0)+" "+u[i];
        }}
        function setBadge(el, level, text) {{
          el.className = "badge " + (level ? "b-"+level : "");
          el.textContent = text || "";
        }}
        function rssiBarsHTML(dbm) {{
          if (dbm===null || dbm===undefined) return "(n/a)";
          const v = Number(dbm);
          let bars = 0;
          if (v >= -50) bars = 5; else if (v >= -60) bars = 4; else if (v >= -67) bars = 3; else if (v >= -75) bars = 2; else bars = 0;
          const spans = Array.from({{length:5}}, (_,i)=>`<span class="${{i<bars?"on":""}}" style="height:${{4+i*2}}px"></span>`).join("");
          return `<span class="bars" title="${{v}} dBm">${{spans}}</span><span class="muted"> ${{v}} dBm</span>`;
        }}
        function humanUptimeDHMS(sec) {{
          let s = Number(sec);
          const d=Math.floor(s/86400); s-=d*86400;
          const h=Math.floor(s/3600); s-=h*3600;
          const m=Math.floor(s/60);
          const parts=[]; if (d) parts.push(d+"d"); parts.push(h+"h"); parts.push(m+"m");
          return parts.join(" ");
        }}
        let prev = {{}};
        function upDownFlash(el, key, newVal) {{
          const was = prev[key]; prev[key] = newVal;
          const n = Number(newVal); const w = Number(was);
          if (!isFinite(n) || !isFinite(w)) return; // numeric only
          if (n > w) {{ el.classList.remove("downflash"); void el.offsetWidth; el.classList.add("upflash"); }}
          else if (n < w) {{ el.classList.remove("upflash"); void el.offsetWidth; el.classList.add("downflash"); }}
        }}
        function copyJSON() {{
          const pre = document.getElementById('rawjson');
          navigator.clipboard.writeText(pre.textContent).then(()=>{{
            const n = document.getElementById('copynote'); n.textContent = "Copied!";
            setTimeout(()=> n.textContent="", 1200);
          }});
        }}
        let lastUpdateEpoch = 0;
        function tickAgo() {{
          const el = document.getElementById('lastUpdated');
          if (!lastUpdateEpoch) {{ el.textContent = "—"; return; }}
          const secs = Math.round((Date.now() - lastUpdateEpoch)/1000);
          el.textContent = "Updated " + (secs===0 ? "just now" : secs + "s ago");
        }}
        setInterval(tickAgo, 1000);

        // ---- contact form helpers ----
        let contactInitialized = false; // Only set fields on first load or after Save

        function setContactForm(c) {{
          document.getElementById('c_name').value = c?.name || "";
          document.getElementById('c_address').value = c?.address || "";
          document.getElementById('c_phone').value = c?.phone || "";
          document.getElementById('c_email').value = c?.email || "";
          document.getElementById('c_notes').value = c?.notes || "";
        }}
        async function saveContact(e) {{
          e.preventDefault();
          const btn = document.getElementById('c_save');
          const status = document.getElementById('c_status');
          btn.disabled = true; status.textContent = "Saving...";
          try {{
            const payload = {{
              name: document.getElementById('c_name').value,
              address: document.getElementById('c_address').value,
              phone: document.getElementById('c_phone').value,
              email: document.getElementById('c_email').value,
              notes: document.getElementById('c_notes').value,
            }};
            const r = await fetch('/health/contact', {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify(payload),
            }});
            const j = await r.json();
            if (j && j.ok) {{
              setContactForm(j.contact || {{}});
              contactInitialized = true; // prevent SSE refreshes from overwriting edits
              status.textContent = "Saved.";
              lastUpdateEpoch = Date.now(); tickAgo();
              setTimeout(()=>{{ status.textContent = ""; }}, 1500);
            }} else {{
              status.textContent = "Save failed.";
            }}
          }} catch (_e) {{
            status.textContent = "Save error.";
          }} finally {{
            btn.disabled = false;
          }}
        }}

        // ---- main render ----
        function render(data) {{
          // show server time in local browser time
          const dt = new Date(String(data.time_utc).replace(' ', 'T') + 'Z');
          document.getElementById('localTime').textContent = dt.toLocaleString();

          document.getElementById('rawjson').textContent = JSON.stringify(data, null, 2);

          // Label the interface names
          document.getElementById('sta_if').textContent = data.ifaces?.sta || "(n/a)";
          document.getElementById('ap_if').textContent  = data.ifaces?.ap  || "(n/a)";

          // Environment
          const tempEl = document.getElementById('tempF');
          tempEl.textContent = fmt(data.tempF);
          upDownFlash(tempEl, "tempF", data.tempF);

          const distEl = document.getElementById('distanceInches');
          distEl.textContent = fmt(data.distanceInches);
          upDownFlash(distEl, "distanceInches", data.distanceInches);

          const camBadge = document.getElementById('cameraBadge');
          setBadge(camBadge, (data.camera==="running"?"ok":"idle"), (data.camera==="running"?"Running":"Idle"));

          // Wi-Fi (STA)
          const ws = data.wifi_sta || {{}};
          const ssid = ws.ssid || null;
          document.getElementById('ssid').textContent = ssid || "Not connected";
          document.getElementById('freq').textContent = fmt(ws.freq_mhz);
          document.getElementById('bssid').textContent = fmt(ws.bssid);
          const rssi = ws.signal_dbm;
          document.getElementById('rssiBars').innerHTML = rssiBarsHTML(rssi);
          upDownFlash(document.getElementById('rssiBars'), "rssi", rssi);
          const wifiStatus = document.getElementById('wifiStatus');
          setBadge(wifiStatus, ssid ? "ok" : "warn", ssid ? "Connected" : "Not connected");

          // Wi-Fi (AP)
          const ap = data.wifi_ap || {{}};
          document.getElementById('ap_ssid').textContent = fmt(ap.ssid);
          document.getElementById('ap_chan').textContent = fmt(ap.channel);
          document.getElementById('ap_mode').textContent = fmt(ap.hw_mode);

          // Networking (resolve IPs by iface map)
          const sta = data.ifaces?.sta;
          const apif = data.ifaces?.ap;
          const ip_sta = sta ? data.ip?.[sta] : null;
          const ip_ap  = apif ? data.ip?.[apif] : null;
          document.getElementById('ip_sta').textContent = fmt(ip_sta);
          document.getElementById('ip_ap').textContent  = fmt(ip_ap);
          document.getElementById('gw_sta').textContent = fmt(data.gateway_sta);
          document.getElementById('gw_ap').textContent  = fmt(data.gateway_ap);
          document.getElementById('dns').textContent = (data.dns && data.dns.length) ? data.dns.join(", ") : "(n/a)";

          // System
          document.getElementById('hostname').textContent = fmt(data.system.hostname);
          document.getElementById('cpuTemp').textContent = fmt(data.system.cpu_temp_c);
          const cpuB = document.getElementById('cpuBadge');
          let cpuLv = ""; let cpuTx="";
          if (isFinite(data.system.cpu_temp_c)) {{
            if (data.system.cpu_temp_c >= {CPU_TEMP_CRIT_C}) {{ cpuLv="crit"; cpuTx="Hot"; }}
            else if (data.system.cpu_temp_c >= {CPU_TEMP_WARN_C}) {{ cpuLv="warn"; cpuTx="Warm"; }}
            else {{ cpuLv="ok"; cpuTx="Cool"; }}
          }}
          setBadge(cpuB, cpuLv, cpuTx);

          const cpuUtilEl = document.getElementById('cpuUtil');
          cpuUtilEl.textContent = (data.system.cpu_util_pct == null) ? "(n/a)" : Number(data.system.cpu_util_pct).toFixed(1);
          upDownFlash(cpuUtilEl, "cpuUtil", data.system.cpu_util_pct);

          document.getElementById('uptime').textContent = humanUptimeDHMS(data.system.uptime_seconds);
          const bootDt = new Date(String(data.system.boot_time_utc).replace(' ', 'T') + 'Z');
          document.getElementById('bootLocal').textContent = bootDt.toLocaleString();

          const d = data.system.disk;
          document.getElementById('diskPct').textContent = fmt(d.percent);
          document.getElementById('diskSizes').textContent = `${{bytes(d.used)}} / ${{bytes(d.total)}}`;

          const m = data.system.mem;
          document.getElementById('memPct').textContent = fmt(m.percent);
          document.getElementById('memTotal').textContent = bytes(m.total||0);
          document.getElementById('memUsed').textContent  = bytes(m.used||0);
          document.getElementById('memFree').textContent  = bytes(m.free||0);

          const th = document.getElementById('thumb');
          if (th && th.style.display!=="none") {{ th.src = "/snapshot?cb=" + Date.now(); }}

          // Contact (populate only once per page load; not on every refresh)
          if (!contactInitialized) {{
            setContactForm(data.contact || {{}});
            contactInitialized = true;
          }}

          lastUpdateEpoch = Date.now(); tickAgo();
        }}

        // Seed immediately from server-rendered payload
        try {{
          const seed = JSON.parse(document.getElementById('seed').textContent);
          render(seed);
        }} catch (_e) {{}}

        // SSE hookup (fallback to polling if SSE unsupported)
        let es = null;
        function connectSSE() {{
          if (!window.EventSource) {{ document.getElementById('connDot').className = "dot"; pollFallback(); return; }}
          es = new EventSource('/health.sse');
          const dot = document.getElementById('connDot');
          es.onopen = () => {{ dot.className="dot ok"; }};
          es.onerror = () => {{ dot.className="dot err"; try {{ es.close(); }} catch(_e) {{}}; setTimeout(connectSSE, 3000); }};
          es.addEventListener('health', (e) => {{ const data = JSON.parse(e.data); render(data); dot.className="dot ok"; }});
        }}
        async function pollFallback() {{
          const dot = document.getElementById('connDot');
          async function once() {{
            try {{ const r = await fetch('/health.json', {{cache:'no-store'}}); const data = await r.json(); render(data); dot.className="dot ok"; }}
            catch(_e) {{ dot.className="dot err"; }}
          }}
          once(); setInterval(once, 5000);
        }}
        connectSSE();
      </script>
    """
    return render_page("Keuka Sensor – Health", body, extra_head)

# -------- JSON (programmatic/fallback) --------
@health_bp.route("/health.json")
def health_json():
    return build_health_payload()

# -------- Server-Sent Events --------
@health_bp.route("/health.sse")
def health_sse():
    def stream():
        # Initial burst
        yield f"event: health\ndata: {json.dumps(build_health_payload())}\n\n"
        last_ping = time.time()
        while True:
            time.sleep(5)
            yield f"event: health\ndata: {json.dumps(build_health_payload())}\n\n"
            # Proxy keepalive comment every ~15s
            if time.time() - last_ping > 15:
                yield ": keepalive\n\n"
                last_ping = time.time()

    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return Response(stream(), headers=headers)

# -------- Contact info API (persisted to contact.txt) --------
@health_bp.route("/health/contact", methods=["GET", "POST"])
def health_contact():
    if request.method == "GET":
        return {"ok": True, "contact": contact_get()}
    try:
        payload = request.get_json(force=True, silent=True) or {}
        info = {
            "name": payload.get("name", ""),
            "address": payload.get("address", ""),
            "phone": payload.get("phone", ""),
            "email": payload.get("email", ""),
            "notes": payload.get("notes", ""),
        }
        contact_set(info)
        # Return the freshly saved values
        return {"ok": True, "contact": contact_get()}
    except Exception:
        # Even if persisting fails, return current on-disk view so UI can recover.
        return {"ok": False, "contact": contact_get()}, 500
