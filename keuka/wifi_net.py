# wifi_net.py
# -----------------------------------------------------------------------------
# Wi-Fi and IPv4 helpers
#  - STA = WLAN_STA_IFACE (USB, e.g., wlan1), AP = WLAN_AP_IFACE (builtin, wlan0)
#  - Uses wpa_cli (NO sudo) via control socket; requires user "pi" in group netdev
# -----------------------------------------------------------------------------

from __future__ import annotations
import os
import re
import time
import ipaddress
from pathlib import Path
from typing import Optional, List

from config import (
    WLAN_STA_IFACE, WLAN_AP_IFACE, WPA_SUP_CONF,
    DHCPCD_CONF, DHCPCD_MARK_BEGIN, DHCPCD_MARK_END,
)
from utils import sh, read_text, write_text_atomic

# ---- helpers ---------------------------------------------------------------
def ap_ssid_current(prefix: str = "KeukaSensor") -> str:
    """
    1) Prefer /run/keuka-ap-ssid written by ks-set-ap-ssid.sh
    2) Else derive <prefix>_<last4hex> from AP MAC
    3) Fallback to "<prefix>_####"
    """
    try:
        txt = (Path("/run/keuka-ap-ssid").read_text(errors="ignore") or "").strip()
        if txt:
            return txt
    except Exception:
        pass
    try:
        mac = (Path(f"/sys/class/net/{WLAN_AP_IFACE}/address")
               .read_text(errors="ignore").strip().replace(":", ""))
        if mac:
            return f"{prefix}_{mac[-4:].upper()}"
    except Exception:
        pass
    return f"{prefix}_####"


def _wpacli(*args: str) -> tuple[int, str]:
    """Run wpa_cli against the STA iface WITHOUT sudo (uses control socket)."""
    # Prefer absolute path if present; fall back to plain "wpa_cli"
    for exe in ("/sbin/wpa_cli", "/usr/sbin/wpa_cli", "wpa_cli"):
        if os.path.exists(exe) or exe == "wpa_cli":
            return sh([exe, "-i", WLAN_STA_IFACE, *args])
    return (1, "wpa_cli not found")

def _ensure_iface_up(iface: str) -> None:
    sh(["/sbin/ip", "link", "set", iface, "up"])
    # Disable powersave on STA; helps with some Realtek dongles
    sh(["/sbin/iw", "dev", iface, "set", "power_save", "off"])

def ensure_wpa_running() -> bool:
    """
    Ensure /etc/wpa_supplicant/wpa_supplicant-<STA>.conf exists
    and wpa_supplicant@<STA> is enabled & running.
    """
    conf_path = Path(WPA_SUP_CONF or f"/etc/wpa_supplicant/wpa_supplicant-{WLAN_STA_IFACE}.conf")
    if not conf_path.exists():
        # Minimal safe config allowing nonroot control via netdev group
        text = (
            "ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\n"
            "update_config=1\n"
            "country=US\n"
        )
        ok = write_text_atomic(conf_path, text, sudo_mv=True)
        if not ok:
            return False
        # Permissions that let group netdev access the control socket
        sh(["sudo", "chown", "root:netdev", str(conf_path)])
        sh(["sudo", "chmod", "640", str(conf_path)])

    _ensure_iface_up(WLAN_STA_IFACE)
    sh(["sudo", "/bin/systemctl", "enable", f"wpa_supplicant@{WLAN_STA_IFACE}.service"])
    code, _ = sh(["sudo", "/bin/systemctl", "start", f"wpa_supplicant@{WLAN_STA_IFACE}.service"])
    return code == 0

def wait_for_ip(iface: str, timeout_s: int = 45) -> Optional[str]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        ip = ip_addr4(iface)
        if ip:
            return ip
        time.sleep(1)
    return None

# ---- status / scan / connect ----------------------------------------------

