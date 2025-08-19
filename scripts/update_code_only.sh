#!/usr/bin/env bash
set -euo pipefail
# update_code_only.sh — code-only deploy for keuka/ (PYTHON FILES ONLY, ALWAYS PRUNE)
# VERSION: 2025-08-19T00:35Z
#
# What this does:
#   - STOP the service
#   - BACK UP only existing *.py files under APP_ROOT/keuka to keuka.pybak.TIMESTAMP/
#   - COPY ONLY *.py files from STAGE_DIR/keuka into APP_ROOT/keuka (create dirs as needed)
#   - ALWAYS prune local *.py that no longer exist in staged code (repo)
#   - WRITE commit markers
#   - RESTART the service, run a simple health check
#
# What it does NOT do:
#   - It does NOT touch keuka/static or any other non-*.py content.
#   - It does NOT move/overwrite the entire keuka/ directory.

usage() {
  cat <<'USAGE'
Usage: update_code_only.sh --stage <STAGE_DIR> --root <APP_ROOT> --service <SERVICE_NAME> [--commit <SHA>] [--apply]

Replaces ONLY the *.py files under <APP_ROOT>/keuka with those from <STAGE_DIR>/keuka,
preserving non-Python files (e.g., static assets). Always prunes local *.py not present upstream.
Restarts the specified systemd service.

Environment variables (optional fallback):
  STAGE_DIR, APP_ROOT, SERVICE_NAME, COMMIT_SHA
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

if [[ -z "${STAGE_DIR}" || -z "${APP_ROOT}" || -z "${SERVICE_NAME}" ]]; then
  echo "[update_code_only] ERROR: STAGE_DIR/APP_ROOT/SERVICE_NAME are required."
  usage; exit 2
fi

LOG_DIR="${APP_ROOT}/logs"
UPDATER_LOG="${LOG_DIR}/updater.log"
mkdir -p "${LOG_DIR}"
exec > >(stdbuf -o0 awk '{print; fflush()}' | tee -a "${UPDATER_LOG}") 2>&1

echo "[update_code_only] starting..."
echo "[update_code_only] STAGE_DIR=${STAGE_DIR}"
echo "[update_code_only] APP_ROOT=${APP_ROOT}"
echo "[update_code_only] SERVICE_NAME=${SERVICE_NAME}"
if [[ -n "${COMMIT_SHA}" ]]; then
  echo "[update_code_only] commit=${COMMIT_SHA}"
fi

KEUKA_CUR="${APP_ROOT}/keuka"
KEUKA_NEW="${STAGE_DIR}/keuka"
PENDING_NEXT="${APP_ROOT}/.keuka_commit.next"
if [[ ! -d "${KEUKA_NEW}" ]]; then
  echo "[update_code_only] ERROR: staged keuka/ not found at ${KEUKA_NEW}"
  exit 2
fi
if [[ ! -d "${KEUKA_CUR}" ]]; then
  echo "[update_code_only] ERROR: current keuka/ not found at ${KEUKA_CUR}"
  exit 2
fi

# Snapshot staged payload to avoid races
SNAP_DIR="$(mktemp -d /tmp/keuka_apply_XXXXXX)"
cleanup_snap() { [[ -n "${SNAP_DIR:-}" && -d "${SNAP_DIR}" ]] && rm -rf "${SNAP_DIR}"; }
trap cleanup_snap EXIT
echo "[update_code_only] snapshotting staged code to ${SNAP_DIR}"
cp -a "${KEUKA_NEW}" "${SNAP_DIR}/"   # -> ${SNAP_DIR}/keuka

write_markers() {
  local sha="$1"
  if [[ -n "$sha" ]]; then
    echo "$sha" > "${KEUKA_CUR}/.keuka_commit" || true
    echo "$sha" > "${APP_ROOT}/.keuka_commit"   || true
    echo "[update_code_only] wrote .keuka_commit markers"
  fi
}

# Back up current *.py files (relative tree) so we can roll back if needed
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
  echo "[update_code_only] installing new *.py files from snapshot (preserving static and non-*.py)"
  local SRC_ROOT="${SNAP_DIR}/keuka"
  local DST_ROOT="${KEUKA_CUR}"

  local copied=0
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --chmod=F0644 --chown=pi:pi \
      --include='*/' --include='*.py' --exclude='*' \
      "${SRC_ROOT}/" "${DST_ROOT}/"
    copied=$(cd "${SRC_ROOT}" && find . -type f -name '*.py' | wc -l | awk '{print $1}')
  else
    while IFS= read -r -d '' rel; do
      rel="${rel#./}"
      local src="${SRC_ROOT}/${rel}"
      local dst="${DST_ROOT}/${rel}"
      install -D -m 0644 "${src}" "${dst}"
      chown pi:pi "${dst}" || true
      copied=$((copied+1))
    done < <(cd "${SRC_ROOT}" && find . -type f -name "*.py" -print0)
  fi
  echo "[update_code_only] copied/updated ${copied} *.py files."

  # Clear old bytecode to avoid stale modules
  find "${DST_ROOT}" -type d -name "__pycache__" -exec rm -rf {} + || true
}

