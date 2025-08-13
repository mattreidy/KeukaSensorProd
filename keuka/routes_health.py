# routes_health.py
# -----------------------------------------------------------------------------
# Health dashboard + data:
#   /health      - Responsive HTML page (dark-mode aware, mobile-friendly)
#   /health.json - JSON payload (programmatic or fallback polling)
#   /health.sse  - Server-Sent Events stream that pushes a fresh payload ~5s
#
# Now shows BOTH interfaces:
#   • AP iface (wlan0 by default): SSID, mode, MAC, state, channel/freq, clients
#   • STA iface (wlan1 by default): SSID, RSSI bars, freq, MAC, state
#
# Notes:
#   - Never pokes hostapd; read-only queries for AP info.
#   - STA data comes from wifi_net.wifi_status() (managed iface).
#   - AP SSID is pulled from iw info if present, else /etc/hostapd/hostapd.conf.
# -----------------------------------------------------------------------------

import json
import time
import re
from pathlib import Path
from datetime import datetime, timedelta
from flask import Blueprint, Response

from ui import render_page
from utils import utcnow_str, sh, read_text
from config import (
    WLAN_STA_IFACE, WLAN_AP_IFACE, VERSION,
    TEMP_WARN_F, TEMP_CRIT_F, RSSI_WARN_DBM, RSSI_CRIT_DBM,
    CPU_TEMP_WARN_C, CPU_TEMP_CRIT_C,
)
from camera import camera
from sensors import read_temp_fahrenheit, median_distance_inches
from wifi_net import wifi_status, ip_addr4, gw4, dns_servers
from system_diag import cpu_temp_c, uptime_seconds, disk_usage_root, mem_usage

health_bp = Blueprint("health", __name__)

# -------- helpers (AP/iface info) --------------------------------------------

def _iface_mac(iface: str) -> str | None:
    p = Path(f"/sys/class/net/{iface}/address")
    try:
        return p.read_text(encoding="utf-8").strip()
    except Exception:
        return None

def _iface_state(iface: str) -> str | None:
    # ip -br link show dev wlan0  ->  wlan0  UP  xx:xx...
    code, out = sh(["ip", "-br", "link", "show", "dev", iface])
    if code != 0 or not out.strip():
        return None
    # states are the 2nd column; examples: UNKNOWN, DOWN, UP
    try:
        return out.split()[1]
    except Exception:
        return None

def _iw_info(iface: str) -> dict:
    """
    Light parser for `iw dev <iface> info`.
    We try to extract: type, ssid (if printed), channel, freq_mhz.
    """
    info = {"type": None, "ssid": None, "channel": None, "freq_mhz": None}
    code, out = sh(["/sbin/iw", "dev", iface, "info"])
    if code != 0:
        return info
    for ln in out.splitlines():
        s = ln.strip()
        if s.lower().startswith("type "):
            info["type"] = s.split(" ", 1)[1].strip().lower()
        elif s.lower().startswith("ssid "):
            info["ssid"] = s.split(" ", 1)[1].strip()
        elif s.lower().startswith("channel "):
            # e.g. "channel 6 (2437 MHz), width: 20 MHz"
            m = re.search(r"channel\s+(\d+)\s+\((\d+)\s*MHz\)", s, re.I)
            if m:
                info["channel"] = int(m.group(1))
                info["freq_mhz"] = int(m.group(2))
    return info

def _hostapd_ssid(conf_path: str = "/etc/hostapd/hostapd.conf") -> str | None:
    try:
        txt = read_text(Path(conf_path))
        m = re.search(r"^\s*ssid\s*=\s*(.+)$", txt, re.M)
        return m.group(1).strip() if m else None
    except Exception:
        return None

def _ap_client_count(iface: str) -> int | None:
    # `iw dev wlan0 station dump` and count lines starting with "Station "
    code, out = sh(["/sbin/iw", "dev", iface, "station", "dump"])
    if code != 0:
        return None
    return sum(1 for ln in out.splitlines() if ln.strip().lower().startswith("station "))

