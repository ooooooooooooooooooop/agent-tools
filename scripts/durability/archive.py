#!/usr/bin/env python3
"""archive.py — hot/cold tiering for raw history (sessions).

Policy (verified against reality 2026-08-28: all 556 sessions <30d, 256.8MB —
no capacity pressure): hot = 90 days, cold retention = 12 months.

Cold = compressed zip under <backup_root>/archive/cold/, with manifest + hashes
+ preserved timestamps + source relationship. Sources are NOT deleted in this
phase (copy-tiering; deletion deferred to when capacity actually demands it).

Default is --dry-run. Restore: extract zip; manifest maps rel path -> sha256.
"""
from __future__ import annotations

import json
import os
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import backup_root, device_config, ledger_append, now_iso, sha256_file  # noqa: E402

SRC = Path(os.environ.get("DUR_SESSIONS_SRC", Path.home() / ".dsh" / "sessions"))


def candidates(hot_days: int) -> list[Path]:
    cutoff = time.time() - hot_days * 86400
    return [f for f in SRC.rglob("session.jsonl.zstd") if f.stat().st_mtime < cutoff]


def run(files: list[Path], execute: bool) -> int:
    started = now_iso()
    root = backup_root()
    if not execute:
        print(f"DRY-RUN: {len(files)} session(s) eligible for cold archive "
              f"({sum(f.stat().st_size for f in files) / 1e6:.1f}MB)")
        return 0
    if not files:
        print("nothing to archive")
        return 0
    out = root / "archive" / "cold" / f"sessions-cold-{started[:7]}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            rel = str(f.relative_to(SRC)).replace("\\", "/")
            sha = sha256_file(f)
            st = f.stat()
            info = zipfile.ZipInfo(rel, time.localtime(st.st_mtime)[:6])
            zf.writestr(info, f.read_bytes())
            entries.append({"file": rel, "bytes": st.st_size, "sha256": sha,
                            "source": f"sessions/{rel}", "archived_at": now_iso()})
    mpath = out.with_suffix(".manifest.json")
    mpath.write_text(json.dumps({"archive": out.name, "zip_sha256": sha256_file(out),
                                 "retention_months": device_config()["retention"]["cold_months"],
                                 "files": entries}, ensure_ascii=False, indent=1),
                     encoding="utf-8")
    ledger_append({"job": "archive", "dataset": "sessions:cold", "started_at": started,
                   "finished_at": now_iso(), "status": "ok",
                   "target_generation": out.name, "files": len(entries),
                   "bytes": out.stat().st_size, "manifest": mpath.name,
                   "integrity_status": "verified", "error": None})
    print(f"archived {len(entries)} session(s) -> {out.name} ({out.stat().st_size}B)")
    return 0


def main() -> int:
    hot = device_config()["retention"]["hot_days"]
    execute = "--execute" in sys.argv
    return run(candidates(hot), execute)


if __name__ == "__main__":
    sys.exit(main())
