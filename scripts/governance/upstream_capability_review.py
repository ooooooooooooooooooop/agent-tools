#!/usr/bin/env python3
"""Proposal-only review of upstream Harness and AI infrastructure capabilities.

This module compares generated AIC discovery with adapter baselines and records
review proposals. It never adopts a capability or mutates canonical registries.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import REPO, gov_log, load_yaml, propose  # noqa: E402

AIC = REPO / "scripts" / "aic" / "aic.py"
HARNESS_COMMANDS = {
    "dsh": ["dsh", "--version"],
    "codex": ["codex", "--version"],
    "claude": ["claude", "--version"],
    "gemini": ["gemini", "--version"],
}


def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return -1, f"{type(exc).__name__}: {exc}"


def _version_tuple(text: str | None) -> tuple[int, ...] | None:
    if not text:
        return None
    match = re.search(r"\d+(?:\.\d+){1,3}", str(text))
    return tuple(int(part) for part in match.group(0).split(".")) if match else None


def compare_versions(observed: str | None, baseline: str | None) -> str:
    """Return NEWER/OLDER/SAME/UNKNOWN without treating prerelease text as adoption."""
    left, right = _version_tuple(observed), _version_tuple(baseline)
    if not left or not right:
        return "UNKNOWN"
    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    return "NEWER" if left > right else "OLDER" if left < right else "SAME"


def latest_discovery() -> dict:
    files = sorted((REPO / "registry" / "inventory").glob("discovered-models-*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        data["_source"] = str(path)
        return data
    return {"models": [], "_source": None}


def harness_baselines() -> dict[str, str | None]:
    out = {}
    for name in HARNESS_COMMANDS:
        path = REPO / "registry" / "harnesses" / f"{name}.yaml"
        data = load_yaml(path) if path.is_file() else {}
        out[name] = data.get("version_observed")
    return out


def main() -> int:
    rc, discover_out = _run([sys.executable, str(AIC), "discover", "--propose-admissions"], 120)
    if rc != 0:
        print(f"UPSTREAM_CAPABILITY_REVIEW_ERROR: aic discover failed rc={rc}: {discover_out[-300:]}")
        gov_log("upstream_capability_review", "error", 1,
                {"cause": "AIC_DISCOVER_FAILED", "exit_code": rc})
        return 2

    findings = []
    baselines = harness_baselines()
    for name, cmd in HARNESS_COMMANDS.items():
        hrc, out = _run(cmd)
        observed = out.splitlines()[0].strip() if hrc == 0 and out else None
        relation = compare_versions(observed, baselines.get(name))
        if relation == "NEWER":
            finding = {"name": name, "kind": "upstream_harness_version_newer",
                       "observed": observed, "baseline": baselines.get(name),
                       "review_questions": [
                           "Does the release solve a current problem or improve capability, quality, stability, safety, or cost?",
                           "Can a mature native capability replace an existing local implementation?",
                           "What compatibility, rollback, provenance, and regression evidence is required before adoption?",
                       ]}
            propose("upstream_capability_review", finding, "low",
                    f"registry/harnesses/{name}.yaml",
                    "Review upstream release notes and validate one bounded candidate; discovery is not adoption",
                    safe_auto=False)
            findings.append(finding)
        elif hrc != 0:
            findings.append({"name": name, "kind": "harness_probe_unavailable",
                             "error": out.splitlines()[-1][:200] if out else f"exit={hrc}"})

    discovery = latest_discovery()
    print(f"discovery_source={discovery.get('_source') or 'NONE'} "
          f"models={len(discovery.get('models', []))}")
    for finding in findings:
        print(f"REVIEW: {finding['kind']} {finding['name']}")
    proposal_count = sum(1 for f in findings if f["kind"] == "upstream_harness_version_newer")
    probe_limits = sum(1 for f in findings if f["kind"] == "harness_probe_unavailable")
    gov_log("upstream_capability_review", "findings" if findings else "ok", findings,
            {"proposal_count": proposal_count, "probe_limitations": probe_limits,
             "discovery_source": discovery.get("_source")})
    print(f"upstream_candidates={proposal_count} probe_limitations={probe_limits} "
          "adopted=0 (proposal-only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
