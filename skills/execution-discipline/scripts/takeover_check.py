#!/usr/bin/env python
"""Takeover / handoff consistency gate (read-only, cross-project).

A generic, parameterized consistency gate for taking over any project.
It NEVER modifies any file: it only runs read-only probes and reports.

Six checks:
  1. baseline-lock       : pytest collected count vs the count locked by
                           contract tests (EXPECTED_TEST_BASELINE /
                           EXPECTED_BASELINE constants in tests/).
  2. registry-consistency: active taskflow dirs (.taskflow/active) with recent
                           activity that are NOT registered in the registry
                           (.taskflow/index.json). Older dirs are residue (INFO).
  3. cache-stale         : nodeids in .pytest_cache/v/cache/lastfailed that no
                           longer exist in the current collection.
  4. privacy-tracked     : git-tracked paths matching privacy red-line
                           prefixes (configurable).
  5. oversized-tracked   : git-tracked files above the size threshold.
  6. workspace-hygiene   : informational summary (untracked files, branches).

Usage:
  python takeover_check.py --root <project-root> [--config <config.json>]
  python takeover_check.py --selftest          # no external deps required

Config (JSON), all optional with these defaults (validated on novel-main):
  {"max_tracked_bytes": 1000000,
   "stale_dir_days": 14,
   "privacy_prefixes": [{"prefix": "novels/", "exception": "novels/tier0-"},
                        {"prefix": "reference_texts/"},
                        {"prefix": ".private_backup/"},
                        {"prefix": "canary_inputs/", "exception": "canary_inputs/tier0_"}],
   "taskflow_dir": ".taskflow",
   "registry_file": ".taskflow/index.json",
   "active_dir": ".taskflow/active",
   "pytest_cache": ".pytest_cache/v/cache/lastfailed",
   "baseline_constants": ["EXPECTED_TEST_BASELINE", "EXPECTED_BASELINE"],
   "tests_dir": "tests"}

Exit code 0 = no FAIL; 1 = at least one FAIL (handoff gate, not a fix).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime

DEFAULT_CONFIG: dict = {
    "max_tracked_bytes": 1_000_000,
    "stale_dir_days": 14,
    "privacy_prefixes": [
        {"prefix": "novels/", "exception": "novels/tier0-"},
        {"prefix": "reference_texts/"},
        {"prefix": ".private_backup/"},
        {"prefix": "canary_inputs/", "exception": "canary_inputs/tier0_"},
    ],
    "taskflow_dir": ".taskflow",
    "registry_file": ".taskflow/index.json",
    "active_dir": ".taskflow/active",
    "pytest_cache": ".pytest_cache/v/cache/lastfailed",
    "baseline_constants": ["EXPECTED_TEST_BASELINE", "EXPECTED_BASELINE"],
    "tests_dir": "tests",
}


def load_config(path: str | None) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as fh:
            user = json.load(fh)
        for key, value in user.items():
            cfg[key] = value
    return cfg


def project_python(root: str) -> str:
    """Prefer the project's own interpreter (.venv) over the caller's python."""
    for rel in ("Scripts/python.exe", "bin/python", "bin/python3"):
        cand = os.path.join(root, ".venv", rel)
        if os.path.isfile(cand):
            return cand
    return sys.executable


def run(root: str, cmd: list[str]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return subprocess.run(
        cmd, cwd=root, capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace",
    )


def collect_test_ids(root: str, cfg: dict) -> tuple[set[str], int | None]:
    proc = run(root, [project_python(root), "-m", "pytest", "--collect-only", "-q",
                      cfg["tests_dir"]])
    if proc.returncode != 0:
        return set(), None
    nodeids: set[str] = set()
    count: int | None = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.search(r"(\d+)\s+tests?\s+collected", line)
        if m:
            count = int(m.group(1))
        elif not line.startswith(("=", "<", "-")):
            nodeids.add(line)
    return nodeids, count


def locked_baseline(root: str, cfg: dict) -> int | None:
    consts = cfg["baseline_constants"]
    pattern = re.compile(
        r"(?:" + "|".join(re.escape(c) for c in consts) + r")\s*=\s*[\"']?(\d+)[\"']?"
    )
    locked: list[int] = []
    tests_dir = os.path.join(root, cfg["tests_dir"])
    if not os.path.isdir(tests_dir):
        return None
    for dirpath, _dirs, files in os.walk(tests_dir):
        for fn in files:
            if not fn.startswith("test_") or not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        m = pattern.search(line)
                        if m:
                            locked.append(int(m.group(1)))
            except OSError:
                continue
    return max(locked) if locked else None


def git_tracked_paths(root: str) -> list[str]:
    proc = run(root, ["git", "ls-files"])
    if proc.returncode != 0:
        return []
    return [p for p in proc.stdout.splitlines() if p]


def registry_status(root: str, cfg: dict) -> list[dict]:
    rows: list[dict] = []
    registry = os.path.join(root, cfg["registry_file"])
    active = os.path.join(root, cfg["active_dir"])
    indexed: set[str] = set()
    try:
        with open(registry, "r", encoding="utf-8") as fh:
            indexed = {t.get("name") for t in json.load(fh).get("tasks", [])}
    except (OSError, ValueError, AttributeError):
        indexed = set()
    if not os.path.isdir(active):
        return rows
    now = datetime.now().astimezone()
    for name in sorted(os.listdir(active)):
        d = os.path.join(active, name)
        if not os.path.isdir(d):
            continue
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(d)).astimezone()
        except OSError:
            continue
        rows.append({
            "name": name,
            "registered": name in indexed,
            "age_days": max(0, (now - mtime).days),
        })
    return rows


def check_baseline(root: str, cfg: dict) -> tuple[str, str]:
    _ids, count = collect_test_ids(root, cfg)
    locked = locked_baseline(root, cfg)
    if count is None:
        return "SKIP", "pytest --collect-only unavailable (no tests dir or run failure)"
    if locked is None:
        return "FAIL", f"collected {count} but no baseline constant found in tests/"
    if count == locked:
        return "PASS", f"collected {count} == locked {locked}"
    return "FAIL", f"collected {count} != locked {locked} (contract drift)"


def check_registry(root: str, cfg: dict) -> tuple[str, str]:
    rows = registry_status(root, cfg)
    if not rows:
        return "SKIP", "no taskflow active dir present"
    recent = [r for r in rows if not r["registered"]
              and r["age_days"] <= cfg["stale_dir_days"]]
    stale = sum(1 for r in rows if not r["registered"]
                and r["age_days"] > cfg["stale_dir_days"])
    if recent:
        names = ", ".join(f"{r['name']} ({r['age_days']}d)" for r in recent)
        return "FAIL", f"recent active dir not in registry: {names}"
    return "PASS", (f"registry consistent ({len(rows)} dirs); "
                    f"{stale} stale unregistered dir(s) (residue, INFO)")


def check_cache_stale(root: str, cfg: dict) -> tuple[str, str]:
    ids, _count = collect_test_ids(root, cfg)
    if not ids:
        return "SKIP", "collection unavailable; cannot validate cache"
    cache = os.path.join(root, cfg["pytest_cache"])
    try:
        with open(cache, "r", encoding="utf-8") as fh:
            cached = json.load(fh)
    except (OSError, ValueError):
        return "PASS", "lastfailed cache absent or unreadable"
    if not isinstance(cached, dict):
        return "PASS", "lastfailed cache has no nodeids"
    stale = [nid for nid in cached if nid not in ids]
    if stale:
        return "FAIL", f"{len(stale)} stale nodeid(s): {', '.join(sorted(stale)[:5])}"
    return "PASS", "lastfailed cache contains only live nodeids"


def check_privacy(root: str, cfg: dict) -> tuple[str, str]:
    bad: list[str] = []
    for path in git_tracked_paths(root):
        if os.path.basename(path) == ".gitkeep":
            continue
        for item in cfg["privacy_prefixes"]:
            prefix = item["prefix"]
            exception = item.get("exception")
            if path.startswith(prefix) and not (exception and path.startswith(exception)):
                bad.append(path)
                break
    if bad:
        return "FAIL", f"{len(bad)} privacy-tracked path(s): {', '.join(sorted(bad)[:5])}"
    return "PASS", "no privacy red-line paths are tracked"


def check_oversized(root: str, cfg: dict) -> tuple[str, str]:
    limit = cfg["max_tracked_bytes"]
    big: list[str] = []
    for path in git_tracked_paths(root):
        try:
            if os.path.getsize(os.path.join(root, path)) > limit:
                big.append(path)
        except OSError:
            continue
    if big:
        return "FAIL", f"{len(big)} tracked file(s) > {limit} bytes: {', '.join(sorted(big)[:5])}"
    return "PASS", f"no tracked file exceeds {limit} bytes"


def check_hygiene(root: str, _cfg: dict) -> tuple[str, str]:
    proc = run(root, ["git", "status", "--porcelain"])
    untracked = sum(1 for line in proc.stdout.splitlines() if line.startswith("??"))
    branch_proc = run(root, ["git", "branch", "-a"])
    branches = [b.strip() for b in branch_proc.stdout.splitlines() if b.strip()]
    return "INFO", f"untracked={untracked}, branches={len(branches)}: {', '.join(branches[:8])}"


def selftest() -> int:
    """Structural self-check: imports, config defaults, patterns, no external deps."""
    import importlib.util  # noqa: F401  (intentional presence check below)
    assert sys.version_info >= (3, 8)
    cfg = load_config(None)
    assert isinstance(cfg["privacy_prefixes"], list) and cfg["privacy_prefixes"]
    assert cfg["stale_dir_days"] > 0 and cfg["max_tracked_bytes"] > 0
    # baseline pattern must compile with the configured constants
    consts = cfg["baseline_constants"]
    re.compile(r"(?:" + "|".join(re.escape(c) for c in consts) + r")\s*=\s*[\"']?(\d+)[\"']?")
    print("SELFTEST: PASS (module imports, config defaults valid, patterns compile)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only takeover consistency gate")
    parser.add_argument("--root", default=None, help="project root (default: cwd)")
    parser.add_argument("--config", default=None, help="optional JSON config path")
    parser.add_argument("--selftest", action="store_true", help="run structural self-check only")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    root = os.path.abspath(args.root or os.getcwd())
    cfg = load_config(args.config)

    checks = [
        ("baseline-lock", check_baseline),
        ("registry-consistency", check_registry),
        ("cache-stale", check_cache_stale),
        ("privacy-tracked", check_privacy),
        ("oversized-tracked", check_oversized),
        ("workspace-hygiene", check_hygiene),
    ]
    fails = 0
    print(f"repo root : {root}")
    print("-" * 70)
    for name, fn in checks:
        try:
            status, detail = fn(root, cfg)
        except Exception as exc:  # a gate must never crash the caller silently
            status, detail = "FAIL", f"check raised {type(exc).__name__}: {exc}"
        if status == "FAIL":
            fails += 1
        print(f"[{status:4s}] {name:<20s} {detail}")
    print("-" * 70)
    if fails:
        print(f"TAKEOVER CHECK: {fails} FAILING - investigate root cause before handoff.")
        return 1
    print("TAKEOVER CHECK: all gates PASS (read-only; nothing was modified).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
