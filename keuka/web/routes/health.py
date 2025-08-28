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

from ...ui import render_page
from ...core.utils import utcnow_str, read_text
from ...config import (
    APP_DIR,
    WLAN_STA_IFACE, WLAN_AP_IFACE, VERSION,
    TEMP_WARN_F, TEMP_CRIT_F, RSSI_WARN_DBM, RSSI_CRIT_DBM,
    CPU_TEMP_WARN_C, CPU_TEMP_CRIT_C,
)
from ...camera import camera
from ...sensors import read_temp_fahrenheit, median_distance_inches, read_gps_lat_lon_elev
from ...wifi_net import wifi_status, ip_addr4, gw4, dns_servers
from ...system_diag import cpu_temp_c, uptime_seconds, disk_usage_root, mem_usage
from ...core.log_reader import log_reader

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

    try:
        CONTACT_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONTACT_FILE.with_suffix(".tmp")
        
        # Write with explicit flush and sync to prevent hanging
        with tmp.open('w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()  # Ensure data is written to OS buffers
            import os
            os.fsync(f.fileno())  # Force write to disk
        
        tmp.replace(CONTACT_FILE)  # atomic on POSIX
        
    except OSError as e:
        # Log but don't fail completely - contact updates shouldn't kill the app
        import logging
        logging.error(f"Failed to save contact info to {CONTACT_FILE}: {e}")
        raise  # Re-raise so the API can return appropriate error

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
# Simple cache to avoid re-reading sensors too frequently
_health_cache = {"data": None, "timestamp": 0, "fast_mode": None}
_cache_ttl_seconds = 1.5  # Cache valid for 1.5 seconds

def build_health_payload(fast_mode: bool = False) -> dict:
    # Check cache first
    current_time = time.time()
    if (_health_cache["data"] is not None and 
        current_time - _health_cache["timestamp"] < _cache_ttl_seconds and
        _health_cache["fast_mode"] == fast_mode):
        return _health_cache["data"].copy()  # Return cached copy
    # Sensor readings (gracefully handle missing hardware)
    # Use optimized parameters for faster loading if requested
    if fast_mode:
        # Optimized for web page loading - reduced timeouts and samples
        tF = read_temp_fahrenheit()  # Already has reasonable retry logic
        dIn = median_distance_inches(samples=5)  # Reduce from 11 to 5 samples
        turbidity = None  # Placeholder for future turbidity sensor (not yet implemented)

        # GPS with reduced timeout for web requests
        lat, lon, alt_m = read_gps_lat_lon_elev(duration_s=0.5)  # Reduce from 2.0s to 0.5s
        elev_ft = alt_m * 3.28084 if not (alt_m != alt_m) else float('nan')  # NaN check
    else:
        # Normal mode with full timeouts for background updates
        tF = read_temp_fahrenheit()
        dIn = median_distance_inches()
        turbidity = None  # Placeholder for future turbidity sensor (not yet implemented)

        # GPS (lat, lon in degrees; alt in meters) -> convert elevation to feet
        lat, lon, alt_m = read_gps_lat_lon_elev()
        elev_ft = alt_m * 3.28084 if not (alt_m != alt_m) else float('nan')  # NaN check

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

    result = {
        "time_utc": utcnow_str(),
        "tempF": None if (tF != tF) else round(tF, 2),
        "distanceInches": None if (dIn != dIn) else round(dIn, 2),
        "turbidityNTU": None if (turbidity is None or turbidity != turbidity) else round(turbidity, 2),
        "gps": {
            "lat": None if (lat != lat) else round(lat, 6),
            "lon": None if (lon != lon) else round(lon, 6),
            "elevation_ft": None if (elev_ft != elev_ft) else round(elev_ft, 1),
        },
        "camera": {
            "status": "running" if camera.running else "idle",
            "available": camera.available,
            "buffer_stats": camera.get_buffer_stats()
        },
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
    
    # Update cache
    _health_cache["data"] = result.copy()
    _health_cache["timestamp"] = current_time
    _health_cache["fast_mode"] = fast_mode
    
    return result

# -------- HTML dashboard --------
@health_bp.route("/health")
def health():
    # Use fast mode for initial page load to reduce loading time
    info = build_health_payload(fast_mode=True)
    extra_head = f"""
    <script id="seed" type="application/json">{json.dumps(info)}</script>
    <!-- Leaflet map (client-side; no Python deps) -->
    <link
      rel="stylesheet"
      href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
      integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
      crossorigin=""
    />
    <script
      src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
      integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
      crossorigin=""
    ></script>
    <style>
      #map {{ height: 220px; width: 100%; border-radius: 8px; }}
    </style>
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
            <tr><th>Turbidity</th><td><span id="turbidityNTU"></span> NTU <span class="muted">(not installed)</span></td></tr>
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
          <table style="margin-bottom:.8rem">
            <tr><th>Buffer Size</th><td><span id="bufferSize"></span> / <span id="maxBuffer"></span></td></tr>
            <tr><th>Frame Age</th><td><span id="frameAge"></span>s</td></tr>
          </table>
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

      <!-- New Location card -->
      <div class="card" style="margin-top:1rem;">
        <h3 style="margin-top:0">Location</h3>
        <table>
          <tr><th>GPS Lat</th><td class="mono"><span id="gpsLat"></span></td></tr>
          <tr><th>GPS Lon</th><td class="mono"><span id="gpsLon"></span></td></tr>
          <tr><th>GPS Elevation</th><td><span id="gpsElevFt"></span> ft</td></tr>
        </table>
        <div id="map" style="margin-top:.6rem;"></div>
        <div id="mapNote" class="muted" style="margin-top:.3rem">Awaiting GPS fix or hardware...</div>

      </div>

      <!-- Log Viewer Section -->
      <div class="card" style="margin-top:1rem">
        <div class="flex" style="align-items:center;justify-content:space-between;margin-bottom:.8rem">
          <div class="flex" style="align-items:center;gap:.8rem">
            <strong>Recent Log Entries</strong>
            <button id="logToggle" class="btn" onclick="toggleLogSection()">▼ Expand</button>
            <span id="logStats" class="muted">—</span>
          </div>
          <div class="flex" style="align-items:center;gap:.4rem">
            <button id="refreshLogs" class="btn" onclick="loadLogs()" style="display:none">🔄 Refresh</button>
            <select id="logLevel" onchange="loadLogs()" style="display:none">
              <option value="ERROR">Errors Only</option>
              <option value="WARNING" selected>Warnings & Errors</option>
              <option value="INFO">Info & Above</option>
              <option value="DEBUG">All Logs</option>
            </select>
          </div>
        </div>
        
        <div id="logSection" style="display:none">
          <!-- Filter Controls -->
          <div class="flex" style="gap:.8rem;margin-bottom:.8rem;flex-wrap:wrap">
            <input id="logFilter" type="text" placeholder="Filter logs (sensor name, message, etc.)" style="flex:1;min-width:200px">
            <select id="logHours" onchange="loadLogs()">
              <option value="1">Last Hour</option>
              <option value="6">Last 6 Hours</option>
              <option value="24" selected>Last 24 Hours</option>
              <option value="168">Last Week</option>
            </select>
            <button class="btn" onclick="clearLogFilter()">Clear Filter</button>
          </div>
          
          <!-- Log Status -->
          <div id="logStatus" class="muted" style="margin-bottom:.4rem">Loading logs...</div>
          
          <!-- Log Entries Container -->
          <div id="logEntries" style="max-height:400px;overflow-y:auto;border:1px solid var(--border);border-radius:8px;padding:.8rem;background:var(--card-bg)">
            <div class="muted">No logs to display</div>
          </div>
          
          <!-- Auto-refresh toggle -->
          <div class="flex" style="align-items:center;gap:.8rem;margin-top:.8rem">
            <label>
              <input id="autoRefreshLogs" type="checkbox" checked onchange="toggleAutoRefresh()">
              Auto-refresh every 30 seconds
            </label>
            <span id="lastLogRefresh" class="muted">—</span>
          </div>
        </div>
      </div>

      <div class="card" style="margin-top:1rem">
        <div class="flex"><strong>Raw JSON</strong><button class="btn" onclick="copyJSON()">Copy</button><span id="copynote" class="muted"></span></div>
        <pre id="rawjson" class="mono" style="white-space:pre-wrap;margin-top:.4rem"></pre>
      </div>

      <script>
        // ---- helpers ----
        function fmtLatLonHem(d, isLat) {{
          if (d===null || d===undefined || d==="") return "(n/a)";
          const v = Number(d);
          if (!isFinite(v)) return "(n/a)";
          const dir = isLat ? (v >= 0 ? "N" : "S") : (v >= 0 ? "E" : "W");
          const abs = Math.abs(v).toFixed(6);
          return abs + " " + dir;
        }}
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

        // ---- Leaflet map state ----
        let map = null;
        let mapMarker = null;
        function ensureMap() {{
          if (map) return;
          map = L.map('map').setView([0, 0], 2); // safe default world view
          L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
            maxZoom: 19,
            attribution: '&copy; OpenStreetMap contributors'
          }}).addTo(map);
        }}
        function updateMap(lat, lon) {{
          if (!isFinite(lat) || !isFinite(lon)) return;
          ensureMap();
          const latlng = [lat, lon];
          if (mapMarker) mapMarker.setLatLng(latlng);
          else mapMarker = L.marker(latlng).addTo(map);
          if (!updateMap._didFit) {{ map.setView(latlng, 12); updateMap._didFit = true; }}
        }}

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
            const r = await fetch((window.getProxyAwareUrl || (p => p))('/health/contact'), {{
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

          const turbidityEl = document.getElementById('turbidityNTU');
          turbidityEl.textContent = fmt(data.turbidityNTU);
          upDownFlash(turbidityEl, "turbidityNTU", data.turbidityNTU);

          const camBadge = document.getElementById('cameraBadge');
          const camData = data.camera || {{}};
          const isRunning = camData.status === "running" && camData.available;
          setBadge(camBadge, (isRunning ? "ok" : "idle"), (isRunning ? "Running" : "Idle"));
          
          // Update camera buffer stats
          const bufStats = camData.buffer_stats || {{}};
          document.getElementById('bufferSize').textContent = fmt(bufStats.buffer_size, "0");
          document.getElementById('maxBuffer').textContent = fmt(bufStats.max_buffer_size, "0");
          document.getElementById('frameAge').textContent = 
            (bufStats.last_frame_age !== undefined && isFinite(bufStats.last_frame_age)) 
            ? bufStats.last_frame_age.toFixed(1) 
            : "∞";

          // GPS (now in Location section)
          const gps = data.gps || {{}};
          document.getElementById('gpsLat').textContent = fmtLatLonHem(gps.lat, true);
          document.getElementById('gpsLon').textContent = fmtLatLonHem(gps.lon, false);
          document.getElementById('gpsElevFt').textContent = fmt(gps.elevation_ft);
          const hasLat = (typeof gps.lat === 'number') && isFinite(gps.lat);
          const hasLon = (typeof gps.lon === 'number') && isFinite(gps.lon);
          const noteEl = document.getElementById('mapNote');

          if (hasLat && hasLon) {{
            updateMap(gps.lat, gps.lon);
            if (noteEl) noteEl.textContent = "Map centered on current GPS fix.";
          }} else {{
            // keep map at safe default; don't jump to (0,0)
            if (noteEl) noteEl.textContent = "No GPS data (no lock or hardware).";
          }}

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

          // Smart thumbnail refresh - only update if camera is producing fresh frames
          const th = document.getElementById('thumb');
          if (th && th.style.display!=="none") {{
            const bufStats = camData.buffer_stats || {{}};
            const frameAge = bufStats.last_frame_age || Infinity;
            const bufferSize = bufStats.buffer_size || 0;
            
            // Only refresh thumbnail if we have fresh frames (< 5 seconds old) and active buffer
            if (frameAge < 5 && bufferSize > 0) {{
              th.src = (window.getProxyAwareUrl || (p => p))("/snapshot?cb=" + Date.now());
            }}
          }}

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
          es = new EventSource((window.getProxyAwareUrl || (p => p))('/health.sse'));
          const dot = document.getElementById('connDot');
          es.onopen = () => {{ dot.className="dot ok"; }};
          es.onerror = () => {{ dot.className="dot err"; try {{ es.close(); }} catch(_e) {{}}; setTimeout(connectSSE, 3000); }};
          es.addEventListener('health', (e) => {{ const data = JSON.parse(e.data); render(data); dot.className="dot ok"; }});
        }}
        async function pollFallback() {{
          const dot = document.getElementById('connDot');
          async function once() {{
            try {{ const r = await fetch((window.getProxyAwareUrl || (p => p))('/health.json'), {{cache:'no-store'}}); const data = await r.json(); render(data); dot.className="dot ok"; }}
            catch(_e) {{ dot.className="dot err"; }}
          }}
          once(); setInterval(once, 5000);
        }}
        connectSSE();
        
        // ---- Log Viewer Functionality ----
        let logSectionExpanded = false;
        let autoRefreshInterval = null;
        let filterTimeout = null;
        
        function toggleLogSection() {{
          const section = document.getElementById('logSection');
          const toggle = document.getElementById('logToggle');
          const refreshBtn = document.getElementById('refreshLogs');
          const levelSelect = document.getElementById('logLevel');
          
          if (logSectionExpanded) {{
            section.style.display = 'none';
            toggle.textContent = '▼ Expand';
            refreshBtn.style.display = 'none';
            levelSelect.style.display = 'none';
            stopAutoRefresh();
          }} else {{
            section.style.display = 'block';
            toggle.textContent = '▲ Collapse';
            refreshBtn.style.display = 'inline-block';
            levelSelect.style.display = 'inline-block';
            loadLogs();
            startAutoRefresh();
          }}
          logSectionExpanded = !logSectionExpanded;
        }}
        
        async function loadLogs() {{
          const statusEl = document.getElementById('logStatus');
          const entriesEl = document.getElementById('logEntries');
          
          try {{
            statusEl.textContent = 'Loading logs...';
            
            const level = document.getElementById('logLevel').value;
            const hours = document.getElementById('logHours').value;
            const filter = document.getElementById('logFilter').value;
            
            let url = `/health/logs?level=${{level}}&hours=${{hours}}&max_entries=100`;
            if (filter) {{
              url += `&filter=${{encodeURIComponent(filter)}}`;
            }}
            
            const response = await fetch((window.getProxyAwareUrl || (p => p))(url));
            const data = await response.json();
            
            if (data.ok && data.logs) {{
              renderLogEntries(data.logs);
              updateLogStats(data);
              statusEl.textContent = `${{data.total_entries}} entries found`;
            }} else {{
              entriesEl.innerHTML = `<div class="muted">Error loading logs: ${{data.error || 'Unknown error'}}</div>`;
              statusEl.textContent = 'Error loading logs';
            }}
            
            document.getElementById('lastLogRefresh').textContent = `Last updated: ${{new Date().toLocaleTimeString()}}`;
            
          }} catch (error) {{
            entriesEl.innerHTML = `<div class="muted">Network error loading logs</div>`;
            statusEl.textContent = 'Network error';
          }}
        }}
        
        function renderLogEntries(logs) {{
          const entriesEl = document.getElementById('logEntries');
          
          if (!logs || logs.length === 0) {{
            entriesEl.innerHTML = '<div class="muted">No log entries found</div>';
            return;
          }}
          
          const html = logs.map(entry => {{
            const levelClass = entry.level.toLowerCase();
            const ageText = entry.age_seconds < 60 ? 
              `${{entry.age_seconds}}s ago` : 
              entry.age_seconds < 3600 ? 
              `${{Math.floor(entry.age_seconds / 60)}}m ago` : 
              `${{Math.floor(entry.age_seconds / 3600)}}h ago`;
              
            return `
              <div class="log-entry log-${{levelClass}}" style="margin-bottom:.6rem;padding:.4rem;border-radius:4px;border-left:3px solid var(--${{levelClass === 'error' ? 'red' : levelClass === 'warning' ? 'orange' : 'blue'}})">
                <div class="flex" style="justify-content:space-between;align-items:center;margin-bottom:.2rem">
                  <span class="log-level badge b-${{levelClass === 'error' ? 'crit' : levelClass === 'warning' ? 'warn' : 'ok'}}">${{entry.level}}</span>
                  <span class="muted" style="font-size:.85em">${{entry.timestamp_local}} (${{ageText}})</span>
                </div>
                <div class="log-logger muted" style="font-size:.9em;margin-bottom:.2rem">${{entry.logger}}</div>
                <div class="log-message">${{entry.message}}</div>
              </div>
            `;
          }}).join('');
          
          entriesEl.innerHTML = html;
          // Auto-scroll to top to see newest entries
          entriesEl.scrollTop = 0;
        }}
        
        function updateLogStats(data) {{
          const statsEl = document.getElementById('logStats');
          if (data.total_entries > 0) {{
            statsEl.textContent = `${{data.total_entries}} entries`;
          }} else {{
            statsEl.textContent = 'No entries';
          }}
        }}
        
        function clearLogFilter() {{
          document.getElementById('logFilter').value = '';
          loadLogs();
        }}
        
        function startAutoRefresh() {{
          if (document.getElementById('autoRefreshLogs').checked) {{
            autoRefreshInterval = setInterval(() => {{
              if (logSectionExpanded) {{
                loadLogs();
              }}
            }}, 30000); // 30 seconds
          }}
        }}
        
        function stopAutoRefresh() {{
          if (autoRefreshInterval) {{
            clearInterval(autoRefreshInterval);
            autoRefreshInterval = null;
          }}
        }}
        
        function toggleAutoRefresh() {{
          stopAutoRefresh();
          if (logSectionExpanded) {{
            startAutoRefresh();
          }}
        }}
        
        // Add debounced filter input
        document.getElementById('logFilter').addEventListener('input', function() {{
          clearTimeout(filterTimeout);
          filterTimeout = setTimeout(loadLogs, 500); // Wait 500ms after typing stops
        }});
        
        // Load log stats on page load
        async function loadLogStats() {{
          try {{
            const response = await fetch((window.getProxyAwareUrl || (p => p))('/health/logs/stats'));
            const data = await response.json();
            
            if (data.ok && data.stats) {{
              const stats = data.stats;
              let statsText = '';
              
              if (stats.total_entries > 0) {{
                const errorCount = stats.by_level.ERROR || 0;
                const warningCount = stats.by_level.WARNING || 0;
                
                if (errorCount > 0 || warningCount > 0) {{
                  statsText = `${{errorCount}} errors, ${{warningCount}} warnings (24h)`;
                }} else {{
                  statsText = `${{stats.total_entries}} log entries (24h)`;
                }}
              }} else {{
                statsText = 'No recent logs';
              }}
              
              document.getElementById('logStats').textContent = statsText;
            }}
          }} catch (error) {{
            // Silently fail - log stats are not critical
          }}
        }}
        
        // Load initial log stats
        loadLogStats();
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

# -------- Log viewing endpoints --------
@health_bp.route("/health/logs")
def health_logs():
    """Get recent log entries with filtering."""
    try:
        # Get query parameters
        max_entries = min(int(request.args.get("max_entries", 50)), 200)  # Cap at 200
        min_level = request.args.get("level", "WARNING").upper()
        hours_back = min(int(request.args.get("hours", 24)), 168)  # Cap at 1 week
        filter_text = request.args.get("filter", "").strip()
        
        # Validate log level
        if min_level not in ["DEBUG", "INFO", "WARNING", "ERROR"]:
            min_level = "WARNING"
        
        # Get log entries
        if filter_text:
            entries = log_reader.get_entries_by_filter(
                filter_text=filter_text,
                max_entries=max_entries,
                min_level=min_level
            )
        else:
            entries = log_reader.get_recent_entries(
                max_entries=max_entries,
                min_level=min_level,
                hours_back=hours_back
            )
        
        # Convert to JSON-serializable format
        log_data = [entry.to_dict() for entry in entries]
        
        return {
            "ok": True,
            "logs": log_data,
            "total_entries": len(log_data),
            "filters": {
                "max_entries": max_entries,
                "min_level": min_level,
                "hours_back": hours_back,
                "filter_text": filter_text
            }
        }
        
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "logs": []
        }, 500

@health_bp.route("/health/logs/stats")
def health_log_stats():
    """Get log statistics for monitoring."""
    try:
        stats = log_reader.get_log_stats()
        return {
            "ok": True,
            "stats": stats
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "stats": {}
        }, 500
