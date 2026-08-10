#!/usr/bin/env python3
"""Run a deterministic behavior-quality preflight for one Skill or a Skill repository."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


FRONTMATTER_RE = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---(?:\s*\r?\n|$)", re.DOTALL)
CJK_RE = re.compile(r"[\u3400-\u9fff]")
SIGNALS = {
    "boundary": re.compile(r"(?i)scope|trigger|when not|not for|applicability|适用|触发|不适用|边界"),
    "output": re.compile(r"(?i)output|contract|输出|契约"),
    "verification": re.compile(r"(?i)verify|validation|test|safety|guardrail|验证|回归|安全|门禁"),
}


def parse_frontmatter(text: str) -> tuple[dict[str, str], list[str]]:
    errors: list[str] = []
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, ["缺少 YAML frontmatter"]

    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key in {"name", "description"}:
            values[key] = value

    for required in ("name", "description"):
        if not values.get(required):
            errors.append(f"frontmatter 缺少 {required}")
    return values, errors


def display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def check_package(package: Path, root: Path) -> dict[str, Any]:
    skill_path = package / "SKILL.md"
    errors: list[str] = []
    warnings: list[str] = []
    if not skill_path.is_file():
        return {
            "skill": package.name,
            "path": display_path(package, root),
            "pass": False,
            "errors": ["缺少 SKILL.md"],
            "warnings": [],
        }

    try:
        text = skill_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        return {
            "skill": package.name,
            "path": display_path(package, root),
            "pass": False,
            "errors": [f"SKILL.md 不是 UTF-8: {exc}"],
            "warnings": [],
        }

    frontmatter, frontmatter_errors = parse_frontmatter(text)
    errors.extend(frontmatter_errors)
    body = FRONTMATTER_RE.sub("", text, count=1)
    line_count = len(text.splitlines())
    if line_count > 500:
        errors.append(f"SKILL.md 超过 500 行: {line_count}")
    if "TODO" in text:
        errors.append("SKILL.md 仍包含 TODO 占位符")
    for name, pattern in SIGNALS.items():
        if not pattern.search(body):
            errors.append(f"缺少 {name} 质量信号")

    agent_path = package / "agents" / "openai.yaml"
    if not agent_path.is_file():
        errors.append("缺少 agents/openai.yaml")
    else:
        agent_text = agent_path.read_text(encoding="utf-8-sig")
        for field in ("display_name", "short_description", "default_prompt"):
            if not re.search(rf"(?m)^\s+{field}:\s*\S+", agent_text):
                errors.append(f"agents/openai.yaml 缺少 interface.{field}")
        if not CJK_RE.search(agent_text):
            errors.append("agents/openai.yaml 缺少中文 UI 描述")

    examples = sorted((package / "examples").glob("*.md")) if (package / "examples").is_dir() else []
    if not examples:
        errors.append("缺少非空 examples/*.md")
    elif any(not example.read_text(encoding="utf-8-sig").strip() for example in examples):
        errors.append("examples/ 中存在空文件")

    if len(body.split()) < 20:
        warnings.append("正文过短，可能缺少可执行流程")

    return {
        "skill": frontmatter.get("name", package.name),
        "path": display_path(package, root),
        "lines": line_count,
        "pass": not errors,
        "errors": errors,
        "warnings": warnings,
    }


def discover_packages(root: Path, selected: str | None) -> list[Path]:
    if selected:
        return [(root / selected).resolve()]

    manifest_path = root / "skills.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        return [(root / entry["path"]).resolve() for entry in manifest.get("skills", [])]
    return sorted(
        child for child in root.iterdir() if child.is_dir() and (child / "SKILL.md").is_file()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Skill 仓库或单个 Skill 的父目录")
    parser.add_argument("--skill", help="只检查指定的仓库相对 Skill 目录")
    parser.add_argument("--strict", action="store_true", help="将警告也视为失败")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    root = args.root.resolve()
    packages = discover_packages(root, args.skill)
    results = [check_package(package, root) for package in packages]
    errors = [f"{item['skill']}: {error}" for item in results for error in item["errors"]]
    warnings = [f"{item['skill']}: {warning}" for item in results for warning in item["warnings"]]
    passed = not errors and (not args.strict or not warnings)
    result = {"pass": passed, "root": str(root), "results": results, "errors": errors, "warnings": warnings}

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for item in results:
            state = "PASS" if item["pass"] else "FAIL"
            print(f"{state}: {item['skill']} errors={len(item['errors'])} warnings={len(item['warnings'])}")
        print(f"Summary: {'PASS' if passed else 'FAIL'} ({len(results)} skill(s))")
        for message in errors:
            print(f"ERROR: {message}")
        for message in warnings:
            print(f"WARN: {message}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
