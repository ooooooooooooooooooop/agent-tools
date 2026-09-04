#!/usr/bin/env python3
"""Blind evaluation: anonymization, pairwise judging, initial-answer ranking,
and novelty/harm judging.

Anonymity controls:
- vendor/model names are scrubbed from every document;
- documents are reduced to their decision-bearing sections so that condition-
  specific formatting (e.g. the production skill's report scaffold) does not
  identify the mechanism;
- pair order and A/B labels are randomized with a per-(task, judge) seed;
- both judges evaluate all pairs; neither judge participates in any condition.

Residual limitation recorded in the report: style differences can still leak
mechanism identity; judges are instructed to judge decision content only.
"""

from __future__ import annotations

import hashlib
import json
import random
import re

from client import JUDGES, call_model
from tasks import Task

SCRUB_PATTERNS = [
    r"\bclaude\b", r"\banthropic\b", r"\bsonnet\b", r"\bopus\b", r"\bgpt\b",
    r"\bopenai\b", r"\bgemini\b", r"\bgoogle\b", r"\bdeepseek\b", r"\bkimi\b",
    r"\bmoonshot\b", r"\bglm\b", r"\bzhipu\b", r"\bqwen\b", r"\balibaba\b",
    r"\bbai\b", r"\bcodex\b", r"\bflash\b", r"\bhigh-effort\b",
]


def scrub(text: str) -> str:
    out = text or ""
    for pat in SCRUB_PATTERNS:
        out = re.sub(pat, "[model]", out, flags=re.I)
    return out


def _extract_section(text: str, title_regex: str) -> str:
    """Extract the body under a heading, stopping at the next heading of the
    SAME or HIGHER level (shallower ``#``). Deeper sub-headings (``###`` inside
    a ``##`` section) are kept as content.

    This matters for the production-skill CURRENT output, where
    ``## Section 6: Moderator Synthesis`` is immediately followed by ``###``
    sub-sections. Stopping at the first ``###`` would return an empty body and
    the caller would silently fall back to the trailing skill scaffold --
    starving CURRENT of its actual decision content in the blind comparisons.
    """
    # Plain concatenation, NOT an f-string: the regex contains ``{1,6}``
    # quantifiers that an f-string would treat as replacement fields.
    # Separator after the section number may be ':', '.', ':', or '—'/'–'/'-'
    # (the production skill emits both '## Section 6: Moderator Synthesis'
    # and '## Section 6 — Moderator Synthesis').
    pat = (
        "(?im)^(#{1,6})[ \\t]*(?:section[ \\t]*\\d+[ \\t]*[:\\u2014\\u2013\\-.][ \\t]*)?"
        "(?:\\d+\\.?[ \\t]*)?{title}[ \\t]*:?[ \\t]*$"
    ).replace("{title}", title_regex)
    m = re.search(pat, text or "")
    if not m:
        return ""
    level = len(m.group(1))
    tail = text[m.end():]
    nxt_pat = ("(?im)^#{{1,{lv}}}[ \t]+\S").format(lv=level)
    nxt = re.search(nxt_pat, tail)
    return tail[: nxt.start()].strip() if nxt else tail.strip()


def extract_decision_doc(task: Task, text: str, condition: str) -> str:
    """Reduce a condition's full output to its decision-bearing document."""
    if condition == "CURRENT":
        parts = []
        for title in (r"Moderator Synthesis", r"Uncertainty Ledger"):
            sec = _extract_section(text, title)
            if sec:
                parts.append(sec)
        body = "\n\n".join(parts)
        body = re.split(r"(?im)^#{2,3}\s*Post-Use Self-Check", body)[0]
        if body.strip():
            return scrub(re.sub(r"(?im)^#{1,3}.*$", "", body).strip() or body.strip())
    # generic contract: Final Judgment (+ Confidence)
    fj = _extract_section(text, r"Final Judgment")
    conf = _extract_section(text, r"Confidence")
    if fj:
        doc = fj + ("\n\nConfidence: " + conf if conf else "")
        return scrub(doc)
    # fallback: last ~900 chars
    return scrub((text or "")[-900:])


def seeded_rng(*parts) -> random.Random:
    seed = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return random.Random(int(seed[:12], 16))


def _ask_json(judge: str, prompt: str, run_id: str, calls_dir, tag: str, max_tokens: int = 4000) -> dict:
    rec = call_model(
        judge,
        [{"role": "system", "content": (
            "You are a strict, impartial evaluation judge. Judge decision "
            "content quality only; ignore formatting and writing style. Reply "
            "with JSON only. Write in English.")},
         {"role": "user", "content": prompt}],
        run_id=run_id, tag=tag, calls_dir=calls_dir, max_tokens=max_tokens,
    )
    text = rec.get("content") or ""
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {"_unparseable": text[:500]}


