# keuka/version.py
# -----------------
# Helpers to identify the local (running) commit and the latest remote commit.
# Local version is resolved in this order:
#   1) <APP_ROOT>/keuka/.keuka_commit
#   2) <APP_ROOT>/.keuka_commit
#   3) git rev-parse HEAD at <APP_ROOT>
# The function also returns which source was used so the UI can display it.

from __future__ import annotations

import os
import subprocess
from typing import Optional, Tuple


def _run(cmd, cwd=None, env=None) -> Tuple[int, str]:
    try:
        out = subprocess.check_output(cmd, cwd=cwd, env=env, stderr=subprocess.STDOUT, text=True)
        return 0, out.strip()
    except subprocess.CalledProcessError as e:
        return e.returncode, (e.output or "").strip()
    except FileNotFoundError:
        return 127, "command not found"
    except Exception as e:
        return 1, str(e)


def _read_file(path: str) -> Optional[str]:
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                v = f.read().strip()
                if v:
                    return v
    except Exception:
        pass
    return None


def get_local_commit_with_source(app_root: str) -> Tuple[Optional[str], str]:
    """
    Returns (sha, source), where source in {"marker-keuka","marker-root","git","none"}.
    """
    # 1) marker inside keuka/
    m1 = os.path.join(app_root, "keuka", ".keuka_commit")
    sha = _read_file(m1)
    if sha:
        return sha, "marker-keuka"

    # 2) marker at repo root
    m2 = os.path.join(app_root, ".keuka_commit")
    sha = _read_file(m2)
    if sha:
        return sha, "marker-root"

    # 3) git HEAD
    rc, out = _run(["git", "rev-parse", "HEAD"], cwd=app_root)
    if rc == 0 and out and len(out) >= 7:
        return out.strip(), "git"

    # final fallback
    return None, "none"


def get_local_commit(app_root: str) -> Optional[str]:
    sha, _ = get_local_commit_with_source(app_root)
    return sha


def get_remote_commit(repo_url: str) -> Optional[str]:
    """
    Returns the full 40-char SHA of the remote's default HEAD.
    Uses: git ls-remote <url> HEAD
    """
    rc, out = _run(["git", "ls-remote", repo_url, "HEAD"])
    # Output looks like: "<sha>\tHEAD"
    if rc == 0 and out:
        line = out.splitlines()[0].strip()
        if line:
            sha = line.split()[0]
            if len(sha) >= 7:
                return sha
    return None


def short_sha(sha: Optional[str]) -> str:
    return (sha[:7] if sha else "-")
