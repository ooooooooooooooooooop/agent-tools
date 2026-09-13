#!/usr/bin/env python3
"""gdrive_snapshot.py — deterministic append-only private-asset snapshot.

Mechanical pipeline (no model discretion):
  configured roots -> timestamped staging dir -> file copy preserving relpaths
  -> SHA256SUMS + manifest.json -> prints the upload plan for GDrive MCP.

Upload discipline (enforced by the caller/agent, not this script):
  - append-only: each run produces a NEW timestamped remote dir; never
    delete/overwrite remote files; never sync remote -> local.
  - optional LATEST pointer file updated per snapshot.

Asset buckets are ALWAYS listed as four cognitive-layer groups in the
manifest — l0 / l1 / l2-private / l3-private — even when a bucket's root
list is empty; backup coverage of every private layer is explicit, not
implicit by "some directory happened to contain it".

Config: JSON file (private machine state, never committed), e.g.
  {
    "staging_dir": "C:/Users/<u>/gdrive-staging/Personal-AI-Private",
    "remote_base": "Personal-AI-Private/snapshots",
    "roots": {
      "l0":          ["<dsh>/world-model/ledger"],
      "l1":          ["<dsh>/world-model/runs", "<dsh>/sessions"],
      "l2-private":  ["<world-model>/pilot"],
      "l3-private":  ["<world-model>/pilot-l3"]
    }
  }
Pass via --config or GDRIVE_SNAPSHOT_CONFIG env var.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

BUCKETS = ("l0", "l1", "l2-private", "l3-private")
EXCLUDE_NAMES = {"__pycache__", ".DS_Store", "Thumbs.db", ".gitkeep"}
DEFAULT_CONFIG = Path.home() / ".dsh" / "gdrive-snapshot.json"


def load_config(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    roots = cfg.get("roots") or {}
    return {
        "staging_dir": Path(cfg["staging_dir"]),
        "remote_base": cfg.get("remote_base", "Personal-AI-Private/snapshots"),
        "roots": {b: [Path(r) for r in roots.get(b, [])] for b in BUCKETS},
        "notes": cfg.get("notes", {}),
    }


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect(roots: dict) -> list[tuple[str, Path, Path]]:
    """Return [(bucket, src_file, rel_under_bucket)] — deterministic order."""
    out: list[tuple[str, Path, Path]] = []
    for bucket in BUCKETS:
        for root in roots[bucket]:
            if not root.is_dir():
                continue
            for p in sorted(root.rglob("*")):
                if not p.is_file() or p.name in EXCLUDE_NAMES:
                    continue
                rel = p.relative_to(root)
                out.append((bucket, p, Path(root.name) / rel))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.environ.get(
        "GDRIVE_SNAPSHOT_CONFIG", str(DEFAULT_CONFIG)),
        help="snapshot config JSON (private machine state)")
    ap.add_argument("--apply", action="store_true",
                    help="actually stage the snapshot (default: dry-run plan)")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.is_file():
        print(f"config not found: {cfg_path}\n"
              "create one (see module docstring) — private machine state, "
              "never committed.", file=sys.stderr)
        return 2
    cfg = load_config(cfg_path)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    y, m = ts[:4], ts[4:6]
    dest = cfg["staging_dir"] / "snapshots" / y / m / ts
    files = collect(cfg["roots"])
    total = sum(p.stat().st_size for _, p, _ in files)

    plan = {
        "snapshot_id": ts,
        "staging_dir": str(dest),
        "remote_dir": f"{cfg['remote_base']}/{y}/{m}/{ts}",
        "buckets": {b: sum(1 for x in files if x[0] == b) for b in BUCKETS},
        "file_count": len(files),
        "total_bytes": total,
        "sources": {b: [str(r) for r in cfg["roots"][b]] for b in BUCKETS},
        "notes": cfg.get("notes", {}),
    }
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if not args.apply:
        print("DRY-RUN — pass --apply to stage.", file=sys.stderr)
        return 0

    sums: list[str] = []
    for bucket, src, rel in files:
        dst = dest / bucket / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        sums.append(f"{sha256(dst)}  {bucket}/{rel.as_posix()}")
    (dest / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    (dest / "manifest.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (dest / "LATEST").write_text(ts + "\n", encoding="utf-8")
    print(f"STAGED {len(files)} files ({total} bytes) -> {dest}", file=sys.stderr)
    print("UPLOAD: gdrive upload dir -> " + plan["remote_dir"], file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
