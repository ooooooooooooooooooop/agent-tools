#!/usr/bin/env python3
"""Validate the publishable structure of this skills repository.

The validator intentionally uses only the Python standard library so it can run
on a clean machine before any optional tooling is installed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "skills.json"
MCP_MANIFEST_PATH = ROOT / "mcp.json"
FRONTMATTER_RE = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---(?:\s*\r?\n|$)", re.DOTALL)
LOCAL_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VALID_CATEGORIES = {"reasoning", "workflow", "writing", "reporting", "maintenance"}
VALID_PRIORITIES = {"P0", "P1", "P2"}
VALID_TIERS = {"core", "conditional", "optional"}
CJK_RE = re.compile(r"[\u3400-\u9fff]")
EXCLUDED_DIRS = {
    ".git",
    ".claude",
    ".grepai",
    ".taskflow",
    "node_modules",
    "Users",
    "__pycache__",
    "_template",
}
MCP_FORBIDDEN_NAMES = {
    "config.json",
    "state.sqlite",
    "agent-broker.log",
}
MCP_FORBIDDEN_SUFFIXES = {".jsonl", ".log"}


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def inside_root(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def parse_frontmatter(path: Path) -> Tuple[Dict[str, str], List[str]]:
    errors: List[str] = []
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        return {}, [f"{rel(path)} is not valid UTF-8: {exc}"]

    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, [f"{rel(path)} is missing YAML frontmatter"]

    values: Dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key in {"name", "description", "version"}:
            values[key] = value

    for required in ("name", "description"):
        if not values.get(required):
            errors.append(f"{rel(path)} frontmatter is missing {required}")
    return values, errors


def discover_skill_dirs() -> List[Path]:
    """Locate Skill package directories (a dir containing SKILL.md).

    The canonical layout nests packages under ``skills/<name>/`` (Skills CLI
    convention). For backward compatibility a package directly under the root is
    still recognised. ``EXCLUDED_DIRS`` and dotted dirs are never treated as Skill
    packages."""
    result: List[Path] = []

    def consider(dir_candidate: Path) -> bool:
        if not dir_candidate.is_dir() or dir_candidate.name in EXCLUDED_DIRS:
            return False
        if dir_candidate.name.startswith("."):
            return False
        return (dir_candidate / "SKILL.md").is_file()

    # Canonical: skills/<name>/
    skills_root = ROOT / "skills"
    if (skills_root / "SKILL.md").is_dir():  # defensive; normally skills/ has no own SKILL.md
        pass
    if skills_root.is_dir():
        for child in skills_root.iterdir():
            if consider(child):
                result.append(child)

    # Legacy compatibility: a package directly at the repo root.
    for child in ROOT.iterdir():
        if child == skills_root:
            continue
        if consider(child):
            result.append(child)

    return sorted(result, key=lambda item: item.name.lower())


def parse_agent_metadata(path: Path) -> Tuple[Dict[str, str], List[str]]:
    """Read the small, fixed UI metadata contract without requiring PyYAML."""
    errors: List[str] = []
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        return {}, [f"{rel(path)} is not valid UTF-8: {exc}"]

    if not re.search(r"(?m)^interface:\s*$", text):
        errors.append(f"{rel(path)} is missing an interface mapping")

    values: Dict[str, str] = {}
    for key in ("display_name", "short_description", "default_prompt"):
        match = re.search(rf"(?m)^\s+{key}:\s*(.*?)\s*$", text)
        if match:
            value = match.group(1).strip().strip('"').strip("'")
            if value and value not in {"|", ">"}:
                values[key] = value
        if not values.get(key):
            errors.append(f"{rel(path)} is missing interface.{key}")
        elif not CJK_RE.search(values[key]):
            errors.append(f"{rel(path)} interface.{key} must contain a Chinese description")
    return values, errors


def check_local_links() -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    for path in ROOT.rglob("*.md"):
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            errors.append(f"{rel(path)} is not valid UTF-8: {exc}")
            continue

        for match in LOCAL_LINK_RE.finditer(text):
            target = match.group(1).strip().strip("<>")
            if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target = target.split("#", 1)[0]
            if not target:
                continue
            target_path = (path.parent / target).resolve()
            if not inside_root(target_path):
                warnings.append(f"{rel(path)} links outside the repository: {target}")
            elif not target_path.exists():
                errors.append(f"{rel(path)} links to missing path: {target}")
    return errors, warnings


def validate_mcp_manifest() -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    """Validate MCP packages without imposing the Skill package contract."""
    checked: List[Dict[str, Any]] = []
    errors: List[str] = []
    warnings: List[str] = []
    if not MCP_MANIFEST_PATH.is_file():
        return checked, ["mcp.json is missing"], warnings
    try:
        manifest = json.loads(MCP_MANIFEST_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return checked, [f"mcp.json cannot be read as JSON: {exc}"], warnings

    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        errors.append("mcp.json schema_version must be 1")
    entries = manifest.get("mcp_servers") if isinstance(manifest, dict) else None
    if not isinstance(entries, list):
        errors.append("mcp.json must contain a top-level mcp_servers array")
        return checked, errors, warnings

    names: set[str] = set()
    required_text = (
        "name",
        "path",
        "description",
        "upstream_repository",
        "upstream_commit",
        "upstream_version",
        "distribution_revision",
        "entrypoint",
        "installer",
        "license",
        "license_file",
        "python",
    )
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"mcp_servers[{index}] must be an object")
            continue
        for field in required_text:
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                errors.append(f"mcp_servers[{index}] is missing a valid {field}")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        if not SKILL_NAME_RE.fullmatch(name):
            errors.append(f"mcp_servers[{index}] has invalid hyphenated name: {name}")
        if name in names:
            errors.append(f"duplicate MCP name in mcp.json: {name}")
        names.add(name)
        if not CJK_RE.search(str(entry.get("description") or "")):
            errors.append(f"{name} MCP description must contain Chinese")
        if not re.fullmatch(r"[0-9a-f]{40}", str(entry.get("upstream_commit") or "")):
            errors.append(f"{name} upstream_commit must be a lowercase 40-character Git SHA")
        platforms = entry.get("platforms")
        if not isinstance(platforms, list) or not platforms or not all(
            isinstance(item, str) and item.strip() for item in platforms
        ):
            errors.append(f"{name} must declare a non-empty platforms array")

        package_path = entry.get("path")
        if not isinstance(package_path, str) or not package_path:
            continue
        package = (ROOT / package_path).resolve()
        if not inside_root(package):
            errors.append(f"{name} points outside the repository: {package_path}")
            continue
        if not package.is_dir():
            errors.append(f"{name} MCP package directory is missing: {package_path}")
            continue

        for field in ("entrypoint", "installer", "license_file"):
            value = entry.get(field)
            if not isinstance(value, str) or not value:
                continue
            target = (package / value).resolve()
            if not inside_root(target) or not target.is_file():
                errors.append(f"{name} {field} is missing or outside the repository: {value}")
        readme = package / "README.md"
        if not readme.is_file():
            errors.append(f"{name} MCP package is missing README.md")
        tests_dir = package / "tests"
        if not tests_dir.is_dir() or not any(tests_dir.glob("test_*.py")):
            warnings.append(f"{name} MCP package has no Python regression tests")

        license_path = package / str(entry.get("license_file") or "LICENSE")
        if license_path.is_file():
            try:
                license_text = license_path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError as exc:
                errors.append(f"{rel(license_path)} is not valid UTF-8: {exc}")
            else:
                if entry.get("license") == "PolyForm-Noncommercial-1.0.0":
                    if "PolyForm Noncommercial License 1.0.0" not in license_text:
                        errors.append(f"{name} license file does not contain the declared PolyForm license")
                    if "Required Notice:" not in license_text:
                        errors.append(f"{name} license file is missing the upstream Required Notice")

        for path in package.rglob("*"):
            if not path.is_file():
                continue
            lowered = path.name.lower()
            if lowered in MCP_FORBIDDEN_NAMES or path.suffix.lower() in MCP_FORBIDDEN_SUFFIXES:
                errors.append(f"{name} publishes forbidden runtime file: {rel(path)}")

        checked.append(
            {
                "name": name,
                "path": package_path,
                "upstream_commit": entry.get("upstream_commit"),
                "upstream_version": entry.get("upstream_version"),
                "distribution_revision": entry.get("distribution_revision"),
                "license": entry.get("license"),
                "platforms": platforms,
            }
        )
    return checked, errors, warnings


def validate(strict: bool = False) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []

    if not MANIFEST_PATH.is_file():
        errors.append("skills.json is missing")
        return {"pass": False, "errors": errors, "warnings": warnings, "skills": []}

    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"skills.json cannot be read as JSON: {exc}")
        return {"pass": False, "errors": errors, "warnings": warnings, "skills": []}

    entries = manifest.get("skills") if isinstance(manifest, dict) else None
    if not isinstance(entries, list):
        errors.append("skills.json must contain a top-level skills array")
        entries = []

    manifest_names: List[str] = []
    checked_skills: List[Dict[str, Any]] = []
    dependency_checks: List[Tuple[str, List[str]]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"skills[{index}] must be an object")
            continue
        name = entry.get("name")
        package_path = entry.get("path")
        if not isinstance(name, str) or not name:
            errors.append(f"skills[{index}] is missing a valid name")
            continue
        if name in manifest_names:
            errors.append(f"duplicate skill name in skills.json: {name}")
        if not SKILL_NAME_RE.fullmatch(name):
            errors.append(f"skills[{index}] has invalid hyphenated name: {name}")
        manifest_names.append(name)
        if not isinstance(package_path, str) or not package_path:
            errors.append(f"{name} is missing a valid path")
            continue

        if not isinstance(entry.get("version"), str) or not entry["version"].strip():
            errors.append(f"{name} is missing a valid version")
        if not isinstance(entry.get("description"), str) or not entry["description"].strip():
            errors.append(f"{name} is missing a valid registry description")
        elif not CJK_RE.search(entry["description"]):
            errors.append(f"{name} registry description must contain Chinese")
        languages = entry.get("lang")
        if not isinstance(languages, list) or not languages or not all(isinstance(item, str) and item.strip() for item in languages):
            errors.append(f"{name} must declare a non-empty lang array")
        category = entry.get("category")
        if category not in VALID_CATEGORIES:
            errors.append(f"{name} has invalid category: {category!r}")
        priority = entry.get("priority")
        if priority not in VALID_PRIORITIES:
            errors.append(f"{name} has invalid priority: {priority!r}")
        tier = entry.get("tier")
        if tier not in VALID_TIERS:
            errors.append(f"{name} has invalid tier: {tier!r}")
        dependencies = entry.get("depends_on")
        if not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
            errors.append(f"{name} must declare depends_on as a list of skill names")
            dependencies = []
        dependency_checks.append((name, dependencies))

        package = (ROOT / package_path).resolve()
        if not inside_root(package):
            errors.append(f"{name} points outside the repository: {package_path}")
            continue
        skill_file = package / "SKILL.md"
        if not package.is_dir() or not skill_file.is_file():
            errors.append(f"{name} does not contain SKILL.md: {package_path}")
            continue

        frontmatter, frontmatter_errors = parse_frontmatter(skill_file)
        errors.extend(frontmatter_errors)
        try:
            skill_text = skill_file.read_text(encoding="utf-8-sig")
            frontmatter_match = FRONTMATTER_RE.match(skill_text)
            if not frontmatter_match or not CJK_RE.search(frontmatter_match.group(1)):
                errors.append(f"{rel(skill_file)} frontmatter description must contain Chinese")
        except UnicodeDecodeError:
            pass
        if frontmatter.get("name") and frontmatter["name"] != name:
            errors.append(
                f"{rel(skill_file)} name does not match manifest: "
                f"{frontmatter['name']} != {name}"
            )

        missing_optional: List[str] = []
        agent_file = package / "agents" / "openai.yaml"
        if not agent_file.is_file():
            missing_optional.append("agents/openai.yaml")
        else:
            _, agent_errors = parse_agent_metadata(agent_file)
            errors.extend(agent_errors)

        examples_dir = package / "examples"
        example_files = sorted(examples_dir.glob("*.md")) if examples_dir.is_dir() else []
        if not example_files:
            missing_optional.append("examples/*.md")
        for example in example_files:
            try:
                if not example.read_text(encoding="utf-8-sig").strip():
                    errors.append(f"{rel(example)} is empty")
            except UnicodeDecodeError as exc:
                errors.append(f"{rel(example)} is not valid UTF-8: {exc}")
        if missing_optional:
            warnings.append(f"{name} is missing optional package parts: {', '.join(missing_optional)}")

        checked_skills.append(
            {
                "name": name,
                "path": package_path,
                "version": entry.get("version"),
                "frontmatter_version": frontmatter.get("version"),
                "missing_optional": missing_optional,
                "category": entry.get("category"),
                "priority": entry.get("priority"),
                "tier": entry.get("tier"),
                "depends_on": entry.get("depends_on", []),
            }
        )

    known_names = set(manifest_names)

    profiles = manifest.get("profiles") if isinstance(manifest, dict) else None
    if not isinstance(profiles, dict) or not profiles:
        errors.append("skills.json must contain a non-empty profiles mapping")
        profiles = {}
    for profile_name, profile_skills in profiles.items():
        if not isinstance(profile_name, str) or not SKILL_NAME_RE.fullmatch(profile_name):
            errors.append(f"invalid install profile name: {profile_name!r}")
            continue
        if not isinstance(profile_skills, list) or not all(isinstance(item, str) for item in profile_skills):
            errors.append(f"profile {profile_name} must contain a list of skill names")
            continue
        if len(profile_skills) != len(set(profile_skills)):
            errors.append(f"profile {profile_name} contains duplicate skill names")
        unknown_profile_skills = sorted(set(profile_skills) - known_names)
        for skill_name in unknown_profile_skills:
            errors.append(f"profile {profile_name} contains unregistered skill: {skill_name}")
    full_profile = profiles.get("full")
    if isinstance(full_profile, list) and set(full_profile) != known_names:
        errors.append("profile full must contain every registered skill exactly once")
    core_profile = profiles.get("core")
    tier_by_name = {
        item["name"]: item.get("tier")
        for item in checked_skills
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if isinstance(core_profile, list):
        for skill_name in core_profile:
            if tier_by_name.get(skill_name) != "core":
                errors.append(f"profile core contains a non-core skill: {skill_name}")

    for name, dependencies in dependency_checks:
        for dependency in dependencies:
            if dependency == name:
                errors.append(f"{name} cannot depend on itself")
            elif dependency not in known_names:
                errors.append(f"{name} depends on unregistered skill: {dependency}")

    discovered = {path.name: path for path in discover_skill_dirs()}
    for name in sorted(discovered):
        if name not in manifest_names:
            errors.append(f"skill directory is not registered in skills.json: {name}")

    checked_mcp, mcp_errors, mcp_warnings = validate_mcp_manifest()
    errors.extend(mcp_errors)
    warnings.extend(mcp_warnings)

    link_errors, link_warnings = check_local_links()
    errors.extend(link_errors)
    warnings.extend(link_warnings)

    if strict:
        errors.extend(warnings)
        warnings = []

    return {
        "pass": not errors,
        "root": str(ROOT),
        "skills": checked_skills,
        "mcp_servers": checked_mcp,
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="treat optional package warnings as errors")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    result = validate(strict=args.strict)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        status = "PASS" if result["pass"] else "FAIL"
        print(
            f"{status}: {len(result['skills'])} manifest skill(s), "
            f"{len(result.get('mcp_servers', []))} MCP server(s) checked"
        )
        for message in result["errors"]:
            print(f"ERROR: {message}")
        for message in result["warnings"]:
            print(f"WARN: {message}")
        print(
            f"Summary: errors={len(result['errors'])}, "
            f"warnings={len(result['warnings'])}"
        )
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
