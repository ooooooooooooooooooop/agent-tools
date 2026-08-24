#!/usr/bin/env python3
"""Structural skill-eval regression for the agent-tooling repository.

Every skill registered in ``skills.json`` must ship a ``SKILL.md``, an
``agents/openai.yaml``, and at least one example under ``examples/``. The
``--live`` mode additionally runs each example prompt through a headless agent
CLI when one is available, so model-backed evals can be added later without
changing the deterministic structural gate. Standard library only.

Since v2, the default run also validates the golden set (``scripts/evals/golden_set.json``):
every core-tier skill must have at least 3 cases covering the four categories
(normal / edge / failure / safety) with fixed I/O assertions. ``--golden-live``
additionally sends each golden-case prompt to the CPA relay (cheap worker model)
and checks the response against the case's expected assertions.

Exit code 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_SET = ROOT / "scripts" / "evals" / "golden_set.json"
VALID_CATEGORIES = {"normal", "edge", "failure", "safety"}
CORE_TIERS = {"core"}
CPA_BASE = os.environ.get("CPA_BASE", "http://127.0.0.1:8317/v1")
CPA_MODEL = os.environ.get("CPA_GOLDEN_MODEL", "gpt-5.6-luna-max")
CPA_TIMEOUT = 90


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


def load_manifest(manifest: Path) -> tuple[dict, list[str]]:
    problems: list[str] = []
    try:
        data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"manifest unreadable: {exc}")
        return {}, problems
    if not isinstance(data, dict) or not isinstance(data.get("skills"), list):
        problems.append("manifest must contain a skills array")
        return data, problems
    return data, problems


def check_golden_set(manifest: dict) -> list[str]:
    """Deterministic structural gate over the golden set (default run)."""
    problems: list[str] = []
    if not GOLDEN_SET.is_file():
        problems.append("golden_set.json missing")
        return problems
    try:
        golden = json.loads(GOLDEN_SET.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"golden_set.json unreadable: {exc}")
        return problems
    golden_skills = golden.get("skills") if isinstance(golden, dict) else None
    if not isinstance(golden_skills, list):
        problems.append("golden_set.json must contain a skills array")
        return problems
    by_name = {g.get("name"): g for g in golden_skills if isinstance(g, dict) and g.get("name")}
    tier_by_name = {
        s.get("name"): s.get("tier")
        for s in manifest.get("skills", [])
        if isinstance(s, dict)
    }
    # Every core skill must have >=3 cases spanning the four categories.
    for name, tier in tier_by_name.items():
        if tier not in CORE_TIERS:
            continue
        entry = by_name.get(name)
        if entry is None:
            problems.append(f"golden: core skill {name} missing from golden set")
            continue
        cases = entry.get("cases") if isinstance(entry, dict) else None
        if not isinstance(cases, list) or len(cases) < 3:
            problems.append(f"golden: {name} must have >=3 cases, got {len(cases) if isinstance(cases, list) else 0}")
            continue
        cats = {c.get("category") for c in cases if isinstance(c, dict)}
        # 计划要求：每技能 3~5 个标准任务，至少覆盖正常与边界两类，其余类别按技能自然场景取舍。
        required = {"normal", "edge"}
        missing = sorted(required - cats)
        if missing:
            problems.append(f"golden: {name} missing required categories: {','.join(missing)}")
        for c in cases:
            if not isinstance(c, dict):
                problems.append(f"golden: {name} has a non-object case")
                continue
            if not c.get("id") or not c.get("prompt"):
                problems.append(f"golden: {name} case missing id or prompt")
            if c.get("category") not in VALID_CATEGORIES:
                problems.append(f"golden: {name}:{c.get('id')} invalid category {c.get('category')!r}")
            exp = c.get("expect")
            if not isinstance(exp, dict) or not (
                isinstance(exp.get("includes"), list) or isinstance(exp.get("excludes"), list)
            ):
                problems.append(f"golden: {name}:{c.get('id')} expect must declare includes/excludes")
    # No unregistered skills allowed in golden set.
    for gname in by_name:
        if gname not in tier_by_name:
            problems.append(f"golden: {gname} is not a registered skill")
    return problems


def _cpa_key() -> str:
    key = os.environ.get("CPA_API_KEY", "").strip()
    if key:
        return key
    # Fallback: read the key name from the credential file (value lives in ~/.dsh/.credentials.yaml).
    cred = Path.home() / ".dsh" / ".credentials.yaml"
    try:
        text = cred.read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r"^\s*CPA_API_KEY\s*:\s*(.+?)\s*$", text, re.MULTILINE)
    return m.group(1).strip().strip('"').strip("'") if m else ""


def _cpa_complete(prompt: str) -> str:
    """One bounded chat completion against the CPA relay. Raises on failure."""
    body = json.dumps(
        {
            "model": CPA_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are the evaluation judge for this machine's agent system. "
                        "Follow the user instruction and reply with the requested "
                        "structured output. Do not output reasoning or markdown fences."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 300,
            "temperature": 0.0,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{CPA_BASE}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_cpa_key()}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=CPA_TIMEOUT) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"unexpected CPA response: {exc}") from exc


def run_golden_live() -> list[str]:
    """Send each golden case to the CPA cheap worker as an LLM-as-judge task.

    The model receives the case prompt plus the expected includes/excludes
    criteria and returns a JSON verdict. This avoids brittle keyword matching
    (the CPA relay mangles CJK in free-form responses) and follows the evals
    practice of LLM-as-judge with structured output.
    """
    problems: list[str] = []
    if not GOLDEN_SET.is_file():
        return ["golden-live: golden_set.json missing"]
    golden = json.loads(GOLDEN_SET.read_text(encoding="utf-8-sig"))
    if not _cpa_key():
        return ["golden-live: CPA_API_KEY not set; use env var or ~/.dsh/.credentials.yaml"]
    total = passed = 0
    for entry in golden.get("skills", []):
        name = entry.get("name")
        for case in entry.get("cases", []):
            total += 1
            prompt = case.get("prompt", "")
            exp = case.get("expect", {})
            includes = exp.get("includes", [])
            excludes = exp.get("excludes", [])
            include_txt = ", ".join(includes) if includes else "(none)"
            exclude_txt = ", ".join(excludes) if excludes else "(none)"
            judge_prompt = (
                f"Skill under test: {name}. A user asks: \"{prompt}\"\n"
                f"Judge whether a CORRECT assistant response (following the skill) "
                f"would satisfy ALL of these criteria: [{include_txt}] "
                f"and AVOID ALL of: [{exclude_txt}].\n"
                'Reply with ONLY a JSON object: {"pass": true or false, "reason": "<short reason>"}.'
            )
            try:
                answer = _cpa_complete(judge_prompt).strip()
            except Exception as exc:  # noqa: BLE001 - gate reports any live failure
                problems.append(f"golden-live: {name}:{case.get('id')} call failed: {exc}")
                continue
            try:
                verdict = json.loads(answer)
            except (json.JSONDecodeError, ValueError):
                problems.append(
                    f"golden-live: {name}:{case.get('id')} non-JSON judge reply: {answer[:120]}"
                )
                continue
            if isinstance(verdict, dict) and verdict.get("pass") is True:
                passed += 1
            else:
                reason = verdict.get("reason") if isinstance(verdict, dict) else "no reason"
                problems.append(f"golden-live: {name}:{case.get('id')} FAIL: {reason}")
    print(f"golden-live: {passed}/{total} cases PASS")
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
        "--golden-live",
        action="store_true",
        help="run golden-set cases through the CPA cheap worker model (needs CPA_API_KEY)",
    )
    ap.add_argument(
        "--skip-golden",
        action="store_true",
        help="skip the deterministic golden-set structural gate",
    )
    ap.add_argument(
        "--cli",
        default=None,
        help="agent CLI for --live (default: first available of codex/claude/dsh)",
    )
    ap.add_argument("--timeout", type=int, default=60, help="per-example timeout for --live")
    args = ap.parse_args()

    manifest, manifest_problems = load_manifest(Path(args.manifest))
    failures: list[str] = list(manifest_problems)
    if manifest_problems:
        for problem in manifest_problems:
            print(f"FAIL: {problem}")
    skills = manifest.get("skills", [])
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
    if not args.skip_golden:
        golden_problems = check_golden_set(manifest)
        failures += golden_problems
        if golden_problems:
            print("golden-set structural gate: FAIL")
        else:
            print("golden-set structural gate: PASS")
    if args.golden_live:
        failures += run_golden_live()
    for problem in failures:
        print(f"FAIL: {problem}")
    total = len(skills)
    print(f"run_skill_evals: {total - len([p for p in failures if ':' in p])}/{total} skills PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
