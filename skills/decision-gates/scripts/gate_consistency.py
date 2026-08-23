#!/usr/bin/env python3
"""Gate 2: Cross-package consistency check (zero-token, pure data logic).

Verify that multiple worker reports from one parallel phase agree on file
coverage and do not conflict on modified file hashes. Run this BEFORE
accepting any worker's result when >= 2 workers ran in parallel.

Input: one JSON file with schema:
{
  "phase_id": "p-003",
  "target_files": ["src/a.py", "src/b.py", "src/legacy/c.py"],
  "workers": [
    {
      "worker_id": "w1",
      "files": [
        {"path": "src/a.py", "conclusion": "PASS", "sha256_after": "aa11"},
        {"path": "src/b.py", "conclusion": "PASS", "sha256_after": "bb22"}
      ]
    },
    {
      "worker_id": "w2",
      "files": [
        {"path": "src/b.py", "conclusion": "PASS", "sha256_after": "bb22"},
        {"path": "src/legacy/c.py", "conclusion": "SKIP", "reason": "ambiguous pattern", "sha256_after": null}
      ]
    }
  ]
}

Output: a JSON consistency report with verdict CONFLICT / WARN / PASS.

Exit code: 0 for PASS or WARN, 1 for CONFLICT or input error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


def load_input(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read input JSON: {exc}")
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    return data


def collect_files(workers: List[Any]) -> Dict[str, List[Tuple[str, Dict[str, Any]]]]:
    """Map path -> list of (worker_id, file_entry)."""
    mapping: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    for worker in workers:
        if not isinstance(worker, dict):
            continue
        worker_id = str(worker.get("worker_id", "?"))
        for entry in worker.get("files", []):
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            if isinstance(path, str) and path:
                mapping.setdefault(path, []).append((worker_id, entry))
    return mapping


def check_consistency(data: Dict[str, Any]) -> Dict[str, Any]:
    workers = data.get("workers")
    if not isinstance(workers, list) or not workers:
        raise ValueError("input must contain a non-empty workers array")

    by_path = collect_files(workers)
    target_files = data.get("target_files")
    targets: List[str] = []
    if isinstance(target_files, list):
        targets = [str(item) for item in target_files if isinstance(item, str)]

    conflicts: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    # 1. Overlap: same file concluded differently or hash differs across workers.
    for path, owners in sorted(by_path.items()):
        if len(owners) < 2:
            continue
        conclusions = {entry.get("conclusion") for _, entry in owners}
        hashes = {entry.get("sha256_after") for _, entry in owners if entry.get("sha256_after")}
        if len(conclusions) > 1:
            conflicts.append({
                "type": "overlap_conclusion_conflict",
                "path": path,
                "workers": [wid for wid, _ in owners],
                "conclusions": sorted(str(item) for item in conclusions if item is not None),
            })
        elif len(hashes) > 1:
            conflicts.append({
                "type": "overlap_hash_conflict",
                "path": path,
                "workers": [wid for wid, _ in owners],
                "hashes": sorted(str(item) for item in hashes),
            })
        elif len(owners) >= 2:
            warnings.append({
                "type": "overlap_duplicate",
                "path": path,
                "workers": [wid for wid, _ in owners],
            })

    # 2. Gaps: target files never covered by any worker.
    covered = set(by_path.keys())
    if targets:
        for target in sorted(set(targets) - covered):
            warnings.append({"type": "coverage_gap", "path": target})

    # 3. Skipped items must be surfaced, never silently folded into PASS.
    for worker in workers:
        if not isinstance(worker, dict):
            continue
        worker_id = str(worker.get("worker_id", "?"))
        for entry in worker.get("files", []):
            if not isinstance(entry, dict):
                continue
            if entry.get("conclusion") in ("SKIP", "WARN", "PENDING"):
                warnings.append({
                    "type": "unresolved_marker",
                    "worker_id": worker_id,
                    "path": entry.get("path"),
                    "conclusion": entry.get("conclusion"),
                    "reason": entry.get("reason"),
                })

    if conflicts:
        verdict = "CONFLICT"
    elif warnings:
        verdict = "WARN"
    else:
        verdict = "PASS"

    return {
        "phase_id": data.get("phase_id"),
        "verdict": verdict,
        "conflicts": conflicts,
        "warnings": warnings,
        "rule": "overlap conclusions/hashes must agree; every target file must be covered; SKIP/WARN/PENDING must be surfaced",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="path to the workers JSON report")
    parser.add_argument("--output", type=Path, help="write the report to this path")
    args = parser.parse_args()

    try:
        data = load_input(args.input)
        report = check_consistency(data)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    return 1 if report["verdict"] == "CONFLICT" else 0


if __name__ == "__main__":
    sys.exit(main())
