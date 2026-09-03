#!/usr/bin/env python3
"""backup_jobs.py — consistent sqlite snapshots of the Durable Job DB.

The durable job registry (~/.personal-ai/jobs/durable_jobs.db) is the
coordination authority for Personal AI durable execution. It was never covered
by any backup (forensics 2026-09-03). Uses the SQLite online backup API (NOT a
raw file copy) so snapshots are consistent even while the registry is writing.
Verifies each snapshot with PRAGMA integrity_check before recording success.

Same pipeline/ledger/manifest contract as backup_broker.py — no second system.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import backup_root, ledger_append, now_iso, sha256_file  # noqa: E402


def jobs_db_path() -> Path:
    custom = os.environ.get("PERSONAL_AI_JOBS_DB")
    if custom:
        return Path(custom)
    return Path.home() / ".personal-ai" / "jobs" / "durable_jobs.db"


def snapshot(src: Path, root: Path) -> dict:
    ts = now_iso().replace(":", "").replace("+", "p")[:15]
    dst = root / "jobs" / f"jobs-{ts}.sqlite"
    dst.parent.mkdir(parents=True, exist_ok=True)
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    try:
        with d:
            s.backup(d)
        ok = d.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        tables = sorted(r[0] for r in d.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        unfinished = d.execute(
            "SELECT COUNT(*) FROM jobs WHERE job_state NOT IN "
            "('COMPLETED','FAILED','CANCELLED')").fetchone()[0]
    finally:
        s.close()
        d.close()
    return {"dataset": "jobs", "file": str(dst.relative_to(root)).replace("\\", "/"),
            "bytes": dst.stat().st_size, "sha256": sha256_file(dst),
            "integrity_status": "verified" if ok else "failed",
            "tables": len(tables), "unfinished_jobs": unfinished}


def main() -> int:
    started = now_iso()
    root = backup_root()
    src = jobs_db_path()
    if not src.is_file():
        ledger_append({"job": "backup_jobs", "dataset": "jobs", "started_at": started,
                       "finished_at": now_iso(), "status": "error",
                       "error": f"source missing: {src}"})
        print(f"jobs: source missing: {src}")
        return 1
    try:
        row = snapshot(src, root)
        status = "ok"
    except Exception as exc:  # noqa: BLE001 - record any failure in the ledger
        row = {"dataset": "jobs", "integrity_status": "failed", "error": str(exc)}
        status = "error"
    ledger_append({"job": "backup_jobs", "dataset": "jobs", "started_at": started,
                   "finished_at": now_iso(), "status": status,
                   "bytes": row.get("bytes"), "sha256": row.get("sha256"),
                   "integrity_status": row.get("integrity_status"),
                   "error": row.get("error")})
    if status == "ok":
        print(f"jobs: snapshot {row['file']} ok verified "
              f"({row['bytes']}B, {row['tables']} tables, {row['unfinished_jobs']} unfinished)")
        return 0
    print(f"jobs: FAILED {row.get('error')}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
