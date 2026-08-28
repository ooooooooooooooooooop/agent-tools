#!/usr/bin/env python3
"""durability/common.py — shared helpers for backup/archive/verify jobs.

Boundary: these are standalone OS-scheduled tools. aic (control plane) knows
nothing about them; they never call aic, MemoryProvider, or any LLM.
Device binding lives in personal-ai-state/sync/this-device.yaml (private).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import yaml

SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|xox[bap]-|AKIA[0-9A-Z]{16}"
    r"|AIza[0-9A-Za-z_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")


def device_config() -> dict:
    root = Path(os.environ.get("PERSONAL_AI_STATE", Path.home() / "personal-ai-state"))
    cfg = root / "sync" / "this-device.yaml"
    if not cfg.is_file():
        raise SystemExit(f"device config missing: {cfg}")
    return yaml.safe_load(cfg.read_text(encoding="utf-8-sig"))


def backup_root() -> Path:
    return Path(device_config()["backup_root"])


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ledger_append(row: dict) -> Path:
    """Append one run record to the append-only jsonl ledger. Secret-scanned."""
    blob = json.dumps(row, ensure_ascii=False)
    if SECRET_RE.search(blob):
        raise SystemExit("refuse to ledger row containing secret-shaped material")
    row.setdefault("ts", now_iso())
    led = backup_root() / "ledger"
    led.mkdir(parents=True, exist_ok=True)
    with (led / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return led / "runs.jsonl"


def ledger_rows() -> list[dict]:
    f = backup_root() / "ledger" / "runs.jsonl"
    if not f.is_file():
        return []
    out = []
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def write_manifest(dirpath: Path, entries: list[dict], extra: dict | None = None) -> Path:
    manifest = {"generated_at": now_iso(), "files": entries}
    if extra:
        manifest.update(extra)
    mpath = dirpath / "manifest.json"
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return mpath
