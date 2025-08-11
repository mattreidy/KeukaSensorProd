#!/usr/bin/env python3
import os
import sys
import time
import json
import threading
import subprocess
from datetime import datetime
from pathlib import Path
from flask import Flask, Response, request, abort, redirect, url_for, make_response

# Camera: OpenCV (optional)
try:
    import cv2  # type: ignore
except Exception:
    cv2 = None

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

# ---------------- Flask -----------------
app = Flask(__name__)

# ---------------- Utils -----------------
def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def basic_auth_ok(req) -> bool:
    a = req.authorization
    return bool(a and a.username == ADMIN_USER and a.password == ADMIN_PASS)

def sh(cmd):
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        return 0, out
    except subprocess.CalledProcessError as e:
        return e.returncode, e.output

# ---- Camera worker ----
class Camera:
    def __init__(self, index=0, w=640, h=480):
        self.index = index; self.w=w; self.h=h
        self.cap=None; self.lock=threading.Lock(); self.frame=None; self.running=False
        self.thread = threading.Thread(target=self._worker, daemon=True)
    def start(self):
        if cv2 is None: return
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

# ---- Ultrasonic (JSN-SR04T) ----
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

# ---- DS18B20 ----
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

# ---- Wi-Fi helpers (wlan1 STA) ----
def wifi_status():
    code,out=sh(["iw","dev",WLAN_STA_IFACE,"link"])
    ssid=None; rssi=None; freq=None
    for ln in out.splitlines():
        ln=ln.strip()
        if ln.startswith('SSID:'): ssid=ln.split(':',1)[1].strip()
        elif ln.startswith('signal:'):
            try: rssi=int(ln.split()[1])
            except: pass
        elif ln.startswith('freq:'):
            try: freq=int(ln.split()[1])
            except: pass
    return {"iface":WLAN_STA_IFACE, "ssid":ssid, "signal_dbm":rssi, "freq_mhz":freq}

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
    # dedupe SSIDs, keep best RSSI
    best={}
    for n in nets:
        k=n.get('ssid'); 
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
    # Remove any existing block for this SSID (simple parser)
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
    # Append new block
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
    html = (
        "<!doctype html><html><head><meta charset='utf-8'><title>Webcam</title></head>"
        "<body style='margin:0;background:#000;display:flex;align-items:center;justify-content:center;'>"
        "<img src='/stream' style='max-width:100%;height:auto;'>"
        "</body></html>"
    )
    return html

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

@app.route('/health')
def health():
    tF=read_temp_fahrenheit(); dIn=median_distance_inches()
    st=wifi_status()
    return {
        "time": now(),
        "tempF": None if (tF!=tF) else round(tF,2),
        "distanceInches": None if (dIn!=dIn) else round(dIn,2),
        "camera": "running" if camera.running else "idle",
        "wifi": st,
        "app": "keuka-sensor",
        "version": "1.3.0"
    }

@app.route('/admin', methods=['GET'])
@app.route('/admin/<action>', methods=['POST'])
def admin(action=None):
    if not basic_auth_ok(request):
        return Response('Auth required',401,{"WWW-Authenticate":'Basic realm="KeukaSensor"'})
    if request.method=='POST':
        if action=='restart':
            subprocess.Popen(['sudo','systemctl','restart','keuka-sensor.service'])
            return redirect(url_for('admin'))
        if action=='reboot':
            subprocess.Popen(['sudo','reboot'])
            return 'Rebooting...',202
        if action=='update':
            script=str(APP_DIR/'update.sh')
            if os.path.exists(script):
                subprocess.Popen(['bash',script], cwd=str(APP_DIR))
                return redirect(url_for('admin'))
            return 'No update.sh found',404
    # info page
    ip=subprocess.getoutput("hostname -I | awk '{print $1}'")
    host=subprocess.getoutput('hostname')
    st=wifi_status()
    html = f"""
    <!doctype html><html><head><meta charset='utf-8'><title>Keuka Sensor Admin</title>
    <style>body{{font-family:system-ui,Segoe UI,Arial;margin:2rem}}button{{padding:.6rem 1rem;margin-right:.6rem}}.card{{border:1px solid #ddd;border-radius:10px;padding:1rem;margin:.5rem 0}}</style></head>
    <body>
      <h1>Keuka Sensor – Admin</h1>
      <div class='card'><b>Time:</b> {now()}<br><b>Host:</b> {host}<br><b>IP:</b> {ip}<br>
        <b>Wi-Fi (STA {WLAN_STA_IFACE}):</b> SSID {st.get('ssid') or '(n/a)'} | RSSI {st.get('signal_dbm') or '(n/a)'} dBm | Freq {st.get('freq_mhz') or '(n/a)'} MHz
      </div>
      <div class='card'>
        <form method='post' action='/admin/update' style='display:inline'><button>Update Code</button></form>
        <form method='post' action='/admin/restart' style='display:inline'><button>Restart Service</button></form>
        <form method='post' action='/admin/reboot' style='display:inline' onsubmit="return confirm('Reboot now?')"><button>Reboot Pi</button></form>
        <a href='/admin/wifi' style='margin-left:1rem'>Wi-Fi Setup</a>
      </div>
    </body></html>
    """
    return html

@app.route('/admin/wifi')
def admin_wifi():
    if not basic_auth_ok(request):
        return Response('Auth required',401,{"WWW-Authenticate":'Basic realm="KeukaSensor"'})
    html = f"""
    <!doctype html><html><head><meta charset='utf-8'><title>Wi-Fi Setup</title>
    <style>body{{font-family:system-ui,Segoe UI,Arial;margin:2rem}} table{{border-collapse:collapse}} td,th{{border:1px solid #ddd;padding:.4rem .6rem}}</style>
    <script>
      async function doScan(){{
        const r = await fetch('/admin/wifi/scan');
        const data = await r.json();
        const tb = document.getElementById('nets'); tb.innerHTML='';
        data.forEach(n=>{{
          const tr=document.createElement('tr');
          tr.innerHTML=`<td>${{n.ssid||'(hidden)'}} </td><td>${{n.signal_dbm||''}}</td><td>${{n.freq_mhz||''}}</td>
                         <td><button onclick=\\"sel('${{n.ssid||''}}')\\">Select</button></td>`;
          tb.appendChild(tr);
        }});
      }}
      function sel(ssid){{ document.getElementById('ssid').value = ssid; window.scrollTo(0,document.body.scrollHeight); }}
    </script>
    </head><body>
      <h1>Wi-Fi Setup (STA {WLAN_STA_IFACE})</h1>
      <button onclick='doScan()'>Scan Networks</button>
      <table style='margin-top:1rem'><thead><tr><th>SSID</th><th>RSSI (dBm)</th><th>Freq (MHz)</th><th></th></tr></thead><tbody id='nets'></tbody></table>
      <h2>Add/Connect</h2>
      <form method='post' action='/admin/wifi/connect'>
        <label>SSID <input id='ssid' name='ssid' required></label>
        <label style='margin-left:1rem'>Password <input name='psk' type='password' required></label>
        <button type='submit' style='margin-left:1rem'>Connect</button>
      </form>
      <p>Tip: If scan shows nothing (adapter missing), you can still enter SSID and password manually.</p>
    </body></html>
    """
    return html

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

if __name__=='__main__':
    app.run(host='0.0.0.0', port=80, debug=False, threaded=True)
