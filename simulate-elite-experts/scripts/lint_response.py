#!/usr/bin/env python3
"""Portable hard-gate validator for simulate-elite-experts responses."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple


PROFILE_CONFIG: Dict[str, Dict[str, object]] = {
    "micro": {
        "headings": [
            "Good Group To Explore X",
            "Dialogue Round 1: Initial Positions",
            "Dialogue Round 2: Final Statements",
            "Moderator Synthesis",
            "Uncertainty Ledger",
        ],
        "rounds": ["Dialogue Round 1: Initial Positions", "Dialogue Round 2: Final Statements"],
        "roles": 2,
        "snapshots": 1,
        "confidence": 1,
    },
    "lean": {
        "headings": [
            "Good Group To Explore X",
            "Dialogue Round 1: Initial Positions",
            "Dialogue Round 2: Cross-Examination",
            "Dialogue Round 3: Revised Positions",
            "Dialogue Round 4: Final Statements",
            "Moderator Synthesis",
            "Uncertainty Ledger",
        ],
        "rounds": [
            "Dialogue Round 1: Initial Positions",
            "Dialogue Round 2: Cross-Examination",
            "Dialogue Round 3: Revised Positions",
            "Dialogue Round 4: Final Statements",
        ],
        "roles": 4,
        "snapshots": 3,
        "confidence": 2,
    },
    "classic": {
        "headings": [
            "Good Group To Explore X",
            "Dialogue Round 1: Initial Positions",
            "Dialogue Round 2: Cross-Examination",
            "Dialogue Round 3: Revised Positions",
            "Dialogue Round 4: Final Statements",
            "Moderator Synthesis",
            "Uncertainty Ledger",
        ],
        "rounds": [
            "Dialogue Round 1: Initial Positions",
            "Dialogue Round 2: Cross-Examination",
            "Dialogue Round 3: Revised Positions",
            "Dialogue Round 4: Final Statements",
        ],
        "roles": 4,
        "snapshots": 3,
        "confidence": 2,
    },
    "deep": {
        "headings": [
            "Good Group To Explore X",
            "Dialogue Round 1: Initial Positions",
            "Dialogue Round 2: Cross-Examination",
            "Dialogue Round 3: Revised Positions",
            "Dialogue Round 4: Final Statements",
            "Dialogue Round 5: Stress Test",
            "Dialogue Round 6: Contingency Planning",
            "Moderator Synthesis",
            "Uncertainty Ledger",
        ],
        "rounds": [
            "Dialogue Round 1: Initial Positions",
            "Dialogue Round 2: Cross-Examination",
            "Dialogue Round 3: Revised Positions",
            "Dialogue Round 4: Final Statements",
            "Dialogue Round 5: Stress Test",
            "Dialogue Round 6: Contingency Planning",
        ],
        "roles": 4,
        "snapshots": 3,
        "confidence": 2,
    },
}


def section_block(text: str, heading: str) -> str:
    start = re.search(rf"(?m)^##\s*\d+\.\s*{re.escape(heading)}\b.*$", text)
    if not start:
        return ""
    tail = text[start.end() :]
    next_heading = re.search(r"(?m)^##\s*\d+\..+$", tail)
    return tail[: next_heading.start()] if next_heading else tail


def validate_text(text: str, profile: str) -> List[str]:
    config = PROFILE_CONFIG[profile]
    errors: List[str] = []
    headings = list(re.finditer(r"(?m)^##\s*\d+\..+$", text))
    expected = config["headings"]
    if len(headings) != len(expected):
        errors.append(f"Profile '{profile}' expects exactly {len(expected)} numbered top-level headings, found {len(headings)}.")

    positions: List[int] = []
    for number, heading in enumerate(expected, 1):
        match = re.search(rf"(?m)^##\s*\d+\.\s*{re.escape(heading)}\b", text)
        if not match:
            errors.append(f"Missing required section #{number} for profile '{profile}'.")
            continue
        positions.append(match.start())
    if positions != sorted(positions):
        errors.append("Required section order is incorrect.")

    roster = section_block(text, expected[0])
    if len(re.findall(r"(?m)^-\s+", roster)) < (2 if profile == "micro" else 4):
        errors.append("Roster has too few bullet items.")
    for label in ("Decision frame", "Execution mode", "Context basis", "Roster score", "Roster diversity"):
        if not re.search(rf"(?i){re.escape(label)}", roster):
            errors.append(f"Roster is missing required field: {label}.")
    if not re.search(r"(?i)Real Person A|现实人物A", roster):
        errors.append("Roster is missing Real Person A.")
    if not re.search(r"(?i)Domain Expert Archetype|领域专家抽象", roster):
        errors.append("Roster is missing Domain Expert Archetype.")
    if profile != "micro" and not re.search(r"(?i)Real Person B|现实人物B", roster):
        errors.append("Roster is missing Real Person B.")
    if profile != "micro" and not re.search(r"(?i)Omniscient Agent Archetype|全知智能体抽象", roster):
        errors.append("Roster is missing Omniscient Agent Archetype.")

    for index, round_heading in enumerate(config["rounds"], 1):
        block = section_block(text, round_heading)
        turns = len(re.findall(r"(?m)^-\s*`?\[", block))
        if turns != config["roles"]:
            errors.append(f"Round {index} expects exactly {config['roles']} role turns, found {turns}.")
        confidence = len(re.findall(r"(?i)\[confidence:\s*(?:high|medium|low)\]|\[置信度:\s*(?:高|中|低)\]", block))
        if confidence < config["confidence"]:
            errors.append(f"Round {index} expects at least {config['confidence']} confidence tags, found {confidence}.")

    if not re.search(r"(?i)simulated viewpoints|public work|公开信息|模拟推断", text):
        errors.append("Missing explicit simulation boundary marker.")
    total_confidence = len(re.findall(r"(?i)\[confidence:\s*(?:high|medium|low)\]|\[置信度:\s*(?:高|中|低)\]", text))
    if total_confidence < config["confidence"] * len(config["rounds"]):
        errors.append("There are too few confidence tags for real-person turns.")
    self_check = re.search(r"(?im)^#{2,3}\s*(?:Post-Use Self-Check|使用后自检清单).*$", text)
    if not self_check:
        errors.append("Missing mandatory Post-Use Self-Check appendix.")
    elif len(re.findall(r"(?m)^\d+\.\s+", text[self_check.end() :])) != 5:
        errors.append("Post-Use Self-Check expects exactly 5 numbered questions.")
    snapshots = len(re.findall(r"(?im)^-\s*`?(?:Uncertainty snapshot|不确定性快照)", text))
    if snapshots < config["snapshots"]:
        errors.append(f"Expected at least {config['snapshots']} uncertainty snapshots, found {snapshots}.")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--profile", choices=sorted(PROFILE_CONFIG), default="classic")
    args = parser.parse_args()
    try:
        text = args.file.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        errors = validate_text(text, args.profile)
    except (OSError, UnicodeDecodeError) as exc:
        errors = [str(exc)]
    result = {"file": str(args.file), "profile": args.profile, "pass": not errors, "hard_gate_errors": errors}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