def pairwise(task: Task, question_hint: str, doc_x: str, doc_y: str,
             judge: str, run_id: str, calls_dir) -> dict:
    rng = seeded_rng(run_id, task.id, judge, doc_x[:80], doc_y[:80])
    a_first = rng.random() < 0.5
    label_first, label_second = ("X", "Y") if a_first else ("Y", "X")
    prompt = (
        f"Question given to the analysts:\n\n{task.prompt}\n\n"
        f"{('Additional judging guidance: ' + task.judge_notes) if task.judge_notes else ''}\n\n"
        f"Below are two final answers from different decision-support methods. "
        f"Which is the better answer for this question, considering: decision "
        f"quality, correctness of reasoning, actionability, honest handling of "
        f"uncertainty and disagreement? Ignore length, format, and style.\n\n"
        f"=== Answer {label_first} ===\n{doc_x if a_first else doc_y}\n\n"
        f"=== Answer {label_second} ===\n{doc_y if a_first else doc_x}\n\n"
        'Reply with JSON only: {"winner": "X" or "Y" or "tie", '
        '"worse_missing_something_important": true or false, '
        '"key_reason": "one or two sentences"}'
    )
    res = _ask_json(judge, prompt, run_id, calls_dir, f"judge-pair:{task.id}")
    winner = res.get("winner")
    if winner in ("X", "Y") and not a_first:
        res["winner"] = "X" if winner == "Y" else "Y"
    res["x_is_doc_x"] = a_first
    res["judge"] = judge
    return res


def rank_initials(task: Task, initials_docs: dict[str, str], judge: str,
                  run_id: str, calls_dir) -> dict:
    """Rank the 5 anonymized initial answers. Returns ordered model aliases."""
    aliases = list(initials_docs.keys())
    rng = seeded_rng(run_id, task.id, judge, "rank")
    shuffled = aliases[:]
    rng.shuffle(shuffled)
    blocks = "\n\n".join(
        f"=== Answer {chr(65 + i)} ===\n{initials_docs[a]}" for i, a in enumerate(shuffled)
    )
    prompt = (
        f"Question given to the analysts:\n\n{task.prompt}\n\n"
        f"{('Additional judging guidance: ' + task.judge_notes) if task.judge_notes else ''}\n\n"
        f"Below are five independent answers from different analysts. Rank them "
        f"from best to worst for answering this question (decision quality, "
        f"correctness, actionability; ignore style and length).\n\n{blocks}\n\n"
        'Reply with JSON only: {"ranking": ["<letter>", ...] best to worst, '
        '"top_reason": "..."}'
    )
    res = _ask_json(judge, prompt, run_id, calls_dir, f"judge-rank:{task.id}")
    ranking = res.get("ranking") or []
    ordered = []
    for letter in ranking:
        if isinstance(letter, str) and len(letter) == 1 and letter.upper() in "ABCDE":
            ordered.append(shuffled["ABCDE".index(letter.upper())])
    res["ordered_aliases"] = ordered
    res["judge"] = judge
    return res


def novelty(task: Task, initials_docs: dict[str, str], final_doc: str,
            judge: str, run_id: str, calls_dir) -> dict:
    aliases = list(initials_docs.keys())
    rng = seeded_rng(run_id, task.id, judge, "novelty")
    shuffled = aliases[:]
    rng.shuffle(shuffled)
    blocks = "\n\n".join(
        f"=== Initial answer {chr(65 + i)} ===\n{initials_docs[a]}" for i, a in enumerate(shuffled)
    )
    prompt = (
        f"Question:\n\n{task.prompt}\n\n"
        f"Below are five independent initial answers, then a final answer "
        f"produced after those analysts interacted. Analyze what the interaction "
        f"added or destroyed.\n\n{blocks}\n\n"
        f"=== FINAL answer (post-interaction) ===\n{final_doc}\n\n"
        "1. List every important element of the FINAL answer that is absent from "
        "ALL five initial answers. For each element classify: type "
        "(new_information / new_reasoning / new_option / reframing / mostly_paraphrase), "
        "decision_relevant (bool), plausible_or_correct (bool). An element that is "
        "just a recombination of what initials already said is mostly_paraphrase. "
        "Be strict: 'important' means it could change the decision or its risk.\n"
        "2. blind_spot: did the FINAL answer raise an important issue that none of "
        "the initials noticed? (bool + one sentence)\n"
        "3. harm: did the FINAL answer LOSE any important correct content that at "
        "least one initial answer had? (bool + one sentence)\n"
        'Reply with JSON only: {"new_elements": [{"element": "...", "type": "...", '
        '"decision_relevant": bool, "plausible_or_correct": bool}], '
        '"blind_spot": {"found": bool, "what": "..."}, '
        '"harm": {"lost_something_important": bool, "what": "..."}}'
    )
    res = _ask_json(judge, prompt, run_id, calls_dir, f"judge-novelty:{task.id}", max_tokens=6000)
    res["judge"] = judge
    return res
