# keuka/admin/wan.py
# -----------------------------------------------------------------------------
# WAN/public IP helper
#
# Endpoint:
#   - GET /api/wanip  -> {"ok": True, "ip": "...", "changed_at": ISO, "checked_at": ISO}
#
# Behavior:
#   - Reads/writes wan_ip.json under APP_ROOT (same as before) to track last IP
#     and the timestamp when it changed. Uses api.ipify.org to check quickly.
# -----------------------------------------------------------------------------

from __future__ import annotations

from flask import Blueprint, jsonify
from pathlib import Path
from datetime import datetime, timezone
import json
import re
from urllib.request import urlopen

from updater import APP_ROOT

WAN_TRACK = Path(APP_ROOT) / "wan_ip.json"

def _fetch_public_ip() -> str | None:
    """Fast external check for IPv4; returns dotted quad or None."""
    try:
        with urlopen("https://api.ipify.org", timeout=4) as f:
            ip = f.read().decode("utf-8", "ignore").strip()
            if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", ip):
                return ip
    except Exception:
        pass
    return None

def attach(bp: Blueprint) -> None:
    @bp.route("/api/wanip")
    def api_wanip():
        """
        Returns {"ok": True, "ip": "...", "changed_at": ISO8601, "checked_at": ISO8601}
        Updates wan_ip.json if the IP changed.
        """
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        prev = {}
        try:
            if WAN_TRACK.exists():
                prev = json.loads(WAN_TRACK.read_text())
        except Exception:
            prev = {}

        prev_ip = prev.get("ip")
        prev_changed = prev.get("changed_at")

        ip = _fetch_public_ip()
        if ip and ip != prev_ip:
            prev_ip = ip
            prev_changed = now_iso
            try:
                WAN_TRACK.write_text(json.dumps({
                    "ip": ip,
                    "changed_at": prev_changed,
                    "checked_at": now_iso,
                }))
            except Exception:
                pass
        else:
            try:
                if ip:
                    WAN_TRACK.write_text(json.dumps({
                        "ip": prev_ip or ip,
                        "changed_at": prev_changed,
                        "checked_at": now_iso,
                    }))
            except Exception:
                pass

        return jsonify({
            "ok": True,
            "ip": ip or (prev_ip or None),
            "changed_at": prev_changed,
            "checked_at": now_iso,
        })
