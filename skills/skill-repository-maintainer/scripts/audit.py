#!/usr/bin/env python3
"""Read-only audit for a repository following the Codex skill package layout."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER_RE = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---(?:\s*\r?\n|$)", re.DOTALL)
IGNORED_DIRS = {".git", ".claude", ".grepai", ".taskflow", "node_modules", "Users", "_template"}


def read_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8-sig")
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def audit(root: Path) -> dict[str, object]:
    errors: list[str] = []
    warnings: list[str] = []
    manifest_path = root / "skills.json"
    if not manifest_path.is_file():
        return {"pass": False, "errors": ["skills.json is missing"], "warnings": []}

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"pass": False, "errors": [f"skills.json is unreadable: {exc}"], "warnings": []}

    entries = manifest.get("skills") if isinstance(manifest, dict) else None
    if not isinstance(entries, list):
        return {"pass": False, "errors": ["skills.json.skills must be an array"], "warnings": []}

    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append("manifest contains a non-object entry")
            continue
        name = entry.get("name")
        package_path = entry.get("path")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            errors.append(f"invalid skill name: {name!r}")
            continue
        if name in seen:
            errors.append(f"duplicate skill: {name}")
        seen.add(name)
        package = (root / str(package_path)).resolve()
        try:
            package.relative_to(root.resolve())
        except ValueError:
            errors.append(f"package is outside root: {name}")
            continue
        skill_file = package / "SKILL.md"
        if not skill_file.is_file():
            errors.append(f"{name}: SKILL.md is missing")
            continue
        try:
            frontmatter = read_frontmatter(skill_file)
        except UnicodeDecodeError as exc:
            errors.append(f"{name}: SKILL.md is not UTF-8: {exc}")
            continue
        if frontmatter.get("name") != name or not frontmatter.get("description"):
            errors.append(f"{name}: frontmatter name/description is invalid")
        agent = package / "agents" / "openai.yaml"
        if not agent.is_file():
            errors.append(f"{name}: agents/openai.yaml is missing")
        examples = list((package / "examples").glob("*.md")) if (package / "examples").is_dir() else []
        if not examples:
            warnings.append(f"{name}: no Markdown example")

    registered = seen
    # Skill packages live under skills/ (canonical) — scan there; also check legacy
    # root-level packages for unregistered SKILL.md dirs.
    scan_roots = [root / "skills"] if (root / "skills").is_dir() else [root]
    if root / "skills" != root:
        scan_roots.append(root)
    for base in scan_roots:
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.name in IGNORED_DIRS:
                continue
            if child.is_dir() and (child / "SKILL.md").is_file() and child.name not in registered:
                errors.append(f"unregistered skill directory: {child.name}")

    return {"pass": not errors, "errors": errors, "warnings": warnings, "skill_count": len(entries)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = audit(args.root.resolve())
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{'PASS' if result['pass'] else 'FAIL'}: {result.get('skill_count', 0)} skill(s)")
        for message in result["errors"]:
            print(f"ERROR: {message}")
        for message in result["warnings"]:
            print(f"WARN: {message}")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
