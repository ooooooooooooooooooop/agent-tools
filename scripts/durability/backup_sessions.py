#!/usr/bin/env python3
"""backup_sessions.py — nightly incremental DSH session backup (local recovery tier).

Only changed files are copied (size+mtime+sha index). Never overwrites the only
old version: each day gets its own dated folder; index tracks last-copied state.
Prints no session content. Exit non-zero on any copy/verify failure.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import backup_root, ledger_append, now_iso, sha256_file, write_manifest  # noqa: E402

SRC = Path.home() / ".dsh" / "sessions"


def main() -> int:
    started = now_iso()
    root = backup_root()
    state_file = root / "state" / "sessions-index.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    index = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else {}
    day_dir = root / "sessions" / ("daily-" + started[:10])
    day_dir.mkdir(parents=True, exist_ok=True)

    copied, skipped, failed, total_bytes = [], 0, [], 0
    entries = []
    for f in sorted(SRC.rglob("session.jsonl.zstd")):
        rel = str(f.relative_to(SRC)).replace("\\", "/")
        st = f.stat()
        prev = index.get(rel)
        if prev and prev["size"] == st.st_size and prev["mtime"] == int(st.st_mtime):
            skipped += 1
            continue
        sha = sha256_file(f)
        dst = day_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(f, dst)
            if sha256_file(dst) != sha:
                raise IOError("post-copy hash mismatch")
            index[rel] = {"size": st.st_size, "mtime": int(st.st_mtime), "sha256": sha,
                          "backup": str(dst.relative_to(root)).replace("\\", "/")}
            copied.append(rel)
            total_bytes += st.st_size
            entries.append({"file": rel, "bytes": st.st_size, "sha256": sha})
        except OSError as exc:
            failed.append(f"{rel}: {exc}")

    mpath = write_manifest(day_dir, entries, {"dataset": "sessions",
                                              "incremental": True})
    state_file.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    status = "ok" if not failed else "error"
    ledger_append({"job": "backup_sessions", "dataset": "sessions", "started_at": started,
                   "finished_at": now_iso(), "status": status,
                   "target_generation": day_dir.name, "files": len(entries),
                   "bytes": total_bytes, "skipped_unchanged": skipped,
                   "manifest": str(mpath.relative_to(root)).replace("\\", "/"),
                   "integrity_status": "verified" if status == "ok" else "failed",
                   "error": "; ".join(failed) if failed else None})
    print(f"sessions backup: copied={len(copied)} skipped={skipped} failed={len(failed)} "
          f"bytes={total_bytes} -> {day_dir.name} [{status}]")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
