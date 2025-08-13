#!/usr/bin/env python3
import os
import sys
import time
import json
import re
import shutil
from typing import Optional
import ipaddress
import threading
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, Response, request, abort, redirect, url_for, make_response

# --- Ensure NumPy is loaded before cv2 on some Pi builds ---
try:
    import numpy as _np  # noqa: F401
except Exception:
    _np = None

# Camera: OpenCV (optional, headless OK)
try:
    import cv2  # type: ignore
except Exception as _e:
    cv2 = None
    print(f"OpenCV not available: {_e}", file=sys.stderr)

# GPIO (allow import on dev machines)
try:
    import RPi.GPIO as GPIO  # type: ignore
except Exception:
    class _DummyGPIO:
        BCM = BOARD = IN = OUT = LOW = HIGH = PUD_DOWN = PUD_UP = None
        def setmode(self, *a, **k): pass
        def setwarnings(self, *a, **k): pass
        def setup(self, *a, **k): pass
        def output(self, *a, **k): pass
        def input(self, *a, **k): return 0
        def cleanup(self): pass
    GPIO = _DummyGPIO()

try:
    from w1thermsensor import W1ThermSensor  # type: ignore
except Exception:
    W1ThermSensor = None

# ---------------- Config ----------------
TRIG_PIN = 23
ECHO_PIN = 24
ULTRASONIC_TIMEOUT_S = 0.03
SAMPLES = 7
CAMERA_INDEX = 0
FRAME_W, FRAME_H = 640, 480
MJPEG_JPEG_QUALITY = 70

ADMIN_USER = os.environ.get("KS_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("KS_ADMIN_PASS", "password")

APP_DIR = Path(__file__).resolve().parent
WLAN_STA_IFACE = os.environ.get("KS_STA_IFACE", "wlan1")  # USB adapter (external antenna)
WLAN_AP_IFACE  = os.environ.get("KS_AP_IFACE",  "wlan0")  # onboard AP
WPA_SUP_CONF = Path("/etc/wpa_supplicant/wpa_supplicant-" + WLAN_STA_IFACE + ".conf")

DHCPCD_CONF = Path("/etc/dhcpcd.conf")
DHCPCD_MARK_BEGIN = "# KS-STATIC-BEGIN"
DHCPCD_MARK_END   = "# KS-STATIC-END"

DUCKDNS_CONF = APP_DIR / "duckdns.conf"           # token=... , domains=example1,example2
DUCKDNS_LAST = APP_DIR / "duckdns_last.txt"

# UI thresholds (override with env vars if desired)
TEMP_WARN_F = float(os.environ.get("KS_TEMP_WARN_F", "85"))
TEMP_CRIT_F = float(os.environ.get("KS_TEMP_CRIT_F", "95"))
RSSI_WARN_DBM = int(os.environ.get("KS_RSSI_WARN_DBM", "-70"))
RSSI_CRIT_DBM = int(os.environ.get("KS_RSSI_CRIT_DBM", "-80"))
CPU_TEMP_WARN_C = float(os.environ.get("KS_CPU_WARN_C", "75"))  # warn at 75°C
CPU_TEMP_CRIT_C = float(os.environ.get("KS_CPU_CRIT_C", "85"))  # crit at 85°C

VERSION = "1.6.0"

# ---------------- Flask -----------------
app = Flask(__name__)

# ---------------- Utils -----------------
def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def utcnow_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def basic_auth_ok(req) -> bool:
    a = req.authorization
    return bool(a and a.username == ADMIN_USER and a.password == ADMIN_PASS)

def sh(cmd):
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        return 0, out
    except subprocess.CalledProcessError as e:
        return e.returncode, e.output

def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""

def write_text_atomic(path: Path, content: str, sudo_mv: bool = False) -> bool:
    tmp = Path("/tmp") / (path.name + ".new")
    tmp.write_text(content, encoding="utf-8")
    if sudo_mv:
        code, _ = sh(["sudo", "/bin/mv", str(tmp), str(path)])
        return code == 0
    else:
        tmp.replace(path)
        return True

# ---------------- Camera worker ----------------
class Camera:
    def __init__(self, index=0, w=640, h=480):
        self.index = index; self.w=w; self.h=h
        self.cap=None; self.lock=threading.Lock(); self.frame=None; self.running=False
        self.thread = threading.Thread(target=self._worker, daemon=True)
    def start(self):
        if cv2 is None: return
        if self.running: return
        self.cap = cv2.VideoCapture(self.index)
        if not self.cap or not self.cap.isOpened(): return
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.w)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.h)
        self.running=True; self.thread.start()
    def _worker(self):
        while self.running and self.cap and self.cap.isOpened():
            ok, frm = self.cap.read()
            if ok:
                ok2, jpg = cv2.imencode('.jpg', frm, [int(cv2.IMWRITE_JPEG_QUALITY), MJPEG_JPEG_QUALITY])
                if ok2:
                    with self.lock:
                        self.frame = jpg.tobytes()
            else:
                time.sleep(0.05)
        if self.cap: self.cap.release()
    def get_jpeg(self):
        with self.lock: return self.frame
    def stop(self): self.running=False

