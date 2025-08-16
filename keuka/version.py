# keuka/version.py
# -----------------
# Helpers for local/remote commit reporting and pretty printing.

from __future__ import annotations

import os
import subprocess
from typing import Optional, Tuple

def _read_file(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            s = f.read().strip()
            return s if s else None
    except Exception:
        return None

def short_sha(sha: Optional[str]) -> str:
    return (sha or "")[:7] if sha else "unknown"

def get_remote_commit(repo_url: str) -> Optional[str]:
    """Fetch remote HEAD SHA without cloning the whole repo."""
    try:
        out = subprocess.check_output(
            ["git", "ls-remote", repo_url, "HEAD"],
            stderr=subprocess.STDOUT,
            text=True,
        ).strip()
        if not out:
            return None
        sha = out.split()[0]
        return sha if sha else None
    except Exception:
        return None

def get_local_commit(app_root: str) -> Optional[str]:
    """
    Returns the *effective* local commit for UI comparison.
    Priority:
      1) pending marker (.keuka_commit.next) if present -> treat as in-progress deploy target
      2) final marker in keuka/.keuka_commit
      3) final marker in .keuka_commit
      4) git HEAD of the worktree at app_root
    """
    pending = _read_file(os.path.join(app_root, ".keuka_commit.next"))
    if pending:
        return pending

    mk_keuka = _read_file(os.path.join(app_root, "keuka", ".keuka_commit"))
    if mk_keuka:
        return mk_keuka

    mk_root = _read_file(os.path.join(app_root, ".keuka_commit"))
    if mk_root:
        return mk_root

    try:
        out = subprocess.check_output(
            ["git", "-C", app_root, "rev-parse", "HEAD"],
            stderr=subprocess.STDOUT,
            text=True,
        ).strip()
        return out if out else None
    except Exception:
        return None

def get_local_commit_with_source(app_root: str) -> Tuple[Optional[str], str]:
    """Like get_local_commit but also returns a source tag for UI display."""
    p = os.path.join(app_root, ".keuka_commit.next")
    v = _read_file(p)
    if v:
        return v, "marker-pending"

    p = os.path.join(app_root, "keuka", ".keuka_commit")
    v = _read_file(p)
    if v:
        return v, "marker-keuka"

    p = os.path.join(app_root, ".keuka_commit")
    v = _read_file(p)
    if v:
        return v, "marker-root"

    try:
        out = subprocess.check_output(
            ["git", "-C", app_root, "rev-parse", "HEAD"],
            stderr=subprocess.STDOUT,
            text=True,
        ).strip()
        return (out if out else None), "git"
    except Exception:
        return None, "unknown"
