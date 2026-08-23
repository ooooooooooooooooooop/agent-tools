#!/usr/bin/env python3
"""Gate Deadband: Trajectory fingerprint and loop detection script.

Zero-token algorithm gate. Analyzes recent tool-call and error trajectories.
Trips a circuit-breaker if identical failure signatures occur consecutively
(default 2-strike threshold), forcing hypothesis invalidation and pivot.

Input: JSON payload containing:
  - trajectory: list of action/event dicts, each with:
      - tool: name of the tool called (e.g. edit, write, pwsh)
      - target: file or command target (optional)
      - exit_code: integer or null
      - error_signature: short error string or fingerprint
  - max_consecutive_failures: optional integer (default: 2)

Exit codes:
  0: PASS (no deadband loop detected)
  1: DEADBAND_TRIPPED (consecutive loop detected, must pivot)
  2: BAD_INPUT (malformed JSON or invalid schema)
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def compute_signature(step: dict) -> str:
    tool = str(step.get("tool", "")).strip().lower()
    target = str(step.get("target", "")).strip().lower()
    err = str(step.get("error_signature", "")).strip()
    exit_code = str(step.get("exit_code", ""))
    
    raw = f"{tool}:{target}:{exit_code}:{err}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def detect_deadband(data: dict) -> tuple[int, dict]:
    trajectory = data.get("trajectory", [])
    max_fails = int(data.get("max_consecutive_failures", 2))

    if not isinstance(trajectory, list):
        return 2, {"verdict": "BAD_INPUT", "error": "trajectory must be a list"}

    if len(trajectory) < max_fails:
        return 0, {"verdict": "PASS", "message": "Trajectory shorter than threshold"}

    # Extract consecutive failure signatures
    consecutive_signatures: list[str] = []
    consecutive_steps: list[dict] = []

    for step in trajectory:
        is_failure = step.get("exit_code", 0) not in (0, None) or bool(step.get("error_signature"))
        if is_failure:
            sig = compute_signature(step)
            if consecutive_signatures and consecutive_signatures[-1] == sig:
                consecutive_signatures.append(sig)
                consecutive_steps.append(step)
            else:
                consecutive_signatures = [sig]
                consecutive_steps = [step]
        else:
            consecutive_signatures.clear()
            consecutive_steps.clear()

        if len(consecutive_signatures) >= max_fails:
            return 1, {
                "verdict": "DEADBAND_TRIPPED",
                "loop_count": len(consecutive_signatures),
                "threshold": max_fails,
                "signature": consecutive_signatures[0],
                "tripped_step": step,
                "action_required": "PIVOT_HYPOTHESIS",
                "guidance": "检测到针对同一目标的相同失败已连续发生 >= 2 次。当前技术假设已证伪，禁止盲目微调，必须退回上一断点重新制定方案！",
            }

    return 0, {
        "verdict": "PASS",
        "message": "No looping failure detected in trajectory",
        "total_steps": len(trajectory),
    }


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"verdict": "BAD_INPUT", "error": "Usage: gate_deadband.py <trajectory.json>"}))
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

    code, output = detect_deadband(data)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
