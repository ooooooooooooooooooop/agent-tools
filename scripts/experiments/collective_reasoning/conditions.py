#!/usr/bin/env python3
"""Condition runners: CURRENT / INDEPENDENT / COUNCIL (COLLECTIVE in collective.py).

Isolation guarantees:
- Every model call is a stateless HTTP request whose message list is built
  explicitly for that role; no hidden shared state exists between calls.
- Participant initials are shared (cached) across INDEPENDENT / COUNCIL /
  COLLECTIVE so the three multi-model conditions start from identical answers.
- No participant is ever assigned a thought-role persona.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from client import MODELS, PARTICIPANTS, call_model, map_pool
from tasks import Task, task_user_prompt

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_MD = REPO_ROOT / "skills" / "simulate-elite-experts" / "SKILL.md"

NEUTRAL_SYSTEM = (
    "You are an experienced senior analyst. Think carefully and give your own "
    "complete, honest judgment. Do not adopt any persona or role other than "
    "this one. Write in English."
)

# Stable display names used inside transcripts. These are neutral handles,
# not personas; they only exist so speakers can be told apart.
POOL_LABELS = {alias: f"Participant-{i + 1}" for i, alias in enumerate(PARTICIPANTS)}
ALIAS_BY_LABEL = {v: k for k, v in POOL_LABELS.items()}


def _user_text(rec: dict) -> str:
    return (rec.get("content") or "").strip()


def _save_state(out_dir: Path, name: str, state: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8"
    )


# ---------------------------------------------------------------- CURRENT ---


def run_current(task: Task, run_id: str, calls_dir: Path, out_dir: Path) -> dict:
    """Baseline: the production skill's real mechanism, one strong model."""
    skill_text = SKILL_MD.read_text(encoding="utf-8")
    system = (
        skill_text
        + "\n\nExecution mode: one-shot. Active profile: classic. Produce the "
        "complete output now, in English, and make sure the Moderator Synthesis "
        "section incorporates the structured answer requested by the task "
        "(Analysis / Final Judgment / Confidence content).\n"
    )
    tag = f"current:{task.id}"
    rec = call_model(
        "claude-sonnet",
        [{"role": "system", "content": system},
         {"role": "user", "content": task_user_prompt(task, for_current=True)}],
        run_id=run_id, tag=tag, calls_dir=calls_dir, max_tokens=16000,
    )
    state = {
        "condition": "CURRENT", "task": task.id, "mechanism": "production skill, single model",
        "model": "claude-sonnet", "response": _user_text(rec), "call_key": rec["key"],
        "provenance": {k: rec[k] for k in ("requested_model", "reported_model", "usage", "latency_s")},
    }
    _save_state(out_dir, f"current_{task.id}", state)
    return state


# ------------------------------------------------------------ INDEPENDENT ---


def run_initials(task: Task, run_id: str, calls_dir: Path) -> dict[str, dict]:
    """The 5 independent initial answers, shared by conditions B/C/D."""
    user = task_user_prompt(task)

    def one(alias: str) -> dict:
        return call_model(
            alias,
            [{"role": "system", "content": NEUTRAL_SYSTEM},
             {"role": "user", "content": user}],
            run_id=run_id, tag=f"initial:{task.id}", calls_dir=calls_dir, max_tokens=8000,
        )

    recs = map_pool(one, PARTICIPANTS)
    return {alias: rec for alias, rec in zip(PARTICIPANTS, recs)}


def run_independent(task: Task, run_id: str, calls_dir: Path, out_dir: Path) -> dict:
    initials = run_initials(task, run_id, calls_dir)
    state = {
        "condition": "INDEPENDENT", "task": task.id,
        "mechanism": "5 independent models, isolated contexts, no persona assignment",
        "initials": {
            alias: {
                "label": POOL_LABELS[alias],
                "model": alias,
                "provenance": {k: rec[k] for k in ("requested_model", "reported_model", "usage", "latency_s")},
                "text": _user_text(rec),
            }
            for alias, rec in initials.items()
        },
    }
    _save_state(out_dir, f"independent_{task.id}", state)
    return state


# ---------------------------------------------------------------- COUNCIL ---


def run_council(task: Task, run_id: str, calls_dir: Path, out_dir: Path) -> dict:
    """Traditional council/MAD: initials -> peer critique -> moderator synthesis."""
    initials = run_initials(task, run_id, calls_dir)
    user_prompt = task_user_prompt(task)

    def critique(alias: str) -> dict:
        others = [o for o in PARTICIPANTS if o != alias]
        body = [f"## {POOL_LABELS[o]}\n\n{_user_text(initials[o])}" for o in others]
        prompt = (
            f"{user_prompt}\n\n---\nBelow are the independent answers of other "
            "analysts to this same question. Review each of them: what is right, "
            "what is wrong or unsupported, what is missing. Be specific and "
            "technical. Do not restate your own answer; review theirs.\n\n"
            + "\n\n".join(body)
        )
        return call_model(
            alias,
            [{"role": "system", "content": NEUTRAL_SYSTEM},
             {"role": "user", "content": prompt}],
            run_id=run_id, tag=f"council-critique:{task.id}", calls_dir=calls_dir, max_tokens=8000,
        )

    critiques = {alias: rec for alias, rec in zip(PARTICIPANTS, map_pool(critique, PARTICIPANTS))}

    synth_body = []
    for alias in PARTICIPANTS:
        synth_body.append(f"## {POOL_LABELS[alias]} initial answer\n\n{_user_text(initials[alias])}")
    for alias in PARTICIPANTS:
        synth_body.append(f"## {POOL_LABELS[alias]} peer review\n\n{_user_text(critiques[alias])}")
    synth_prompt = (
        f"{user_prompt}\n\n---\nBelow are independent answers from five analysts "
        "and their peer reviews of each other. Write the final answer: select and "
        "synthesize the strongest content, correct what the reviews showed to be "
        "wrong, and produce the best single final answer.\n\n" + "\n\n".join(synth_body)
    )
    synth = call_model(
        "claude-sonnet",
        [{"role": "system", "content": NEUTRAL_SYSTEM},
         {"role": "user", "content": synth_prompt}],
        run_id=run_id, tag=f"council-synth:{task.id}", calls_dir=calls_dir, max_tokens=8000,
    )
    state = {
        "condition": "COUNCIL", "task": task.id,
        "mechanism": "independent initials -> peer critique -> moderator synthesis (traditional MAD)",
        "initials": {a: _user_text(r) for a, r in initials.items()},
        "critiques": {a: _user_text(r) for a, r in critiques.items()},
        "synthesis_model": "claude-sonnet",
        "synthesis": _user_text(synth),
        "provenance": {
            "initials": {a: r["usage"] for a, r in initials.items()},
            "critiques": {a: r["usage"] for a, r in critiques.items()},
            "synthesis": synth["usage"],
        },
    }
    _save_state(out_dir, f"council_{task.id}", state)
    return state
