#!/usr/bin/env python3
"""Run portable smoke checks for all four output profiles."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from lint_response import PROFILE_CONFIG, validate_text


def fixture(profile: str) -> str:
    config = PROFILE_CONFIG[profile]
    sections = [f"## {index}. {heading}" for index, heading in enumerate(config["headings"], 1)]
    roster = (
        "- Decision frame: choose a bounded path.\n"
        "- Execution mode: one-shot.\n"
        "- Context basis: public work and stated assumptions.\n"
        "- Real Person A: relevant public practitioner.\n"
        "- Domain Expert Archetype: accountable implementer.\n"
        "- Roster score: 6/6.\n"
        "- Roster diversity: 6/6.\n"
    )
    if profile != "micro":
        roster += "- Real Person B: contrasting public practitioner.\n- Omniscient Agent Archetype: system-risk analyst.\n"
    body = [sections[0], roster]
    for index, heading in enumerate(config["rounds"], 1):
        body.append(f"## {index + 1}. {heading}")
        for role in range(config["roles"]):
            confidence = " [confidence: high]" if role < config["confidence"] else ""
            body.append(f"- [Role {role + 1}] A bounded position with simulated viewpoints from public work.{confidence}")
        if index <= config["snapshots"]:
            body.append("- Uncertainty snapshot: assumption recorded and evidence needed next.")
    synth_index = len(config["rounds"]) + 2
    body.extend([
        f"## {synth_index}. Moderator Synthesis",
        "- Final recommendation: choose the bounded option.",
        f"## {synth_index + 1}. Uncertainty Ledger",
        "- Facts: the prompt and public work anchors.",
        "- Assumptions: local context is incomplete.",
        "- Speculation: the option may scale.",
        "### Post-Use Self-Check",
        "1. What was your initial view?",
        "2. Which argument changed it?",
        "3. Which assumption matters most?",
        "4. What evidence comes next?",
        "5. What would you decide now?",
    ])
    return "\n\n".join(body) + "\n"


def main() -> int:
    results = []
    with tempfile.TemporaryDirectory(prefix="elite-experts-smoke-") as raw:
        path = Path(raw) / "fixture.md"
        for profile in PROFILE_CONFIG:
            path.write_text(fixture(profile), encoding="utf-8")
            errors = validate_text(path.read_text(encoding="utf-8"), profile)
            results.append({"profile": profile, "pass": not errors, "errors": errors})
    print(json.dumps({"pass": all(item["pass"] for item in results), "results": results}, ensure_ascii=False, indent=2))
    return 0 if all(item["pass"] for item in results) else 1


if __name__ == "__main__":
    sys.exit(main())
