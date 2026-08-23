#!/usr/bin/env python3
"""Gate 4: Defense self-check (zero-token, pure data logic).

Scan every raw evidence anchor under a checkpoints directory and count how
often the recorded alignment_decision is more optimistic than the worst
signal in that anchor's evidence (summary_bias). DEFENSE_DRIFT is declared
when the bias count reaches a threshold (default 2). No LLM involved.

Input: a directory containing raw evidence anchor JSON files, each matching
templates/raw_evidence_anchors.json at least on these fields:
  checkpoint_id (str)
  alignment_decision ("PASS" | "PARTIAL" | "FAIL")
  evidence[] with entries carrying either {"type":"command_output",
  "exit_code": int} or a "conclusion" field of
  "PASS"/"SKIP"/"WARN"/"PENDING"/"FAIL"

Bias rule: an anchor whose alignment_decision is PASS while any evidence
entry carries a non-zero exit code, or a conclusion of
SKIP/WARN/PENDING/FAIL, is one summary_bias count.

Exit code: 0 when verdict is PASS, 1 when verdict is DEFENSE_DRIFT or input error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

NONZERO_MARKERS = {"SKIP", "WARN", "PENDING", "FAIL"}
NONZERO_CONCLUSIONS = {"SKIP", "WARN", "PENDING", "FAIL"}


def worst_signal(evidence: List[Any]) -> str:
    """Return the worst signal found in an anchor's evidence list."""
    worst = "PASS"
    for entry in evidence:
        if not isinstance(entry, dict):
            continue
        entry_type = entry.get("type")
        if entry_type == "command_output" and isinstance(entry.get("exit_code"), int) and entry["exit_code"] != 0:
            worst = "FAIL"
            break
        conclusion = entry.get("conclusion")
        if isinstance(conclusion, str) and conclusion in NONZERO_CONCLUSIONS:
            if conclusion == "FAIL":
                worst = "FAIL"
                break
            if worst != "FAIL":
                worst = conclusion
    return worst


def load_anchors(directory: Path) -> List[Dict[str, Any]]:
    anchors: List[Dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read anchor {path.name}: {exc}")
        if isinstance(data, dict):
            data["_path"] = path.name
            anchors.append(data)
    return anchors


def self_check(directory: Path, threshold: int) -> Dict[str, Any]:
    anchors = load_anchors(directory)
    biased: List[Dict[str, Any]] = []
    clean: List[Dict[str, Any]] = []
    for anchor in anchors:
        decision = str(anchor.get("alignment_decision") or "")
        evidence = anchor.get("evidence")
        if not isinstance(evidence, list):
            evidence = []
        signal = worst_signal(evidence)
        is_biased = decision == "PASS" and signal != "PASS"
        entry = {
            "checkpoint_id": anchor.get("checkpoint_id"),
            "path": anchor.get("_path"),
            "alignment_decision": decision,
            "worst_evidence_signal": signal,
            "summary_bias": is_biased,
        }
        (biased if is_biased else clean).append(entry)

    verdict = "DEFENSE_DRIFT" if len(biased) >= threshold else "PASS"
    return {
        "checkpoint_dir": str(directory),
        "verdict": verdict,
        "anchors_scanned": len(anchors),
        "summary_bias_count": len(biased),
        "threshold": threshold,
        "biased_anchors": biased,
        "rule": "PASS decision with non-zero exit code or SKIP/WARN/PENDING/FAIL evidence is summary bias",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="checkpoints directory holding raw evidence anchors")
    parser.add_argument("--output", type=Path, help="write the report to this path")
    parser.add_argument("--threshold", type=int, default=2, help="bias count that triggers DEFENSE_DRIFT (default 2)")
    args = parser.parse_args()

    if not args.directory.is_dir():
        print(f"ERROR: not a directory: {args.directory}", file=sys.stderr)
        return 1

    try:
        report = self_check(args.directory, args.threshold)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    return 1 if report["verdict"] == "DEFENSE_DRIFT" else 0


if __name__ == "__main__":
    sys.exit(main())