def wifi_status_sta():
    """Return link info for station iface using `iw dev <sta> link`."""
    code, out = sh(["/sbin/iw", "dev", WLAN_STA_IFACE, "link"])
    ssid = rssi = freq = bssid = bitrate = None
    if code == 0:
        for ln in out.splitlines():
            s = ln.strip()
            if s.startswith("SSID:"):
                ssid = s.split(":", 1)[1].strip()
            elif s.startswith("signal:"):
                try: rssi = int(s.split()[1])
                except: pass
            elif s.startswith("freq:"):
                try: freq = int(s.split()[1])
                except: pass
            elif s.startswith("tx bitrate:"):
                bitrate = s.split(":", 1)[1].strip()
            elif s.startswith("Connected to"):
                parts = s.split()
                if len(parts) >= 3:
                    bssid = parts[2]
    return {
        "iface": WLAN_STA_IFACE,
        "ssid": ssid,
        "bssid": bssid,
        "signal_dbm": rssi,
        "freq_mhz": freq,
        "tx_bitrate": bitrate,
    }

def wifi_scan() -> List[dict]:
    """
    Scan via wpa_supplicant (no sudo):
      wpa_cli scan ; wpa_cli scan_results
    """
    if not ensure_wpa_running():
        return []
    _ensure_iface_up(WLAN_STA_IFACE)
    _wpacli("scan")
    # Give the scan a moment
    time.sleep(2.0)
    code, out = _wpacli("scan_results")
    if code != 0:
        return []
    # Parse: bssid / freq / rssi / flags / ssid
    nets = []
    lines = out.strip().splitlines()
    for ln in lines[1:]:
        parts = ln.split("\t")
        if len(parts) < 5:
            continue
        bssid, freq, rssi, flags, ssid = parts[0], parts[1], parts[2], parts[3], parts[4]
        try:
            nets.append({
                "ssid": ssid,
                "signal_dbm": int(rssi),
                "freq_mhz": int(freq),
            })
        except:
            pass
    # keep strongest per SSID
    best = {}
    for n in nets:
        k = n.get("ssid")
        if not k: continue
        if k not in best or (n.get("signal_dbm", -999) > best[k].get("signal_dbm", -999)):
            best[k] = n
    return sorted(best.values(), key=lambda x: x.get("signal_dbm", -999), reverse=True)

def wifi_connect(ssid: str, psk: str) -> tuple[bool, str, dict]:
    """
    Create/enable network using wpa_cli (NO sudo) so config persists via save_config.
    Steps:
      - ensure wpa_supplicant@<STA> running + iface UP
      - add_network / set ssid/psk (or open) / priority
      - enable + select + save_config + reconfigure
      - force DHCP for first connect, wait for IP
    """
    if not ssid:
        return False, "Missing SSID", {}
    if not ensure_wpa_running():
        return False, "wpa_supplicant not running on STA", {}

    _ensure_iface_up(WLAN_STA_IFACE)

    code, out = _wpacli("add_network")
    net_id = out.strip()
    if code != 0 or not net_id.isdigit():
        return False, f"wpa_cli add_network failed: {out.strip()}", {}

    # Quote values so wpa_supplicant accepts spaces/specials
    cmds = [
        ("set_network", net_id, "ssid", f'"{ssid}"'),
        (("set_network", net_id, "psk", f'"{psk}"') if psk
         else ("set_network", net_id, "key_mgmt", "NONE")),
        ("set_network", net_id, "priority", "10"),
    ]
    for c in cmds:
        c0, o0 = _wpacli(*c)
        if c0 != 0 or "OK" not in o0:
            return False, f"wpa_cli set_network failed: {' '.join(c)} -> {o0.strip()}", {}

    for c in (("enable_network", net_id),
              ("select_network", net_id),
              ("save_config",),
              ("reconfigure",)):
        c0, o0 = _wpacli(*c)
        if c0 != 0:
            return False, f"wpa_cli command failed: {' '.join(c)} -> {o0.strip()}", {}

    # Make sure STA uses DHCP for the first connect
    ok, msg = _apply_dhcpcd("dhcp")
    if not ok:
        return False, f"Connected at Wi-Fi layer, but DHCP apply failed: {msg}", {}

    ip = wait_for_ip(WLAN_STA_IFACE, timeout_s=45)
    st = wifi_status_sta()
    if not ip:
        return False, "Associated, but no DHCP lease yet", {"status": st}

    return True, "Connected", {"ip": ip, "status": st}

