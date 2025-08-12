#!/usr/bin/env bash
set -euo pipefail

cd /home/pi/KeukaSensorProd

# Update code (assumes this dir is a git clone)
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "[update] Fetching latest…"
  git fetch --all
  git reset --hard origin/main
else
  echo "[update] Not a git repo; put new files here manually."
fi

# Install pinned deps
if [[ -x "venv/bin/pip" ]]; then
  echo "[update] Installing requirements…"
  /home/pi/KeukaSensorProd/venv/bin/pip install -r /home/pi/KeukaSensorProd/requirements.txt
fi

# Restart app
echo "[update] Restarting keuka-sensor.service…"
sudo systemctl restart keuka-sensor.service
echo "[update] Done."
