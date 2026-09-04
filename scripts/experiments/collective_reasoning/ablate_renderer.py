#!/usr/bin/env python3
"""Stage A of the renderer-causality ablation (REASONING constant, RENDERER variable).

Uses the FROZEN exp1 collective states. It does NOT re-run any collective
reasoning. The only new model calls are:
  - R1 rendering  (util-gemini-3.7, decision-synthesis contract) x T3,T4,T5
  - blind pairwise of R1 vs COUNCIL and R1 vs R0       x 3 tasks x 2 judges

R0 = exp1's frozen neutral-renderer output (read-only, zero calls).
R1 = new render of the SAME participant finals, but under a decision-synthesis
     contract: it must commit to a best judgment, sequenced action plan, key
     reasons, flag only genuinely unresolved disagreements, and state what
     would change the decision. Same model as R0's renderer so the only
     variable is the contract.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from client import JUDGES, call_model, MODELS
from conditions import POOL_LABELS, _user_text
from judge import extract_decision_doc, pairwise, scrub
from tasks import TASKS_BY_ID

ART = Path(__file__).resolve().parents[3] / "artifacts" / "collective_reasoning"
SRC_RUN = "exp1"      # frozen source data (already committed checkpoint)
RUN_ID = "exp1r"      # this ablation's run dir (separate cache namespace)

OPEN_TASKS = ["T3", "T4", "T5"]

RENDERER = "util-gemini-3.7"  # same model as exp1's neutral renderer


def run_dir():
    d = ART / RUN_ID
    (d / "calls").mkdir(parents=True, exist_ok=True)
    (d / "state").mkdir(parents=True, exist_ok=True)
    (d / "judge").mkdir(parents=True, exist_ok=True)
    return d


def load_src_state(tid: str, name: str) -> dict:
    p = ART / SRC_RUN / "state" / f"{name}_{tid}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_json(sub: str, name: str, data: dict) -> None:
    d = run_dir() / sub
    (d / f"{name}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def clip(text: str, n: int = 3200) -> str:
    text = (text or "").strip()
    return text[:n] + "\n[... truncated ...]" if len(text) > n else text


# ------------------------------------------------------------------ R1 render


def render_r1(tid: str) -> dict:
    col = load_src_state(tid, "collective")
    task = TASKS_BY_ID[tid]
    finals = col.get("finals", {})
    finals_block = "\n\n".join(
        f"## {POOL_LABELS[a]} final judgment\n\n{clip(finals[a])}"
        for a in finals if (finals.get(a) or "").strip()
    )
    user_prompt = task.prompt
    prompt = (
        f"{user_prompt}\n\n---\nFive analysts each wrote a final judgment after "
        "an open discussion. You are the decision synthesizer who turns their "
        "final state into the single best actionable answer for the decision "
        "maker. You MAY synthesize: weigh the finals, adopt the strongest "
        "reasoning, and commit to a recommendation.\n\n"
        "You MUST NOT fabricate facts or evidence that are not present in the "
        "finals, and you MUST NOT silently erase a disagreement that would "
        "change the decision if resolved differently — if one exists, surface "
        "it explicitly under 'Unresolved'. Base every part of your answer only "
        "on what the analysts wrote.\n\n"
        "Structure your answer:\n"
        "## Analysis\nwhat the strongest reasoning across the finals supports, "
        "at most 350 words.\n"
        "## Final Judgment\nyour committed decision/answer for the task, at "
        "most 150 words, concrete and actionable (if a concrete number/set/"
        "architecture is at stake, state it).\n"
        "## Actions\nfirst concrete next steps, at most 3, each 1-2 lines.\n"
        "## Key Reasons\nthe 2-4 decisive reasons, one line each.\n"
        "## Unresolved\nonly disagreements among the finals that could change "
        "the decision, each one line, or 'none'.\n"
        "## What Would Change This\nwhat new evidence would overturn the "
        "decision, at most 2 lines.\n\n" + finals_block
    )
    rec = call_model(
        RENDERER,
        [{"role": "system", "content": (
            "You are a rigorous decision synthesizer. You commit to the best "
            "supported judgment and give a concrete, actionable answer, while "
            "never fabricating content or erasing decision-changing "
            "disagreements. Write in English.")},
         {"role": "user", "content": prompt}],
        run_id=RUN_ID, tag=f"render-r1:{tid}", calls_dir=run_dir() / "calls",
        max_tokens=8000,
    )
    out = {
        "run_id": RUN_ID, "task": tid, "condition": "R1_DECISION_SYNTHESIS",
        "model": RENDERER,
        "source": f"{SRC_RUN}/collective_{tid}.json (finals reused verbatim)",
        "text": _user_text(rec),
        "provenance": {k: rec[k] for k in
                       ("requested_model", "reported_model", "usage", "latency_s", "error")},
    }
    save_json("state", f"r1_{tid}", out)
    return out


# ------------------------------------------------------------------ judging


def _r1_decision_doc(task, text: str) -> str:
    """Build R1's judged document from ALL its decision-bearing sections.

    R1 has a deliberately terse ``## Final Judgment`` (<=150 words) followed by
    ``## Actions``/``## Key Reasons``/``## Unresolved``/``## What Would Change``.
    extract_decision_doc's generic branch stops at the first ``##`` after Final
    Judgment, which would hand the judge only the terse verdict and silently
    drop the argumentation (Actions/Key Reasons) — unfairly short vs COUNCIL's
    prose synthesis. So for R1 we concatenate everything from Final Judgment on.
    """
    import re
    m = re.search(r"(?im)^##\s*Final Judgment\b", text or "")
    body = text[m.end():] if m else text
    return scrub(body.strip())


def decision_docs(tid: str) -> dict:
    """R0 (frozen neutral render), R1 (decision synthesis), R2 (best participant final),
    COUNCIL, CURRENT, BEST_INITIAL."""
    task = TASKS_BY_ID[tid]
    col = load_src_state(tid, "collective")
    cou = load_src_state(tid, "council")
    cur = load_src_state(tid, "current")
    ind = load_src_state(tid, "independent")
    r1 = (run_dir() / "state" / f"r1_{tid}.json")
    docs = {}
    if r1.exists():
        docs["R1"] = _r1_decision_doc(task, json.loads(r1.read_text(encoding="utf-8"))["text"])
    if col.get("rendered_final"):
        docs["R0"] = extract_decision_doc(task, col["rendered_final"], "COLLECTIVE")
    if cou.get("synthesis"):
        docs["COUNCIL"] = extract_decision_doc(task, cou["synthesis"], "COUNCIL")
    if cur.get("response"):
        docs["CURRENT"] = extract_decision_doc(task, cur["response"], "CURRENT")
    # R2: strongest participant's final from the collective discussion (claude-sonnet)
    finals = col.get("finals", {})
    if finals.get("claude-sonnet"):
        docs["R2"] = extract_decision_doc(task, finals["claude-sonnet"], "initial")
    # BEST_INITIAL: first-round best initial (glm-5.3 for T3, T4, T5)
    init_texts = ind.get("initials", {})
    if init_texts.get("glm-5.3"):
        docs["BEST_INITIAL"] = extract_decision_doc(task, init_texts["glm-5.3"]["text"], "initial")
    return docs


def run_pairs(tid: str, docs: dict) -> None:
    task = TASKS_BY_ID[tid]
    calls_dir = run_dir() / "calls"
    # Stage A pairs (cached): R1 vs R0, R1 vs COUNCIL, R1 vs CURRENT
    # Stage B pairs (new): R2 vs COUNCIL, R1 vs R2, R1 vs BEST_INITIAL
    pairs_to_run = [
        ("R1", "R0"),
        ("R1", "COUNCIL"),
        ("R1", "CURRENT"),
        ("R2", "COUNCIL"),
        ("R1", "R2"),
        ("R1", "BEST_INITIAL"),
    ]
    for x, y in pairs_to_run:
        if x not in docs or y not in docs:
            print(f"  {tid}: missing {x if x not in docs else y}, skip {x} vs {y}")
            continue
        for judge in JUDGES:
            name = f"pair_{tid}_{x}__vs__{y}_{judge}"
            if (run_dir() / "judge" / f"{name}.json").exists():
                continue
            res = pairwise(task, "", docs[x], docs[y], judge, RUN_ID, calls_dir)
            res.update({"x": x, "y": y, "judge": judge})
            save_json("judge", name, res)
            print(f"  {tid} {x} vs {y} [{judge}]: {res.get('winner')}")


def main() -> None:
    for tid in OPEN_TASKS:
        print(f"=== {tid} ===")
        # R1 render (idempotent via call cache)
        if not (run_dir() / "state" / f"r1_{tid}.json").exists():
            render_r1(tid)
            print(f"  R1 rendered ({tid})")
        else:
            print(f"  R1 cached ({tid})")
        docs = decision_docs(tid)
        run_pairs(tid, docs)


if __name__ == "__main__":
    main()
