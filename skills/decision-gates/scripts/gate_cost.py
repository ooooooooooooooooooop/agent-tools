#!/usr/bin/env python3
"""Gate 3: Cost reconciliation (zero-token, pure data logic).

Compare the authorized model tier and token budget recorded in a dispatch
order against the actual tier/tokens consumed by the worker, and mark cost
deviations. Run on every worker return. Callers keep a rolling count of
COST_DEVIATION results and must pause for routing review after 3 in a row.

Input: one JSON file with schema:
{
  "dispatch_id": "d-004",
  "authorized": {"tier": "luna", "model": "gpt-5.6-luna-max", "budget_tokens": 5000},
  "actual": {"tier": "sol", "model": "gpt-5.6-sol-max", "tokens": 12000}
}

Budget tolerance: actual tokens must be <= budget_tokens * (1 + tolerance),
where tolerance defaults to 0.2 (20%).

Exit code: 0 when verdict is OK, 1 when verdict is COST_DEVIATION or input error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict


def load_input(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read input JSON: {exc}")
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    return data


def reconcile(data: Dict[str, Any], tolerance: float) -> Dict[str, Any]:
    authorized = data.get("authorized")
    actual = data.get("actual")
    if not isinstance(authorized, dict) or not isinstance(actual, dict):
        raise ValueError("input must contain authorized and actual objects")

    deviations: list[str] = []
    authorized_tier = str(authorized.get("tier") or "")
    actual_tier = str(actual.get("tier") or "")
    if authorized_tier and actual_tier and authorized_tier != actual_tier:
        deviations.append(f"tier drift: authorized={authorized_tier} actual={actual_tier}")

    authorized_model = str(authorized.get("model") or "")
    actual_model = str(actual.get("model") or "")
    if authorized_model and actual_model and authorized_model != actual_model:
        deviations.append(f"model drift: authorized={authorized_model} actual={actual_model}")

    budget = authorized.get("budget_tokens")
    consumed = actual.get("tokens")
    if isinstance(budget, (int, float)) and isinstance(consumed, (int, float)):
        cap = budget * (1.0 + tolerance)
        if consumed > cap:
            deviations.append(
                f"token overrun: budget={budget} cap={cap:.0f} actual={consumed}"
            )

    verdict = "COST_DEVIATION" if deviations else "OK"
    return {
        "dispatch_id": data.get("dispatch_id"),
        "verdict": verdict,
        "deviations": deviations,
        "tolerance": tolerance,
        "rule": "actual tier/model must match authorized; actual tokens must stay within budget * (1 + tolerance)",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="path to the dispatch/actual JSON")
    parser.add_argument("--output", type=Path, help="write the report to this path")
    parser.add_argument("--tolerance", type=float, default=0.2, help="budget overrun tolerance (default 0.2)")
    args = parser.parse_args()

    try:
        data = load_input(args.input)
        report = reconcile(data, args.tolerance)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    return 1 if report["verdict"] == "COST_DEVIATION" else 0


if __name__ == "__main__":
    sys.exit(main())
