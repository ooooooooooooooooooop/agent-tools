#!/usr/bin/env python3
"""gdrive_snapshot.py — deterministic append-only private-asset snapshot.

Mechanical pipeline (no model discretion):
  configured roots -> timestamped staging dir -> file copy preserving relpaths
  -> SHA256SUMS + manifest.json (instance-contract-v1 §6) -> snapshots-index
  receipt under the instance root -> prints the upload plan for GDrive MCP.

Upload discipline (enforced by the caller/agent, not this script):
  - append-only: each run produces a NEW timestamped remote dir; never
    delete/overwrite remote files; never sync remote -> local.
  - LATEST pointer file updated per snapshot.

Manifest contract (docs/instance-contract-v1 §6): fixed fields, logical
relative paths only — device absolute paths are forbidden inside manifest.json
(they remain in the local stdout plan and never leave the machine).

Config: JSON file (private machine state, never committed), e.g.
  {
    "staging_dir": "C:/Users/<u>/gdrive-staging/Personal-AI-Private",
    "remote_base": "Personal-AI-Private/snapshots",
    "roots": {
      "l0":          ["<dsh>/world-model/ledger"],
      "l1":          ["<dsh>/world-model/runs", "<dsh>/sessions"],
      "l2-private":  ["<world-model>/pilot"],
      "l3-private":  ["<world-model>/pilot-l3"]
    },
    "source_repos": {"world_model": "<world-model repo path>"}
  }
Pass via --config or GDRIVE_SNAPSHOT_CONFIG env var.

Verify: `--verify <snapshot-dir>` recomputes SHA256SUMS and the per-layer
aggregate digests from manifest.json — tamper or truncation fails closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BUCKETS = ("l0", "l1", "l2-private", "l3-private")
EXCLUDE_NAMES = {"__pycache__", ".DS_Store", "Thumbs.db", ".gitkeep"}
DEFAULT_CONFIG = Path.home() / ".dsh" / "gdrive-snapshot.json"
SNAPSHOT_SCHEMA_VERSION = 1


def _is_instance_root(p: Path) -> bool:
    return p.is_dir() and ((p / "instance.yaml").is_file() or (p / "state").is_dir())


def instance_root() -> Path | None:
    for var in ("PERSONAL_AI_HOME", "PERSONAL_AI_STATE"):
        v = os.environ.get(var)
        if v and _is_instance_root(Path(v)):
            return Path(v)
    for cand in (Path.home() / ".personal-ai", Path.home() / "personal-ai-state"):
        if _is_instance_root(cand):
            return cand
    return None


def instance_meta(root: Path | None) -> dict:
    """instance_id + schema version from <root>/instance.yaml (no yaml dep)."""
    if not root:
        return {}
    f = root / "instance.yaml"
    if not f.is_file():
        return {}
    out = {}
    for line in f.read_text(encoding="utf-8-sig").splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip().strip('"')
    return {
        "instance_id": out.get("instance_id"),
        "model_schema_version": out.get("model_schema_version")
        or out.get("instance_schema_version"),
    }


def git_head(repo: Path | None) -> str | None:
    if not repo or not (repo / ".git").exists():
        return None
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def load_config(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    roots = cfg.get("roots") or {}
    return {
        "staging_dir": Path(cfg["staging_dir"]),
        "remote_base": cfg.get("remote_base", "Personal-AI-Private/snapshots"),
        "roots": {b: [Path(r) for r in roots.get(b, [])] for b in BUCKETS},
        "source_repos": {k: Path(v) for k, v in
                         (cfg.get("source_repos") or {}).items()},
        "notes": cfg.get("notes", {}),
    }


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def layer_digest(entries: list[str]) -> str:
    """Aggregate hash of a bucket's 'sha256  relpath' lines (sorted, stable)."""
    return hashlib.sha256("\n".join(sorted(entries)).encode("utf-8")).hexdigest()


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


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def verify(snapshot_dir: Path) -> int:
    sums = snapshot_dir / "SHA256SUMS"
    manifest_f = snapshot_dir / "manifest.json"
    if not sums.is_file() or not manifest_f.is_file():
        print(f"VERIFY FAIL: missing SHA256SUMS/manifest.json in {snapshot_dir}",
              file=sys.stderr)
        return 2
    manifest = json.loads(manifest_f.read_text(encoding="utf-8-sig"))
    bad = []
    per_bucket: dict[str, list[str]] = {b: [] for b in BUCKETS}
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sha, _, rel = line.partition("  ")
        fp = snapshot_dir / rel.strip()
        per_bucket[rel.split("/", 1)[0]].append(line.strip())
        if not fp.is_file() or sha256(fp) != sha:
            bad.append(rel.strip())
    layers = manifest.get("layers") or {}
    for bucket, meta in layers.items():
        expect = (meta or {}).get("sha256")
        if expect and expect != layer_digest(per_bucket.get(bucket, [])):
            bad.append(f"layer-digest:{bucket}")
    if bad:
        print(f"VERIFY FAIL ({len(bad)}): {bad[:10]}", file=sys.stderr)
        return 1
    print(f"VERIFY OK: {snapshot_dir} "
          f"(snapshot_id={manifest.get('snapshot_id')}, "
          f"instance_id={manifest.get('instance_id')})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.environ.get(
        "GDRIVE_SNAPSHOT_CONFIG", str(DEFAULT_CONFIG)),
        help="snapshot config JSON (private machine state)")
    ap.add_argument("--apply", action="store_true",
                    help="actually stage the snapshot (default: dry-run plan)")
    ap.add_argument("--verify", metavar="DIR", type=Path,
                    help="verify a staged/restored snapshot dir and exit")
    args = ap.parse_args()

    if args.verify:
        return verify(args.verify)

    cfg_path = Path(args.config)
    if not cfg_path.is_file():
        print(f"config not found: {cfg_path}\n"
              "create one (see module docstring) — private machine state, "
              "never committed.", file=sys.stderr)
        return 2
    cfg = load_config(cfg_path)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    snapshot_id = f"{ts}-{uuid.uuid4().hex[:8]}"
    y, m = ts[:4], ts[4:6]
    dest = cfg["staging_dir"] / "snapshots" / y / m / snapshot_id
    files = collect(cfg["roots"])
    total = sum(p.stat().st_size for _, p, _ in files)
    iroot = instance_root()
    imeta = instance_meta(iroot)

    manifest = {
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "instance_id": imeta.get("instance_id"),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "state_revision": git_head(iroot),
        "model_schema_version": imeta.get("model_schema_version"),
        "source_versions": dict(
            {"personal_ai": git_head(repo_root())},
            **{k: git_head(v) for k, v in cfg["source_repos"].items()}),
        "layers": {},  # filled in --apply; dry-run lists file counts only
        "remote_dir": f"{cfg['remote_base']}/{y}/{m}/{snapshot_id}",
        "file_count": len(files),
        "total_bytes": total,
    }
    plan = dict(manifest)
    plan["staging_dir"] = str(dest)          # local-only, never in manifest.json
    plan["sources"] = {b: [str(r) for r in cfg["roots"][b]] for b in BUCKETS}
    plan["buckets"] = {b: sum(1 for x in files if x[0] == b) for b in BUCKETS}
    plan["notes"] = cfg.get("notes", {})
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if not args.apply:
        print("DRY-RUN — pass --apply to stage.", file=sys.stderr)
        return 0

    sums: list[str] = []
    per_bucket: dict[str, list[str]] = {b: [] for b in BUCKETS}
    for bucket, src, rel in files:
        dst = dest / bucket / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        line = f"{sha256(dst)}  {bucket}/{rel.as_posix()}"
        sums.append(line)
        per_bucket[bucket].append(line)
    (dest / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    manifest["layers"] = {
        b: {"relpath": f"{b}/", "sha256": layer_digest(per_bucket[b])}
        for b in BUCKETS
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    (dest / "LATEST").write_text(snapshot_id + "\n", encoding="utf-8")

    # Contract §6: git ledger side stores receipt (snapshot_id + layer hash),
    # never a GDrive URL. snapshots-index/ lives in the instance root.
    if iroot:
        idx = iroot / "snapshots-index"
        idx.mkdir(exist_ok=True)
        receipt = {
            "snapshot_id": snapshot_id,
            "created_at": manifest["created_at"],
            "state_revision": manifest["state_revision"],
            "layer_hashes": {b: manifest["layers"][b]["sha256"] for b in BUCKETS},
            "sha256sums_sha256": sha256(dest / "SHA256SUMS"),
        }
        receipt_path = idx / f"{snapshot_id}.json"
        receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"INDEX-RECEIPT {receipt_path}", file=sys.stderr)

    print(f"STAGED {len(files)} files ({total} bytes) -> {dest}", file=sys.stderr)
    print("UPLOAD: gdrive upload dir -> " + manifest["remote_dir"], file=sys.stderr)
    print(f"VERIFY: {sys.argv[0]} --verify \"{dest}\"", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
