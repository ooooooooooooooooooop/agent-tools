"""cli.py — Command-line interface for Personal AI Sync V2."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from sync_v2.engine import run_sync
from sync_v2.models import OverallStatus


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(
        prog="sync_v2",
        description="Personal AI Sync V2 — True Convergence Engine",
    )
    parser.add_argument("--restart", action="store_true", help="Request controlled DSH restart if deployed!=active")
    parser.add_argument("--check-only", action="store_true", help="Inspect status without applying changes")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON receipt")
    args = parser.parse_args()

    receipt, human_text = run_sync(request_restart=args.restart, check_only=args.check_only)

    if args.json:
        print(receipt.to_json())
    else:
        print(human_text)

    # Return exit code based on overall status
    if receipt.overall in (
        OverallStatus.PASS,
        OverallStatus.PASS_NO_CHANGE,
        OverallStatus.PASS_WITH_HEALTH_WARNINGS,
        OverallStatus.PARTIAL_RESTART_REQUIRED,
        OverallStatus.PARTIAL,
    ):
        return 0
    elif receipt.overall in (
        OverallStatus.PASS_WITH_HEALTH_FAILURE,
        OverallStatus.REVIEW_REQUIRED,
    ):
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
