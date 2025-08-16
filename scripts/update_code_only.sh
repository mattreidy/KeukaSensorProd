#!/usr/bin/env bash
set -euo pipefail
# update_code_only.sh — code-only deploy for keuka/
# VERSION: 2025-08-16T00:05Z

# Replaces only APP_ROOT/keuka with STAGE_DIR/keuka and restarts SERVICE_NAME.
# Detaches before stopping the service so it can complete even when called from within the service.
# Writes the deployed commit SHA into both:
#   - APP_ROOT/keuka/.keuka_commit
#   - APP_ROOT/.keuka_commit

usage() {
  cat <<'USAGE'
Usage: update_code_only.sh --stage <STAGE_DIR> --root <APP_ROOT> --service <SERVICE_NAME> [--commit <SHA>]

Replaces <APP_ROOT>/keuka with <STAGE_DIR>/keuka (backing up the current keuka/),
then restarts the specified systemd service.

Environment variables (optional fallback):
  STAGE_DIR, APP_ROOT, SERVICE_NAME, COMMIT_SHA
USAGE
}

# -------- parse args with env fallback --------
STAGE_DIR_ARG=""; APP_ROOT_ARG=""; SERVICE_ARG=""; COMMIT_ARG=""
RUN_APPLY=0

# Parse all args; if we see __run_apply__, set RUN_APPLY=1 and keep parsing (do not break)
while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage)   STAGE_DIR_ARG="${2:-}"; shift 2 ;;
    --root)    APP_ROOT_ARG="${2:-}";  shift 2 ;;
    --service) SERVICE_ARG="${2:-}";   shift 2 ;;
    --commit)  COMMIT_ARG="${2:-}";    shift 2 ;;
    __run_apply__) RUN_APPLY=1; shift ;;  # marker that tells us to run apply inline
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1"; usage; exit 2 ;;
  esac
done

STAGE_DIR="${STAGE_DIR_ARG:-${STAGE_DIR:-}}"
APP_ROOT="${APP_ROOT_ARG:-${APP_ROOT:-}}"
SERVICE_NAME="${SERVICE_ARG:-${SERVICE_NAME:-}}"
COMMIT_SHA="${COMMIT_ARG:-${COMMIT_SHA:-}}"

if [[ -z "${STAGE_DIR}" || -z "${APP_ROOT}" || -z "${SERVICE_NAME}" ]]; then
  echo "[update_code_only] ERROR: STAGE_DIR/APP_ROOT/SERVICE_NAME are required."
  usage; exit 2
fi

LOG_DIR="${APP_ROOT}/logs"
UPDATER_LOG="${LOG_DIR}/updater.log"
mkdir -p "${LOG_DIR}"
# Write everything to both stdout and the persistent log
exec > >(stdbuf -o0 awk '{print; fflush()}' | tee -a "${UPDATER_LOG}") 2>&1

echo "[update_code_only] starting..."
echo "[update_code_only] STAGE_DIR=${STAGE_DIR}"
echo "[update_code_only] APP_ROOT=${APP_ROOT}"
echo "[update_code_only] SERVICE_NAME=${SERVICE_NAME}"
if [[ -n "${COMMIT_SHA}" ]]; then
  echo "[update_code_only] commit=${COMMIT_SHA}"
fi

KEUKA_CUR="${APP_ROOT}/keuka"
KEUKA_BAK="${APP_ROOT}/keuka.bak.$(date +%Y%m%d-%H%M%S)"
KEUKA_NEW="${STAGE_DIR}/keuka"

if [[ ! -d "${KEUKA_NEW}" ]]; then
  echo "[update_code_only] ERROR: staged keuka/ not found at ${KEUKA_NEW}"
  exit 2
fi

# Make a self-contained snapshot of the staged payload so we never race
SNAP_DIR="$(mktemp -d /tmp/keuka_apply_XXXXXX)"
cleanup_snap() { [[ -n "${SNAP_DIR:-}" && -d "${SNAP_DIR}" ]] && rm -rf "${SNAP_DIR}"; }
trap cleanup_snap EXIT
echo "[update_code_only] snapshotting staged code to ${SNAP_DIR}"
cp -a "${KEUKA_NEW}" "${SNAP_DIR}/"   # results in ${SNAP_DIR}/keuka

write_markers() {
  local sha="$1"
  if [[ -n "$sha" ]]; then
    echo "$sha" > "${KEUKA_CUR}/.keuka_commit" || true
    echo "$sha" > "${APP_ROOT}/.keuka_commit"   || true
    echo "[update_code_only] wrote .keuka_commit markers"
  fi
}

