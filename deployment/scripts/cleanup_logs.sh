#!/bin/bash
# Keuka Sensor Log Cleanup Script
# Automatically prunes logs and temporary files to prevent disk space issues

# Configuration
APP_ROOT="/home/pi/KeukaSensorProd"
LOG_DIR="$APP_ROOT/logs"
TMP_DIR="$APP_ROOT/tmp"
BACKUP_RETENTION_DAYS=7
echo "$(date): Starting log cleanup..."

# 1. Clean application tmp directory (keep only files newer than 1 day)
if [ -d "$TMP_DIR" ]; then
    echo "Cleaning temporary files older than 1 day..."
    find "$TMP_DIR" -type f -mtime +1 -delete 2>/dev/null || true
    find "$TMP_DIR" -type d -empty -delete 2>/dev/null || true
fi

# 2. Remove old Python backup directories
echo "Removing old backup directories..."
find "$APP_ROOT" -maxdepth 1 -name "keuka.pybak.*" -type d -mtime +$BACKUP_RETENTION_DAYS -exec rm -rf {} \; 2>/dev/null || true

# 3. Rotate updater log if it gets too large (>1MB)
if [ -f "$LOG_DIR/updater.log" ]; then
    size_bytes="$(stat -c%s "$LOG_DIR/updater.log" 2>/dev/null || echo 0)"
    if [ "$size_bytes" -gt 1048576 ]; then
        echo "Rotating large updater.log..."
        mv "$LOG_DIR/updater.log" "$LOG_DIR/updater.log.old" 2>/dev/null || true
        : > "$LOG_DIR/updater.log" 2>/dev/null || true
        # Remove old rotation after 3 days
        find "$LOG_DIR" -name "updater.log.old" -mtime +3 -delete 2>/dev/null || true
    fi
fi

# 4. Journal cleanup (keep only 30 days, max 100MB)
# Note: This usually requires root; running as 'pi' may no-op.
echo "Cleaning systemd journal (may require root)..."
journalctl --vacuum-time=30d --vacuum-size=100M >/dev/null 2>&1 || true

echo "$(date): Log cleanup completed."