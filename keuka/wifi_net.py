# wifi_net.py
# -----------------------------------------------------------------------------
# Wi-Fi and IPv4 helpers focused on STA = WLAN_STA_IFACE (wlan1) and
# AP = WLAN_AP_IFACE (wlan0). Uses system tools: iw, wpa_cli, ip, systemctl.
# - wifi_status_sta / wifi_scan / wifi_connect
# - ip_addr4 / gw4 / dns_servers
# - dhcpcd_current_mode / dhcpcd_render / apply_network (static vs DHCP)
# - wait_for_ip, ensure_wpa_running
# -----------------------------------------------------------------------------

from __future__ import annotations
import re
import time
import ipaddress
from pathlib import Path
from typing import Optional

from config import (
    WLAN_STA_IFACE, WLAN_AP_IFACE, WPA_SUP_CONF,
    DHCPCD_CONF, DHCPCD_MARK_BEGIN, DHCPCD_MARK_END,
)
from utils import sh, read_text, write_text_atomic

# -------- Wi-Fi (STA) status/scan/connect --------

def wifi_status_sta():
    """Return link info for station iface using `iw dev <sta> link`."""
    code, out = sh(["iw", "dev", WLAN_STA_IFACE, "link"])
    ssid = rssi = freq = bssid = bitrate = None
    for ln in out.splitlines():
        s = ln.strip()
        if s.startswith('SSID:'):
            ssid = s.split(':', 1)[1].strip()
        elif s.startswith('signal:'):
            try:
                rssi = int(s.split()[1])
            except Exception:
                pass
        elif s.startswith('freq:'):
            try:
                freq = int(s.split()[1])
            except Exception:
                pass
        elif s.startswith('tx bitrate:'):
            bitrate = s.split(':', 1)[1].strip()
        elif s.startswith('Connected to'):
            try:
                bssid = s.split()[2]
            except Exception:
                pass
    return {
        "iface": WLAN_STA_IFACE,
        "ssid": ssid,
        "bssid": bssid,
        "signal_dbm": rssi,
        "freq_mhz": freq,
        "tx_bitrate": bitrate,
    }

def wifi_scan() -> list[dict]:
    """
    Scan visible Wi-Fi networks on STA iface and return best signal per SSID.
    Uses sudo because scan sometimes needs elevated caps on raspbian.
    """
    code, out = sh(["sudo", "/sbin/iw", "dev", WLAN_STA_IFACE, "scan", "-u"])
    if code != 0:
        return []
    nets = []
    cur = {}
    for ln in out.splitlines():
        s = ln.strip()
        if s.startswith('BSS '):
            if cur.get('ssid'):
                nets.append(cur)
            cur = {}
        elif s.startswith('SSID:'):
            cur['ssid'] = s.split(':', 1)[1].strip()
        elif s.startswith('signal:'):
            try:
                cur['signal_dbm'] = int(s.split()[1])
            except Exception:
                pass
        elif s.startswith('freq:'):
            try:
                cur['freq_mhz'] = int(s.split()[1])
            except Exception:
                pass
    if cur.get('ssid'):
        nets.append(cur)
    # keep strongest per SSID
    best = {}
    for n in nets:
        k = n.get('ssid')
        if not k:
            continue
        if k not in best or (n.get('signal_dbm', -999) > best[k].get('signal_dbm', -999)):
            best[k] = n
    return sorted(best.values(), key=lambda x: x.get('signal_dbm', -999), reverse=True)

def ensure_wpa_running() -> bool:
    """
    Make sure wpa_supplicant@<STA> is enabled and running.
    """
    sh(["sudo", "/bin/systemctl", "enable", f"wpa_supplicant@{WLAN_STA_IFACE}"])
    code, _ = sh(["sudo", "/bin/systemctl", "start", f"wpa_supplicant@{WLAN_STA_IFACE}"])
    return code == 0

def wait_for_ip(iface: str, timeout_s: int = 45) -> Optional[str]:
    """
    Poll for an IPv4 address on iface and return it (CIDR). None on timeout.
    """
    deadline = time.time() + timeout_s
    got_one = None
    while time.time() < deadline:
        got_one = ip_addr4(iface)
        if got_one:
            return got_one
        time.sleep(1.0)
    return None

