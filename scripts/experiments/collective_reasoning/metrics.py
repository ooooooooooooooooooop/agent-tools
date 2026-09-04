#!/usr/bin/env python3
"""Metric aggregation for the collective-reasoning experiment."""

from __future__ import annotations

import json
import re
from pathlib import Path

from client import PARTICIPANTS
from tasks import TASKS_BY_ID
from verify_objective import check_objective

OBJ_TASKS = ["T1", "T2", "T6"]


def _load(run_id: str, sub: str, name: str) -> dict | None:
    p = ART(run_id) / sub / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def ART(run_id: str) -> Path:
    return Path(__file__).resolve().parents[3] / "artifacts" / "collective_reasoning" / run_id


def objective_results(run_id: str) -> dict:
    out = {}
    for tid in OBJ_TASKS:
        task = TASKS_BY_ID[tid]
        entries = {}

        initials_state = _load(run_id, "state", f"independent_{tid}")
        if initials_state:
            for alias, info in initials_state["initials"].items():
                entries[f"initial:{alias}"] = check_objective(tid, info["text"])

        cur = _load(run_id, "state", f"current_{tid}")
        if cur:
            entries["CURRENT"] = check_objective(tid, cur["response"])

        cou = _load(run_id, "state", f"council_{tid}")
        if cou:
            entries["COUNCIL"] = check_objective(tid, cou["synthesis"])

        col = _load(run_id, "state", f"collective_{tid}")
        if col:
            entries["COLLECTIVE:rendered"] = check_objective(tid, col["rendered_final"])
            for alias, text in col["finals"].items():
                entries[f"COLLECTIVE:final:{alias}"] = check_objective(tid, text)

        # harm: initial had it right, collective final lost it
        harm = {"T1": [], "T2": [], "T6": []}
        rendered = entries.get("COLLECTIVE:rendered")
        if rendered:
            if tid == "T1":
                lost = [k for k, v in entries.items()
                        if k.startswith("initial:") and v["defects_detected"] > rendered["defects_detected"]]
                harm["T1"] = lost
            elif tid == "T2":
                lost = [k for k, v in entries.items()
                        if k.startswith("initial:") and v["correct"] and not rendered["correct"]]
                harm["T2"] = lost
            elif tid == "T6":
                lost = [k for k, v in entries.items()
                        if k.startswith("initial:") and v["correct"] and not rendered["correct"]]
                harm["T6"] = lost
        out[tid] = {"entries": entries, "initial_correct_but_final_wrong": harm[tid]}
    return out


def pairwise_summary(run_id: str) -> dict:
    jdir = ART(run_id) / "judge"
    pairs: dict[tuple, dict] = {}
    best_initial: dict[str, str] = {}
    for p in sorted(jdir.glob("pair_*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        x, y = data["x"], data["y"]
        key = (x, y)
        e = pairs.setdefault(key, {"x_wins": 0, "y_wins": 0, "tie": 0, "judges": [], "details": []})
        w = data.get("winner")
        if w == "X":
            e["x_wins"] += 1
        elif w == "Y":
            e["y_wins"] += 1
        else:
            e["tie"] += 1
        e["judges"].append(data.get("judge"))
        e["details"].append({
            "judge": data.get("judge"), "winner": w,
            "worse_missing_something_important": data.get("worse_missing_something_important"),
            "key_reason": data.get("key_reason"),
        })
    for p in sorted(jdir.glob("bestinitial_*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        best_initial[data["best_alias"]] = p.stem.split("_")[1]
    return {"pairs": {f"{x} vs {y}": v for (x, y), v in pairs.items()}, "best_initial_by_task": best_initial}


def novelty_summary(run_id: str) -> dict:
    jdir = ART(run_id) / "judge"
    out = {}
    for p in sorted(jdir.glob("novelty_*.json")):
        tid, judge = p.stem.split("_")[1], p.stem.split("_")[2]
        data = json.loads(p.read_text(encoding="utf-8"))
        elems = [
            e for e in data.get("new_elements", [])
            if e.get("type") != "mostly_paraphrase" and e.get("decision_relevant")
        ]
        strong = [e for e in elems if e.get("plausible_or_correct")]
        o = out.setdefault(tid, {})
        o[judge] = {
            "strong_new_elements": strong,
            "all_elements": data.get("new_elements", []),
            "blind_spot": data.get("blind_spot"),
            "harm": data.get("harm"),
        }
    return out


def cost_summary(run_id: str) -> dict:
    calls_dir = ART(run_id) / "calls"
    agg: dict[str, dict] = {}
    for p in sorted(calls_dir.glob("*.json")):
        rec = json.loads(p.read_text(encoding="utf-8"))
        tag = rec.get("tag") or "unknown"
        parts = tag.split(":")
        cond = parts[0]
        task = parts[1] if len(parts) > 1 else "-"
        key = f"{cond}/{task}"
        e = agg.setdefault(key, {"calls": 0, "errors": 0, "prompt_tokens": 0, "completion_tokens": 0,
                                 "reasoning_tokens": 0, "latency_s": 0.0, "by_model": {}})
        e["calls"] += 1
        if rec.get("error"):
            e["errors"] += 1
        usage = rec.get("usage") or {}
        e["prompt_tokens"] += usage.get("prompt_tokens") or 0
        e["completion_tokens"] += usage.get("completion_tokens") or 0
        e["reasoning_tokens"] += (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        e["latency_s"] += rec.get("latency_s") or 0.0
        m = rec.get("model_alias") or "?"
        bm = e["by_model"].setdefault(m, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
        bm["calls"] += 1
        bm["prompt_tokens"] += usage.get("prompt_tokens") or 0
        bm["completion_tokens"] += usage.get("completion_tokens") or 0
    return agg


def collective_dynamics(run_id: str) -> dict:
    out = {}
    for p in sorted((ART(run_id) / "state").glob("collective_*.json")):
        tid = p.stem.split("_")[1]
        st = json.loads(p.read_text(encoding="utf-8"))
        abstains = sum(1 for m in st["transcript"] if m["kind"] == "abstain")
        changed = 0
        for alias, text in st["finals"].items():
            m = re.search(r"(?im)^\s*WHAT CHANGED FOR ME\s*:\s*(.+)$", text or "")
            if m and not re.search(r"nothing|no change|unchanged|n/?a\b", m.group(1), re.I):
                changed += 1
        out[tid] = {
            "rounds_run": st["provenance"]["rounds_run"],
            "stop_reason": st["stop_reason"],
            "budget_capped": bool(st.get("budget_note")),
            "abstentions": abstains,
            "stance_changes_self_reported": changed,
            "materiality_gate": st.get("materiality_gate"),
            "stopping_log": st.get("stopping_log"),
        }
    return out


def aggregate(run_id: str) -> dict:
    return {
        "objective": objective_results(run_id),
        "pairwise": pairwise_summary(run_id),
        "novelty": novelty_summary(run_id),
        "cost": cost_summary(run_id),
        "collective_dynamics": collective_dynamics(run_id),
    }
