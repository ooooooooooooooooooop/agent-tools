#!/usr/bin/env python3
"""Publish-readiness check for marketplace submission.

Checks that every registered package (skills.json skills, mcp.json servers) is
clean enough to publish externally: required files present, license present for
MCP servers, and no device-specific absolute paths inside code/config files.
Structural consistency itself is covered by ``scripts/validate_repo.py
--strict``; this script only guards the marketplace-facing surface.

Markdown docs are exempt from the path scan (illustrative examples are
expected); the scan covers code/config files (.py .js .ts .yaml .yml .json
.toml .cfg .conf .ini .sh .ps1). Standard library only. Exit 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".ts", ".yaml", ".yml", ".json", ".toml", ".cfg", ".conf", ".ini", ".sh", ".ps1"}
# Packages intentionally kept out of the publish scope. weekly-work-summary is
# a personal-office skill bound to this machine (Shanghai calendar, fixed
# C:\Desktop paths, fixed three-person mapping); per user decision it is not
# templated or published, so it is exempt from the marketplace-facing scan.
PUBLISH_EXCLUDED = {"weekly-work-summary"}
DEVICE_NEEDLES = (
    "c:\\users\\",
    "c:\\desktop\\",
    "c:\\\\users\\\\",
    "c:\\\\desktop\\\\",
    "d:\\users\\",
    "d:\\desktop\\",
    "d:\\\\users\\\\",
    "d:\\\\desktop\\\\",
)


def scan_device_paths(pkg: Path) -> list[str]:
    hits: list[str] = []
    for path in pkg.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SCAN_SUFFIXES:
            continue
        if "tests" in path.parts:
            continue  # test fixtures may hold illustrative paths; not a publish surface
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        lowered = text.lower()
        for needle in DEVICE_NEEDLES:
            if needle in lowered:
                hits.append(f"{path.relative_to(ROOT).as_posix()}: contains {needle!r}")
    return hits


def normalize_license(text: str) -> str:
    """Compare license identifiers ignoring hyphen/space formatting and the
    word 'license' (registry uses SPDX-style IDs, files use human titles)."""
    compact = "".join(ch for ch in text.lower() if ch not in " -_")
    return compact.replace("license", "")


def check_skills() -> list[str]:
    problems: list[str] = []
    manifest = json.loads((ROOT / "skills.json").read_text(encoding="utf-8"))
    for entry in manifest.get("skills", []):
        name = entry.get("name")
        if name in PUBLISH_EXCLUDED:
            print(f"skill {name}: excluded from publish scope (intentionally internal)")
            continue
        base = ROOT / entry.get("path", "").lstrip("./")
        for rel, what in (("SKILL.md", "SKILL.md"), ("agents/openai.yaml", "agents/openai.yaml")):
            if not (base / rel).is_file():
                problems.append(f"skill {name}: missing {what}")
        problems += [f"skill {name}: {hit}" for hit in scan_device_paths(base)]
    return problems


def check_mcp() -> list[str]:
    problems: list[str] = []
    manifest_path = ROOT / "mcp.json"
    if not manifest_path.is_file():
        return ["mcp.json missing"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for server in manifest.get("mcp_servers", []):
        name = server.get("name")
        base = ROOT / server.get("path", "").lstrip("./")
        if not base.is_dir():
            problems.append(f"mcp {name}: package dir missing")
            continue
        for rel, what in (
            ("LICENSE", "license file"),
            (server.get("entrypoint", ""), "entrypoint"),
        ):
            if rel and not (base / rel).is_file():
                problems.append(f"mcp {name}: missing {what} ({rel})")
        license_field = server.get("license")
        if license_field:
            lic_name = server.get("license_file", "LICENSE")
            lic_path = base / lic_name
            lic_text = lic_path.read_text(encoding="utf-8", errors="replace") if lic_path.is_file() else ""
            if normalize_license(license_field) not in normalize_license(lic_text):
                problems.append(f"mcp {name}: license field {license_field!r} not found in {lic_name}")
        problems += [f"mcp {name}: {hit}" for hit in scan_device_paths(base)]
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quiet", action="store_true", help="only print problems and the final verdict")
    args = ap.parse_args()

    problems = check_skills() + check_mcp()
    if not args.quiet:
        for problem in problems:
            print(f"FAIL: {problem}")
    if problems:
        print(f"publish_check: FAIL ({len(problems)} issue(s))")
        return 1
    print("publish_check: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
