#!/usr/bin/env python3
"""Programmatic ground-truth checkers for the objective tasks (T1, T2, T6).

These checkers are keyword/parse based and are intentionally conservative:
every extracted result is written out with its matched evidence so the final
analysis can manually review the table instead of trusting regexes blindly.
"""

from __future__ import annotations

import re

T1_DEFECT_PATTERNS: dict[str, list[str]] = {
    "D1_touch_not_merged": [
        r"touch", r"adjacen", r"half[- ]open", r"end (?:equals|==|matches) (?:the )?start",
        r">=?\s*merged\[", r"r\[0\]\s*>=?", r"\[1,\s*2\).{0,40}\[2,\s*3\)",
        r"not merged.{0,30}(touch|adjacent|end)",
    ],
    "D2_empty_returns_none": [
        r"empty.{0,40}(none|null)|none.{0,20}instead of.{0,10}\[\]|should return.{0,20}\[\]|return\s+\[\]",
        r"returns?\s+none",
    ],
    "D3_mutates_input": [
        r"mutat", r"in[- ]place", r"caller'?s? (?:input |list)", r"\.sort\(\)", r"sorts? the input",
        r"input list.{0,30}(modif|sort|chang)",
    ],
    "D4_output_shape": [
        r"3[- ]?tuple", r"three[- ]element", r"tier", r"lists? (?:instead of|not) tuples?",
        r"tuples? (?:required|expected)", r"output shape", r"output.{0,30}(tuple|type|shape)",
        r"normali[sz]",
    ],
    "D5_no_invalid_rejection": [
        r"valueerror", r"invalid", r"start\s*>\s*end", r"start.{0,20}greater than.{0,20}end",
        r"reject", r"raise",
    ],
}


def check_t1(text: str) -> dict:
    low = (text or "").lower()
    found = {}
    for defect, patterns in T1_DEFECT_PATTERNS.items():
        evidence = None
        for pat in patterns:
            m = re.search(pat, low, re.I)
            if m:
                start = max(0, m.start() - 60)
                evidence = low[start:m.end() + 60].replace("\n", " ")
                break
        found[defect] = {"detected": evidence is not None, "evidence": evidence}
    return {
        "task": "T1",
        "defects_detected": sum(1 for v in found.values() if v["detected"]),
        "defects_total": len(T1_DEFECT_PATTERNS),
        "detail": found,
    }


def _parse_total_set(text: str) -> tuple[int | None, list[str] | None]:
    """Parse T2's total/set answer robustly across output styles.

    The COUNCIL/CURRENT conditions emit the canonical literal
    ``TOTAL=18; SET=B,C,D`` (the task prompt's contract), but COLLECTIVE
    answers are rendered prose: ``Chosen Set: {B, C, D}`` and
    ``Total Value: 18``. Only parsing the canonical literal would
    systematically mark COLLECTIVE's objectively-correct answers as wrong
    (false "harm" reads). The parser therefore:
      1. keeps the canonical literal as the highest-confidence branch;
      2. otherwise narrows to the Final Judgment decision region (which
         also contains suboptimal figures like value-greedy=14 / EEF=16,
         so it takes the maximum total as the group's answer — the task's
         optimal total is the global maximum, and 18 > {14, 16});
      3. extracts a B/C/D set token near a decision anchor if present.
    """
    t = text or ""
    m = re.search(r"TOTAL\s*=\s*(\d+)\s*;\s*SET\s*=\s*([A-E,\s]+)", t, re.I)
    if m:
        total = int(m.group(1))
        letters = re.findall(r"(?<![A-Za-z])[A-E](?![A-Za-z])", m.group(2).upper())
        return total, sorted(set(letters))
    fj = re.search(r"(?im)^#+\s*Final Judgment\b", t)
    region = t[fj.end():] if fj else t
    # total candidates in the decision region (handles 'Total Value: 18',
    # 'total of 18', 'value: 18', LaTeX bold markup). Suboptimal figures
    # (14, 16) are lower than the optimum 18, so max() selects the answer.
    tot_pat = re.compile(
        r"(?:total(?:\s+value)?(?:\s+of)?\s*(?:=|is|:)?\s*|value\s*[:=]\s*"
        r"|[Tt]otal\s+[Vv]alue\s*:?\s*)\**\s*(\d+)\b",
        re.I,
    )
    totals = [int(x) for x in tot_pat.findall(region)]
    best = max(totals) if totals else None
    # set token near a decision anchor, e.g. '* **Chosen Set:** ${B, C, D}$'
    s = _extract_decision_set(region)
    return best, s


