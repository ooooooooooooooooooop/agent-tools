#!/usr/bin/env python3
"""Export or restore DSH user config as a sanitized skeleton package.

The command never writes to a destination unless --apply is given, and never
deletes destination files. It hard-excludes credentials and runtime state. Use
``--check`` for a read-only comparison and ``--apply`` only when the destination
is intentional.

Reports PASS/PARTIAL/FAIL and prints a manifest digest. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

DEFAULT_INCLUDE = ("AGENTS.md", "settings.yaml")
OPTIONAL_INCLUDE = ("profiles", ".agent-presets", "patches")
HARD_EXCLUDE_DIRS = {"sessions", "storages", "skills", "__pycache__"}
HARD_EXCLUDE_NAMES = {".credentials.yaml"}
FORBIDDEN_SNIPPETS = {".jsonl", ".log"}
SECRET_VALUE_RE = None  # placeholder; kept simple to avoid over-engineering


def home_dir() -> Path:
    import os

    return Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))


def sensible_value(name: str) -> bool:
    """Treat an apiKeyEnv/secret value as safe when it looks like a var NAME."""
    n = name.lower()
    return n.isupper() or "_" in name or n in {"null", ""}


def scan_sensitive(pkg_root: Path) -> list[str]:
    issues: list[str] = []
    for path in pkg_root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in HARD_EXCLUDE_DIRS for part in path.parts):
            issues.append(f"runtime dir present: {path}")
            continue
        if path.name in HARD_EXCLUDE_NAMES:
            issues.append(f"credential file present: {path}")
        lowered = path.name.lower()
        if any(s in lowered for s in FORBIDDEN_SNIPPETS):
            issues.append(f"runtime file present: {path}")
    settings = pkg_root / "settings.yaml"
    if settings.is_file():
        for line in settings.read_text(encoding="utf-8-sig").splitlines():
            if "apiKey:" in line or "secret:" in line:
                issues.append(f"{settings}: inline credential key present")
    return issues


def sha1_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_files(root: Path) -> dict[str, Path]:
    return {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file()
    }


def compare(dest_root: Path, manifest: dict) -> tuple[list[str], list[str], list[str]]:
    dest_files = collect_files(dest_root)
    missing, different, extra = [], [], []
    for rel, digest in manifest.get("digests", {}).items():
        dest = dest_root / rel
        if not dest.is_file():
            missing.append(rel)
        elif sha1_file(dest) != digest:
            different.append(rel)
    for rel in dest_files:
        if rel not in manifest.get("digests", {}):
            extra.append(rel)
    return missing, different, extra


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["export", "check", "apply"])
    ap.add_argument("--source", default=None, help="DSH home to export from")
    ap.add_argument("--display", default=None, help="repo skeleton dir (export target / apply package)")
    ap.add_argument("--destination", default=None, help="target DSH home for check/apply")
    ap.add_argument("--name", default="manifest.json")
    args = ap.parse_args()

    src = Path(args.source) if args.source else home_dir()
    display = Path(args.display) if args.display else Path("dsh-config")

    if args.mode == "export":
        if not src.is_dir():
            print(f"FAIL: source is not a directory: {src}")
            return 1
        display.mkdir(parents=True, exist_ok=True)
        digests: dict[str, str] = {}
        for rel in DEFAULT_INCLUDE:
            f = src / rel
            if f.is_file():
                shutil.copy2(f, display / rel)
                digests[rel] = sha1_file(f)
        issues = scan_sensitive(display)
        if issues:
            for i in issues:
                print(f"FAIL: {i}")
            return 1
        (display / args.name).write_text(
            json.dumps({"dsh_home_src": str(src), "digests": digests}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"export: completed -> {display}")
        print(f"sensitive-data scan: PASS")
        print(f"files: {', '.join(digests) or '(none)'}")
        return 0

    if not (display / args.name).is_file():
        print(f"FAIL: no manifest at {display / args.name}")
        return 1
    manifest = json.loads((display / args.name).read_text(encoding="utf-8-sig"))
    dest = Path(args.destination) if args.destination else home_dir()

    missing, different, extra = compare(dest, manifest)
    print(f"mode: {args.mode}")
    print(f"missing={missing} different={different} extra={extra}")
    if args.mode == "check":
        status = "PASS" if not (missing or different) else "PARTIAL"
        print(f"check: {status}")
        return 0
    if not missing and not different:
        print("apply: nothing to do")
        return 0
    for rel in list(missing) + list(different):
        src_f = display / rel
        if not src_f.is_file():
            continue
        dst_f = dest / rel
        dst_f.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_f, dst_f)
    missing2, different2, _ = compare(dest, manifest)
    status = "PASS" if not (missing2 or different2) else "FAIL"
    print(f"apply: completed")
    print(f"post-apply SHA-256 check: {status}")
    print(f"destination-only files kept: {len(extra)}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
