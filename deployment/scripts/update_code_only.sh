#!/usr/bin/env bash
set -euo pipefail
umask 022
# update_code_only.sh — code-only deploy for keuka/
# PY FILES ONLY, PRESERVE STATIC, ALWAYS PRUNE, VERBOSE
# VERSION: 2025-08-19T02:55Z

usage() {
  cat <<'USAGE'
Usage: update_code_only.sh --root <APP_ROOT> --service <SERVICE_NAME> [--stage <STAGE_DIR>] [--commit <SHA>] [--apply]

Replaces ONLY the *.py files under <APP_ROOT>/keuka with those from a snapshot of <STAGE_DIR>/keuka,
preserving non-Python files (e.g., static assets). Always prunes local *.py not present upstream.
Also updates push service files in /opt/keuka from push-service/ directory.
If SNAP_DIR is provided (env), the script REUSES that snapshot and does not require --stage.

Environment variables:
  SNAP_DIR         If set to a directory containing keuka/ it will be used instead of --stage
  KS_ADMIN_USER    Optional for /admin/version health check
  KS_ADMIN_PASS    Optional for /admin/version health check

Detaching:
  Parent writes a snapshot & then detaches via systemd-run with SNAP_DIR in the environment.
USAGE
}

# -------- parse args with env fallback --------
STAGE_DIR_ARG=""; APP_ROOT_ARG=""; SERVICE_ARG=""; COMMIT_ARG=""
RUN_APPLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage)   STAGE_DIR_ARG="${2:-}"; shift 2 ;;
    --root)    APP_ROOT_ARG="${2:-}";  shift 2 ;;
    --service) SERVICE_ARG="${2:-}";   shift 2 ;;
    --commit)  COMMIT_ARG="${2:-}";    shift 2 ;;
    --apply)   RUN_APPLY=1; shift ;;
    __run_apply__) RUN_APPLY=1; shift ;;  # backward-compat
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1"; usage; exit 2 ;;
  esac
done

STAGE_DIR="${STAGE_DIR_ARG:-${STAGE_DIR:-}}"
APP_ROOT="${APP_ROOT_ARG:-${APP_ROOT:-}}"
SERVICE_NAME="${SERVICE_ARG:-${SERVICE_NAME:-}}"
COMMIT_SHA="${COMMIT_ARG:-${COMMIT_SHA:-}}"
SNAP_DIR="${SNAP_DIR:-}"

if [[ -z "${APP_ROOT}" || -z "${SERVICE_NAME}" ]]; then
  echo "[update_code_only] ERROR: APP_ROOT and SERVICE_NAME are required."
  usage; exit 2
fi
# Require either a stage dir (to create a snapshot) OR an existing snapshot
if [[ -z "${STAGE_DIR}" && -z "${SNAP_DIR}" ]]; then
  echo "[update_code_only] ERROR: Provide --stage <STAGE_DIR> or set SNAP_DIR."
  usage; exit 2
fi

LOG_DIR="${APP_ROOT}/logs"
UPDATER_LOG="${LOG_DIR}/updater.log"
mkdir -p "${LOG_DIR}"
# Log only to file (more reliable under systemd)
exec >> "${UPDATER_LOG}" 2>&1

echo "[update_code_only] starting..."
echo "[update_code_only] RUN_APPLY=${RUN_APPLY}"
echo "[update_code_only] APP_ROOT=${APP_ROOT}"
echo "[update_code_only] SERVICE_NAME=${SERVICE_NAME}"
[[ -n "${COMMIT_SHA}" ]] && echo "[update_code_only] commit=${COMMIT_SHA}"
[[ -n "${STAGE_DIR}" ]] && echo "[update_code_only] STAGE_DIR=${STAGE_DIR}"
[[ -n "${SNAP_DIR}"  ]] && echo "[update_code_only] SNAP_DIR(pre)=${SNAP_DIR}"

KEUKA_CUR="${APP_ROOT}/keuka"
PENDING_NEXT="${APP_ROOT}/.keuka_commit.next"

if [[ ! -d "${KEUKA_CUR}" ]]; then
  echo "[update_code_only] ERROR: current keuka/ not found at ${KEUKA_CUR}"
  exit 2
fi

