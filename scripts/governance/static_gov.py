#!/usr/bin/env python3
"""static_gov.py — static instruction boundary governance.

Static files must never absorb dynamic context (goals, project state, memories,
context packages). Regression: any new dynamic-signature line added to a static
instruction file is detected as STATIC_CONTEXT_BOUNDARY_VIOLATION.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import HOME, REPO, gov_log  # noqa: E402

FILES = [
    HOME / ".dsh" / "AGENTS.md",
    HOME / ".codex" / "AGENTS.md",
    HOME / ".claude" / "CLAUDE.md",
    HOME / ".gemini" / "GEMINI.md",
    HOME / ".codex" / "AGENT_GROUND_RULES.md",
    REPO / "AGENTS.md",
]

# dynamic-context signatures (present tense state, not policy)
DYNAMIC_RE = re.compile(
    r"(active goal|当前目标|当前任务|current task|next_actions|下一步行动|"
    r"unresolved|未解决|context package|上下文包|memory record|episodic|"
    r"last_verified|goal_id|project state\s*[:=])", re.IGNORECASE)


def scan_file(path: Path) -> list[dict]:
    hits = []
    if not path.is_file():
        return hits
    for i, line in enumerate(path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1):
        if DYNAMIC_RE.search(line):
            hits.append({"file": str(path), "line": i, "text": line.strip()[:120]})
    return hits


def main() -> int:
    violations = []
    for f in FILES:
        violations.extend(scan_file(f))
    for v in violations:
        print(f"STATIC_CONTEXT_BOUNDARY_VIOLATION: {v['file']}:{v['line']} {v['text']}")
    gov_log("static_gov", "ok" if not violations else "findings", violations)
    print(f"static files scanned={sum(1 for f in FILES if f.is_file())} "
          f"violations={len(violations)}")
    return 0 if not violations else 1


if __name__ == "__main__":
    sys.exit(main())
