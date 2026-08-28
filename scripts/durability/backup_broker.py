#!/usr/bin/env python3
"""backup_broker.py — consistent sqlite snapshots (agent-switchboard + cc-switch).

Uses the SQLite online backup API (NOT a raw file copy) so snapshots are
consistent even while the broker is writing. Verifies each snapshot with
PRAGMA integrity_check before recording success.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import backup_root, ledger_append, now_iso, sha256_file  # noqa: E402

TARGETS = {
    "broker": Path.home() / ".agent-broker" / "state.sqlite",
    "cc-switch": Path.home() / ".cc-switch" / "cc-switch.db",
}


def snapshot(name: str, src: Path, root: Path) -> dict:
    ts = now_iso().replace(":", "").replace("+", "p")[:15]
    dst = root / "broker" / f"{name}-{ts}.sqlite"
    dst.parent.mkdir(parents=True, exist_ok=True)
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    with d:
        s.backup(d)
    s.close()
    cur = d.execute("PRAGMA integrity_check")
    ok = cur.fetchone()[0] == "ok"
    d.close()
    return {"dataset": f"broker:{name}", "file": str(dst.relative_to(root)).replace("\\", "/"),
            "bytes": dst.stat().st_size, "sha256": sha256_file(dst),
            "integrity_status": "verified" if ok else "failed"}


def main() -> int:
    started = now_iso()
    root = backup_root()
    rows, status = [], "ok"
    for name, src in TARGETS.items():
        if not src.is_file():
            rows.append({"dataset": f"broker:{name}", "status": "error",
                         "error": f"source missing: {src.name}"})
            status = "error"
            continue
        try:
            rows.append(snapshot(name, src, root))
        except Exception as exc:  # noqa: BLE001
            rows.append({"dataset": f"broker:{name}", "status": "error", "error": str(exc)[:200]})
            status = "error"
    for r in rows:
        ledger_append({"job": "backup_broker", "started_at": started,
                       "finished_at": now_iso(),
                       "status": "ok" if r.get("integrity_status") == "verified" else "error",
                       **{k: v for k, v in r.items() if k != "integrity_status"},
                       "integrity_status": r.get("integrity_status", "failed")})
    for r in rows:
        print(f"{r['dataset']}: {r.get('integrity_status', 'error')} "
              f"{r.get('bytes', 0)}B {r.get('file', r.get('error', ''))}")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
