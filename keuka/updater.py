# keuka/updater.py
# ----------------
# Code-only updater for the Keuka app. Fetches the repo, stages keuka/, backs up current keuka/,
# executes the replacement script, and provides progress logs for the Admin UI.
# Persists logs to disk so they survive service restarts.
# Shows ONLY the most recent attempt’s logs across restarts.

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from typing import List, Optional, Tuple

from version import get_local_commit, get_remote_commit, short_sha

# -------- Configuration (env-driven with safe defaults) --------
REPO_URL = os.environ.get("KEUKA_REPO_URL", "https://github.com/mattreidy/KeukaSensorProd.git")
APP_ROOT = os.environ.get("KEUKA_APP_ROOT", "/home/pi/KeukaSensorProd")
SERVICE_NAME = os.environ.get("KEUKA_SERVICE_NAME", "keuka-sensor")
UPDATE_SCRIPT = os.environ.get("KEUKA_UPDATE_SCRIPT", os.path.join(APP_ROOT, "scripts", "update_code_only.sh"))
SUDO = os.environ.get("KEUKA_SUDO", "sudo")  # set "" to disable sudo

# Log file (persist across restarts)
LOG_DIR = os.path.join(APP_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "updater.log")
os.makedirs(LOG_DIR, exist_ok=True)

# Marker written at the start of each attempt (used to extract only the last run)
RUN_MARK = "----"
RUN_HEADER_SUFFIX = "(new run) starting..."

# -------- States --------
_STATE_IDLE = "idle"
_STATE_RUNNING = "running"
_STATE_SUCCESS = "success"
_STATE_ERROR = "error"
_STATE_CANCELED = "canceled"


def _append_log_file(line: str) -> None:
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _read_last_run_from_file(max_lines: int = 4000) -> List[str]:
    """
    Return ONLY the lines from the most recent attempt.
    We find the last RUN_MARK or last header containing RUN_HEADER_SUFFIX,
    and return from that point onward.
    """
    try:
        if not os.path.isfile(LOG_FILE):
            return []
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        # Trim first to reduce scan cost
        if len(lines) > max_lines:
            lines = lines[-max_lines:]

        last_idx = -1
        for i in range(len(lines) - 1, -1, -1):
            ln = lines[i]
            if ln.strip() == RUN_MARK or RUN_HEADER_SUFFIX in ln:
                last_idx = i
                break
        if last_idx >= 0:
            return lines[last_idx:]
        return lines[-max_lines:]
    except Exception:
        return []