def _build_iface_payload() -> dict:
    """
    Build a dict with detailed info for both wlan ifaces.
    Keys: WLAN_AP_IFACE and WLAN_STA_IFACE (names from config).
    """
    # STA details via wifi_status (managed)
    sta = wifi_status() or {}
    sta_iface = sta.get("iface") or WLAN_STA_IFACE

    sta_payload = {
        "iface": sta_iface,
        "role": "sta",
        "mac": _iface_mac(sta_iface),
        "state": _iface_state(sta_iface),
        "mode": (_iw_info(sta_iface).get("type") or "managed"),
        "ssid": sta.get("ssid"),
        "signal_dbm": sta.get("signal_dbm"),
        "freq_mhz": sta.get("freq_mhz"),
        "tx_bitrate": sta.get("tx_bitrate"),
        "ip_cidr": ip_addr4(sta_iface),
        "gateway": gw4(sta_iface),
    }

    # AP details (read-only; don't touch hostapd)
    ap_info = _iw_info(WLAN_AP_IFACE)
    ap_ssid = ap_info.get("ssid") or _hostapd_ssid()  # fallback to hostapd.conf
    ap_payload = {
        "iface": WLAN_AP_IFACE,
        "role": "ap",
        "mac": _iface_mac(WLAN_AP_IFACE),
        "state": _iface_state(WLAN_AP_IFACE),
        "mode": ap_info.get("type") or "ap",
        "ssid": ap_ssid,
        "channel": ap_info.get("channel"),
        "freq_mhz": ap_info.get("freq_mhz"),
        "clients": _ap_client_count(WLAN_AP_IFACE),
        "ip_cidr": ip_addr4(WLAN_AP_IFACE),
        "gateway": gw4(WLAN_AP_IFACE),
    }

    return {WLAN_AP_IFACE: ap_payload, WLAN_STA_IFACE: sta_payload}

# -------- Shared payload builder ---------------------------------------------

def build_health_payload() -> dict:
    # Sensor readings
    tF = read_temp_fahrenheit()
    dIn = median_distance_inches()

    # Per-interface detail (AP + STA)
    ifaces = _build_iface_payload()

    # System stats
    cpu_c = cpu_temp_c()
    up_s = uptime_seconds()

    # CPU utilization from /proc/stat deltas
    cpu_util = None
    try:
        with open("/proc/stat", "r") as f:
            parts = f.readline().split()[1:]  # skip "cpu"
        nums = list(map(int, parts[:8]))     # user nice system idle iowait irq softirq steal
        idle = nums[3] + nums[4]             # idle + iowait
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

    # Back-compat top-level wifi/ip/gateway keys based on STA/AP
    sta_key = WLAN_STA_IFACE
    ap_key = WLAN_AP_IFACE
    st = {
        "iface": sta_key,
        "ssid": ifaces.get(sta_key, {}).get("ssid"),
        "bssid": None,  # not derived here
        "signal_dbm": ifaces.get(sta_key, {}).get("signal_dbm"),
        "freq_mhz": ifaces.get(sta_key, {}).get("freq_mhz"),
        "tx_bitrate": ifaces.get(sta_key, {}).get("tx_bitrate"),
    }

    return {
        "time_utc": utcnow_str(),
        "tempF": None if (tF != tF) else round(tF, 2),
        "distanceInches": None if (dIn != dIn) else round(dIn, 2),
        "camera": "running" if camera.running else "idle",

        # New: detailed per-interface info
        "ifaces": ifaces,

        # Back-compat fields (used by older UI bits)
        "wifi": st,
        "ip": {
            sta_key: ifaces.get(sta_key, {}).get("ip_cidr"),
            ap_key:  ifaces.get(ap_key, {}).get("ip_cidr"),
        },
        "gateway_sta": ifaces.get(sta_key, {}).get("gateway"),
        "gateway_ap":  ifaces.get(ap_key, {}).get("gateway"),
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
            "hostname": __import__("subprocess").getoutput("hostname")
        },
        "thresholds": {
            "temp_warn_f": TEMP_WARN_F,
            "temp_crit_f": TEMP_CRIT_F,
            "rssi_warn_dbm": RSSI_WARN_DBM,
            "rssi_crit_dbm": RSSI_CRIT_DBM,
            "cpu_warn_c": CPU_TEMP_WARN_C,
            "cpu_crit_c": CPU_TEMP_CRIT_C
        }
    }