prune_removed_py() {
  echo "[update_code_only] pruning removed *.py (always on)"
  local SRC_ROOT="${SNAP_DIR}/keuka"
  local pruned=0
  while IFS= read -r -d '' rel; do
    rel="${rel#./}"
    if [[ ! -f "${SRC_ROOT}/${rel}" ]]; then
      rm -f "${KEUKA_CUR}/${rel}" || true
      pruned=$((pruned+1))
      echo "[update_code_only] pruned: ${rel}"
    fi
  done < <(cd "${KEUKA_CUR}" && find . -type f -name "*.py" -print0)
  echo "[update_code_only] pruned ${pruned} files."
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

  echo "[update_code_only] stopping service: ${SERVICE_NAME}"
  systemctl daemon-reload || true
  if ! systemctl stop "${SERVICE_NAME}"; then
    echo "[update_code_only] note: systemctl stop ${SERVICE_NAME} returned non-zero (may not be running yet)"
  fi
  systemctl reset-failed "${SERVICE_NAME}" || true

  backup_current_py
  copy_python_files
  prune_removed_py
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
    fi
  fi

  rm -f "${PENDING_NEXT}" || true
  echo "[update_code_only] (apply) done."
}

# ----- Parent-only work: write pending marker and detach -----
if [[ "${RUN_APPLY}" -eq 0 ]]; then
  if [[ -n "${COMMIT_SHA:-}" ]]; then
    echo "${COMMIT_SHA}" > "${PENDING_NEXT}" || true
    echo "[update_code_only] wrote pending marker ${PENDING_NEXT}"
  fi

  if command -v systemd-run >/dev/null 2>&1; then
    UNIT="keuka-apply-$(date +%s)"
    echo "[update_code_only] detaching via systemd-run unit ${UNIT}"

    LAUNCH="$(mktemp /tmp/keuka_apply_launch_XXXXXX.sh)"
    cat > "${LAUNCH}" <<LAUNCH_EOF
#!/usr/bin/env bash
exec /bin/bash "$(printf '%q' "$0")" \
  --stage "$(printf '%q' "${STAGE_DIR}")" \
  --root  "$(printf '%q' "${APP_ROOT}")" \
  --service "$(printf '%q' "${SERVICE_NAME}")" \
  --commit "$(printf '%q' "${COMMIT_SHA}")" \
  --apply
LAUNCH_EOF
    chmod +x "${LAUNCH}"

    systemd-run --unit="${UNIT}" --collect "${LAUNCH}" || echo "[update_code_only] WARNING: systemd-run failed; falling back to nohup"
    exit 0
  fi

  echo "[update_code_only] detaching via nohup background subshell"
  nohup /bin/bash -lc \
    "$(printf '%q' "$0") --stage $(printf '%q' "${STAGE_DIR}") --root $(printf '%q' "${APP_ROOT}") --service $(printf '%q' "${SERVICE_NAME}") --commit $(printf '%q' "${COMMIT_SHA}") --apply" >/dev/null 2>&1 &
  disown || true
  exit 0
fi

# ----- Child path: perform apply inline -----
do_apply
