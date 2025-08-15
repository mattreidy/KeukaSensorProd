# Production Design: Raspberry Pi 3A+ Water Temp & Level Node (v1.5.0, no-code edition)

This document summarizes the complete build **without embedding file contents**. It references file names and paths that already exist in your GitHub repo. Use it as a handoff to builders and reviewers.

---

## 0) Endpoints (compatibility preserved)

- `GET /` → plain text: `waterTempF,medianDistanceInches` (two decimals). (Same as prototype.)
- `GET /webcam` → simple page that embeds the MJPEG stream.
- `GET /stream` → MJPEG stream (from UVC webcam).
- `GET /health` → JSON with readings + status.
- `GET /admin` (Basic Auth) → Update / Restart / Reboot; links to Wi‑Fi, Network, DuckDNS.
- `GET /admin/wifi` → scan SSIDs (STA `wlan1`) and connect.
- `GET /admin/network` → show live IPv4 & switch **DHCP ⇄ Static** for `wlan1`.
- `GET /admin/duckdns` → configure **DuckDNS** and control the updater timer.

Default admin credentials (change in systemd env): **`admin/password`**.

---

## 1) High‑level overview

- Measures **water level** (down‑looking ultrasonic) and **water temperature** (DS18B20 at lake bottom).
- Streams a **USB UVC webcam** for situational awareness.
- **Provisioning AP** for iPhone setup: onboard Wi‑Fi (**wlan0**) advertises `keukasensor` / `KeukaLake` at `192.168.50.1`.
- Field link uses a **USB 2.4 GHz Wi‑Fi adapter with external RP‑SMA antenna** on **wlan1** for range.
- Robustness: managed by **systemd** (auto‑restart), optional **Wi‑Fi watchdog**, median filtering/timeouts, no custom PCBs.

---

## 2) Consolidated Bill of Materials (BoM)

Estimated 2025  prices



### 📦 Bill of Materials (BOM)

| Qty | Item                                                                                                     | Price (USD) | Notes |
|-----|----------------------------------------------------------------------------------------------------------|-------------|-------|
| 1   | Raspberry Pi 3B+                                                                                         | $50         | Main controller board |
| 1   | HCDC RPi GPIO Status LED & Terminal Block Breakout Board HAT                                             | $20         | Simplifies wiring and provides GPIO status LEDs |
| 1   | Raspberry Pi Mini Camera Video Module (OV5647, 5 MP, 1080p)                                              | $10         | CSI ribbon connection for live video feed |
| 1   | AC600 Mbps Dual Band USB Wi-Fi Adapter (2.4/5 GHz) with Antenna                                          | $15         | Improved wireless connectivity |
| 1   | JSN-SR04T Waterproof Ultrasonic Sensor                                                                   | $10           | Distance measurement |
| 2   | Resistors (1 kΩ, 2 kΩ)                                                                                   | —           | Voltage divider for ultrasonic ECHO pin |
| 1   | DS18B20 Temperature Sensor                                                                               | $10           | Temperature measurement |
| 1   | Resistor (4.7 kΩ)                                                                                         | —           | Temperature |
| —   | Misc wiring and other components                                                                         | $15           |  |
| 1   | Zulkit Junction Box Waterproof Clear Cover 8.7"x6.7"x4.3"                                                | $30         |  |


**Typical build total:** **≈ $200**.

---

## 3) Mechanical layout & mounting

- Enclosure mounted **vertical**, clear lid **down** facing water.  
- Drill **~18 mm hole** in the lid for the **JSN‑SR04T transducer head** (must be exposed; seal with O‑ring/silicone). Controller PCB stays inside.
- Place **USB webcam** behind the clear lid; add a small black foam shroud to reduce reflections.
- Side penetrations: **PG7** (DS18B20) and **PG9** (power). Drill **6.5–7 mm** for the **RP‑SMA bulkhead**.
- Target transducer height **36–60 in** above typical water level.
- Mounting: **L‑bracket + stainless clamps** to dock/post. Slight tilt if the camera sees the ultrasonic head.

---

## 4) Electrical Wiring (Summary)

This system uses both a **JSN-SR04T ultrasonic sensor** (with resistor divider) and a **DS18B20 temperature sensor**.  
Follow these connections to ensure reliable operation:

