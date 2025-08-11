# Raspberry Pi 3A+ Water Temp & Level Node (v1.4)

This is a complete, copy-and-build design for a small, inexpensive, resilient water temperature + level station based on Raspberry Pi 3A+.  
It keeps your prototype’s exact HTTP output at `/`, adds a webcam stream, and includes a password-protected admin area with remote update/restart/reboot.  
It also adds a first-boot iPhone-friendly Wi-Fi provisioning AP (`keukasensor` / `KeukaLake`) and a long-range 2.4 GHz path using a USB Wi-Fi adapter with an external antenna.

---

## 1. High-level overview

### What it does
- Measures water level (ultrasonic down-looking sensor, mounted 3–5 ft above water).
- Measures water temperature (DS18B20 probe on the lake bottom).
- Serves plain text at `http://<ip>/` → `waterTempF,medianDistanceInches` (two decimals).
- Serves webcam page at `http://<ip>/webcam` (MJPEG stream at `/stream`).
- Admin panel at `http://<ip>/admin` (Basic Auth: `admin/password` initially — change it).

### Why it’s robust
- All services managed by `systemd` (auto-restart).
- Wi-Fi watchdog auto-recovers connectivity.
- Median filtering, timeouts, and error handling in sensors.
- No custom PCBs; all commodity parts.

### Footprint & mounting
- IP66/68 polycarbonate enclosure with clear lid, mounted vertically.
- Ultrasonic transducer passes through lid pointing straight down.
- Webcam looks through clear window.

---

## 2. Bill of Materials (BoM)
| # | Item | Notes | Qty | Est. Unit | Subtotal |
|---|------|-------|-----|-----------|----------|
| 1 | Raspberry Pi 3A+ | Full-size USB, onboard Wi-Fi | 1 | $45 | $45 |
| 2 | microSD 32 GB (A1) | OS & app | 1 | $7 | $7 |
| 3 | 5V 3A PSU + micro-USB cable | Outdoor-rated preferred | 1 | $10 | $10 |
| 4 | USB UVC webcam (720p) | MJPEG capable | 1 | $12 | $12 |
| 5 | JSN-SR04T-2.0/3.0 ultrasonic | Waterproof head + PCB | 1 | $9 | $9 |
| 6 | DS18B20 waterproof probe | 3-wire, 1-Wire, 3–10 m | 1 | $8 | $8 |
| ... | ... | ... | ... | ... | ... |

**Typical build total:** ≈ $188  
**Likely range:** $155 – $199

---

## 3. External Wi-Fi & 2.4 GHz range
- Use USB Wi-Fi adapter with external RP-SMA antenna.
- Keep onboard Wi-Fi for provisioning AP.
- Drill hole for RP-SMA bulkhead, mount antenna vertical & unobstructed.

```bash
sudo raspi-config nonint do_wifi_country US
```

---

## 4. Initial Wi-Fi setup (Provisioning AP)
On first boot:
- SSID: `keukasensor`
- Password: `KeukaLake`
- Static IP: `192.168.50.1`
- Visit `http://192.168.50.1/admin` → Wi-Fi Setup.

Install packages:
```bash
sudo apt update
sudo apt install -y hostapd dnsmasq
```

---

## 5. Mechanical layout & mounting
- Mount vertical with clear lid facing water.
- Ultrasonic head: 36–60 in above water.
- Webcam lens near clear lid, use foam shroud.

---

## 6. Electrical wiring
- JSN-SR04T: TRIG → GPIO 23, ECHO → voltage divider → GPIO 24, VCC 5V, GND GND.
- DS18B20: DATA → GPIO 4, VCC 3.3V, GND GND, plus 4.7k pull-up.

Voltage divider:
```
ECHO ---[ 1kΩ ]---+--- to GPIO24
                  |
                 [ 2kΩ ]
                  |
                 GND
```

---

## 7. Software & OS setup
- Flash Raspberry Pi OS Lite (64-bit).
- Enable 1-Wire in `/boot/firmware/config.txt`.
- Install Python deps:
```bash
pip install flask opencv-python w1thermsensor numpy
```

---

## 8. Application code
Full Python app: See [`keuka_sensor.py`](opt/keuka-sensor/keuka_sensor.py).

---

## 9. Wi-Fi watchdog
Script at `/opt/keuka-sensor/wifi-watchdog.sh`, systemd unit at `/etc/systemd/system/wifi-watchdog.service`.

---

## 10. App service unit
Systemd unit: `/etc/systemd/system/keuka-sensor.service`.

---

## 11. Update script
`/opt/keuka-sensor/update.sh` pulls latest code if repo present.

---

## 12. Testing checklist
- `curl http://<ip>/` → e.g., `77.79,31.11`.
- Webcam page loads.
- `/health` returns JSON.
- Admin panel actions work.
- AP provisioning works.

---

## 13. Assembly summary
1. Drill enclosure holes.
2. Mount Pi, sensors, webcam.
3. Wire voltage divider & DS18B20 pull-up.
4. Seal with silicone.
5. Flash OS, install software.
6. Strap enclosure to post.

---

## 14. Budget summary
- Typical: ≈ $188
- Range: $155 – $199

---

## 15. Maintenance
- Change admin creds in systemd unit → reload & restart.
- Update code via Admin panel.
- View logs:
```bash
journalctl -u keuka-sensor -f
```
