# wifi_net.py
# -----------------------------------------------------------------------------
# Wi-Fi and IPv4 helpers:
#  - wifi_status / wifi_scan / wifi_connect
#  - ip_addr4 / gw4 / dns_servers
#  - dhcpcd_current_mode / dhcpcd_render / apply_network (static vs DHCP)
# These functions wrap system tools: iw, wpa_cli, ip, systemctl, etc.
# -----------------------------------------------------------------------------

from __future__ import annotations
import re
import time
import ipaddress
from typing import Optional

from config import (
    WLAN_STA_IFACE, WLAN_AP_IFACE, WPA_SUP_CONF,
    DHCPCD_CONF, DHCPCD_MARK_BEGIN, DHCPCD_MARK_END,
)
from utils import sh, read_text, write_text_atomic

def wifi_status():
    """Return current station link info for WLAN_STA_IFACE using `iw dev ... link`."""
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
    return {"iface": WLAN_STA_IFACE, "ssid": ssid, "bssid": bssid, "signal_dbm": rssi, "freq_mhz": freq, "tx_bitrate": bitrate}

def wifi_scan() -> list:
    """Scan visible Wi-Fi networks and return best signal per SSID (uniqued)."""
    code, out = sh(["sudo", "/sbin/iw", "dev", WLAN_STA_IFACE, "scan", "-u"])
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

def wifi_connect(ssid: str, psk: str) -> bool:
    """
    Append a network block to the interface-specific wpa_supplicant conf
    (removing any existing block for the same SSID), then `wpa_cli reconfigure`.
    """
    WPA_SUP_CONF.parent.mkdir(parents=True, exist_ok=True)
    if not WPA_SUP_CONF.exists():
        WPA_SUP_CONF.write_text(
            "ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\n"
            "update_config=1\n"
            "country=US\n",
            encoding='utf-8'
        )
    conf = WPA_SUP_CONF.read_text(encoding='utf-8').splitlines()
    out_lines = []
    in_block = False
    keep_block = True
    block = []

    def flush_block():
        nonlocal out_lines, block, keep_block
        if keep_block:
            out_lines.extend(block)

    i = 0
    while i < len(conf):
        ln = conf[i]
        if not in_block and ln.strip().startswith("network={"):
            in_block = True
            block = [ln]
            keep_block = True
            i += 1
            continue
        if in_block:
            block.append(ln)
            if 'ssid="' in ln:
                try:
                    existing = ln.split('ssid="', 1)[1].split('"', 1)[0]
                    if existing == ssid:
                        keep_block = False
                except Exception:
                    pass
            if ln.strip() == "}":
                in_block = False
                flush_block()
                block = []
            i += 1
            continue
        out_lines.append(ln)
        i += 1

    new_block = (
        "\nnetwork={\n"
        f'    ssid="{ssid}"\n'
        f'    psk="{psk}"\n'
        "    priority=10\n"
        "}\n"
    )
    out_text = "\n".join(out_lines) + new_block
    WPA_SUP_CONF.write_text(out_text, encoding='utf-8')
    sh(["sudo", "/sbin/wpa_cli", "-i", WLAN_STA_IFACE, "reconfigure"])
    return True

# -------- IPv4 info & config (DHCP/Static on wlan1) --------

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

from pathlib import Path  # placed here to avoid top-level import cycle warning

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

def apply_network(mode: str, ip_cidr: str = "", router: str = "", dns_csv: str = "") -> tuple[bool, str]:
    """
    Apply DHCP or static IPv4 settings:
      - Validates inputs.
      - Writes /etc/dhcpcd.conf atomically (via sudo mv).
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

    new_text = dhcpcd_render(mode, ip_cidr, router, dns_list)
    ok = write_text_atomic(DHCPCD_CONF, new_text, sudo_mv=True)
    if not ok:
        return False, "Failed to write /etc/dhcpcd.conf (sudo mv)"

    code, out = sh(["sudo", "/usr/bin/systemctl", "restart", "dhcpcd"])
    if code != 0:
        return False, "Failed to restart dhcpcd: " + out

    sh(["sudo", "/sbin/wpa_cli", "-i", WLAN_STA_IFACE, "reconfigure"])
    time.sleep(1.0)
    return True, "Applied"