- **JSN-SR04T** (waterproof ultrasonic):
  - **TRIG** → GPIO 23
  - **ECHO** → GPIO 24 via **1 kΩ + 2 kΩ** resistor divider (5 V → 3.3 V)
  - **VCC** → 5 V
  - **GND** → Ground
  - *(Optional)* **0.1 µF** ceramic capacitor across VCC and GND at the sensor for noise suppression

- **DS18B20** (temperature):
  - **DATA** → GPIO 4
  - **VCC** → 3.3 V
  - **GND** → Ground
  - **4.7 kΩ** pull-up resistor between DATA and 3.3 V

- **USB Wi-Fi** adapter → any free Raspberry Pi USB port

---

## 5) Software & OS preparation

1. Flash **Raspberry Pi OS Lite (64‑bit)**; set hostname and enable SSH in Imager if desired.  
2. Enable **1‑Wire** by adding the overlay to `/boot/firmware/config.txt` (see repo instructions).  
3. Set Wi‑Fi country (e.g., `US`) using `raspi-config` or your provisioning script.  
4. Create the project dir and Python venv at `/opt/keuka-sensor/` and install Python deps listed in the repo README.  
5. Copy/sync the following from GitHub into place:
   - **Application**: `/opt/keuka-sensor/keuka_sensor.py`
   - **Updater**: `/opt/keuka-sensor/update.sh`
   - **Wi‑Fi watchdog** (optional): `/opt/keuka-sensor/wifi-watchdog.sh`
   - **DuckDNS updater**: `/opt/keuka-sensor/duckdns_update.sh`
   - **Systemd units**: see Section 8 for installation paths
   - **AP configs** (hostapd/dnsmasq) and **wpa_supplicant** templates are referenced below.

> **No file contents are included here**—use the versions already in your GitHub repo unchanged.

---

## 6) Provisioning AP (onboard wlan0)

- **SSID**: `keukasensor`  
- **Password**: `KeukaLake`  
- **AP address**: `192.168.50.1`  
- **Config files and exact content**: already in your repo. Place them at:
  - `/etc/hostapd/hostapd.conf`
  - `/etc/dnsmasq.d/keukasensor.conf`
  - AP static IP lines in `/etc/dhcpcd.conf` for `wlan0`
- Enable services per your repo’s setup instructions. From iPhone: join AP → visit `http://192.168.50.1/admin` → Wi‑Fi setup for field SSID on `wlan1`.

---

## 7) Field Wi‑Fi (USB adapter on wlan1)

- Base config file exists in your repo: `/etc/wpa_supplicant/wpa_supplicant-wlan1.conf` (empty template / country, update_config, ctrl_interface).  
- The **Admin → Wi‑Fi** page appends a network block for the selected SSID and triggers reconfigure.  
- `wlan1` operates at **2.4 GHz** with the **external RP‑SMA antenna** for best range.

---

## 8) Services & runtime (systemd)

Install the unit files from your repo to:

- **App**: `/etc/systemd/system/keuka-sensor.service`  
  - Exposes **env** vars: `KS_ADMIN_USER`, `KS_ADMIN_PASS`, `KS_STA_IFACE=wlan1`, `KS_AP_IFACE=wlan0`  
  - Exec: Python app at `/opt/keuka-sensor/keuka_sensor.py` (port 80)

- **Wi‑Fi watchdog (optional)**: `/etc/systemd/system/wifi-watchdog.service`

- **DuckDNS updater**:  
  - `/etc/systemd/system/duckdns-update.service`  
  - `/etc/systemd/system/duckdns-update.timer` (5‑minute interval)

Reload and enable per your repo’s instructions.  
Create a sudoers drop‑in (filename and content already in your repo) at `/etc/sudoers.d/keuka-sensor` to allow only the specific commands the app needs.

---

## 9) Static vs DHCP (Admin → Network)

- The app manages a **delimited block** in `/etc/dhcpcd.conf` to switch `wlan1` between **DHCP** and **Static** IPv4.  
- Block markers (do not edit manually):  
  - `# KS-STATIC-BEGIN` … `# KS-STATIC-END`  
- **Admin → Network** shows live info (SSID, RSSI, IP/CIDR, Gateway, DNS) and applies changes.  
- If you change the IP while connected via STA, reconnect using the AP at `http://192.168.50.1/admin`.

