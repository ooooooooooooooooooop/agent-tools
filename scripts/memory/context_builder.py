#!/usr/bin/env python3
"""Context Builder — task-aware retrieval + Context Package (V2.1 §D.3).

No fixed counts: memories are ranked by derived score and packed under a
character budget; omitted items are counted and reported. Every included
memory carries provenance. Subagents never query the provider directly;
the main agent injects a package subset honoring access_policy.inject.

Usage:
  python scripts/memory/context_builder.py --state-root <personal-ai-state> \
      --project-root <project repo> --project <name> --task "..." [--budget 4000]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from provider import FileMemoryProvider  # noqa: E402

STATE_SECTIONS = ("goal", "current_state", "next_actions")


def _extract_project_state(project_root: Path) -> dict:
    state_file = project_root / ".ai" / "state" / "state.md"
    if not state_file.is_file():
        return {"available": False}
    text = state_file.read_text(encoding="utf-8-sig")
    out: dict = {"available": True, "source": ".ai/state/state.md"}
    for name in STATE_SECTIONS:
        m = re.search(rf"^##\s+{name}\b.*?\n(.*?)(?=^##\s|\Z)", text,
                      re.IGNORECASE | re.MULTILINE | re.DOTALL)
        if m:
            out[name] = m.group(1).strip()
    return out


def build_context_package(state_root: str | Path, project: str | None,
                          task: str, budget_chars: int = 4000,
                          project_root: str | Path | None = None,
                          device_id: str = "context-builder") -> dict:
    prov = FileMemoryProvider(state_root, device_id=device_id)
    scopes = [f"project:{project}"] if project else []
    scopes += ["personal", "global"]

    package: dict = {"task": task, "budget_chars": budget_chars,
                     "project": project, "memories": [], "omitted": 0}
    used = 0
    if project:
        ps = _extract_project_state(Path(project_root or ""))
        package["project_state"] = ps
        used += len(json.dumps(ps, ensure_ascii=False))

    for scope in scopes:  # scope chain: project first, then personal/global
        hits = prov.search(task, scope=scope)
        for h in hits:
            blob = json.dumps({"content": h["content"]}, ensure_ascii=False)
            if used + len(blob) > budget_chars:
                package["omitted"] += 1
                continue
            package["memories"].append({
                "id": h["id"], "scope": h["scope"], "type": h["type"],
                "confidence": h["confidence"], "provenance": h["provenance"],
                "content": h["content"], "derived_score": h["derived_score"]})
            used += len(blob)
    package["used_chars"] = used
    return package


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state-root", required=True)
    ap.add_argument("--project-root")
    ap.add_argument("--project")
    ap.add_argument("--task", required=True)
    ap.add_argument("--budget", type=int, default=4000)
    args = ap.parse_args()
    pkg = build_context_package(args.state_root, args.project, args.task,
                                args.budget)
    print(json.dumps(pkg, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