# ----- Build or reuse snapshot -----
build_or_reuse_snapshot() {
  if [[ -n "${SNAP_DIR}" && -d "${SNAP_DIR}/keuka" ]]; then
    echo "[update_code_only] reusing existing SNAP_DIR: ${SNAP_DIR}"
    chmod -R a+rx "${SNAP_DIR}" || true
    return 0
  fi

  if [[ -z "${STAGE_DIR}" ]]; then
    echo "[update_code_only] ERROR: SNAP_DIR not valid and no STAGE_DIR provided."
    exit 2
  fi

  local KEUKA_NEW="${STAGE_DIR}/keuka"
  if [[ ! -d "${KEUKA_NEW}" ]]; then
    echo "[update_code_only] ERROR: staged keuka/ not found at ${KEUKA_NEW}"
    exit 2
  fi

  local SNAP_BASE="${APP_ROOT}/tmp"
  mkdir -p "${SNAP_BASE}"
  SNAP_DIR="$(mktemp -d "${SNAP_BASE}/keuka_apply_XXXXXX")"
  echo "[update_code_only] snapshotting staged code to ${SNAP_DIR}"
  cp -a "${KEUKA_NEW}" "${SNAP_DIR}/"   # -> ${SNAP_DIR}/keuka
  echo "[update_code_only] SNAP_DIR kept at: ${SNAP_DIR}"
  chmod -R a+rx "${SNAP_DIR}" || true
  export SNAP_DIR
}

write_markers() {
  local sha="$1"
  if [[ -n "$sha" ]]; then
    echo "$sha" > "${KEUKA_CUR}/.keuka_commit" || true
    echo "$sha" > "${APP_ROOT}/.keuka_commit"   || true
    echo "[update_code_only] wrote .keuka_commit markers"
  fi
}

# Back up current *.py files (relative tree)
BACKUP_DIR="${APP_ROOT}/keuka.pybak.$(date +%Y%m%d-%H%M%S)"
backup_current_py() {
  echo "[update_code_only] backing up current *.py files to ${BACKUP_DIR}"
  local count=0
  while IFS= read -r -d '' rel; do
    local src="${KEUKA_CUR}/${rel}"
    local dst="${BACKUP_DIR}/${rel}"
    install -D -m 0644 "${src}" "${dst}" || true
    count=$((count+1))
  done < <(cd "${KEUKA_CUR}" && find . -type f -name "*.py" -print0)
  echo "[update_code_only] backup saved (${count} files)."
}

restore_from_backup() {
  if [[ ! -d "${BACKUP_DIR}" ]]; then
    echo "[update_code_only] ROLLBACK: no backup dir found (${BACKUP_DIR})"
    return 0
  fi
  echo "[update_code_only] ROLLBACK: restoring *.py from ${BACKUP_DIR}"
  local count=0
  while IFS= read -r -d '' rel; do
    local src="${BACKUP_DIR}/${rel}"
    local dst="${KEUKA_CUR}/${rel}"
    install -D -m 0644 "${src}" "${dst}" || true
    chown pi:pi "${dst}" || true
    count=$((count+1))
  done < <(cd "${BACKUP_DIR}" && find . -type f -name "*.py" -print0)
  echo "[update_code_only] ROLLBACK: restored ${count} files."
}

copy_python_files() {
  if [[ ! -f "${SNAP_DIR}/keuka/app.py" ]]; then
    echo "[update_code_only] ERROR: snapshot missing keuka/app.py at ${SNAP_DIR}/keuka"
    exit 3
  fi
  echo "[update_code_only] BEGIN:SOURCE_LIST"
  ( cd "${SNAP_DIR}/keuka" && find . -type f -name '*.py' -print | sort )
  echo "[update_code_only] END:SOURCE_LIST"

  echo "[update_code_only] installing new *.py files from snapshot (preserving static and non-*.py)"
  local SRC_ROOT="${SNAP_DIR}/keuka"
  local DST_ROOT="${KEUKA_CUR}"

  local copied=0
  if command -v rsync >/dev/null 2>&1; then
    rsync -aiv --ignore-times --chmod=F0644 --chown=pi:pi \
      --include='*/' --include='*.py' --exclude='*' \
      "${SRC_ROOT}/" "${DST_ROOT}/" | sed 's/^/[rsync] /'
    copied=$(cd "${SRC_ROOT}" && find . -type f -name '*.py' | wc -l | awk '{print $1}')
  else
    while IFS= read -r -d '' rel; do
      rel="${rel#./}"
      local src="${SRC_ROOT}/${rel}"
      local dst="${DST_ROOT}/${rel}"
      echo "[copy] ${rel}"
      install -D -m 0644 "${src}" "${dst}"
      chown pi:pi "${dst}" || true
      copied=$((copied+1))
    done < <(cd "${SRC_ROOT}" && find . -type f -name "*.py" -print0)
  fi
  echo "[update_code_only] copied/updated ${copied} *.py files."

  # Clear old bytecode to avoid stale modules
  find "${DST_ROOT}" -type d -name "__pycache__" -exec rm -rf {} + || true
  # Ensure directory perms look sane
  find "${DST_ROOT}" -type d -exec chmod 0755 {} + || true

  # Proof-of-apply check
  if command -v sha256sum >/dev/null 2>&1; then
    echo "[update_code_only] verify app.py hash (src vs dst)"
    sha256sum "${SNAP_DIR}/keuka/app.py" || true
    sha256sum "${KEUKA_CUR}/app.py" || true
  fi
}