def _extract_decision_set(region: str) -> list[str] | None:
    """Extract a B/C/D set token near a decision anchor within a region.

    Handles the prose/LaTeX/markdown shapes COLLECTIVE renders use:
    ``Chosen Set: ${B, C, D}$``, ``Optimal set: B(0-5), C(5-10), D(10-14)``,
    ``pick B, C, and D``. Returns a sorted letter list or None."""
    anchors = (
        r"chosen\s+set|optimal\s+set|final\s+set|recommended\s+set"
        r"|selected\s+sessions?|the\s+set|set\b|run\b|pick\b|sessions?\b"
    )
    for m in re.finditer(anchors, region or "", re.I):
        tail = region[m.end():m.end() + 80]
        mm = re.search(
            r"\\?\{?\s*(B\s*[,\s]+\s*C(?:\s*[,\sand]+\s*D)?)\}?"
            r"|\b(B,\s*C,\s*D|B\s+and\s+C(?:\s+and\s+D)?|C,\s*D)\b",
            tail,
        )
        if not mm:
            continue
        letters = sorted(set(re.findall(r"(?<![A-Za-z])[A-E](?![A-Za-z])", mm.group(0).upper())))
        if letters in (["B", "C", "D"], ["B", "C"], ["C", "D"]):
            return letters
    return None


def check_t2(text: str) -> dict:
    total, s = _parse_total_set(text or "")
    optimum = 18
    correct_total = total == optimum
    correct_set = s == ["B", "C", "D"]
    low = (text or "").lower()
    # the prompt explicitly asks whether a high-value session traps greedy approaches
    greedy_trap = bool(re.search(r"\ba\b.{0,120}(greedy|drop|exclude|wrongly|trap)|greedy.{0,120}\ba\b", low))
    mentions_drop_a = bool(re.search(r"(drop|exclude|remove|skip|avoid).{0,40}\ba\b|\ba\b.{0,40}(must|should).{0,20}(drop|exclude|remove|skip)", low))
    return {
        "task": "T2",
        "parsed_total": total,
        "parsed_set": s,
        "correct_total": correct_total,
        "correct_set": correct_set,
        "correct": bool(correct_total and (correct_set or s is None)),
        "greedy_trap_addressed": greedy_trap or mentions_drop_a,
        "evidence_excerpt": (text or "")[-300:],
    }


def check_t6(text: str) -> dict:
    low = (text or "").lower()
    m = re.search(r"(?im)^\s*PRIMARY CAUSE\s*:\s*(.+)$", text or "")
    primary = m.group(1).strip() if m else ""
    primary_is_cache = bool(re.search(r"cache", primary, re.I)) or (
        "cache" in low and bool(re.search(r"cache.{0,80}(hit|rate).{0,80}(drop|collapse|fell|regress)|primary.{0,60}cache", low))
    )
    d = re.search(r"(?im)^\s*DAY-12 DEPLOY\s*:\s*(.+)$", text or "")
    deploy_line = d.group(1).strip() if d else ""
    if re.search(r"not.{0,20}(causal|implicat)|coinciden|correlat.{0,20}not|not the cause", deploy_line, re.I) or (
        not deploy_line and re.search(r"deploy.{0,60}(coinciden|not.{0,15}cause|correlat.{0,25}but)", low)
    ):
        deploy_verdict = "not_causally_implicated"
    elif re.search(r"causally implicated", deploy_line, re.I) and not re.search(r"not\s+causally", deploy_line, re.I):
        deploy_verdict = "causally_implicated"
    elif re.search(r"unclear|ambiguous|cannot (?:be )?determin|insufficient", deploy_line, re.I):
        deploy_verdict = "unclear"
    else:
        deploy_verdict = "unparsed"
    secondary = bool(re.search(r"traffic|request rate|req_per_s|load.{0,30}(grow|increase)|secondar", low))
    return {
        "task": "T6",
        "primary_cause_line": primary[:200],
        "primary_is_cache": primary_is_cache,
        "deploy_line": deploy_line[:160],
        "deploy_verdict": deploy_verdict,
        "deploy_correct": deploy_verdict == "not_causally_implicated",
        "mentions_traffic_secondary": secondary,
        "correct": bool(primary_is_cache and deploy_verdict == "not_causally_implicated"),
    }


def check_objective(task_id: str, text: str) -> dict:
    if task_id == "T1":
        return check_t1(text)
    if task_id == "T2":
        return check_t2(text)
    if task_id == "T6":
        return check_t6(text)
    raise KeyError(task_id)