# ---- IPv4 info & config ----------------------------------------------------

def ip_addr4(iface: str) -> Optional[str]:
    code, out = sh(["/sbin/ip", "-4", "-o", "addr", "show", "dev", iface])
    if code != 0:
        return None
    m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+/\d+)", out)
    return m.group(1) if m else None

def gw4(iface: str) -> Optional[str]:
    code, out = sh(["/sbin/ip", "route", "show", "default", "dev", iface])
    if code != 0:
        return None
    m = re.search(r"default via\s+(\d+\.\d+\.\d+\.\d+)", out)
    return m.group(1) if m else None

def dns_servers() -> list[str]:
    txt = read_text(Path("/etc/resolv.conf"))
    return re.findall(r"nameserver\s+(\d+\.\d+\.\d+\.\d+)", txt)

def dhcpcd_current_mode() -> dict:
    conf = read_text(DHCPCD_CONF)
    block = re.search(rf"{re.escape(DHCPCD_MARK_BEGIN)}.*?{re.escape(DHCPCD_MARK_END)}", conf, re.S)
    if not block:
        return {"mode": "dhcp"}
    text = block.group(0)
    ip = re.search(r"ip_address=([0-9./]+)", text)
    routers = re.search(r"routers=([0-9.]+)", text)
    dns = re.search(r"domain_name_servers=([0-9. ]+)", text)
    return {
        "mode": "static",
        "ip": ip.group(1) if ip else "",
        "router": routers.group(1) if routers else "",
        "dns": (dns.group(1).split() if dns else [])
    }

def dhcpcd_render(mode: str, ip_cidr: str = "", router: str = "", dns_list: list[str] | None = None) -> str:
    base = read_text(DHCPCD_CONF)
    base2 = re.sub(rf"{re.escape(DHCPCD_MARK_BEGIN)}.*?{re.escape(DHCPCD_MARK_END)}\n?", "", base, flags=re.S)
    if mode == "dhcp":
        return base2
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
    if not base2.endswith("\n"):
        base2 += "\n"
    return base2 + block

def _apply_dhcpcd(mode: str, ip_cidr: str = "", router: str = "", dns_list: list[str] | None = None) -> tuple[bool, str]:
    new_text = dhcpcd_render(mode, ip_cidr, router, dns_list or [])
    ok = write_text_atomic(DHCPCD_CONF, new_text, sudo_mv=True)
    if not ok:
        return False, "Failed to write /etc/dhcpcd.conf (sudo mv)"
    code, out = sh(["sudo", "/bin/systemctl", "restart", "dhcpcd"])
    if code != 0:
        return False, "Failed to restart dhcpcd: " + out
    return True, "Applied"

def apply_network(mode: str, ip_cidr: str = "", router: str = "", dns_csv: str = "") -> tuple[bool, str]:
    if mode not in ("dhcp", "static"):
        return False, "Invalid mode"
    if mode == "static":
        try:
            ipaddress.ip_interface(ip_cidr)
        except Exception:
            return False, "Invalid IP/CIDR"
        try:
            ipaddress.ip_address(router)
        except Exception:
            return False, f"Invalid address: {router}"
        dns_list = []
        for x in [s.strip() for s in dns_csv.split(",") if s.strip()]:
            try: ipaddress.ip_address(x); dns_list.append(x)
            except Exception: return False, f"Invalid address: {x}"
    else:
        dns_list = []
    ok, msg = _apply_dhcpcd(mode, ip_cidr, router, dns_list)
    if not ok:
        return False, msg
    _wpacli("reconfigure")
    time.sleep(1.0)
    return True, "Applied"

# Back-compat
def wifi_status():
    return wifi_status_sta()