prune_removed_py() {
  echo "[update_code_only] pruning removed *.py (always on)"
  local SRC_ROOT="${SNAP_DIR}/keuka"
  local pruned=0
  while IFS= read -r -d '' rel; do
    rel="${rel#./}"
    if [[ ! -f "${SRC_ROOT}/${rel}" ]]; then
      echo "[prune] ${rel}"
      rm -f "${KEUKA_CUR}/${rel}" || true
      pruned=$((pruned+1))
    fi
  done < <(cd "${KEUKA_CUR}" && find . -type f -name "*.py" -print0)
  echo "[update_code_only] pruned ${pruned} files."
}

update_push_service_files() {
  echo "[update_code_only] updating push service files in /opt/keuka"
  
  # Use the APP_ROOT push-service directory since STAGE_DIR only contains keuka/
  local PUSH_SRC_DIR="${APP_ROOT}/push-service"
  local PUSH_DST_DIR="/opt/keuka"
  
  if [[ ! -d "${PUSH_SRC_DIR}" ]]; then
    echo "[update_code_only] WARNING: push-service directory not found at ${PUSH_SRC_DIR}, skipping"
    return 0
  fi
  
  # Create target directory if it doesn't exist
  mkdir -p "${PUSH_DST_DIR}"
  chown pi:pi "${PUSH_DST_DIR}" || true
  
  # Files to update
  local push_files=("sensor_push_service.py" "local_storage.py")
  local updated=0
  
  for file in "${push_files[@]}"; do
    local src="${PUSH_SRC_DIR}/${file}"
    local dst="${PUSH_DST_DIR}/${file}"
    
    if [[ -f "${src}" ]]; then
      echo "[update_code_only] updating push service file: ${file}"
      cp "${src}" "${dst}"
      chmod 755 "${dst}"
      chown pi:pi "${dst}" || true
      updated=$((updated+1))
    else
      echo "[update_code_only] WARNING: push service file not found: ${src}"
    fi
  done
  
  # Copy keuka utils directory for coordinate parser
  local utils_src="${APP_ROOT}/keuka/utils"
  local utils_dst="${PUSH_DST_DIR}/keuka/utils"
  
  if [[ -d "${utils_src}" ]]; then
    echo "[update_code_only] updating keuka utils directory"
    mkdir -p "$(dirname "${utils_dst}")"
    cp -r "${utils_src}" "$(dirname "${utils_dst}")/"
    chown -R pi:pi "${PUSH_DST_DIR}/keuka" || true
  fi
  
  echo "[update_code_only] updated ${updated} push service files."
}