class UpdateManager:
    """Singleton update manager that runs one update at a time and collects logs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: str = _STATE_IDLE
        self._logs: List[str] = []  # in-memory buffer for the CURRENT attempt only
        self._thread: Optional[threading.Thread] = None
        self._started_at: Optional[float] = None
        self._finished_at: Optional[float] = None
        self._tmpdir: Optional[str] = None
        self._cancel_requested: bool = False

    def state(self) -> str:
        with self._lock:
            return self._state

    def get_logs(self) -> Tuple[str, List[str], Optional[float], Optional[float]]:
        with self._lock:
            if self._logs:
                return self._state, list(self._logs), self._started_at, self._finished_at
        # If no current in-memory logs (e.g., after a service restart), show only the last attempt from disk
        return self._state, _read_last_run_from_file(), self._started_at, self._finished_at

    def start(self) -> bool:
        with self._lock:
            if self._state == _STATE_RUNNING:
                return False
            self._state = _STATE_RUNNING
            self._logs.clear()
            self._started_at = time.time()
            self._finished_at = None
            self._cancel_requested = False

            # Per-run header goes to BOTH memory and the persistent file
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            header = f"[{ts}] {RUN_HEADER_SUFFIX}"

            # Write a simple run separator + header to the log file
            _append_log_file(RUN_MARK)
            _append_log_file(header)

            # Mirror the header in memory so the UI shows it immediately
            self._logs.append(RUN_MARK)
            self._logs.append(header)

        t = threading.Thread(target=self._run, name="UpdaterThread", daemon=True)
        t.start()
        with self._lock:
            self._thread = t
        return True

    def cancel(self) -> None:
        with self._lock:
            if self._state == _STATE_RUNNING:
                self._cancel_requested = True
                self._log("Cancellation requested...")

    def _log(self, msg: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        with self._lock:
            self._logs.append(line)
        _append_log_file(line)

    def _finish(self, ok: bool) -> None:
        with self._lock:
            self._state = _STATE_SUCCESS if ok else _STATE_ERROR
            self._finished_at = time.time()

    def _check_cancel(self) -> bool:
        with self._lock:
            return self._cancel_requested

    def _run(self) -> None:
        ok = False
        tmpdir = None
        try:
            # Optional: verify sudo can run non-interactively (prevents silent failures)
            if SUDO.strip():
                rc_chk, _ = self._run_cmd([SUDO, "-n", "true"])
                if rc_chk != 0:
                    self._log("ERROR: sudo non-interactive check failed. Configure passwordless sudo or set KEUKA_SUDO='' to disable.")
                    self._finish(False)
                    return

            local_before = get_local_commit(APP_ROOT)
            remote_head = get_remote_commit(REPO_URL)
            self._log(f"Local commit before: {short_sha(local_before)}")
            self._log(f"Remote HEAD commit: {short_sha(remote_head)}")

            # No-op if already up-to-date
            if local_before and remote_head and local_before == remote_head:
                self._log("Already up-to-date; skipping apply.")
                ok = True
                self._finish(ok)
                return

            self._log("Starting code-only update...")
            if not os.path.isdir(APP_ROOT):
                self._log(f"ERROR: APP_ROOT does not exist: {APP_ROOT}")
                self._finish(False)
                return
            if not os.path.isfile(UPDATE_SCRIPT):
                self._log(f"ERROR: UPDATE_SCRIPT not found: {UPDATE_SCRIPT}")
                self._finish(False)
                return

            tmpdir = tempfile.mkdtemp(prefix="keuka_update_")
            self._tmpdir = tmpdir
            repo_dir = os.path.join(tmpdir, "repo")
            stage_dir = os.path.join(tmpdir, "stage")
            os.makedirs(stage_dir, exist_ok=True)
            self._log(f"Scratch directory: {tmpdir}")

            if self._check_cancel():
                self._log("Canceled before clone.")
                self._finish(False)
                return

            # Shallow clone repo
            self._log(f"Cloning repo (shallow): {REPO_URL}")
            rc, out = self._run_cmd(["git", "clone", "--depth", "1", REPO_URL, repo_dir], cwd=tmpdir)
            self._log(out)
            if rc != 0:
                self._log("ERROR: git clone failed.")
                self._finish(False)
                return

            if self._check_cancel():
                self._log("Canceled after clone.")
                self._finish(False)
                return

            # Determine the exact commit we are deploying from the clone
            rc, head_sha = self._run_cmd(["git", "-C", repo_dir, "rev-parse", "HEAD"], cwd=tmpdir)
            head_sha = (head_sha.strip().splitlines()[-1] if head_sha else "").strip()
            if rc != 0 or not head_sha:
                self._log("ERROR: could not determine cloned repo HEAD SHA.")
                self._finish(False)
                return
            self._log(f"Cloned commit: {short_sha(head_sha)}")

            # Stage only keuka/
            repo_keuka = os.path.join(repo_dir, "keuka")
            if not os.path.isdir(repo_keuka):
                self._log("ERROR: 'keuka/' folder not found in cloned repo.")
                self._finish(False)
                return

            staged_keuka = os.path.join(stage_dir, "keuka")
            self._log("Staging latest keuka/ code...")
            shutil.copytree(repo_keuka, staged_keuka, dirs_exist_ok=True)

            if self._check_cancel():
                self._log("Canceled before apply.")
                self._finish(False)
                return

            # Execute the replacement script via CLI args (no env reliance)
            self._log("Executing replacement script...")
            cmd = [
                UPDATE_SCRIPT,
                "--stage", stage_dir,
                "--root", APP_ROOT,
                "--service", SERVICE_NAME,
                "--commit", head_sha,   # pass the exact commit we deploy
            ]
            if SUDO.strip():
                cmd = [SUDO, "-n", "--preserve-env=STAGE_DIR,APP_ROOT,SERVICE_NAME"] + cmd

            rc, out = self._run_cmd(cmd, cwd=APP_ROOT, env={
                **os.environ,
                "STAGE_DIR": stage_dir,
                "APP_ROOT": APP_ROOT,
                "SERVICE_NAME": SERVICE_NAME,
            })
            self._log(out)
            if rc != 0:
                self._log("ERROR: update script returned a non-zero code.")
                self._finish(False)
                return

            # Version context after update (apply may still be finishing if detached)
            local_after = get_local_commit(APP_ROOT)
            self._log(f"Local commit after: {short_sha(local_after)}")
            self._log("Update completed successfully.")
            ok = True
        except Exception as e:
            self._log(f"ERROR: Unhandled exception: {e}")
            ok = False
        finally:
            try:
                if tmpdir and os.path.isdir(tmpdir):
                    shutil.rmtree(tmpdir, ignore_errors=True)
                    self._log("Cleaned up temporary files.")
            except Exception as e:
                self._log(f"WARNING: temp cleanup failed: {e}")
            self._finish(ok)

    def _run_cmd(self, cmd: List[str], cwd: Optional[str] = None, env: Optional[dict] = None) -> Tuple[int, str]:
        try:
            p = subprocess.Popen(
                cmd, cwd=cwd, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            output_lines: List[str] = []
            while True:
                line = p.stdout.readline() if p.stdout else ""
                if not line and p.poll() is not None:
                    break
                if line:
                    line = line.rstrip("\n")
                    output_lines.append(line)
                    self._log(line)
                time.sleep(0.01)
            rc = p.wait()
            return rc, "\n".join(output_lines)
        except FileNotFoundError:
            return 127, f"Command not found: {cmd[0]}"
        except Exception as e:
            return 1, f"Command failed: {e}"


# Singleton instance used by routes
updater = UpdateManager()

# Public constants for routes
__all__ = ["updater", "REPO_URL", "APP_ROOT", "SERVICE_NAME", "LOG_FILE"]
