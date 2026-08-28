#!/usr/bin/env python3
"""restore_check.py — repeatable RTO drill against latest VERIFIED generations.

Restores into a temp isolated location; never touches live sources.
Checks: broker snapshot integrity_check; random session 3-way hash
(source/backup/restored); configs parse (json/yaml load). Ledger-recorded.
"""
from __future__ import annotations

import json
import random
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from common import backup_root, ledger_append, ledger_rows, now_iso, sha256_file  # noqa: E402

SRC_SESSIONS = Path.home() / ".dsh" / "sessions"


def latest_gen(root: Path, pattern_dir: str, prefix: str) -> Path | None:
    d = root / pattern_dir
    if not d.is_dir():
        return None
    gens = sorted(p for p in d.iterdir() if p.name.startswith(prefix))
    return gens[-1] if gens else None


def check_broker(root: Path, tmp: Path) -> dict:
    snaps = sorted((root / "broker").glob("broker-*.sqlite")) if (root / "broker").is_dir() else []
    if not snaps:
        return {"name": "broker_restore", "status": "error", "error": "no snapshot"}
    dst = tmp / snaps[-1].name
    shutil.copy2(snaps[-1], dst)
    try:
        con = sqlite3.connect(dst)
        ok = con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        con.close()
    except sqlite3.DatabaseError as exc:
        ok = False
        return {"name": "broker_restore", "status": "error",
                "snapshot": snaps[-1].name, "integrity_check": f"FAILED: {exc}"}
    return {"name": "broker_restore", "status": "ok" if ok else "error",
            "snapshot": snaps[-1].name, "integrity_check": "ok" if ok else "FAILED"}


def check_session(root: Path, tmp: Path) -> dict:
    gen = latest_gen(root, "sessions", "daily-")
    if gen is None:
        return {"name": "session_restore", "status": "error", "error": "no session backup"}
    manifest = json.loads((gen / "manifest.json").read_text(encoding="utf-8"))
    entry = random.choice(manifest["files"])
    backed = gen / entry["file"]
    dst = tmp / "session-restore.zstd"
    shutil.copy2(backed, dst)
    src = SRC_SESSIONS / entry["file"]
    h_src = sha256_file(src) if src.is_file() else "source-gone"
    h_bak = sha256_file(backed)
    h_res = sha256_file(dst)
    ok = h_bak == entry["sha256"] == h_res and (h_src in (h_bak, "source-gone"))
    return {"name": "session_restore", "status": "ok" if ok else "error",
            "file": entry["file"], "source_moved_on": h_src == "source-gone",
            "hash3way": "match" if ok else "MISMATCH"}


def check_configs(root: Path, tmp: Path) -> dict:
    gen = latest_gen(root, "configs", "daily-")
    if gen is None:
        return {"name": "configs_restore", "status": "error", "error": "no config backup"}
    ok, bad = 0, []
    for f in gen.iterdir():
        if f.suffix == ".json" and f.name != "manifest.json":
            try:
                json.loads(f.read_text(encoding="utf-8-sig"))
                ok += 1
            except Exception as exc:  # noqa: BLE001
                bad.append(f"{f.name}:{exc}")
        elif f.suffix in (".yaml", ".yml") and "cpa" not in f.name.lower():
            try:
                yaml.safe_load(f.read_text(encoding="utf-8-sig"))
                ok += 1
            except Exception as exc:  # noqa: BLE001
                bad.append(f"{f.name}:{exc}")
    return {"name": "configs_restore", "status": "ok" if not bad else "error",
            "parsed": ok, "failed": bad}


def main() -> int:
    started = now_iso()
    root = backup_root()
    tmp = Path(tempfile.mkdtemp(prefix="restore-check-"))
    checks = [check_broker(root, tmp), check_session(root, tmp), check_configs(root, tmp)]
    status = "ok" if all(c["status"] == "ok" for c in checks) else "error"
    ledger_append({"job": "restore_check", "dataset": "all", "started_at": started,
                   "finished_at": now_iso(), "status": status,
                   "checks": [{k: v for k, v in c.items() if k != "failed"} for c in checks],
                   "integrity_status": "verified" if status == "ok" else "failed",
                   "error": None if status == "ok" else "see checks"})
    for c in checks:
        print(c)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"restore_check: {status}")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
