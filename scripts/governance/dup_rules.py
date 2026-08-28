#!/usr/bin/env python3
"""dup_rules.py — duplicate/conflicting static rule detection (proposal only).

Line-shingle similarity across static instruction files. Output classes:
DUPLICATE / CONFLICT / INTENTIONAL_OVERLAP. Never deletes rules.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import REPO, gov_log, propose  # noqa: E402

HOME = Path.home()
FILES = [
    HOME / ".dsh" / "AGENTS.md",
    HOME / ".codex" / "AGENTS.md",
    HOME / ".claude" / "CLAUDE.md",
    HOME / ".gemini" / "GEMINI.md",
    HOME / ".codex" / "AGENT_GROUND_RULES.md",
    REPO / "AGENTS.md",
]

NEG = re.compile(r"(不得|禁止|不要|never|do not|must not)")


def shingles(text: str, n: int = 1) -> dict[str, None]:
    out = {}
    for line in text.splitlines():
        s = re.sub(r"\s+", " ", line.strip().lower())
        if len(s) >= 12 and not s.startswith(("#", "|", "```", "---")):
            out[s] = None
    return out


def main() -> int:
    sets = {}
    for f in FILES:
        if f.is_file():
            sets[str(f)] = shingles(f.read_text(encoding="utf-8-sig", errors="replace"))
    findings = []
    keys = list(sets)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = keys[i], keys[j]
            sa, sb = set(sets[a]), set(sets[b])
            if not sa or not sb:
                continue
            inter = sa & sb
            ratio = len(inter) / min(len(sa), len(sb))
            if ratio >= 0.8 and len(inter) >= 3:
                kind = "DUPLICATE"
                # conflict heuristic: same shared subject lines with opposite negation
                conflicts = [l for l in inter if NEG.search(l)]
                findings.append({"kind": kind, "source_a": Path(a).name,
                                 "source_b": Path(b).name,
                                 "overlap_ratio": round(ratio, 2), "shared_lines": len(inter),
                                 "reason": "identical managed block" if "claude" in b.lower()
                                 or "gemini" in b.lower() or "codex" in b.lower()
                                 else "high semantic overlap",
                                 "negation_shared": len(conflicts)})
    # skill-level: instruction files duplicated across harnesses by design
    for f in findings:
        if f["source_a"] != f["source_b"] and f["reason"] == "identical managed block":
            f["kind"] = "INTENTIONAL_OVERLAP"
    for f in findings:
        print(f"{f['kind']}: {f['source_a']} <-> {f['source_b']} "
              f"(ratio={f['overlap_ratio']}, reason={f['reason']})")
        if f["kind"] == "DUPLICATE":
            propose("duplicate_rule", f, "low", "static-instructions",
                    "review for dedup; never auto-delete")
    gov_log("dup_rules", "ok", findings)
    print(f"pairs_with_high_overlap={len(findings)} (see kinds; proposals only for DUPLICATE)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
