#!/usr/bin/env python3
"""Check or explicitly sync published skill packages to another device.

The command never deletes destination files. Use ``--check`` for a read-only
comparison and ``--apply`` only when the destination is intentional.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "skills.json"
IGNORED_NAMES = {".DS_Store"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}


def file_is_publishable(path: Path) -> bool:
    if any(part == "__pycache__" for part in path.parts):
        return False
    if path.name in IGNORED_NAMES or path.suffix in IGNORED_SUFFIXES:
        return False
    return path.is_file()


def package_files(package: Path) -> Dict[str, Path]:
    return {
        path.relative_to(package).as_posix(): path
        for path in package.rglob("*")
        if file_is_publishable(path)
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest() -> Dict[str, Any]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict):
        raise ValueError("skills.json must contain an object")
    return manifest


def load_entries(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    entries = manifest.get("skills")
    if not isinstance(entries, list):
        raise ValueError("skills.json must contain a skills array")
    return [entry for entry in entries if isinstance(entry, dict)]


def ensure_destination_is_safe(destination: Path) -> None:
    resolved = destination.resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError:
        return
    raise ValueError(f"destination must be outside the repository: {destination}")


def compare_package(source: Path, destination: Path) -> Dict[str, Any]:
    source_files = package_files(source)
    destination_files = package_files(destination) if destination.exists() else {}
    missing: List[str] = []
    different: List[str] = []
    same: List[str] = []

    for relative, source_file in sorted(source_files.items()):
        destination_file = destination / relative
        if not destination_file.is_file():
            missing.append(relative)
        elif sha256(source_file) == sha256(destination_file):
            same.append(relative)
        else:
            different.append(relative)

    extra = sorted(set(destination_files) - set(source_files))
    return {
        "missing": missing,
        "different": different,
        "same": same,
        "extra": extra,
        "pass": not missing and not different,
    }


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        shutil.copy2(source, temporary_path)
        os.replace(temporary_path, destination)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--skill", action="append", dest="skills", help="limit to one or more skill names")
    selection.add_argument("--profile", help="sync a manifest install profile such as core or full")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="read-only comparison")
    mode.add_argument("--apply", action="store_true", help="copy source files to the destination")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    try:
        from validate_repo import validate

        source_validation = validate(strict=True)
        if not source_validation["pass"]:
            details = "; ".join(source_validation["errors"][:5])
            raise ValueError(f"source repository validation failed: {details}")
        ensure_destination_is_safe(args.destination)
        manifest = load_manifest()
        entries = load_entries(manifest)
        known = {entry.get("name"): entry for entry in entries}
        if args.profile:
            profiles = manifest.get("profiles")
            if not isinstance(profiles, dict) or args.profile not in profiles:
                raise ValueError(f"unknown install profile: {args.profile}")
            selected = set(profiles[args.profile])
        else:
            selected = set(args.skills or [entry.get("name") for entry in entries])
        unknown = sorted(selected - set(known))
        if unknown:
            raise ValueError(f"unknown skill name(s): {', '.join(unknown)}")

        results: List[Dict[str, Any]] = []
        for name in sorted(selected):
            entry = known[name]
            source = (ROOT / entry["path"]).resolve()
            if not source.is_dir() or not (source / "SKILL.md").is_file():
                raise ValueError(f"skill package is missing: {name}")
            destination = args.destination / name
            comparison = compare_package(source, destination)

            if args.apply:
                for relative in sorted(set(comparison["missing"] + comparison["different"])):
                    atomic_copy(source / relative, destination / relative)
                comparison = compare_package(source, destination)

            results.append(
                {
                    "skill": name,
                    "source": str(source),
                    "destination": str(destination),
                    **comparison,
                }
            )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        if args.json:
            print(json.dumps({"pass": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"ERROR: {exc}")
        return 1

    result = {
        "pass": all(item["pass"] for item in results),
        "profile": args.profile,
        "skills": sorted(item["skill"] for item in results),
        "results": results,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for item in results:
            state = "PASS" if item["pass"] else "DIFF"
            print(
                f"{state}: {item['skill']} "
                f"missing={len(item['missing'])} "
                f"different={len(item['different'])} "
                f"extra={len(item['extra'])}"
            )
        print(f"Summary: {'PASS' if result['pass'] else 'DIFF'} ({len(results)} skill(s))")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
