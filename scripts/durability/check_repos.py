#!/usr/bin/env python3
"""check_repos.py — durability risk scan for git repositories.

Detects UNPUSHED_DURABILITY_RISK (unpushed commits / dirty tree). Never commits,
never pushes — detection only; fixing belongs to the user.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import device_config, ledger_append, now_iso  # noqa: E402


def git(repo: Path, *args: str) -> tuple[int, str]:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30)
    return p.returncode, (p.stdout + p.stderr).strip()


def main() -> int:
    started = now_iso()
    risks = 0
    for repo in device_config()["repos"]:
        r = Path(repo)
        row = {"job": "check_repos", "dataset": f"repos:{r.name}", "started_at": started,
               "finished_at": now_iso()}
        if not (r / ".git").exists():
            ledger_append({**row, "status": "error", "error": "not a git repo"})
            risks += 1
            continue
        _, dirty = git(r, "status", "--porcelain")
        rc, unpushed = git(r, "rev-list", "--count", "@{u}..HEAD")
        n_unpushed = int(unpushed) if rc == 0 and unpushed.strip().isdigit() else -1
        risk = bool(dirty) or n_unpushed != 0
        risks += int(risk)
        ledger_append({**row,
                       "status": "UNPUSHED_DURABILITY_RISK" if risk else "ok",
                       "unpushed_commits": n_unpushed, "dirty": bool(dirty)})
        print(f"{r.name}: unpushed={n_unpushed} dirty={bool(dirty)} "
              f"{'UNPUSHED_DURABILITY_RISK' if risk else 'ok'}")
    return 0 if risks == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
