#!/usr/bin/env python3
"""Aggregate the full release gate for this repository.

Runs every check from the publish checklist (docs/publishing.md) in order and
summarizes PASS/FAIL. Exit 0 = all gates passed, 1 = at least one failed.
Standard library only. Intended to be the single command an agent (or human)
runs before publishing: ``python scripts/publish_all.py``.

Individual gates:
  1. validate_repo.py --strict        structure + registries consistent
  2. quality_report.py --root . --strict   skill behavior quality
  3. publish_check.py                  marketplace surface (paths/license/files)
  4. run_skill_evals.py                structural evals
  5. unittest -s tests                 repository regression
  6. unittest -s mcp/agent-switchboard/tests   MCP regression
  7. git diff --check                  whitespace errors

``git status`` dirtiness is reported as a WARNING (not a failure): the meaning
of "only intended changes present" is a human decision.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAIL_LINES = 30
GATE_TIMEOUT = 600

# Each gate: (id, display name, argv list, cwd)
GATES = [
    ("validate_repo", ["python", "scripts/validate_repo.py", "--strict"], ROOT),
    ("quality_report", ["python", "skills/skill-quality-gate/scripts/quality_report.py", "--root", ".", "--strict"], ROOT),
    ("publish_check", ["python", "scripts/publish_check.py"], ROOT),
    ("run_skill_evals", ["python", "scripts/run_skill_evals.py"], ROOT),
    ("tests", ["python", "-m", "unittest", "discover", "-s", "tests", "-v"], ROOT),
    ("mcp_tests", ["python", "-m", "unittest", "discover", "-s", "mcp/agent-switchboard/tests", "-v"], ROOT),
    ("diff_check", ["git", "diff", "--check"], ROOT),
]


def run_gate(gate_id: str, argv: list[str], cwd: Path, timeout: int) -> tuple[bool, str]:
    """Run one gate; return (passed, output_tail)."""
    # Resolve 'python' to the interpreter actually running this script so the
    # same environment is used for every gate.
    resolved = [sys.executable if part == "python" else part for part in argv]
    try:
        proc = subprocess.run(
            resolved,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return False, f"TIMEOUT after {timeout}s"
    output = (proc.stdout or "") + (proc.stderr or "")
    tail = "\n".join(output.splitlines()[-TAIL_LINES:])
    return proc.returncode == 0, tail


def check_git_status(cwd: Path) -> str | None:
    """Return a warning string when the working tree is dirty, else None."""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode == 0 and proc.stdout.strip():
        return f"{len(proc.stdout.splitlines())} uncommitted change(s) present — confirm they are all intended release content"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the full release gate for this repository.")
    parser.add_argument(
        "--skip",
        nargs="*",
        default=[],
        choices=[g[0] for g in GATES],
        help="Gate ids to skip (e.g. --skip mcp_tests).",
    )
    args = parser.parse_args()

    skip = set(args.skip)
    results: list[tuple[str, bool]] = []
    total = len([g for g in GATES if g[0] not in skip])

    for index, (gate_id, argv, cwd) in enumerate(GATES, start=1):
        if gate_id in skip:
            print(f"[{index}/{total + len(skip)}] {gate_id:24s} SKIPPED")
            continue
        passed, tail = run_gate(gate_id, argv, cwd, GATE_TIMEOUT)
        results.append((gate_id, passed))
        print(f"[{index}/{total + len(skip)}] {gate_id:24s} {'PASS' if passed else 'FAIL'}")
        if not passed:
            print("-" * 60)
            print(tail)
            print("-" * 60)

    warning = check_git_status(ROOT)
    if warning:
        print(f"[warn] {warning}")

    failed = [gate_id for gate_id, passed in results if not passed]
    if failed:
        print(f"FAILED GATES: {', '.join(failed)}")
        print("Fix the root cause of each failed gate, then rerun this script.")
        return 1
    print("ALL GATES PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