rollback() {
  echo "[update_code_only] ROLLBACK: restoring previous keuka/ from ${KEUKA_BAK}"
  rm -rf "${KEUKA_CUR}" || true
  if [[ -d "${KEUKA_BAK}" ]]; then
    mv "${KEUKA_BAK}" "${KEUKA_CUR}"
  fi
  systemctl daemon-reload || true
  if ! systemctl restart "${SERVICE_NAME}"; then
    echo "[update_code_only] ROLLBACK: restart failed; showing status/logs"
    systemctl status "${SERVICE_NAME}" --no-pager || true
    journalctl -u "${SERVICE_NAME}" -n 150 --no-pager || true
  fi
}

do_apply() {
  set -euo pipefail
  echo "[update_code_only] (apply) begin"

  # --- STOP SERVICE (always attempt; don't pre-check existence) ---
  echo "[update_code_only] stopping service: ${SERVICE_NAME}"
  systemctl daemon-reload || true
  if ! systemctl stop "${SERVICE_NAME}"; then
    echo "[update_code_only] note: systemctl stop ${SERVICE_NAME} returned non-zero (may not be running yet)"
  fi
  systemctl reset-failed "${SERVICE_NAME}" || true

  # --- BACKUP & INSTALL ---
  if [[ -d "${KEUKA_CUR}" ]]; then
    echo "[update_code_only] backing up current keuka/ to ${KEUKA_BAK}"
    mv "${KEUKA_CUR}" "${KEUKA_BAK}"
  else
    echo "[update_code_only] no existing keuka/ to backup"
  fi

  echo "[update_code_only] installing new keuka/ from snapshot ${SNAP_DIR}"
  cp -a "${SNAP_DIR}/keuka" "${KEUKA_CUR}"

  # Write version marker(s)
  write_markers "${COMMIT_SHA:-}"

  echo "[update_code_only] setting ownership and permissions"
  chown -R pi:pi "${KEUKA_CUR}" || true
  find "${KEUKA_CUR}" -type f -name "*.py" -exec chmod 0644 {} +
  find "${KEUKA_CUR}" -type d -exec chmod 0755 {} +

  # --- RESTART SERVICE (log diagnostics on failure; rollback if needed) ---
  echo "[update_code_only] restarting service: ${SERVICE_NAME}"
  systemctl daemon-reload || true
  if ! systemctl restart "${SERVICE_NAME}"; then
    echo "[update_code_only] ERROR: systemctl restart failed for ${SERVICE_NAME}"
    echo "----- systemctl status ${SERVICE_NAME} -----"
    systemctl status "${SERVICE_NAME}" --no-pager || true
    echo "----- journalctl -u ${SERVICE_NAME} (last 120) -----"
    journalctl -u "${SERVICE_NAME}" -n 120 --no-pager || true
    rollback
    exit 3
  fi

  # --- HEALTH CHECK (use /admin/version) ---
  sleep 2
  if command -v curl >/dev/null 2>&1; then
    echo "[update_code_only] health check /admin/version (best-effort)"
    ok=0
    for i in {1..12}; do
      if curl -sf "http://127.0.0.1:5000/admin/version" >/dev/null; then
        echo "[update_code_only] health OK"
        ok=1
        break
      fi
      sleep 1
    done
    if [[ $ok -eq 0 ]]; then
      echo "[update_code_only] WARNING: health check did not pass; showing status"
      systemctl status "${SERVICE_NAME}" --no-pager || true
      journalctl -u "${SERVICE_NAME}" -n 80 --no-pager || true
      # do not fail deploy solely due to health check
    fi
  fi

  echo "[update_code_only] (apply) done."
}

# Detach only if we are NOT already in apply mode
if [[ "${RUN_APPLY}" -eq 0 ]]; then
  if command -v systemd-run >/dev/null 2>&1; then
    UNIT="keuka-apply-$(date +%s)"
    echo "[update_code_only] detaching via systemd-run unit ${UNIT}"
    # Re-invoke ourselves with __run_apply__ so the child path does the real work
    systemd-run --unit="${UNIT}" --collect /bin/bash -lc \
      "$(printf '%q ' "$0") --stage $(printf '%q' "${STAGE_DIR}") --root $(printf '%q' "${APP_ROOT}") --service $(printf '%q' "${SERVICE_NAME}") --commit $(printf '%q' "${COMMIT_SHA}") __run_apply__" \
      || echo "[update_code_only] WARNING: systemd-run failed; falling back to nohup"
    exit 0
  fi

  echo "[update_code_only] detaching via nohup background subshell"
  nohup bash -lc "$(printf '%q' "$0") --stage $(printf '%q' "${STAGE_DIR}") --root $(printf '%q' "${APP_ROOT}") --service $(printf '%q' "${SERVICE_NAME}") --commit $(printf '%q' "${COMMIT_SHA}") __run_apply__" >/dev/null 2>&1 &
  disown || true
  exit 0
fi

# If we’re here, RUN_APPLY=1 — perform the actual apply inline
do_apply
