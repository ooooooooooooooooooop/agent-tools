#!/usr/bin/env python3
"""project_state_gov.py — governance over real project .ai/state/ files.

Schema check + freshness classification: ACTIVE / PAUSED / STALE_REVIEW /
ARCHIVED / UNKNOWN. Inactive projects are NOT errors — PAUSED is a known state.
"""
from __future__ import annotations

import datetime as dt
import subprocess
import sys
from pathlib import Path

import os

sys.path.insert(0, str(Path(__file__).parent))
from common import gov_log  # noqa: E402


def _projects() -> list[Path]:
    """Project roots: PAI_PROJECT_ROOTS (os.pathsep-separated) or auto-discover
    ~/Desktop/*/ dirs that contain .ai/state/state.md."""
    env = os.environ.get("PAI_PROJECT_ROOTS")
    if env:
        return [Path(p) for p in env.split(os.pathsep) if p]
    desktop = Path.home() / "Desktop"
    if not desktop.is_dir():
        return []
    return sorted(
        d for d in desktop.iterdir()
        if d.is_dir() and (d / ".ai" / "state" / "state.md").is_file())


PROJECTS = _projects()
REQUIRED = ["goal", "current_state", "architecture", "decisions", "constraints",
            "unresolved", "next_actions"]


def classify(repo: Path, state: Path) -> tuple[str, dict]:
    text = state.read_text(encoding="utf-8-sig", errors="replace").lower()
    info = {}
    if "archived" in text or "已归档" in text:
        return "ARCHIVED", info
    if "paused" in text or "停机" in text or "暂停" in text:
        return "PAUSED", info
    try:
        out = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%cI"],
                             capture_output=True, text=True, timeout=15)
        last = dt.datetime.fromisoformat(out.stdout.strip())
        days = (dt.datetime.now().astimezone() - last).days
        info["days_since_commit"] = days
        if days <= 30:
            return "ACTIVE", info
        if days <= 180:
            return "STALE_REVIEW", info
        return "ARCHIVED", info
    except Exception:  # noqa: BLE001
        return "UNKNOWN", info


def main() -> int:
    findings = []
    for repo in PROJECTS:
        state = repo / ".ai" / "state" / "state.md"
        if not state.is_file():
            findings.append({"project": repo.name, "kind": "state_missing"})
            print(f"{repo.name}: UNKNOWN (no .ai/state/state.md)")
            continue
        text = state.read_text(encoding="utf-8-sig", errors="replace")
        missing = [k for k in REQUIRED if f"## {k}" not in text]
        cls, info = classify(repo, state)
        if missing:
            findings.append({"project": repo.name, "kind": "schema_missing_sections",
                             "sections": missing})
        mtime = dt.datetime.fromtimestamp(state.stat().st_mtime).astimezone()
        info["state_age_days"] = (dt.datetime.now().astimezone() - mtime).days
        print(f"{repo.name}: {cls} missing_sections={missing} "
              f"state_age={info['state_age_days']}d days_since_commit={info.get('days_since_commit')}")
    gov_log("project_state_gov", "ok" if not findings else "findings", findings)
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