camera = Camera(CAMERA_INDEX, FRAME_W, FRAME_H)

# ---------------- Ultrasonic (JSN-SR04T) ----------------
def _ultra_setup():
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(TRIG_PIN, GPIO.OUT)
    GPIO.setup(ECHO_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    GPIO.output(TRIG_PIN, GPIO.LOW)
    time.sleep(0.1)

_ultra_ready = False
def _ensure_ultra():
    global _ultra_ready
    if not _ultra_ready:
        _ultra_setup(); _ultra_ready=True

def read_distance_inches(timeout_s: float = ULTRASONIC_TIMEOUT_S) -> float:
    _ensure_ultra()
    GPIO.output(TRIG_PIN, GPIO.LOW); time.sleep(0.000002)
    GPIO.output(TRIG_PIN, GPIO.HIGH); time.sleep(0.000010)
    GPIO.output(TRIG_PIN, GPIO.LOW)
    start=time.time()
    while GPIO.input(ECHO_PIN)==0:
        if time.time()-start>timeout_s: return float('nan')
    t0=time.time()
    while GPIO.input(ECHO_PIN)==1:
        if time.time()-t0>timeout_s: return float('nan')
    t1=time.time(); dt=t1-t0
    return (dt*13503.9)/2.0

def median_distance_inches(samples=SAMPLES)->float:
    vals=[]
    for _ in range(samples):
        v=read_distance_inches()
        if not (v!=v or v==float('inf')): vals.append(v)
        time.sleep(0.075)
    if not vals: return float('nan')
    vals.sort(); return vals[len(vals)//2]

# ---------------- DS18B20 ----------------
def read_temp_fahrenheit()->float:
    try:
        if W1ThermSensor is not None:
            ss=W1ThermSensor.get_available_sensors()
            if ss:
                c=ss[0].get_temperature(); return c*9.0/5.0+32.0
    except Exception: pass
    base='/sys/bus/w1/devices'
    try:
        dev=next((d for d in os.listdir(base) if d.startswith('28-')))
        with open(os.path.join(base,dev,'w1_slave'),'r') as f: data=f.read()
        if 'YES' in data:
            c=float(data.strip().split('t=')[-1])/1000.0
            return c*9.0/5.0+32.0
    except Exception: pass
    return float('nan')

# ---------------- Wi-Fi helpers ----------------
def wifi_status():
    code,out=sh(["iw","dev",WLAN_STA_IFACE,"link"])
    ssid=None; rssi=None; freq=None; bssid=None; bitrate=None
    for ln in out.splitlines():
        s=ln.strip()
        if s.startswith('SSID:'): ssid=s.split(':',1)[1].strip()
        elif s.startswith('signal:'):
            try: rssi=int(s.split()[1])
            except: pass
        elif s.startswith('freq:'):
            try: freq=int(s.split()[1])
            except: pass
        elif s.startswith('tx bitrate:'): bitrate=s.split(':',1)[1].strip()
        elif s.startswith('Connected to'):
            try: bssid=s.split()[2]
            except: pass
    return {"iface":WLAN_STA_IFACE, "ssid":ssid, "bssid":bssid, "signal_dbm":rssi, "freq_mhz":freq, "tx_bitrate":bitrate}

def wifi_scan()->list:
    code,out=sh(["sudo","/sbin/iw","dev",WLAN_STA_IFACE,"scan","-u"])
    nets=[]; cur={}
    for ln in out.splitlines():
        s=ln.strip()
        if s.startswith('BSS '):
            if cur.get('ssid'): nets.append(cur)
            cur={}
        elif s.startswith('SSID:'):
            cur['ssid']=s.split(':',1)[1].strip()
        elif s.startswith('signal:'):
            try: cur['signal_dbm']=int(s.split()[1])
            except: pass
        elif s.startswith('freq:'):
            try: cur['freq_mhz']=int(s.split()[1])
            except: pass
    if cur.get('ssid'): nets.append(cur)
    best={}
    for n in nets:
        k=n.get('ssid')
        if not k: continue
        if k not in best or (n.get('signal_dbm',-999) > best[k].get('signal_dbm',-999)):
            best[k]=n
    return sorted(best.values(), key=lambda x: x.get('signal_dbm',-999), reverse=True)

def wifi_connect(ssid:str, psk:str)->bool:
    WPA_SUP_CONF.parent.mkdir(parents=True, exist_ok=True)
    if not WPA_SUP_CONF.exists():
        WPA_SUP_CONF.write_text(
            "ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\n"
            "update_config=1\n"
            "country=US\n",
            encoding='utf-8'
        )
    conf = WPA_SUP_CONF.read_text(encoding='utf-8').splitlines()
    out_lines=[]; in_block=False; keep_block=True; block=[]
    def flush_block():
        nonlocal out_lines, block, keep_block
        if keep_block: out_lines.extend(block)
    i=0
    while i < len(conf):
        ln = conf[i]
        if not in_block and ln.strip().startswith("network={"):
            in_block=True; block=[ln]; keep_block=True; i+=1; continue
        if in_block:
            block.append(ln)
            if 'ssid="' in ln:
                try:
                    existing = ln.split('ssid="',1)[1].split('"',1)[0]
                    if existing == ssid: keep_block=False
                except Exception:
                    pass
            if ln.strip()=="}":
                in_block=False; flush_block(); block=[]
            i+=1; continue
        out_lines.append(ln); i+=1
    new_block = (
        "\nnetwork={\n"
        f'    ssid="{ssid}"\n'
        f'    psk="{psk}"\n'
        "    priority=10\n"
        "}\n"
    )
    out_text = "\n".join(out_lines) + new_block
    WPA_SUP_CONF.write_text(out_text, encoding='utf-8')
    sh(["sudo","/sbin/wpa_cli","-i",WLAN_STA_IFACE,"reconfigure"])
    return True

# ---------------- IPv4 info & config (DHCP/Static on wlan1) ----------------
def ip_addr4(iface: str) -> Optional[str]:
    code,out=sh(["ip","-4","-o","addr","show","dev",iface])
    if code!=0: return None
    m=re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+/\d+)", out)
    return m.group(1) if m else None

def gw4(iface: str) -> Optional[str]:
    code,out=sh(["ip","route","show","default","dev",iface])
    if code!=0: return None
    m=re.search(r"default via\s+(\d+\.\d+\.\d+\.\d+)", out)
    return m.group(1) if m else None

def dns_servers() -> list[str]:
    txt=read_text(Path("/etc/resolv.conf"))
    return re.findall(r"nameserver\s+(\d+\.\d+\.\d+\.\d+)", txt)

def dhcpcd_current_mode()->dict:
    """
    Reads /etc/dhcpcd.conf and returns {'mode':'dhcp'|'static','ip':..., 'router':..., 'dns':[...]}
    We manage a block delimited by KS-STATIC-BEGIN/END. If missing, we assume DHCP.
    """
    conf = read_text(DHCPCD_CONF)
    block = re.search(rf"{re.escape(DHCPCD_MARK_BEGIN)}.*?{re.escape(DHCPCD_MARK_END)}", conf, re.S)
    if not block:
        return {"mode":"dhcp"}
    text = block.group(0)
    ip = re.search(r"ip_address=([0-9./]+)", text)
    routers = re.search(r"routers=([0-9.]+)", text)
    dns = re.search(r"domain_name_servers=([0-9. ]+)", text)
    return {
        "mode":"static",
        "ip": ip.group(1) if ip else "",
        "router": routers.group(1) if routers else "",
        "dns": (dns.group(1).split() if dns else [])
    }

def dhcpcd_render(mode:str, ip_cidr:str="", router:str="", dns_list: Optional[list[str]] = None)->str:
    base = read_text(DHCPCD_CONF)
    # Remove any previous KS block
    base2 = re.sub(rf"{re.escape(DHCPCD_MARK_BEGIN)}.*?{re.escape(DHCPCD_MARK_END)}\n?", "", base, flags=re.S)
    if mode=="dhcp":
        return base2
    # static block for wlan1
    dns_list = dns_list or []
    dns_line = " ".join(dns_list)
    block = (
        f"{DHCPCD_MARK_BEGIN}\n"
        f"interface {WLAN_STA_IFACE}\n"
        f"static ip_address={ip_cidr}\n"
        f"static routers={router}\n"
        f"static domain_name_servers={dns_line}\n"
        f"{DHCPCD_MARK_END}\n"
    )
    if not base2.endswith("\n"): base2 += "\n"
    return base2 + block

def apply_network(mode:str, ip_cidr:str="", router:str="", dns_csv:str="")->tuple[bool,str]:
    # Validate
    if mode not in ("dhcp","static"):
        return False, "Invalid mode"
    if mode=="static":
        try:
            ipaddress.ip_interface(ip_cidr)
        except Exception:
            return False, "Invalid IP/CIDR"
        for n in [router]+[x.strip() for x in dns_csv.split(",") if x.strip()]:
            try: ipaddress.ip_address(n)
            except Exception: return False, f"Invalid address: {n}"
        dns_list=[x.strip() for x in dns_csv.split(",") if x.strip()]
    else:
        dns_list=[]
    # Render new dhcpcd.conf and install via sudo mv
    new_text = dhcpcd_render(mode, ip_cidr, router, dns_list)
    ok = write_text_atomic(DHCPCD_CONF, new_text, sudo_mv=True)
    if not ok:
        return False, "Failed to write /etc/dhcpcd.conf (sudo mv)"
    # Restart dhcpcd so changes apply
    code, out = sh(["sudo","/usr/bin/systemctl","restart","dhcpcd"])
    if code != 0:
        return False, "Failed to restart dhcpcd: "+out
    # Nudge supplicant just in case
    sh(["sudo","/sbin/wpa_cli","-i",WLAN_STA_IFACE,"reconfigure"])
    time.sleep(1.0)
    return True, "Applied"

# ---------------- System diagnostics ----------------
def cpu_temp_c()->float:
    # Try Linux thermal zone
    try:
        with open("/sys/class/thermal/thermal_zone0/temp","r") as f:
            v = f.read().strip()
            return float(v)/1000.0
    except Exception:
        pass
    # Try vcgencmd
    code,out = sh(["/usr/bin/vcgencmd","measure_temp"])
    if code==0 and "temp=" in out:
        try:
            return float(out.split("temp=")[1].split("'")[0])
        except Exception:
            pass
    return float('nan')

def uptime_seconds()->float:
    try:
        with open("/proc/uptime","r") as f:
            return float(f.read().split()[0])
    except Exception:
        return 0.0

def disk_usage_root()->dict:
    try:
        total, used, free = shutil.disk_usage("/")
        pct = (used/total*100.0) if total>0 else 0.0
        return {"total": total, "used": used, "free": free, "percent": round(pct,1)}
    except Exception:
        return {"total": 0, "used": 0, "free": 0, "percent": 0.0}

def mem_usage()->dict:
    try:
        m = {}
        with open("/proc/meminfo","r") as f:
            for ln in f:
                if ":" in ln:
                    k,v = ln.split(":",1)
                    m[k.strip()] = v.strip()
        def kiB_to_bytes(s):
            try:
                return int(s.split()[0])*1024
            except Exception:
                return 0
        total = kiB_to_bytes(m.get("MemTotal","0 kB"))
        avail = kiB_to_bytes(m.get("MemAvailable","0 kB"))
        used = total - avail
        pct = (used/total*100.0) if total>0 else 0.0
        return {"total": total, "used": used, "free": avail, "percent": round(pct,1)}
    except Exception:
        return {"total": 0, "used": 0, "free": 0, "percent": 0.0}

# ---------------- Shared UI helpers ----------------
def _base_css():
    # CSS for consistent modern style + dark mode
    return """
    :root {
        --bg: #ffffff; --fg: #111; --muted:#666; --card:#f8f9fb; --border:#e5e7eb;
        --ok:#0a7d27; --warn:#b85c00; --crit:#a40000; --idle:#666;
        --link:#2563eb; --badge:#eef2ff;
    }
    @media (prefers-color-scheme: dark) {
        :root {
            --bg:#0b0f14; --fg:#e5e7eb; --muted:#94a3b8; --card:#0f1720; --border:#1f2937;
            --link:#60a5fa; --badge:#111827;
        }
        img { filter: brightness(0.95) contrast(1.05); }
    }
    * { box-sizing: border-box; }
    body { margin:0; background:var(--bg); color:var(--fg); font: 15px/1.45 system-ui, Segoe UI, Roboto, Arial, sans-serif; }
    a { color: var(--link); text-decoration: none; }
    .topbar { position: sticky; top:0; z-index: 10; backdrop-filter: blur(6px);
              background: color-mix(in oklab, var(--bg) 85%, transparent);
              border-bottom: 1px solid var(--border); }
    .topbar-inner { display:flex; gap:1rem; align-items:center; justify-content:space-between; padding:.8rem 1rem; max-width:1100px; margin:0 auto; }
    .brand { font-weight: 700; letter-spacing:.2px; }
    nav a { margin-right:.8rem; }
    .container { max-width:1100px; margin: 1rem auto 2rem auto; padding: 0 1rem; }
    h1 { font-size:1.6rem; margin: .4rem 0 .8rem 0; }
    .muted { color: var(--muted); }
    .grid { display:grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
    .card { background: var(--card); border:1px solid var(--border); border-radius:14px; padding:1rem; }
    table { width:100%; border-collapse: collapse; }
    th, td { padding: .5rem .6rem; border-bottom: 1px solid var(--border); vertical-align: top; }
    th { text-align:left; width: 46%; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    .btn { display:inline-block; padding:.5rem .8rem; border:1px solid var(--border); border-radius:10px; background:var(--bg); cursor:pointer; }
    .badge { display:inline-block; padding:.15rem .5rem; border-radius:999px; font-size:.8rem; border:1px solid var(--border); background:var(--badge); }
    .b-ok   { color:#fff; background: var(--ok); border-color: var(--ok); }
    .b-warn { color:#fff; background: var(--warn); border-color: var(--warn); }
    .b-crit { color:#fff; background: var(--crit); border-color: var(--crit); }
    .b-idle { color:#fff; background: var(--idle); border-color: var(--idle); }
    .flex { display:flex; align-items:center; gap:.6rem; }
    .right { margin-left:auto; }
    .bars { display:inline-flex; gap:2px; align-items:flex-end; height:12px; }
    .bars span { width:3px; background:#cbd5e1; border-radius:2px; opacity:.7; }
    .bars .on { background:#16a34a; opacity:1; }
    @media (prefers-color-scheme: dark) {
        .bars span { background:#334155; }
        .bars .on { background:#22c55e; }
    }
    /* change highlights */
    @keyframes flashUp { 0%{ background: rgba(22,163,74,.3);} 100%{ background: transparent;} }
    @keyframes flashDown { 0%{ background: rgba(220,38,38,.32);} 100%{ background: transparent;} }
    .upflash { animation: flashUp 1.2s ease-out; }
    .downflash { animation: flashDown 1.2s ease-out; }
    /* status dot */
    .dot { inline-size:.6rem; block-size:.6rem; border-radius:999px; background:#9ca3af; display:inline-block; }
    .dot.ok { background:#16a34a; }
    .dot.err { background:#dc2626; }
    /* image thumb */
    .thumb { width:100%; max-width: 420px; border-radius:10px; border:1px solid var(--border); display:block; }
    """

def render_page(title: str, body_html: str, extra_head: str = "") -> str:
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>{_base_css()}</style>
  {extra_head}
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <div class="brand">Keuka Sensor</div>
      <nav class="muted">
        <a href="/health">Health</a>
        <a href="/webcam">Webcam</a>
        <a href="/admin">Admin</a>
      </nav>
    </div>
  </header>
  <main class="container">
    {body_html}
  </main>
</body>
</html>"""

# ---------------- Health payload (shared by JSON & SSE) ----------------
# ---------------- Health payload (shared by JSON & SSE) ----------------
def build_health_payload() -> dict:
    # Sensor readings
    tF = read_temp_fahrenheit()
    dIn = median_distance_inches()
    st = wifi_status() or {}

    # System stats
    cpu_c = cpu_temp_c()
    up_s = uptime_seconds()

    # --- CPU utilization (computed from /proc/stat deltas) ---
    cpu_util = None
    try:
        with open("/proc/stat", "r") as f:
            parts = f.readline().split()[1:]  # skip "cpu"
        nums = list(map(int, parts[:8]))  # user nice system idle iowait irq softirq steal
        idle = nums[3] + nums[4]          # idle + iowait
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

    return {
        "time_utc": utcnow_str(),
        "tempF": None if (tF != tF) else round(tF, 2),
        "distanceInches": None if (dIn != dIn) else round(dIn, 2),
        "camera": "running" if camera.running else "idle",
        "wifi": st,
        "ip": {
            WLAN_STA_IFACE: ip_addr4(WLAN_STA_IFACE),
            WLAN_AP_IFACE:  ip_addr4(WLAN_AP_IFACE),
        },
        "gateway_sta": gw4(WLAN_STA_IFACE),
        "gateway_ap":  gw4(WLAN_AP_IFACE),
        "dns": dns_servers(),
        "app": "keuka-sensor",
        "version": VERSION,
        "system": {
            "cpu_temp_c": None if (cpu_c != cpu_c) else round(cpu_c, 1),
            "cpu_util_pct": cpu_util,                 # <— NEW
            "uptime_seconds": int(up_s),
            "boot_time_utc": boot_utc,
            "disk": disk_usage_root(),
            "mem": mem_usage(),                       # has total/used/free/percent
            "hostname": subprocess.getoutput("hostname")
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

# ---------------- Routes ----------------
@app.route('/')
def root_plaintext():
    tF=read_temp_fahrenheit(); dIn=median_distance_inches()
    tF_out=0.0 if (tF!=tF) else tF
    dIn_out=0.0 if (dIn!=dIn) else dIn
    resp=make_response(f"{tF_out:.2f},{dIn_out:.2f}")
    resp.mimetype='text/plain'; return resp

@app.route('/webcam')
def webcam_page():
    body = """
      <h1>Webcam</h1>
      <div class="card">
        <p class="muted">Live MJPEG stream.</p>
        <img src="/stream" alt="Webcam stream" style="max-width:100%;height:auto;border-radius:12px;border:1px solid var(--border)">
      </div>
      <p class="muted">If the image does not load, OpenCV may be unavailable.</p>
    """
    return render_page("Keuka Sensor – Webcam", body)

@app.route('/stream')
def stream_mjpeg():
    if cv2 is None: abort(503,'Webcam not available (OpenCV missing).')
    if not camera.running: camera.start()
    def gen():
        boundary = 'frame'
        while True:
            frm = camera.get_jpeg()
            if frm is None:
                time.sleep(0.05); continue
            yield (b"--" + boundary.encode() + b"\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + frm + b"\r\n")
    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/snapshot')
def snapshot_jpeg():
    if cv2 is None: abort(503, 'Webcam not available (OpenCV missing).')
    if not camera.running: camera.start()
    # Wait briefly for a frame
    t0=time.time()
    frm=None
    while time.time()-t0 < 2.0:
        frm = camera.get_jpeg()
        if frm: break
        time.sleep(0.05)
    if not frm: abort(503, 'No frame')
    resp = Response(frm, mimetype='image/jpeg')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

# -------- Health (HTML dashboard) --------
@app.route('/health')
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

        <div class="card">
          <h3 style="margin-top:0">Wi-Fi (STA {WLAN_STA_IFACE})</h3>
          <table>
            <tr><th>Status</th><td><span id="wifiStatus" class="badge"></span></td></tr>
            <tr><th>SSID</th><td id="ssid"></td></tr>
            <tr><th>Signal</th><td><span id="rssiBars" class="flex"></span></td></tr>
            <tr><th>Frequency</th><td><span id="freq"></span> MHz</td></tr>
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
        // Helpers
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
          if (v >= -50) bars = 5; else if (v >= -60) bars = 4; else if (v >= -67) bars = 3; else if (v >= -75) bars = 2; else if (v >= -82) bars = 1; else bars = 0;
          const spans = Array.from({{length:5}}, (_,i)=>`<span class="${{i<bars?"on":""}}" style="height:${{4+i*2}}px"></span>`).join("");
          return `<span class="bars" title="${{v}} dBm">${{spans}}</span><span class="muted"> ${{v}} dBm</span>`;
        }}
        function humanUptimeDHMS(sec) {{
          let s = Number(sec);
          const d=Math.floor(s/86400); s-=d*86400;
          const h=Math.floor(s/3600); s-=h*3600;
          const m=Math.floor(s/60);
          const parts=[];
          if (d) parts.push(d+"d");
          parts.push(h+"h");
          parts.push(m+"m");
          return parts.join(" ");
        }}

        let prev = {{}};
        function upDownFlash(el, key, newVal) {{
          const was = prev[key];
          prev[key] = newVal;
          const n = Number(newVal);
          const w = Number(was);
          if (!isFinite(n) || !isFinite(w)) return; // only for numeric
          if (n > w) {{ el.classList.remove("downflash"); void el.offsetWidth; el.classList.add("upflash"); }}
          else if (n < w) {{ el.classList.remove("upflash"); void el.offsetWidth; el.classList.add("downflash"); }}
        }}

        function copyJSON() {{
          const pre = document.getElementById('rawjson');
          navigator.clipboard.writeText(pre.textContent).then(()=>{{
            const n = document.getElementById('copynote');
            n.textContent = "Copied!";
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

        function render(data) {{
          // Local time
          const dt = new Date(String(data.time_utc).replace(' ', 'T') + 'Z');
          document.getElementById('localTime').textContent = dt.toLocaleString();

          // JSON block
          document.getElementById('rawjson').textContent = JSON.stringify(data, null, 2);

          // Env (with change highlights)
          const tempEl = document.getElementById('tempF');
          tempEl.textContent = fmt(data.tempF);
          upDownFlash(tempEl, "tempF", data.tempF);

          const distEl = document.getElementById('distanceInches');
          distEl.textContent = fmt(data.distanceInches);
          upDownFlash(distEl, "distanceInches", data.distanceInches);

          const camBadge = document.getElementById('cameraBadge');
          setBadge(camBadge, (data.camera==="running"?"ok":"idle"), (data.camera==="running"?"Running":"Idle"));

          // Wi-Fi
          const ssid = (data.wifi && data.wifi.ssid) ? data.wifi.ssid : null;
          document.getElementById('ssid').textContent = ssid || "Not connected";
          document.getElementById('freq').textContent = fmt(data.wifi ? data.wifi.freq_mhz : null);
          const rssi = data.wifi ? data.wifi.signal_dbm : null;
          document.getElementById('rssiBars').innerHTML = rssiBarsHTML(rssi);
          upDownFlash(document.getElementById('rssiBars'), "rssi", rssi);
          const wifiStatus = document.getElementById('wifiStatus');
          setBadge(wifiStatus, ssid ? "ok" : "warn", ssid ? "Connected" : "Not connected");

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
          if (isFinite(data.system.cpu_temp_c)) {{
            if (data.system.cpu_temp_c >= {CPU_TEMP_CRIT_C}) {{ cpuLv="crit"; cpuTx="Hot"; }}
            else if (data.system.cpu_temp_c >= {CPU_TEMP_WARN_C}) {{ cpuLv="warn"; cpuTx="Warm"; }}
            else {{ cpuLv="ok"; cpuTx="Cool"; }}
          }}
          setBadge(cpuB, cpuLv, cpuTx);

          const cpuUtilEl = document.getElementById('cpuUtil');
          cpuUtilEl.textContent = (data.system.cpu_util_pct == null) ? "(n/a)" : Number(data.system.cpu_util_pct).toFixed(1);
          upDownFlash(cpuUtilEl, "cpuUtil", data.system.cpu_util_pct);

          // Uptime in days hours minutes
          document.getElementById('uptime').textContent = humanUptimeDHMS(data.system.uptime_seconds);
          const bootDt = new Date(String(data.system.boot_time_utc).replace(' ', 'T') + 'Z');
          document.getElementById('bootLocal').textContent = bootDt.toLocaleString();

          // Disk
          const d = data.system.disk;
          document.getElementById('diskPct').textContent = fmt(d.percent);
          document.getElementById('diskSizes').textContent = `${{bytes(d.used)}} / ${{bytes(d.total)}}`;

          // Memory: total/used/free + percent
          const m = data.system.mem;
          document.getElementById('memPct').textContent = fmt(m.percent);
          document.getElementById('memTotal').textContent = bytes(m.total||0);
          document.getElementById('memUsed').textContent  = bytes(m.used||0);
          document.getElementById('memFree').textContent  = bytes(m.free||0);

          // refresh snapshot (cache-bust)
          const th = document.getElementById('thumb');
          if (th && th.style.display!=="none") {{
            th.src = "/snapshot?cb=" + Date.now();
          }}

          lastUpdateEpoch = Date.now();
          tickAgo();
        }}

        // Seed with server values
        try {{
          const seed = JSON.parse(document.getElementById('seed').textContent);
          render(seed);
        }} catch (_e) {{}}

        // SSE hookup (with fallback to polling)
        let es = null;
        function connectSSE() {{
          if (!window.EventSource) {{ document.getElementById('connDot').className = "dot"; pollFallback(); return; }}
          es = new EventSource('/health.sse');
          const dot = document.getElementById('connDot');
          es.onopen = () => {{ dot.className="dot ok"; }};
          es.onerror = () => {{
            dot.className="dot err";
            try {{ es.close(); }} catch(_e) {{}}
            setTimeout(connectSSE, 3000);
          }};
          es.addEventListener('health', (e) => {{
            const data = JSON.parse(e.data);
            render(data);
            dot.className="dot ok";
          }});
        }}
        async function pollFallback() {{
          const dot = document.getElementById('connDot');
          async function once() {{
            try {{
              const r = await fetch('/health.json', {{cache:'no-store'}});
              const data = await r.json();
              render(data); dot.className="dot ok";
            }} catch(_e) {{ dot.className="dot err"; }}
          }}
          once(); setInterval(once, 5000);
        }}
        connectSSE();
      </script>
    """
    return render_page("Keuka Sensor – Health", body, extra_head)

# JSON endpoint for programmatic access or fallback
@app.route('/health.json')
def health_json():
    return build_health_payload()

# Server-Sent Events endpoint (push updates ~5s)
@app.route('/health.sse')
def health_sse():
    def stream():
        # send initial quickly, then every 5s
        yield f"event: health\ndata: {json.dumps(build_health_payload())}\n\n"
        last_ping = time.time()
        while True:
            time.sleep(5)
            yield f"event: health\ndata: {json.dumps(build_health_payload())}\n\n"
            # keepalive comment every 15s (some proxies)
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

# ---------------- Admin ----------------
def _admin_shell(title, inner_html):
    return render_page(title, f"""
      <h1 style="margin-top:.2rem">{title}</h1>
      <div class="card">{inner_html}</div>
    """)

@app.route('/admin', methods=['GET'])
@app.route('/admin/<action>', methods=['POST'])
def admin(action=None):
    if not basic_auth_ok(request):
        return Response('Auth required',401,{"WWW-Authenticate":'Basic realm="KeukaSensor"'})
    if request.method=='POST':
        if action=='restart':
            subprocess.Popen(['sudo','/usr/bin/systemctl','restart','keuka-sensor.service'])
            return redirect(url_for('admin'))
        if action=='reboot':
            subprocess.Popen(['sudo','/usr/sbin/reboot'])
            return 'Rebooting...',202
        if action=='update':
            script=str(APP_DIR/'update.sh')
            if os.path.exists(script):
                subprocess.Popen(['bash',script], cwd=str(APP_DIR))
                return redirect(url_for('admin'))
            return 'No update.sh found',404
    ip=subprocess.getoutput("hostname -I | awk '{print $1}'")
    host=subprocess.getoutput('hostname')
    st=wifi_status()
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

# ---------------- Admin: Wi-Fi scan/connect ----------------
@app.route('/admin/wifi')
def admin_wifi():
    if not basic_auth_ok(request):
        return Response('Auth required',401,{"WWW-Authenticate":'Basic realm="KeukaSensor"'})
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

@app.route('/admin/wifi/scan')
def wifi_scan_api():
    if not basic_auth_ok(request):
        return Response('Auth required',401,{"WWW-Authenticate":'Basic realm="KeukaSensor"'})
    return Response(json.dumps(wifi_scan()), mimetype='application/json')

@app.route('/admin/wifi/connect', methods=['POST'])
def wifi_connect_api():
    if not basic_auth_ok(request):
        return Response('Auth required',401,{"WWW-Authenticate":'Basic realm="KeukaSensor"'})
    ssid=request.form.get('ssid','').strip(); psk=request.form.get('psk','').strip()
    if not ssid or not psk: return 'Missing ssid/psk', 400
    wifi_connect(ssid, psk)
    time.sleep(1)
    return redirect(url_for('admin'))

# ---------------- Admin: Network (DHCP/Static + info) ----------------
@app.route('/admin/network', methods=['GET'])
def network_page():
    if not basic_auth_ok(request):
        return Response('Auth required',401,{"WWW-Authenticate":'Basic realm="KeukaSensor"'})
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

@app.route('/admin/network/apply', methods=['POST'])
def network_apply():
    if not basic_auth_ok(request):
        return Response('Auth required',401,{"WWW-Authenticate":'Basic realm="KeukaSensor"'})
    mode = request.form.get('mode','dhcp')
    ip_cidr = request.form.get('ip','').strip()
    gw = request.form.get('gw','').strip()
    dns = request.form.get('dns','').strip()
    ok,msg = apply_network(mode, ip_cidr, gw, dns)
    if not ok: return f"Error: {msg}", 400
    return redirect(url_for('network_page'))

# ---------------- Admin: DuckDNS ----------------
def duckdns_read()->dict:
    data = {"token":"","domains":"","enabled": False, "last":""}
    if DUCKDNS_CONF.exists():
        for ln in DUCKDNS_CONF.read_text(encoding="utf-8").splitlines():
            if ln.startswith("token="): data["token"]=ln.split("=",1)[1].strip()
            if ln.startswith("domains="): data["domains"]=ln.split("=",1)[1].strip()
    # enabled?
    code,_ = sh(["systemctl","is-enabled","duckdns-update.timer"])
    data["enabled"] = (code==0)
    if DUCKDNS_LAST.exists():
        data["last"] = DUCKDNS_LAST.read_text(encoding="utf-8")[-200:]
    return data

def duckdns_write(token:str, domains:str)->None:
    txt = f"token={token.strip()}\n" + f"domains={domains.strip()}\n"
    DUCKDNS_CONF.write_text(txt, encoding="utf-8")
    sh(["sudo","/bin/chmod","600", str(DUCKDNS_CONF)])

@app.route('/admin/duckdns', methods=['GET'])
def duckdns_page():
    if not basic_auth_ok(request):
        return Response('Auth required',401,{"WWW-Authenticate":'Basic realm="KeukaSensor"'})
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

@app.route('/admin/duckdns/save', methods=['POST'])
def duckdns_save():
    if not basic_auth_ok(request):
        return Response('Auth required',401,{"WWW-Authenticate":'Basic realm="KeukaSensor"'})
    duckdns_write(request.form.get('token',''), request.form.get('domains',''))
    return redirect(url_for('duckdns_page'))

@app.route('/admin/duckdns/update')
def duckdns_update_now():
    if not basic_auth_ok(request):
        return Response('Auth required',401,{"WWW-Authenticate":'Basic realm="KeukaSensor"'})
    sh(["sudo","/usr/bin/systemctl","start","duckdns-update.service"])
    time.sleep(0.5)
    return redirect(url_for('duckdns_page'))

@app.route('/admin/duckdns/enable')
def duckdns_enable():
    if not basic_auth_ok(request):
        return Response('Auth required',401,{"WWW-Authenticate":'Basic realm="KeukaSensor"'})
    sh(["sudo","/usr/bin/systemctl","enable","--now","duckdns-update.timer"])
    return redirect(url_for('duckdns_page'))

@app.route('/admin/duckdns/disable')
def duckdns_disable():
    if not basic_auth_ok(request):
        return Response('Auth required',401,{"WWW-Authenticate":'Basic realm="KeukaSensor"'})
    sh(["sudo","/usr/bin/systemctl","disable","--now","duckdns-update.timer"])
    return redirect(url_for('duckdns_page'))

# ---------------- Main ----------------
if __name__ == '__main__':
    # Always run on port 5000 for development/testing
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
