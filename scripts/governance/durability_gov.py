#!/usr/bin/env python3
"""durability_gov.py — consume Phase 6 durability outputs (read-only).

Never re-implements backup; reads runs.jsonl + scheduled task status and
surfaces a unified durability view. BACKUP_KEY_CUSTODY stays
WAITING_FOR_CUSTODY_ROOT — never promoted.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "durability"))
from common import STATE, gov_log, load_yaml  # noqa: E402


def runs() -> list[dict]:
    root = Path(load_yaml(STATE / "sync" / "this-device.yaml")["backup_root"])
    f = root / "ledger" / "runs.jsonl"
    if not f.is_file():
        return []
    out = []
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def task_status(name: str) -> str:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-ScheduledTask -TaskName '{name}').State; "
             f"(Get-ScheduledTaskInfo -TaskName '{name}').LastTaskResult"],
            capture_output=True, text=True, timeout=20)
        parts = [p.strip() for p in out.stdout.splitlines() if p.strip()]
        return f"{parts[0]}/result={parts[1]}" if len(parts) >= 2 else "UNKNOWN"
    except Exception:  # noqa: BLE001
        return "UNKNOWN"


def main() -> int:
    rows = runs()
    latest: dict[str, dict] = {}
    for r in rows:
        ds = r.get("dataset")
        if ds and r.get("finished_at", "") >= latest.get(ds, {}).get("finished_at", ""):
            latest[ds] = r
    failures = [r for r in rows[-50:] if r.get("status") in ("error", "UNPUSHED_DURABILITY_RISK")]
    print("=== durability integration (read-only) ===")
    for ds, r in sorted(latest.items()):
        print(f"  {ds}: last={r.get('finished_at')} status={r.get('status')} "
              f"integrity={r.get('integrity_status', '-')}")
    print(f"  scheduled_task PersonalAI-Durability-Nightly: "
          f"{task_status('PersonalAI-Durability-Nightly')}")
    print(f"  recent_failures(last50)={len(failures)}")
    print("  FULL_DR_READINESS=PARTIAL external_blocker=BACKUP_KEY_CUSTODY "
          "(WAITING_FOR_CUSTODY_ROOT — not promoted)")
    gov_log("durability_gov", "ok", len(latest),
            {"recent_failures": len(failures),
             "full_dr_readiness": "PARTIAL",
             "external_blocker": "BACKUP_KEY_CUSTODY"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