---

## 10) DuckDNS (dynamic DNS)

- Create your **DuckDNS account and subdomain(s)** on duckdns.org; copy the **token**.  
- In **Admin → DuckDNS**, paste **token** and **domains** (comma‑separated).  
- Enable the systemd **timer** there or via CLI. Logs are written to `/opt/keuka-sensor/duckdns_last.txt`.  
- Updater script file path: `/opt/keuka-sensor/duckdns_update.sh` (already in your repo).

---

## 11) Testing checklist

1. `/` returns `tempF,distanceInches` (two decimals).  
2. `/webcam` renders live video; `/stream` is MJPEG.  
3. `/health` shows readings and network details.  
4. **Admin → Wi‑Fi**: scan and connect on `wlan1`.  
5. **Admin → Network**: flip **DHCP ⇄ Static** and verify IP/gateway/DNS.  
6. **Admin → DuckDNS**: Save token/domains → Enable timer → Update Now → confirm log.  

---

## 12) Maintenance & operations

- Change admin creds via `KS_ADMIN_USER` / `KS_ADMIN_PASS` in the app service unit.  
- Use **Admin → Update Code** (runs `/opt/keuka-sensor/update.sh`) for repo‑based deployments.  
- Logs: `journalctl -u keuka-sensor -f` and `journalctl -u duckdns-update -f`.  
- Consider **high‑endurance microSD** for harsh power conditions.

---

## 13) Security notes

- Change default admin password immediately.  
- Leave the AP enabled for field recovery, or disable it post‑provisioning if your ops workflow allows.  
- Limit physical access; seal glands and lid; add drip loops.

---

## 14) File/Path reference (authoritative list)

- **/opt/keuka-sensor/keuka_sensor.py** — Flask app (HTTP endpoints, admin UI, sensor I/O)  
- **/opt/keuka-sensor/update.sh** — self‑update helper (invoked from admin)  
- **/opt/keuka-sensor/wifi-watchdog.sh** — optional connectivity watchdog  
- **/opt/keuka-sensor/duckdns_update.sh** — DuckDNS updater (writes `duckdns_last.txt`)  
- **/opt/keuka-sensor/duckdns.conf** — created by the UI (token/domains)
- **/etc/systemd/system/keuka-sensor.service** — app service (env for creds/ifaces)  
- **/etc/systemd/system/wifi-watchdog.service** — optional watchdog service  
- **/etc/systemd/system/duckdns-update.service** — one‑shot updater  
- **/etc/systemd/system/duckdns-update.timer** — periodic trigger (5 min)  
- **/etc/hostapd/hostapd.conf** — provisioning AP config (`wlan0`)  
- **/etc/dnsmasq.d/keukasensor.conf** — AP DHCP/DNS  
- **/etc/dhcpcd.conf** — AP static IP for `wlan0` + **KS static block** for `wlan1`  
- **/etc/wpa_supplicant/wpa_supplicant-wlan1.conf** — station base template  
- **/etc/sudoers.d/keuka-sensor** — least‑privilege sudoers drop‑in (commands listed in repo)

---

## 15) Troubleshooting (quick hits)

- **Webcam not visible**: verify UVC camera enumerates (`lsusb`) and the app’s camera index; reduce resolution in app env if CPU‑bound.  
- **No temp**: confirm 1‑Wire overlay and that a `28‑*` device appears under `/sys/bus/w1/devices/`.  
- **No ultrasonic**: double‑check TRIG/ECHO pins and the **1k/2k divider** into GPIO24.  
- **Wi‑Fi flaky**: verify external antenna seated; use channel 1/6/11; enable the watchdog service.  
- **DuckDNS not updating**: check token/domains in UI and view `/opt/keuka-sensor/duckdns_last.txt`.

---

## 16) Budget roll‑up

- **BoM total (typical):** ≈ **$188**  
- **Range:** **$155–$199** depending on Pi, enclosure, and antenna sourcing.

---

## 17) Change log

- **v1.5.0** — Added **Admin → Network** (DHCP/Static + live network info) and **Admin → DuckDNS** with systemd timer; Wi‑Fi provisioning AP retained; external 2.4 GHz STA plan retained. (No code embedded in this edition.)
