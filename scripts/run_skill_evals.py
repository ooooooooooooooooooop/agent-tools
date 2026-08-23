#!/usr/bin/env python3
"""Structural skill-eval regression for the agent-tooling repository.

Every skill registered in ``skills.json`` must ship a ``SKILL.md``, an
``agents/openai.yaml``, and at least one example under ``examples/``. The
``--live`` mode additionally runs each example prompt through a headless agent
CLI when one is available, so model-backed evals can be added later without
changing the deterministic structural gate. Standard library only.

Exit code 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def check_skill(base: Path, name: str) -> list[str]:
    problems: list[str] = []
    for rel, what in (
        ("SKILL.md", "SKILL.md"),
        ("agents/openai.yaml", "agents/openai.yaml"),
    ):
        if not (base / rel).is_file():
            problems.append(f"{name}: missing {what}")
    examples = sorted((base / "examples").glob("*.md")) if (base / "examples").is_dir() else []
    if not examples:
        problems.append(f"{name}: no examples/*.md")
    return problems


def run_live(base: Path, name: str, cli: str, timeout: int) -> list[str]:
    problems: list[str] = []
    examples = sorted((base / "examples").glob("*.md"))
    for ex in examples:
        text = ex.read_text(encoding="utf-8")
        # First fenced code block is the example prompt when present.
        prompt = text.split("```", 2)[1] if text.count("```") >= 2 else text[:400]
        try:
            proc = subprocess.run(
                [cli, *prompt.splitlines()[:1]],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            problems.append(f"{name}:{ex.name}: live run unavailable: {exc}")
            continue
        if proc.returncode != 0:
            problems.append(f"{name}:{ex.name}: live run exit {proc.returncode}: {proc.stderr[:200]}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", default=str(ROOT / "skills.json"))
    ap.add_argument(
        "--live",
        action="store_true",
        help="run example prompts through a headless agent CLI (experimental)",
    )
    ap.add_argument(
        "--cli",
        default=None,
        help="agent CLI for --live (default: first available of codex/claude/dsh)",
    )
    ap.add_argument("--timeout", type=int, default=60, help="per-example timeout for --live")
    args = ap.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    skills = manifest.get("skills", [])
    failures: list[str] = []
    for entry in skills:
        name = entry.get("name")
        base = ROOT / entry.get("path", "").lstrip("./")
        failures += check_skill(base, name)
        if args.live:
            cli = args.cli or next((c for c in ("codex", "claude", "dsh") if shutil.which(c)), None)
            if cli:
                failures += run_live(base, name, cli, args.timeout)
            else:
                print(f"live: no agent CLI found; structural checks only")
    for problem in failures:
        print(f"FAIL: {problem}")
    total = len(skills)
    print(f"run_skill_evals: {total - len(failures)}/{total} skills PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
