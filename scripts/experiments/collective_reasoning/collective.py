#!/usr/bin/env python3
"""COLLECTIVE condition: minimal open collective-reasoning prototype.

Design constraints taken from the research brief (none assumed beneficial —
they are the treatment being tested):
- every model forms its own complete initial judgment first;
- no thought-roles are assigned to anyone;
- no central model decides how any other model should think;
- after initials, every participant sees the others' actual thoughts verbatim;
- the first cross-reading is comprehensive (not "attack one best point");
- later rounds are abstain-able (no mechanical turn-taking);
- no fixed round count: stop on discussion-state exhaustion;
- no forced consensus: the final state may hold winners, surviving
  alternatives, unresolved disagreements, or no-decision.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from client import PARTICIPANTS, call_model, map_pool
from conditions import POOL_LABELS, _save_state, _user_text, run_initials
from tasks import Task

MAX_ROUNDS = 6  # experiment budget guard, flagged in data when hit (not a protocol rule)
MAX_MSG_CHARS = 3200

FREE_MOVES = (
    "You may freely: attack, refute, deepen, combine, restructure, propose new "
    "explanations, propose new options, give counterexamples, change the "
    "framing of the problem, or flag questions that need external evidence. "
    "You may change your own position, withdraw an earlier conclusion, or "
    "endorse someone else's new idea. Nobody is assigned a role; engage with "
    "whatever you judge most important."
)


def _clip(text: str) -> str:
    text = text.strip()
    if len(text) > MAX_MSG_CHARS:
        return text[:MAX_MSG_CHARS] + "\n[... message truncated for length ...]"
    return text


def _render_transcript(transcript: list, exclude_alias: str | None = None) -> str:
    blocks = []
    for msg in transcript:
        if exclude_alias and msg["alias"] == exclude_alias:
            continue
        blocks.append(
            f"[Round {msg['round']} | {POOL_LABELS[msg['alias']]} | {msg['kind']}]\n{_clip(msg['text'])}"
        )
    return "\n\n".join(blocks) if blocks else "(no messages yet)"


def _extract_stance(text: str) -> str:
    m = re.search(r"(?im)^\s*STANCE:\s*(.+)$", text or "")
    return m.group(1).strip() if m else ""


def _is_abstain(text: str) -> bool:
    head = (text or "").strip().splitlines()
    return bool(head) and head[0].strip().rstrip(".").upper().startswith("NO_CONTRIBUTION")


def _parse_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _initials_section(initials: dict, exclude_alias: str | None = None) -> str:
    parts = []
    for alias in PARTICIPANTS:
        if alias == exclude_alias:
            continue
        parts.append(f"## {POOL_LABELS[alias]} initial answer\n\n{_clip(_user_text(initials[alias]))}")
    return "\n\n".join(parts)


def run_collective(task: Task, run_id: str, calls_dir: Path, out_dir: Path) -> dict:
    user_prompt = task.prompt  # raw task text without the output contract here
    initials = run_initials(task, run_id, calls_dir)
    transcript: list[dict] = []
    budget_note = None

    def c(alias: str, prompt: str, tag: str, max_tokens: int = 8000, system: str | None = None):
        return call_model(
            alias,
            [{"role": "system", "content": system or (
                "You are one of several independent analysts discussing a hard "
                "problem with the others. Write in English.")},
             {"role": "user", "content": prompt}],
            run_id=run_id, tag=tag, calls_dir=calls_dir, max_tokens=max_tokens,
        )

    # ---- Round 1: comprehensive cross-reading (all participants) ----
    def round1(alias: str):
        others = _initials_section(initials, exclude_alias=alias)
        prompt = (
            f"{user_prompt}\n\n---\nYou already wrote your own initial answer (below, for "
            f"reference). Now read ALL of the other participants' actual initial answers "
            f"below. Engage with them comprehensively: respond to every substantive "
            f"point you disagree with or find missing — not just the single most "
            f"attackable one. {FREE_MOVES}\n\n"
            f"## Your own initial answer\n\n{_clip(_user_text(initials[alias]))}\n\n"
            f"## The others' initial answers\n\n{others}\n\n"
            "Write your contribution (at most 450 words). End with exactly one line:\n"
            "STANCE: <one sentence stating your current position>"
        )
        return c(alias, prompt, f"coll-r1:{task.id}")

    round1_recs = {a: r for a, r in zip(PARTICIPANTS, map_pool(round1, PARTICIPANTS))}
    for alias in PARTICIPANTS:
        transcript.append({
            "round": 1, "alias": alias, "kind": "cross-reading",
            "text": _user_text(round1_recs[alias]),
        })

    # ---- Rounds 2..N: abstain-able free discussion until exhaustion ----
    round = 1
    stop_reason = None
    stopping_log = []
    while round < MAX_ROUNDS:
        # stopping evaluator on current state
        eval_prompt = (
            "You are observing a multi-analyst discussion. Decide whether it has "
            "exhausted itself.\n\nExhausted means: mostly repetition of earlier "
            "points, rephrasing, repeated attacks, no new derivation, purely "
            "polite responses.\nStill active means: new viewpoints, new "
            "counterexamples, substantive position changes, new combinations, new "
            "framings, or new branches still worth pursuing are clearly appearing.\n\n"
            f"## Discussion transcript so far\n\n{_render_transcript(transcript)}\n\n"
            'Reply with JSON only: {"status": "active" or "exhausted", "reason": "..."}'
        )
        ev = c("util-gemini-3.7", eval_prompt, f"coll-stop:{task.id}:r{round}", max_tokens=2000)
        verdict = _parse_json(_user_text(ev)) or {}
        stopping_log.append({"after_round": round, "verdict": verdict})
        if verdict.get("status") == "exhausted":
            stop_reason = "evaluator: exhausted"
            break

        round += 1
        def later(alias: str):
            others_msgs = _render_transcript(transcript, exclude_alias=alias)
            prompt = (
                f"{user_prompt}\n\n---\nFull discussion so far (every message is "
                f"verbatim):\n\n{others_msgs}\n\n"
                f"Decide freely whether you have something substantive to add "
                f"(new argument, counterexample, concession, recombination, new "
                f"framing, or an answer to an open attack). {FREE_MOVES}\n"
                "If and only if you have nothing substantive to add, reply with "
                "exactly: NO_CONTRIBUTION\n"
                "Otherwise write your contribution (at most 350 words) and end "
                "with exactly one line:\n"
                "STANCE: <one sentence stating your current position>"
            )
            return c(alias, prompt, f"coll-r{round}:{task.id}")

        recs = {a: r for a, r in zip(PARTICIPANTS, map_pool(later, PARTICIPANTS))}
        n_contrib = 0
        for alias in PARTICIPANTS:
            text = _user_text(recs[alias])
            if _is_abstain(text):
                transcript.append({"round": round, "alias": alias, "kind": "abstain", "text": text})
            else:
                n_contrib += 1
                transcript.append({"round": round, "alias": alias, "kind": "contribution", "text": text})
        if n_contrib == 0:
            stop_reason = "all participants abstained"
            break

    if stop_reason is None:
        stop_reason = f"round budget cap ({MAX_ROUNDS}) reached"
        budget_note = stop_reason

    # ---- Weakest-belief search (all participants) ----
    def weakest(alias: str):
        prompt = (
            f"{user_prompt}\n\n---\nDiscussion transcript:\n\n{_render_transcript(transcript, exclude_alias=alias)}\n\n"
            "Weakest-belief search: among the content that currently influences "
            "your final judgment, what are you least sure about, and why? If it "
            "were wrong, what would change? Answer in at most 200 words. Then "
            "append exactly these lines:\n"
            "CURRENT STANCE: <one sentence>\n"
            "OPEN DISAGREEMENTS I SEE: <comma-separated one-line list of points "
            "where participants still disagree>"
        )
        return c(alias, prompt, f"coll-weakest:{task.id}")

    weakest_recs = {a: r for a, r in zip(PARTICIPANTS, map_pool(weakest, PARTICIPANTS))}

    # ---- Blind-spot search: fresh clean-context instances ----
    stances = []
    for alias in PARTICIPANTS:
        s = _extract_stance(_user_text(weakest_recs[alias])) or _extract_stance(
            next((m["text"] for m in reversed(transcript) if m["alias"] == alias and m["kind"] != "abstain"), "")
        )
        stances.append(f"{POOL_LABELS[alias]}: {s or '(no stance extracted)'}")
    disagreements = []
    for alias in PARTICIPANTS:
        m = re.search(r"(?im)^\s*OPEN DISAGREEMENTS I SEE:\s*(.+)$", _user_text(weakest_recs[alias]) or "")
        if m:
            disagreements.append(m.group(1).strip())
    blindspot_brief = (
        f"{user_prompt}\n\n---\nCurrent main candidate judgments:\n" + "\n".join(stances)
        + "\n\nImportant unresolved disagreements reported by the participants:\n"
        + ("\n".join(f"- {d}" for d in disagreements) if disagreements else "(none reported)")
    )

    def blindspot(alias: str):
        prompt = (
            f"{blindspot_brief}\n\n---\nA group of analysts has been discussing "
            "this problem; you have NOT seen their discussion and know only the "
            "summary above. Blind-spot search: what is the whole discussion most "
            "likely to be collectively missing? Consider: an erroneous framing "
            "shared by everyone, a shared false assumption, a neglected path or "
            "option, second-order effects, or a simpler solution. Answer in at "
            "most 300 words. If you believe nothing important is missing, say "
            "so explicitly."
        )
        return c(alias, prompt, f"coll-blindspot:{task.id}", system=(
            "You are a fresh outside reviewer with no stake in the discussion. "
            "Write in English."))

    blindspot_recs = {
        "util-gemini-3.7": blindspot("util-gemini-3.7"),
        # fresh clean-context instance of a pool model: stateless call with only
        # the brief above, so it never saw the debate transcript
        "kimi-k3-fresh": blindspot("kimi-k3"),
    }

    # ---- Materiality gate: re-enter reasoning or close ----
    gate_prompt = (
        "A discussion reached a near-final state, then two outside reviewers "
        "searched for collective blind spots. Decide whether the reviewers "
        "raised MATERIAL new considerations that the discussion had not already "
        "covered (material = could change the final judgment if taken "
        "seriously; not material = already covered, trivial, or speculative).\n\n"
        "## Reviewer 1\n\n" + _clip(_user_text(blindspot_recs["util-gemini-3.7"]))
        + "\n\n## Reviewer 2\n\n" + _clip(_user_text(blindspot_recs["kimi-k3-fresh"]))
        + "\n\n## Discussion transcript\n\n" + _render_transcript(transcript)
        + '\n\nReply with JSON only: {"material": true or false, "reason": "..."}'
    )
    gate = c("util-gemini-3.7", gate_prompt, f"coll-gate:{task.id}", max_tokens=2000)
    gate_verdict = _parse_json(_user_text(gate)) or {"material": False, "reason": "unparseable"}

    if gate_verdict.get("material"):
        def reenter(alias: str):
            prompt = (
                f"{user_prompt}\n\n---\nDiscussion transcript:\n\n"
                f"{_render_transcript(transcript, exclude_alias=alias)}\n\n"
                "Two outside reviewers searched for collective blind spots and "
                "raised the following:\n\n"
                "## Outside reviewer 1\n\n" + _clip(_user_text(blindspot_recs["util-gemini-3.7"]))
                + "\n\n## Outside reviewer 2\n\n" + _clip(_user_text(blindspot_recs["kimi-k3-fresh"]))
                + f"\n\nIf this changes your judgment, respond accordingly. {FREE_MOVES}\n"
                "Write at most 300 words; end with exactly one line:\n"
                "STANCE: <one sentence stating your current position>"
            )
            return c(alias, prompt, f"coll-reenter:{task.id}")
        reenter_recs = {a: r for a, r in zip(PARTICIPANTS, map_pool(reenter, PARTICIPANTS))}
        for alias in PARTICIPANTS:
            text = _user_text(reenter_recs[alias])
            transcript.append({
                "round": round + 1,
                "alias": alias,
                "kind": "abstain" if _is_abstain(text) else "post-blindspot",
                "text": text,
            })

    # ---- Final judgments (all participants) ----
    def final(alias: str):
        prompt = (
            f"{user_prompt}\n\n---\nFull discussion transcript (verbatim):\n\n"
            f"{_render_transcript(transcript, exclude_alias=alias)}\n\n"
            "Weakest-belief answers of the participants:\n\n"
            + "\n\n".join(f"## {POOL_LABELS[a]}\n{_clip(_user_text(weakest_recs[a]))}" for a in PARTICIPANTS if a != alias)
            + "\n\nWrite your FINAL judgment (at most 350 words). You are free to "
            "keep, change, or withdraw any position; consensus is NOT required — "
            "if you still disagree with others, say so plainly. Structure:\n"
            "FINAL JUDGMENT: <your decision/answer>\n"
            "WHAT CHANGED FOR ME: <what the discussion changed, or 'nothing'>\n"
            "REMAINING DISAGREEMENT: <points where you still disagree with others, "
            "or 'none'>"
        )
        return c(alias, prompt, f"coll-final:{task.id}")

    final_recs = {a: r for a, r in zip(PARTICIPANTS, map_pool(final, PARTICIPANTS))}

    # ---- Renderer (non-participant): format the final state honestly ----
    render_prompt = (
        f"{user_prompt}\n\n---\nFive analysts each wrote a final judgment after "
        "an open discussion. Your ONLY job is to format their collective final "
        "state into one readable document. Do NOT add analysis of your own, do "
        "NOT invent consensus, do NOT drop disagreements. If they disagree, the "
        "document must present the surviving alternatives and who holds them "
        "(by participant number). Structure:\n"
        "## Analysis\nwhat the group converges on, and the main lines of "
        "reasoning supporting it, with attribution to participant numbers\n"
        "## Final Judgment\nthe group's decision state: a single recommendation "
        "IF one exists; otherwise the surviving alternatives and unresolved "
        "disagreements, stated plainly\n"
        "## Confidence\nthe group's confidence situation, including reported "
        "uncertainties\n\n"
        + "\n\n".join(f"## {POOL_LABELS[a]} final judgment\n\n{_clip(_user_text(final_recs[a]))}" for a in PARTICIPANTS)
    )
    rendered = call_model(
        "util-gemini-3.7",
        [{"role": "system", "content": "You are a neutral scribe. Format only; never add or decide content. Write in English."},
         {"role": "user", "content": render_prompt}],
        run_id=run_id, tag=f"coll-render:{task.id}", calls_dir=calls_dir, max_tokens=8000,
    )

    state = {
        "condition": "COLLECTIVE", "task": task.id,
        "mechanism": "open collective reasoning: initials -> comprehensive cross-reading -> "
                     "abstain-able free rounds (state-based stop) -> weakest-belief search -> "
                     "blind-spot search (fresh contexts) -> materiality gate -> finals -> neutral renderer",
        "initials": {a: _user_text(r) for a, r in initials.items()},
        "transcript": transcript,
        "stop_reason": stop_reason,
        "budget_note": budget_note,
        "stopping_log": stopping_log,
        "weakest_belief": {a: _user_text(r) for a, r in weakest_recs.items()},
        "blindspot": {k: _user_text(r) for k, r in blindspot_recs.items()},
        "blindspot_brief": blindspot_brief,
        "materiality_gate": gate_verdict,
        "finals": {a: _user_text(r) for a, r in final_recs.items()},
        "rendered_final": _user_text(rendered),
        "provenance": {
            "rounds_run": max(m["round"] for m in transcript),
            "calls": 5 + 5 * max(m["round"] for m in transcript) + 5 + 2 + 1 + 5 + 1,
        },
    }
    _save_state(out_dir, f"collective_{task.id}", state)
    return state
