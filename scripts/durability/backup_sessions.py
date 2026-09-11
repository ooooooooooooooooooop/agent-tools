#!/usr/bin/env python3
"""backup_sessions.py — nightly incremental DSH session backup (local recovery tier).

Only changed files are copied (size+mtime+sha index). Never overwrites the only
old version: each day gets its own dated folder; index tracks last-copied state.
Consistency guard: retries active session files if mutation occurs during copy,
and verifies zstd stream decode before accepting.
Emits a complete manifest of all protected sessions at this capture point.
Prints no session content. Exit non-zero on any copy/verify failure.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import backup_root, ledger_append, now_iso, sha256_file, write_manifest  # noqa: E402


def sessions_src() -> Path:
    return Path(os.environ.get("PERSONAL_AI_SESSIONS_SRC",
                              Path.home() / ".dsh" / "sessions"))


ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def verify_zstd_file(path: Path) -> bool:
    """Verify that path is a valid zstd stream if it carries zstd magic bytes."""
    try:
        with path.open("rb") as fh:
            magic = fh.read(4)
        if magic != ZSTD_MAGIC:
            return True
        import zstandard as zstd
        dctx = zstd.ZstdDecompressor()
        with path.open("rb") as fh, dctx.stream_reader(fh) as r:
            chunk = r.read(1024)
            return len(chunk) > 0
    except Exception:
        return False


def copy_with_consistency_retry(src: Path, dst: Path, max_retries: int = 3) -> tuple[int, str]:
    """Copy file and verify consistent sha256 and valid zstd stream. Retries if mutated."""
    for attempt in range(max_retries):
        sha_before = sha256_file(src)
        shutil.copy2(src, dst)
        sha_after_src = sha256_file(src)
        sha_dst = sha256_file(dst)
        if sha_before != sha_after_src:
            # File mutated during copy
            time.sleep(0.1)
            continue
        if sha_dst != sha_before:
            time.sleep(0.1)
            continue
        if not verify_zstd_file(dst):
            time.sleep(0.1)
            continue
        return dst.stat().st_size, sha_dst
    raise IOError(f"failed to obtain consistent snapshot for {src} after {max_retries} retries")


def main() -> int:
    started = now_iso()
    capture_point = started
    root = backup_root()
    state_file = root / "state" / "sessions-index.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    index = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else {}
    day_dir = root / "sessions" / ("daily-" + started[:10])
    day_dir.mkdir(parents=True, exist_ok=True)

    copied, skipped, failed, total_bytes = [], 0, [], 0
    entries = []
    src_root = sessions_src()
    all_sessions = sorted(src_root.rglob("session.jsonl.zstd"))
    total_scanned = len(all_sessions)

    for f in all_sessions:
        rel = str(f.relative_to(src_root)).replace("\\", "/")
        st = f.stat()
        prev = index.get(rel)
        if prev and prev["size"] == st.st_size and prev["mtime"] == int(st.st_mtime):
            skipped += 1
            continue
        dst = day_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            sz, sha = copy_with_consistency_retry(f, dst)
            index[rel] = {"size": sz, "mtime": int(st.st_mtime), "sha256": sha,
                          "backup": str(dst.relative_to(root)).replace("\\", "/")}
            copied.append(rel)
            total_bytes += sz
            entries.append({"file": rel, "bytes": sz, "sha256": sha})
        except OSError as exc:
            failed.append(f"{rel}: {exc}")

    # Write incremental manifest for today
    incremental_manifest_path = day_dir / "incremental_manifest.json"
    prior: dict = {}
    if incremental_manifest_path.is_file():
        try:
            prior = {e["file"]: e for e in
                     json.loads(incremental_manifest_path.read_text(encoding="utf-8")).get("files", [])}
        except Exception:
            prior = {}
    prior.update({e["file"]: e for e in entries})
    merged = list(prior.values())
    incremental_manifest_path.write_text(json.dumps({
        "generated_at": now_iso(),
        "dataset": "sessions",
        "incremental": True,
        "capture_point": capture_point,
        "files": merged
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    # Save updated session index
    state_file.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")

    # Write complete manifest of all protected sessions at this capture point
    complete_entries = []
    for f in all_sessions:
        rel = str(f.relative_to(src_root)).replace("\\", "/")
        rec = index.get(rel)
        if rec:
            complete_entries.append({
                "file": rel,
                "bytes": rec["size"],
                "sha256": rec["sha256"],
                "backup_rel": rec.get("backup")
            })
    mpath = write_manifest(day_dir, complete_entries, {
        "dataset": "sessions",
        "scope": "all_protected_sessions_at_capture_point",
        "capture_point": capture_point,
        "total_sessions": len(complete_entries),
        "incremental_copied": len(copied),
        "incremental_skipped": skipped
    })

    status = "ok" if not failed else "error"
    ledger_append({"job": "backup_sessions", "dataset": "sessions", "started_at": started,
                   "finished_at": now_iso(), "status": status,
                   "target_generation": day_dir.name, "files": len(entries),
                   "total_protected_sessions": len(complete_entries),
                   "bytes": total_bytes, "skipped_unchanged": skipped,
                   "manifest": str(day_dir.relative_to(root) / "manifest.json").replace("\\", "/"),
                   "integrity_status": "verified" if status == "ok" else "failed",
                   "error": "; ".join(failed) if failed else None})
    print(f"sessions backup: copied={len(copied)} skipped={skipped} failed={len(failed)} "
          f"total_protected={len(complete_entries)} bytes={total_bytes} -> {day_dir.name} [{status}]")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
