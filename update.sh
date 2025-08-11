#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# If this directory is a git repo, pull latest from origin/main
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "[update] Pulling latest from origin/main..."
  git fetch --all
  git reset --hard origin/main
else
  echo "[update] Not a git repo; place new files here manually or init a repo."
fi

# Restart service if running under systemd
if systemctl is-active --quiet keuka-sensor.service; then
  echo "[update] Restarting keuka-sensor.service"
  sudo systemctl restart keuka-sensor.service
fi

echo "[update] Done."
