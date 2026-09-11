#!/usr/bin/env python3
"""durability_gov.py — consume Phase 6 durability outputs (read-only).

Status semantics (2026-09-06 remediation):
Explicitly separates and requires:
1. Latest real backup run result (run_id, capture_point, status, integrity).
2. Latest isolated restore verification result and time cutoff.
Never infers 'backup healthy' from static task enablement or destination existence.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "durability"))
sys.path.insert(0, str(Path(__file__).parent))
from common import gov_log, load_yaml  # noqa: E402

STATE = Path(os.environ.get("PERSONAL_AI_STATE", Path.home() / "personal-ai-state"))


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
    latest_backup_run: dict | None = None
    latest_restore_check: dict | None = None

    for r in rows:
        job = r.get("job")
        ds = r.get("dataset")
        fin = r.get("run_finished_at") or r.get("finished_at", "")
        if ds and fin >= (latest.get(ds, {}).get("run_finished_at") or latest.get(ds, {}).get("finished_at", "")):
            latest[ds] = r
        if job == "backup_run":
            curr_fin = latest_backup_run.get("run_finished_at") or latest_backup_run.get("finished_at", "") if latest_backup_run else ""
            if latest_backup_run is None or fin >= curr_fin:
                latest_backup_run = r
        if job == "restore_check":
            curr_fin = latest_restore_check.get("run_finished_at") or latest_restore_check.get("finished_at", "") if latest_restore_check else ""
            if latest_restore_check is None or fin >= curr_fin:
                latest_restore_check = r

    failures = [r for r in rows[-50:] if r.get("status") in ("error", "UNPUSHED_DURABILITY_RISK")]

    print("=== durability integration (read-only) ===")
    for ds, r in sorted(latest.items()):
        print(f"  {ds}: last={r.get('finished_at')} status={r.get('status')} "
              f"integrity={r.get('integrity_status', '-')}")

    print(f"  scheduled_task PersonalAI-Durability-Nightly: {task_status('PersonalAI-Durability-Nightly')}")

    # Explicit Run vs Restore verification semantics
    print("\n--- Backup Production Status Semantics ---")
    if latest_backup_run:
        print(f"  Latest Real Backup Run: run_id={latest_backup_run.get('run_id')} "
              f"consistency_model={latest_backup_run.get('consistency_model', 'legacy')} "
              f"started_at={latest_backup_run.get('run_started_at', latest_backup_run.get('started_at'))} "
              f"finished_at={latest_backup_run.get('run_finished_at', latest_backup_run.get('finished_at'))} "
              f"status={latest_backup_run.get('status')} "
              f"integrity={latest_backup_run.get('integrity_status')}")
    else:
        print("  Latest Real Backup Run: NONE (no run-level execution recorded)")

    if latest_restore_check:
        sess_check = next((c for c in latest_restore_check.get("checks", []) if c.get("name") == "sessions_restore"), {})
        sess_detail = f" (sessions: manifest={sess_check.get('manifest_total')}, restored={sess_check.get('restored_count')}, verified={sess_check.get('reader_verified_count')})" if sess_check else ""
        print(f"  Latest Restore Verification: verified_run={latest_restore_check.get('run_id')} "
              f"finished_at={latest_restore_check.get('finished_at')} "
              f"status={latest_restore_check.get('status')} "
              f"integrity={latest_restore_check.get('integrity_status')}{sess_detail}")
    else:
        print("  Latest Restore Verification: NONE (no isolated restore verification recorded)")

    # Anti-false-pass evaluation
    backup_ok = (latest_backup_run is not None
                 and latest_backup_run.get("status") == "ok"
                 and latest_backup_run.get("integrity_status") == "verified")
    restore_ok = (latest_restore_check is not None
                  and latest_restore_check.get("status") == "ok"
                  and latest_restore_check.get("integrity_status") == "verified")

    # Match check: latest restore must verify the latest run
    matched = (backup_ok and restore_ok
               and latest_backup_run.get("run_id") == latest_restore_check.get("run_id"))

    if backup_ok and restore_ok and matched:
        overall_backup_verdict = "PASS"
        time_label = latest_backup_run.get("run_started_at") or latest_backup_run.get("capture_point") or latest_backup_run.get("started_at")
        verdict_note = f"Verified run {latest_backup_run.get('run_id')} started_at={time_label}"
    elif backup_ok and not restore_ok:
        overall_backup_verdict = "FAIL_UNVERIFIED"
        verdict_note = "Latest backup run succeeded but isolated restore verification failed or missing"
    elif not backup_ok:
        overall_backup_verdict = "FAIL_BACKUP"
        verdict_note = "Latest backup run failed or missing"
    else:
        overall_backup_verdict = "FAIL_MISMATCH"
        verdict_note = "Restore verification does not match latest backup run"

    print(f"  NIGHTLY_BACKUP_PATH_VERDICT: {overall_backup_verdict} ({verdict_note})")
    print(f"  recent_failures(last50)={len(failures)}")
    print("  FULL_DR_READINESS=PARTIAL external_blocker=BACKUP_KEY_CUSTODY (WAITING_FOR_CUSTODY_ROOT — not promoted)")

    gov_log("durability_gov", "ok" if overall_backup_verdict == "PASS" else "error", len(latest),
            {"recent_failures": len(failures),
             "backup_verdict": overall_backup_verdict,
             "latest_run_id": latest_backup_run.get("run_id") if latest_backup_run else None,
             "latest_capture_point": latest_backup_run.get("capture_point") if latest_backup_run else None,
             "full_dr_readiness": "PARTIAL",
             "external_blocker": "BACKUP_KEY_CUSTODY"})

    return 0 if overall_backup_verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