rollback() {
  echo "[update_code_only] ROLLBACK starting..."
  restore_from_backup
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
  echo "[update_code_only] using SNAP_DIR=${SNAP_DIR}"

  echo "[update_code_only] stopping service: ${SERVICE_NAME}"
  systemctl daemon-reload || true
  if ! systemctl stop "${SERVICE_NAME}"; then
    echo "[update_code_only] note: systemctl stop ${SERVICE_NAME} returned non-zero (may not be running yet)"
  fi
  systemctl reset-failed "${SERVICE_NAME}" || true

  backup_current_py
  copy_python_files
  prune_removed_py
  update_push_service_files
  write_markers "${COMMIT_SHA:-}"

  echo "[update_code_only] restarting service: ${SERVICE_NAME}"
  systemctl daemon-reload || true
  if ! systemctl restart "${SERVICE_NAME}"; then
    echo "[update_code_only] ERROR: systemctl restart failed for ${SERVICE_NAME}"
    echo "----- systemctl status ${SERVICE_NAME} -----"
    systemctl status "${SERVICE_NAME}" --no-pager || true
    echo "----- journalctl -u ${SERVICE_NAME} (last 120) -----"
    journalctl -u "${SERVICE_NAME}" -n 120 --no-pager || true
    rm -f "${PENDING_NEXT}" || true
    rollback
    exit 3
  fi

  # Restart push service to load updated code
  echo "[update_code_only] restarting push service: keuka-sensor-push.timer"
  if systemctl is-enabled keuka-sensor-push.timer >/dev/null 2>&1; then
    if ! systemctl restart keuka-sensor-push.timer; then
      echo "[update_code_only] WARNING: push service restart failed, but continuing"
      systemctl status keuka-sensor-push.timer --no-pager || true
    else
      echo "[update_code_only] push service timer restarted successfully"
    fi
  else
    echo "[update_code_only] push service timer not found or not enabled, skipping"
  fi

  # Ensure tunnel service is also running after code update
  echo "[update_code_only] ensuring tunnel service is running: keuka-tunnel"
  if systemctl is-enabled keuka-tunnel >/dev/null 2>&1; then
    if ! systemctl restart keuka-tunnel; then
      echo "[update_code_only] WARNING: tunnel service restart failed, but continuing"
      systemctl status keuka-tunnel --no-pager || true
    else
      echo "[update_code_only] tunnel service restarted successfully"
    fi
  else
    echo "[update_code_only] tunnel service not found or not enabled, skipping"
  fi

  # Optional health check (supports Basic Auth if vars are set)
  sleep 2
  if command -v curl >/dev/null 2>&1; then
    echo "[update_code_only] health check /admin/version (best-effort)"
    CURL_AUTH=()
    if [[ -n "${KS_ADMIN_USER:-}" && -n "${KS_ADMIN_PASS:-}" ]]; then
      CURL_AUTH=(-u "${KS_ADMIN_USER}:${KS_ADMIN_PASS}")
    fi
    ok=0
    for i in {1..12}; do
      if curl -sf "${CURL_AUTH[@]}" "http://127.0.0.1:5000/admin/version" >/dev/null; then
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
    fi
  fi

  # ...existing health-check and marker removal...
  rm -f "${PENDING_NEXT}" || true

  # NEW: remove the snapshot to avoid filling disk
  if [[ -n "${SNAP_DIR}" && -d "${SNAP_DIR}" ]]; then
    rm -rf "${SNAP_DIR}" \
      && echo "[update_code_only] cleaned snapshot ${SNAP_DIR}" \
      || echo "[update_code_only] NOTE: could not remove snapshot ${SNAP_DIR}"
  fi

  echo "[update_code_only] (apply) done."
}

# ----- Build/reuse snapshot now -----
build_or_reuse_snapshot

# ----- Parent path: write pending marker and detach (no temp launcher) -----
if [[ "${RUN_APPLY}" -eq 0 ]]; then
  if [[ -n "${COMMIT_SHA:-}" ]]; then
    echo "${COMMIT_SHA}" > "${PENDING_NEXT}" || true
    echo "[update_code_only] wrote pending marker ${PENDING_NEXT}"
  fi

  if command -v systemd-run >/dev/null 2>&1; then
    UNIT="keuka-apply-$(date +%s)"
    echo "[update_code_only] detaching via systemd-run unit ${UNIT}"
    systemd-run --unit="${UNIT}" --collect \
      --property=PrivateTmp=no \
      --setenv=KS_ADMIN_USER="${KS_ADMIN_USER:-}" \
      --setenv=KS_ADMIN_PASS="${KS_ADMIN_PASS:-}" \
      --setenv=SNAP_DIR="${SNAP_DIR}" \
      /bin/bash "$0" \
      --root  "${APP_ROOT}" \
      --service "${SERVICE_NAME}" \
      --commit "${COMMIT_SHA}" \
      --apply || echo "[update_code_only] WARNING: systemd-run failed; falling back to nohup"
    exit 0
  fi

  echo "[update_code_only] detaching via nohup background subshell"
  nohup env SNAP_DIR="${SNAP_DIR}" /bin/bash -lc \
    "$(printf '%q' "$0") --root $(printf '%q' "${APP_ROOT}") --service $(printf '%q' "${SERVICE_NAME}") --commit $(printf '%q' "${COMMIT_SHA}") --apply" >/dev/null 2>&1 &
  disown || true
  exit 0
fi

# ----- Child path: perform apply inline -----
do_apply