# -------- HTML dashboard ------------------------------------------------------

@health_bp.route("/health")
def health():
    info = build_health_payload()
    extra_head = f"""
    <script id="seed" type="application/json">{json.dumps(info)}</script>
    """
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

        <!-- AP card -->
        <div class="card">
          <h3 style="margin-top:0">Wi-Fi (AP {WLAN_AP_IFACE})</h3>
          <table>
            <tr><th>Status</th><td><span id="apStatus" class="badge"></span></td></tr>
            <tr><th>SSID</th><td id="apSsid"></td></tr>
            <tr><th>Mode</th><td id="apMode"></td></tr>
            <tr><th>Channel</th><td><span id="apChan"></span> (<span id="apFreq"></span> MHz)</td></tr>
            <tr><th>Clients</th><td id="apClients"></td></tr>
            <tr><th>MAC</th><td class="mono" id="apMac"></td></tr>
          </table>
        </div>

        <!-- STA card -->
        <div class="card">
          <h3 style="margin-top:0">Wi-Fi (STA {WLAN_STA_IFACE})</h3>
          <table>
            <tr><th>Status</th><td><span id="staStatus" class="badge"></span></td></tr>
            <tr><th>SSID</th><td id="ssid"></td></tr>
            <tr><th>Signal</th><td><span id="rssiBars" class="flex"></span></td></tr>
            <tr><th>Frequency</th><td><span id="freq"></span> MHz</td></tr>
            <tr><th>Mode</th><td id="staMode"></td></tr>
            <tr><th>MAC</th><td class="mono" id="staMac"></td></tr>
          </table>
        </div>

        <div class="card">
          <h3 style="margin-top:0">Networking</h3>
          <table>
            <tr><th>IPv4 ({WLAN_STA_IFACE})</th><td class="mono" id="ip_sta"></td></tr>
            <tr><th>IPv4 ({WLAN_AP_IFACE})</th><td class="mono" id="ip_ap"></td></tr>
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

      <div class="card" style="margin-top:1rem">
        <div class="flex"><strong>Raw JSON</strong><button class="btn" onclick="copyJSON()">Copy</button><span id="copynote" class="muted"></span></div>
        <pre id="rawjson" class="mono" style="white-space:pre-wrap;margin-top:.4rem"></pre>
      </div>

      <script>
        // ---- helpers ----
        function fmt(v, fallback="(n/a)") { return (v===null||v===undefined||v==="") ? fallback : v; }
        function bytes(n) {
          if (n===0) return "0 B";
          const u=["B","KB","MB","GB","TB","PB"]; let i = Math.floor(Math.log(n)/Math.log(1024));
          return (n/Math.pow(1024,i)).toFixed(i?1:0)+" "+u[i];
        }
        function setBadge(el, level, text) {
          el.className = "badge " + (level ? "b-"+level : "");
          el.textContent = text || "";
        }
        function rssiBarsHTML(dbm) {
          if (dbm===null || dbm===undefined) return "(n/a)";
          const v = Number(dbm);
          let bars = 0;
          if (v >= -50) bars = 5; else if (v >= -60) bars = 4; else if (v >= -67) bars = 3; else if (v >= -75) bars = 2; else if (v >= -82) bars = 1; else bars = 0;
          const spans = Array.from({length:5}, (_,i)=>`<span class="${i<bars?"on":""}" style="height:${4+i*2}px"></span>`).join("");
          return `<span class="bars" title="${v} dBm">${spans}</span><span class="muted"> ${v} dBm</span>`;
        }
        function humanUptimeDHMS(sec) {
          let s = Number(sec);
          const d=Math.floor(s/86400); s-=d*86400;
          const h=Math.floor(s/3600); s-=h*3600;
          const m=Math.floor(s/60);
          const parts=[]; if (d) parts.push(d+"d"); parts.push(h+"h"); parts.push(m+"m");
          return parts.join(" ");
        }
        let prev = {};
        function upDownFlash(el, key, newVal) {
          const was = prev[key]; prev[key] = newVal;
          const n = Number(newVal); const w = Number(was);
          if (!isFinite(n) || !isFinite(w)) return; // numeric only
          if (n > w) { el.classList.remove("downflash"); void el.offsetWidth; el.classList.add("upflash"); }
          else if (n < w) { el.classList.remove("upflash"); void el.offsetWidth; el.classList.add("downflash"); }
        }
        function copyJSON() {
          const pre = document.getElementById('rawjson');
          navigator.clipboard.writeText(pre.textContent).then(()=>{
            const n = document.getElementById('copynote'); n.textContent = "Copied!";
            setTimeout(()=> n.textContent="", 1200);
          });
        }
        let lastUpdateEpoch = 0;
        function tickAgo() {
          const el = document.getElementById('lastUpdated');
          if (!lastUpdateEpoch) { el.textContent = "—"; return; }
          const secs = Math.round((Date.now() - lastUpdateEpoch)/1000);
          el.textContent = "Updated " + (secs===0 ? "just now" : secs + "s ago");
        }
        setInterval(tickAgo, 1000);

        // ---- main render ----
        function render(data) {
          // localize server UTC
          const dt = new Date(String(data.time_utc).replace(' ', 'T') + 'Z');
          document.getElementById('localTime').textContent = dt.toLocaleString();

          document.getElementById('rawjson').textContent = JSON.stringify(data, null, 2);

          // Environment
          const tempEl = document.getElementById('tempF');
          tempEl.textContent = fmt(data.tempF);
          upDownFlash(tempEl, "tempF", data.tempF);

          const distEl = document.getElementById('distanceInches');
          distEl.textContent = fmt(data.distanceInches);
          upDownFlash(distEl, "distanceInches", data.distanceInches);

          const camBadge = document.getElementById('cameraBadge');
          setBadge(camBadge, (data.camera==="running"?"ok":"idle"), (data.camera==="running"?"Running":"Idle"));

          // ---- Interfaces (AP + STA) ----
          const ifs = data.ifaces || {};
          const ap = ifs["{WLAN_AP_IFACE}"] || {};
          const sta = ifs["{WLAN_STA_IFACE}"] || {};

          // AP
          setBadge(document.getElementById('apStatus'),
                   (ap.state==="UP" ? "ok" : "warn"),
                   ap.state || "(n/a)");
          document.getElementById('apSsid').textContent = fmt(ap.ssid);
          document.getElementById('apMode').textContent = fmt(ap.mode);
          document.getElementById('apChan').textContent = fmt(ap.channel);
          document.getElementById('apFreq').textContent = fmt(ap.freq_mhz);
          document.getElementById('apClients').textContent = fmt(ap.clients, "0");
          document.getElementById('apMac').textContent = fmt(ap.mac);

          // STA
          setBadge(document.getElementById('staStatus'),
                   (sta.ssid ? "ok" : "warn"),
                   sta.ssid ? "Connected" : "Not connected");
          document.getElementById('ssid').textContent = fmt(sta.ssid, "Not connected");
          document.getElementById('freq').textContent = fmt(sta.freq_mhz);
          document.getElementById('staMode').textContent = fmt(sta.mode);
          document.getElementById('staMac').textContent = fmt(sta.mac);
          document.getElementById('rssiBars').innerHTML = rssiBarsHTML(sta.signal_dbm);
          upDownFlash(document.getElementById('rssiBars'), "rssi", sta.signal_dbm);

          // Networking
          document.getElementById('ip_sta').textContent = fmt(data.ip["{WLAN_STA_IFACE}"]);
          document.getElementById('ip_ap').textContent = fmt(data.ip["{WLAN_AP_IFACE}"]);
          document.getElementById('gw_sta').textContent = fmt(data.gateway_sta);
          document.getElementById('gw_ap').textContent = fmt(data.gateway_ap);
          document.getElementById('dns').textContent = (data.dns && data.dns.length) ? data.dns.join(", ") : "(n/a)";

          // System
          document.getElementById('hostname').textContent = fmt(data.system.hostname);
          document.getElementById('cpuTemp').textContent = fmt(data.system.cpu_temp_c);
          const cpuB = document.getElementById('cpuBadge');
          let cpuLv = ""; let cpuTx="";
          if (isFinite(data.system.cpu_temp_c)) {
            if (data.system.cpu_temp_c >= {CPU_TEMP_CRIT_C}) { cpuLv="crit"; cpuTx="Hot"; }
            else if (data.system.cpu_temp_c >= {CPU_TEMP_WARN_C}) { cpuLv="warn"; cpuTx="Warm"; }
            else { cpuLv="ok"; cpuTx="Cool"; }
          }
          setBadge(cpuB, cpuLv, cpuTx);

          const cpuUtilEl = document.getElementById('cpuUtil');
          cpuUtilEl.textContent = (data.system.cpu_util_pct == null) ? "(n/a)" : Number(data.system.cpu_util_pct).toFixed(1);
          upDownFlash(cpuUtilEl, "cpuUtil", data.system.cpu_util_pct);

          document.getElementById('uptime').textContent = humanUptimeDHMS(data.system.uptime_seconds);
          const bootDt = new Date(String(data.system.boot_time_utc).replace(' ', 'T') + 'Z');
          document.getElementById('bootLocal').textContent = bootDt.toLocaleString();

          const d = data.system.disk;
          document.getElementById('diskPct').textContent = fmt(d.percent);
          document.getElementById('diskSizes').textContent = `${bytes(d.used)} / ${bytes(d.total)}`;

          const m = data.system.mem;
          document.getElementById('memPct').textContent = fmt(m.percent);
          document.getElementById('memTotal').textContent = bytes(m.total||0);
          document.getElementById('memUsed').textContent  = bytes(m.used||0);
          document.getElementById('memFree').textContent  = bytes(m.free||0);

          const th = document.getElementById('thumb');
          if (th && th.style.display!=="none") { th.src = "/snapshot?cb=" + Date.now(); }

          lastUpdateEpoch = Date.now(); tickAgo();
        }

        // Seed immediately
        try { const seed = JSON.parse(document.getElementById('seed').textContent); render(seed); } catch (_e) {}

        // SSE hookup (fallback to polling if SSE unsupported)
        let es = null;
        function connectSSE() {
          if (!window.EventSource) { document.getElementById('connDot').className = "dot"; pollFallback(); return; }
          es = new EventSource('/health.sse');
          const dot = document.getElementById('connDot');
          es.onopen = () => { dot.className="dot ok"; };
          es.onerror = () => { dot.className="dot err"; try { es.close(); } catch(_e) {}; setTimeout(connectSSE, 3000); };
          es.addEventListener('health', (e) => { const data = JSON.parse(e.data); render(data); dot.className="dot ok"; });
        }
        async function pollFallback() {
          const dot = document.getElementById('connDot');
          async function once() {
            try { const r = await fetch('/health.json', {cache:'no-store'}); const data = await r.json(); render(data); dot.className="dot ok"; }
            catch(_e) { dot.className="dot err"; }
          }
          once(); setInterval(once, 5000);
        }
        connectSSE();
      </script>
    """
    return render_page("Keuka Sensor – Health", body, extra_head)

# -------- JSON (programmatic/fallback) ---------------------------------------

@health_bp.route("/health.json")
def health_json():
    return build_health_payload()

# -------- Server-Sent Events -------------------------------------------------

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
