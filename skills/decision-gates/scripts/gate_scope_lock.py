#!/usr/bin/env python3
"""Gate Scope Lock: Physical scope and boundary verification script.

Zero-token algorithm gate. Verifies that changed files stay strictly within
allowed patterns and touch no forbidden/non-goal paths.

Input: JSON payload containing:
  - allowed_patterns: list of glob patterns / path prefixes allowed for modification
  - forbidden_patterns: list of glob patterns / paths explicitly prohibited
  - changed_files: list of relative file paths modified by worker/action

Exit codes:
  0: PASS (all changed files are permitted and none are forbidden)
  1: SCOPE_VIOLATION (one or more changed files violated boundary)
  2: BAD_INPUT (malformed JSON or missing fields)
"""

from __future__ import annotations

import fnmatch
import json
import os
import sys
from pathlib import Path


def normalize_path(path_str: str) -> str:
    return Path(path_str).as_posix().strip("/")


def match_any_pattern(path: str, patterns: list[str]) -> bool:
    norm_path = normalize_path(path)
    for pat in patterns:
        norm_pat = Path(pat).as_posix().strip("/")
        if fnmatch.fnmatch(norm_path, norm_pat) or fnmatch.fnmatch(norm_path, f"{norm_pat}/*"):
            return True
        if norm_path == norm_pat or norm_path.startswith(f"{norm_pat}/"):
            return True
    return False


def verify_scope(data: dict) -> tuple[int, dict]:
    allowed = data.get("allowed_patterns", [])
    forbidden = data.get("forbidden_patterns", [])
    changed = data.get("changed_files", [])

    if not isinstance(allowed, list) or not isinstance(changed, list):
        return 2, {"verdict": "BAD_INPUT", "error": "allowed_patterns and changed_files must be lists"}

    violations: list[dict] = []

    for file_path in changed:
        norm_file = normalize_path(file_path)
        
        # 1. Check forbidden match
        if forbidden and match_any_pattern(norm_file, forbidden):
            violations.append({
                "path": file_path,
                "reason": "MATCHES_FORBIDDEN_PATTERN",
            })
            continue

        # 2. Check allowed match (if allowed list is provided and non-empty)
        if allowed and not match_any_pattern(norm_file, allowed):
            violations.append({
                "path": file_path,
                "reason": "OUTSIDE_ALLOWED_SCOPE",
            })

    if violations:
        return 1, {
            "verdict": "SCOPE_VIOLATION",
            "violation_count": len(violations),
            "violations": violations,
        }

    return 0, {
        "verdict": "PASS",
        "verified_files_count": len(changed),
        "changed_files": changed,
    }


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"verdict": "BAD_INPUT", "error": "Usage: gate_scope_lock.py <scope_payload.json>"}))
        return 2

    target_file = Path(sys.argv[1])
    if not target_file.exists():
        print(json.dumps({"verdict": "BAD_INPUT", "error": f"File not found: {target_file}"}))
        return 2

    try:
        data = json.loads(target_file.read_text(encoding="utf-8"))
    except Exception as exc:
        print(json.dumps({"verdict": "BAD_INPUT", "error": f"Invalid JSON: {exc}"}))
        return 2

    code, output = verify_scope(data)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
