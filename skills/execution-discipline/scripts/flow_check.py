#!/usr/bin/env python
"""Flow completeness gate (read-only, cross-context).

Upgrades the "walk the full process" rule from a prose-level (SKILL.md)
constraint to an executable gate. Multi-step processes must not rely on
prompt wording alone (Anthropic: Building effective agents / OpenAI:
Harness engineering) — they need programmatic gates and verifiable feedback.

This script NEVER modifies any file: it validates a flow/retrospective
record handed to it and reports. Exit code 0 = complete; 1 = incomplete.

Two modes:
  --check-flow <flow.json>      validate a full problem-solving flow record
                                (systematic-optimization 9 stages):
  --check-review <review.md>    validate a retrospective record has all six
                                NASA AAR stages (expectation -> facts ->
                                difference -> lessons -> actions -> closure)
  --selftest                    structural self-check only

flow.json schema (order matters; decision-gate must precede implement):
{
  "task": "one-line task description",
  "stages": [
    {"name": "baseline",      "status": "done", "evidence": "3018 collected"},
    {"name": "problems",      "status": "done", "evidence": "6 items"},
    {"name": "root-cause",    "status": "done", "evidence": "missing constraint"},
    {"name": "borrow",        "status": "done", "evidence": "https://... URL"},
    {"name": "tradeoff",      "status": "done", "evidence": "constraint layer"},
    {"name": "decision-gate", "status": "confirmed", "evidence": "user: 同意"},
    {"name": "implement",     "status": "done", "evidence": "files changed"},
    {"name": "verify",        "status": "done", "evidence": "gates green"},
    {"name": "measure",       "status": "done", "evidence": "before vs after"}
  ]
}

Checks:
  1. All required stage names present, in canonical order.
  2. decision-gate.status must be "confirmed" and must precede implement.
  3. borrow.evidence must contain a real http(s) source URL (no fabricated
     provenance allowed — worse than no reference at all).
  4. Every stage must have non-empty evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

CANONICAL_STAGES = [
    "baseline",
    "problems",
    "root-cause",
    "borrow",
    "tradeoff",
    "decision-gate",
    "implement",
    "verify",
    "measure",
]

# NASA AAR six stages: expectation -> facts -> difference -> lessons ->
# actions -> verification closure. Each maps to a set of anchor keywords
# that must appear in the retrospective text.
REVIEW_STAGES = {
    "expectation": ["预期", "expect", "目标", "计划是什么"],
    "facts": ["事实", "重建", "实际发生", "actual", "证据"],
    "difference": ["差异", "根因", "原因", "difference", "为什么"],
    "lessons": ["经验", "提炼", "教训", "lesson", "可复用"],
    "actions": ["行动", "改", "负责人", "期限", "action", "升级"],
    "closure": ["验证", "闭环", "度量", "复核", "verify", "对照"],
}

URL_RE = re.compile(r"https?://\S+")


def check_flow(path: str) -> tuple[bool, list[str]]:
    errors: list[str] = []
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return False, [f"cannot read flow JSON: {exc}"]

    stages = data.get("stages")
    if not isinstance(stages, list) or not stages:
        return False, ["flow.json has no non-empty 'stages' list"]

    names = [s.get("name") for s in stages]
    for idx, required in enumerate(CANONICAL_STAGES):
        if required not in names:
            errors.append(f"missing required stage: '{required}'")

    # order: decision-gate must precede implement
    if "decision-gate" in names and "implement" in names:
        if names.index("decision-gate") >= names.index("implement"):
            errors.append("decision-gate must precede implement")

    for s in stages:
        name = s.get("name", "?")
        status = s.get("status", "")
        evidence = s.get("evidence", "")
        if not evidence or not str(evidence).strip():
            errors.append(f"stage '{name}' has empty evidence")
        if name == "decision-gate" and status != "confirmed":
            errors.append("decision-gate.status must be 'confirmed'")
        if name == "borrow" and not URL_RE.search(str(evidence)):
            errors.append(
                "borrow.evidence must contain a real http(s) source URL "
                "(fabricated provenance is worse than none)"
            )

    return not errors, errors


def check_review(path: str) -> tuple[bool, list[str]]:
    errors: list[str] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        return False, [f"cannot read review file: {exc}"]

    for stage, anchors in REVIEW_STAGES.items():
        if not any(a.lower() in text.lower() for a in anchors):
            errors.append(f"review missing AAR stage '{stage}' "
                          f"(none of {anchors} found)")
    return not errors, errors


def selftest() -> int:
    import tempfile
    ok = True

    # positive flow fixture
    good = {
        "task": "selftest",
        "stages": [
            {"name": n, "status": "done",
             "evidence": "x" if n != "borrow" else "https://example.com/src",
             } if n != "decision-gate" else
            {"name": n, "status": "confirmed", "evidence": "user: agree"}
            for n in CANONICAL_STAGES
        ],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as fh:
        json.dump(good, fh)
        good_path = fh.name
    try:
        passed, _errs = check_flow(good_path)
        ok = ok and passed
    finally:
        os.unlink(good_path)

    # negative: decision-gate unconfirmed + borrow without URL + order flipped
    bad = {
        "task": "selftest-negative",
        "stages": [
            {"name": n, "status": "done", "evidence": "x"} for n in CANONICAL_STAGES
        ],
    }
    bad["stages"][CANONICAL_STAGES.index("decision-gate")]["status"] = "pending"
    bad["stages"][CANONICAL_STAGES.index("borrow")]["evidence"] = "凭记忆出处"
    # swap decision-gate and implement order
    i_dg = CANONICAL_STAGES.index("decision-gate")
    i_imp = CANONICAL_STAGES.index("implement")
    bad["stages"][i_dg], bad["stages"][i_imp] = bad["stages"][i_imp], bad["stages"][i_dg]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as fh:
        json.dump(bad, fh)
        bad_path = fh.name
    try:
        passed, errs = check_flow(bad_path)
        ok = ok and (not passed) and len(errs) >= 3
    finally:
        os.unlink(bad_path)

    # review positive/negative
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as fh:
        fh.write("预期目标明确\n重建事实\n差异与根因\n提炼经验\n改进行动含负责人与期限\n验证闭环\n")
        review_ok_path = fh.name
    try:
        passed, _errs = check_review(review_ok_path)
        ok = ok and passed
    finally:
        os.unlink(review_ok_path)

    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as fh:
        fh.write("只写了根因\n")
        review_bad_path = fh.name
    try:
        passed, errs = check_review(review_bad_path)
        ok = ok and (not passed) and len(errs) >= 5
    finally:
        os.unlink(review_bad_path)

    print(f"SELFTEST: {'PASS' if ok else 'FAIL'} "
          "(flow positive/negative, review positive/negative)")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only flow/retrospective completeness gate")
    parser.add_argument("--check-flow", metavar="FLOW_JSON",
                        help="validate a problem-solving flow record")
    parser.add_argument("--check-review", metavar="REVIEW_MD",
                        help="validate a retrospective has all six AAR stages")
    parser.add_argument("--selftest", action="store_true",
                        help="run structural self-check only")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    if args.check_flow:
        passed, errs = check_flow(args.check_flow)
        if passed:
            print("FLOW GATE: PASS - all 9 stages present, decision-gate "
                  "confirmed, borrow has real source URL.")
            return 0
        for e in errs:
            print(f"[FAIL] {e}")
        print("FLOW GATE: 1 FAILING - do not implement; walk the full process.")
        return 1

    if args.check_review:
        passed, errs = check_review(args.check_review)
        if passed:
            print("REVIEW GATE: PASS - all six AAR stages present.")
            return 0
        for e in errs:
            print(f"[FAIL] {e}")
        print("REVIEW GATE: 1 FAILING - retrospective is incomplete (no closure).")
        return 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