def wifi_connect(ssid: str, psk: str) -> tuple[bool, str, dict]:
    """
    Create/enable network using wpa_cli so config persists via save_config.
    Flow:
      - ensure wpa_supplicant@<STA> is running
      - wpa_cli add_network -> id
      - set ssid / psk (or open) / priority
      - enable + select + save_config
      - switch dhcpcd to DHCP for STA, restart, wait for IP
    Returns (ok, message, details_dict)
    """
    if not ssid:
        return False, "Missing SSID", {}
    ensure_wpa_running()

    # Add a new network
    code, out = sh(["sudo", "/sbin/wpa_cli", "-i", WLAN_STA_IFACE, "add_network"])
    if code != 0 or not out.strip().isdigit():
        return False, f"wpa_cli add_network failed: {out.strip()}", {}
    net_id = out.strip()

    # Set parameters
    cmds = [
        ["sudo", "/sbin/wpa_cli", "-i", WLAN_STA_IFACE, "set_network", net_id, "ssid", f'"{ssid}"'],
        (["sudo", "/sbin/wpa_cli", "-i", WLAN_STA_IFACE, "set_network", net_id, "psk", f'"{psk}"'] if psk else
         ["sudo", "/sbin/wpa_cli", "-i", WLAN_STA_IFACE, "set_network", net_id, "key_mgmt", "NONE"]),
        ["sudo", "/sbin/wpa_cli", "-i", WLAN_STA_IFACE, "set_network", net_id, "priority", "10"],
    ]
    for cmd in cmds:
        code, out = sh(cmd)
        if code != 0 or "OK" not in out:
            return False, f"wpa_cli set_network failed: {' '.join(cmd[-3:])} -> {out.strip()}", {}

    # Enable/select and save
    for cmd in (
        ["sudo", "/sbin/wpa_cli", "-i", WLAN_STA_IFACE, "enable_network", net_id],
        ["sudo", "/sbin/wpa_cli", "-i", WLAN_STA_IFACE, "select_network", net_id],
        ["sudo", "/sbin/wpa_cli", "-i", WLAN_STA_IFACE, "save_config"],
        ["sudo", "/sbin/wpa_cli", "-i", WLAN_STA_IFACE, "reconfigure"],
    ):
        code, out = sh(cmd)
        if code != 0:
            return False, f"wpa_cli command failed: {' '.join(cmd[5:])}", {}

    # Make sure STA uses DHCP first-connect
    ok, msg = _apply_dhcpcd(mode="dhcp")
    if not ok:
        return False, f"Connected at Wi-Fi layer, but DHCP apply failed: {msg}", {}

    ip = wait_for_ip(WLAN_STA_IFACE, timeout_s=45)
    st = wifi_status_sta()
    if not ip:
        return False, "Associated, but no DHCP lease yet", {"status": st}

    return True, "Connected", {"ip": ip, "status": st}

# -------- IPv4 info & config (DHCP/Static on STA) --------

def ip_addr4(iface: str) -> Optional[str]:
    """Return IPv4/CIDR for iface (e.g., '192.168.1.10/24') via `ip -4 -o addr`."""
    code, out = sh(["ip", "-4", "-o", "addr", "show", "dev", iface])
    if code != 0:
        return None
    m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+/\d+)", out)
    return m.group(1) if m else None

def gw4(iface: str) -> Optional[str]:
    """Return default gateway (IPv4) for iface via `ip route`."""
    code, out = sh(["ip", "route", "show", "default", "dev", iface])
    if code != 0:
        return None
    m = re.search(r"default via\s+(\d+\.\d+\.\d+\.\d+)", out)
    return m.group(1) if m else None

def dns_servers() -> list[str]:
    """Parse /etc/resolv.conf nameserver lines into a list of IPv4 strings."""
    txt = read_text(Path("/etc/resolv.conf"))
    return re.findall(r"nameserver\s+(\d+\.\d+\.\d+\.\d+)", txt)

def dhcpcd_current_mode() -> dict:
    """
    Inspect /etc/dhcpcd.conf for our managed block (KS-STATIC-BEGIN/END).
    If absent, assume DHCP.
    """
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
    """Return new dhcpcd.conf content with/without our managed static block."""
    base = read_text(DHCPCD_CONF)
    # Remove any previous KS block (if present)
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
    code, out = sh(["sudo", "/usr/bin/systemctl", "restart", "dhcpcd"])
    if code != 0:
        return False, "Failed to restart dhcpcd: " + out
    return True, "Applied"

def apply_network(mode: str, ip_cidr: str = "", router: str = "", dns_csv: str = "") -> tuple[bool, str]:
    """
    Apply DHCP or static IPv4 settings on STA iface:
      - Validates inputs.
      - Writes /etc/dhcpcd.conf atomically (sudo mv).
      - Restarts dhcpcd and nudges wpa_supplicant.
    """
    if mode not in ("dhcp", "static"):
        return False, "Invalid mode"

    if mode == "static":
        try:
            ipaddress.ip_interface(ip_cidr)
        except Exception:
            return False, "Invalid IP/CIDR"
        for n in [router] + [x.strip() for x in dns_csv.split(",") if x.strip()]:
            try:
                ipaddress.ip_address(n)
            except Exception:
                return False, f"Invalid address: {n}"
        dns_list = [x.strip() for x in dns_csv.split(",") if x.strip()]
    else:
        dns_list = []

    ok, msg = _apply_dhcpcd(mode, ip_cidr, router, dns_list)
    if not ok:
        return False, msg

    # Nudge wpa_supplicant so dhcpcd notices link events
    sh(["sudo", "/sbin/wpa_cli", "-i", WLAN_STA_IFACE, "reconfigure"])
    time.sleep(1.0)
    return True, "Applied"
# --- Back-compat for older imports ---
def wifi_status():
    """Alias for legacy imports; return STA status (wlan1)."""
    return wifi_status_sta()
