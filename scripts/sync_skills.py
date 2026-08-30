#!/usr/bin/env python3
"""Check or explicitly sync published skill packages and optionally delegate
DSH runtime composition to the canonical AIC apply path. Also supports checking dsh-config archive consistency and
MCP server path validity.

The command never deletes destination files. Use ``--check`` for a read-only
comparison and ``--apply`` only when the destination is intentional.
Pass ``--plugins-destination`` for compatibility; DSH runtime composition is
delegated to ``aic diff/apply dsh`` and is not copied by this script.
Pass ``--dsh-config-dir`` (with ``--check``) to compare a dsh-config archive
against the live ``~/.dsh``.
Pass ``--mcp-cordis`` (with ``--check``) to validate MCP server paths in a
cordis.patch.yml. ``--mcp-dir`` provides an alternative MCP source root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
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


def sha256_portable(path: Path) -> str:
    """SHA-256 over newline-normalized bytes (CRLF/CR -> LF).

    Used for the git-transported dsh-config archive, whose checkout line
    endings depend on each machine's autocrlf; raw hashing false-diffs there.
    """
    data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(data).hexdigest()


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


def discover_dsh_plugins() -> List[Dict[str, Any]]:
    """Discover DSH user plugins under ``dsh/*`` that are registered in cordis.patch.yml.

    Each plugin package's ``cordis.patch.yml`` insert entries reference runtime
    plugin files via ``name: <file>`` or ``name: ./plugins/<file>``. Only those
    referenced files are treated as deployable plugin code, so test scripts and
    READMEs are excluded automatically.
    """
    plugins: List[Dict[str, Any]] = []
    dsh_root = ROOT / "dsh"
    if not dsh_root.is_dir():
        return plugins
    name_pattern = re.compile(r"name:\s*['\"]?(?:\./plugins/)?([^'\"\n]+\.(?:js|mjs))['\"]?")
    for package in sorted(dsh_root.iterdir()):
        if not package.is_dir():
            continue
        patch = package / "cordis.patch.yml"
        if not patch.is_file():
            continue
        text = patch.read_text(encoding="utf-8")
        for match in name_pattern.finditer(text):
            source = package / match.group(1)
            if not source.is_file():
                raise ValueError(f"plugin file referenced in {patch} is missing: {source}")
            plugins.append({"name": match.group(1), "source": source})
    return plugins


def compare_file(source: Path, destination: Path) -> Dict[str, Any]:
    if not destination.is_file():
        return {"missing": [source.name], "different": [], "same": [], "extra": [], "pass": False}
    if sha256(source) == sha256(destination):
        return {"missing": [], "different": [], "same": [source.name], "extra": [], "pass": True}
    return {"missing": [], "different": [source.name], "same": [], "extra": [], "pass": False}


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


def check_dsh_config_archive(archive_dir: Path) -> Dict[str, Any] | None:
    """Compare a dsh-config archive (manifest.json) against the live ``~/.dsh``.

    Returns ``None`` if the archive has no manifest, otherwise a comparison
    dict with the same ``missing``/``different``/``extra``/``pass`` keys.
    """
    manifest_path = archive_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    digests: dict = manifest.get("digests") or {}
    templated: set = set((manifest.get("templates") or {}).get("files") or {})

    dest = Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
    if not dest.is_dir():
        return {"missing": list(digests), "different": [], "extra": [], "pass": False}

    SKIP_DIRS = {"sessions", "storages", "skills", "__pycache__", "node_modules"}
    dest_files: dict[str, Path] = {}
    for dirpath, dirnames, filenames in os.walk(dest, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        base = Path(dirpath)
        for name in filenames:
            p = base / name
            dest_files[p.relative_to(dest).as_posix()] = p

    missing, different, same = [], [], []
    for rel, digest in sorted(digests.items()):
        dst = dest / rel
        if not dst.is_file():
            missing.append(rel)
        elif rel in templated:
            same.append(rel)  # templated: presence only
        elif sha256_portable(dst) != digest:
            different.append(rel)
        else:
            same.append(rel)

    extra = sorted(set(dest_files) - set(digests))
    return {"missing": missing, "different": different, "same": same, "extra": extra, "pass": not missing and not different}


def check_mcp_cordis(cordis_file: Path, mcp_dir: Path | None = None) -> Dict[str, Any]:
    """Verify MCP server entries in a cordis.patch.yml reference valid paths.

    Returns a dict with ``issue`` (list of warning strings) and ``pass``.
    If ``mcp_dir`` is given, the referenced script path is resolved against it.
    """
    issues: list[str] = []
    if not cordis_file.is_file():
        return {"pass": False, "issues": [f"cordis patch file not found: {cordis_file}"]}

    text = cordis_file.read_text(encoding="utf-8")
    # Locate insert blocks that register an @deepseek-ai/dsh-mcp-client
    insert_pattern = re.compile(r"- insert:\s*\n(\s+- id:.*?)(?=\n- insert:|\Z)", re.DOTALL)
    for block_match in insert_pattern.finditer(text):
        block = block_match.group(1)
        if "mcp-agent-switchboard" not in block:
            continue
        # Grab args[0] and cwd
        args_match = re.search(r"args:\s*\n\s+-\s+'([^']+)'", block)
        cwd_match = re.search(r"cwd:\s*'([^']+)'", block)
        if args_match:
            script_path = Path(args_match.group(1))
            if mcp_dir:
                script_path = mcp_dir / script_path.name
            if not script_path.is_file():
                issues.append(f"MCP script not found: {script_path}")
        if cwd_match:
            cwd_path = Path(cwd_match.group(1))
            if not cwd_path.is_dir():
                issues.append(f"MCP cwd not found: {cwd_path}")
    return {"pass": len(issues) == 0, "issues": issues}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--plugins-destination", type=Path, help="also sync dsh/* plugins to this directory")
    parser.add_argument("--dsh-config-dir", type=Path, help="check a dsh-config archive against live ~/.dsh (check mode)")
    parser.add_argument("--mcp-cordis", type=Path, help="check MCP server paths in this cordis.patch.yml (check mode)")
    parser.add_argument("--mcp-dir", type=Path, help="alternative MCP source root for --mcp-cordis path resolution")
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

        if args.plugins_destination:
            # Compatibility flag retained for callers of the old interface.
            # The DSH runtime itself has one owner now: delegate to AIC rather
            # than copying individual plugin files or editing a second patch.
            ensure_destination_is_safe(args.plugins_destination)
            aic_script = ROOT / "scripts" / "aic" / "aic.py"
            profile = args.plugins_destination.parent
            dsh_root = profile.parent.parent
            aic_env = {**os.environ, "DSH_HOME": str(dsh_root)}
            operation = "diff" if args.check else "apply"
            runtime_args = [operation, "dsh"]
            if args.check:
                runtime_args.append("--runtime-only")
            proc = subprocess.run(
                [sys.executable, str(aic_script), *runtime_args],
                capture_output=True, text=True, env=aic_env,
                encoding="utf-8", errors="replace", timeout=1800,
            )
            output = (proc.stdout + proc.stderr).strip()
            comparison = {
                "missing": [], "different": [] if proc.returncode == 0 else ["dsh-runtime"],
                "same": ["dsh-runtime"] if proc.returncode == 0 else [],
                "extra": [], "pass": proc.returncode == 0,
                "note": "delegated to aic apply/diff dsh",
            }
            results.append({
                "skill": "dsh/runtime-composition",
                "kind": "dsh-runtime",
                "source": str(aic_script),
                "destination": str(args.plugins_destination),
                "output": output[-4000:],
                **comparison,
            })

        if args.check and args.dsh_config_dir:
            comparison = check_dsh_config_archive(args.dsh_config_dir)
            if comparison is not None:
                results.append(
                    {
                        "skill": "dsh-config",
                        "kind": "dsh-config",
                        "source": str(args.dsh_config_dir),
                        "destination": str(Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))),
                        **comparison,
                    }
                )

        if args.check and args.mcp_cordis:
            mcp_result = check_mcp_cordis(args.mcp_cordis, args.mcp_dir)
            results.append(
                {
                    "skill": "mcp",
                    "kind": "mcp",
                    "source": str(args.mcp_cordis),
                    "destination": "",
                    "missing": [],
                    "different": [],
                    "same": [],
                    "extra": [],
                    "pass": mcp_result["pass"],
                    "issues": mcp_result["issues"],
                }
            )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        if args.json:
            print(json.dumps({"pass": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"ERROR: {exc}")
        return 1

    skill_results = [item for item in results if item.get("kind") in ("skill", None)]
    plugin_results = [item for item in results if item.get("kind") == "plugin"]
    other_results = [item for item in results if item.get("kind") not in ("skill", "plugin", None)]
    result = {
        "pass": all(item["pass"] for item in results),
        "profile": args.profile,
        "skills": sorted(item["skill"] for item in skill_results),
        "plugins": sorted(item["skill"] for item in plugin_results),
        "results": results,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for item in results:
            state = "PASS" if item["pass"] else "DIFF"
            if item.get("kind") == "mcp":
                print(f"{state}: mcp issues={item.get('issues') or 'none'}")
                continue
            print(
                f"{state}: {item['skill']} "
                f"missing={len(item['missing'])} "
                f"different={len(item['different'])} "
                f"extra={len(item['extra'])}"
            )
        counts = f"{len(skill_results)} skill(s)"
        if plugin_results:
            counts += f", {len(plugin_results)} plugin(s)"
        if other_results:
            counts += f", {len(other_results)} check(s)"
        print(f"Summary: {'PASS' if result['pass'] else 'DIFF'} ({counts})")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
