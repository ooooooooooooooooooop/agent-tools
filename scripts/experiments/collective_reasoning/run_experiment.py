#!/usr/bin/env python3
"""Experiment orchestrator.

Usage (from this directory):
  python run_experiment.py --run-id exp1 --phase run     [--tasks T1,T2] [--conditions current,independent,council,collective]
  python run_experiment.py --run-id exp1 --phase judge   [--tasks T1,T2]
  python run_experiment.py --run-id exp1 --phase metrics
  python run_experiment.py --run-id exp1 --phase all

All model calls are cached under <run>/calls (idempotent; safe to re-run).
Nothing here touches the production skill, routing, or registries.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from client import JUDGES, PARTICIPANTS
from collective import run_collective
from conditions import run_council, run_current, run_independent
from judge import extract_decision_doc, novelty, pairwise, rank_initials, scrub
from tasks import TASKS, TASKS_BY_ID

ART_ROOT = Path(__file__).resolve().parents[3] / "artifacts" / "collective_reasoning"

ALL_CONDITIONS = ["current", "independent", "council", "collective"]
ALL_TASKS = [t.id for t in TASKS]


def run_dir(run_id: str) -> Path:
    d = ART_ROOT / run_id
    (d / "calls").mkdir(parents=True, exist_ok=True)
    (d / "state").mkdir(parents=True, exist_ok=True)
    (d / "judge").mkdir(parents=True, exist_ok=True)
    return d


def log(run_id: str, msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(run_dir(run_id) / "run_log.md", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state(run_id: str, name: str) -> dict | None:
    p = run_dir(run_id) / "state" / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ------------------------------------------------------------------ phase: run


def phase_run(run_id: str, tasks: list[str], conditions: list[str]) -> None:
    calls_dir = run_dir(run_id) / "calls"
    state_dir = run_dir(run_id) / "state"
    for tid in tasks:
        task = TASKS_BY_ID[tid]
        log(run_id, f"=== {tid}: {task.title} ({task.kind}) ===")
        if "current" in conditions:
            if not (state_dir / f"current_{tid}.json").exists():
                t0 = time.time()
                st = run_current(task, run_id, calls_dir, state_dir)
                log(run_id, f"CURRENT done in {time.time() - t0:.0f}s (err={st['provenance'].get('usage') is None})")
            else:
                log(run_id, "CURRENT cached")
        if "independent" in conditions:
            if not (state_dir / f"independent_{tid}.json").exists():
                t0 = time.time()
                run_independent(task, run_id, calls_dir, state_dir)
                log(run_id, f"INDEPENDENT done in {time.time() - t0:.0f}s")
            else:
                log(run_id, "INDEPENDENT cached")
        if "council" in conditions:
            if not (state_dir / f"council_{tid}.json").exists():
                t0 = time.time()
                run_council(task, run_id, calls_dir, state_dir)
                log(run_id, f"COUNCIL done in {time.time() - t0:.0f}s")
            else:
                log(run_id, "COUNCIL cached")
        if "collective" in conditions:
            if not (state_dir / f"collective_{tid}.json").exists():
                t0 = time.time()
                st = run_collective(task, run_id, calls_dir, state_dir)
                log(
                    run_id,
                    f"COLLECTIVE done in {time.time() - t0:.0f}s "
                    f"(stop={st['stop_reason']}, rounds={st['provenance']['rounds_run']})",
                )
            else:
                log(run_id, "COLLECTIVE cached")


# ----------------------------------------------------------------- phase: judge


def _decision_docs(run_id: str, tid: str) -> dict:
    """Build the anonymized decision documents for judging."""
    task = TASKS_BY_ID[tid]
    docs: dict[str, str] = {}
    cur = load_state(run_id, f"current_{tid}")
    cou = load_state(run_id, f"council_{tid}")
    col = load_state(run_id, f"collective_{tid}")
    if cur:
        docs["CURRENT"] = extract_decision_doc(task, cur["response"], "CURRENT")
    if cou:
        docs["COUNCIL"] = extract_decision_doc(task, cou["synthesis"], "COUNCIL")
    if col:
        docs["COLLECTIVE"] = extract_decision_doc(task, col["rendered_final"], "COLLECTIVE")
        for alias in PARTICIPANTS:
            docs[f"initial::{alias}"] = extract_decision_doc(task, col["initials"][alias], "initial")
    return docs


def _best_initial(run_id: str, tid: str, initials_docs: dict[str, str]) -> tuple[str | None, dict]:
    """Best independent initial answer by mean rank across both judges."""
    calls_dir = run_dir(run_id) / "calls"
    task = TASKS_BY_ID[tid]
    ranks: dict[str, list[int]] = {a: [] for a in initials_docs}
    raw = {}
    for judge in JUDGES:
        cached = load_judge(run_id, f"rank_{tid}_{judge}")
        if cached is None:
            cached = rank_initials(task, initials_docs, judge, run_id, calls_dir)
            save_judge(run_id, f"rank_{tid}_{judge}", cached)
        raw[judge] = cached
        for pos, alias in enumerate(cached.get("ordered_aliases", []), start=1):
            if alias in ranks:
                ranks[alias].append(pos)
    if not any(ranks.values()):
        return None, raw
    mean_rank = {a: (sum(v) / len(v) if v else 99) for a, v in ranks.items()}
    best = min(mean_rank, key=mean_rank.get)
    return best, {"mean_rank": mean_rank, "raw": raw}


def save_judge(run_id: str, name: str, data: dict) -> None:
    p = run_dir(run_id) / "judge" / f"{name}.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def load_judge(run_id: str, name: str) -> dict | None:
    p = run_dir(run_id) / "judge" / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def phase_judge(run_id: str, tasks: list[str]) -> None:
    calls_dir = run_dir(run_id) / "calls"
    for tid in tasks:
        task = TASKS_BY_ID[tid]
        docs = _decision_docs(run_id, tid)
        if "COLLECTIVE" not in docs:
            log(run_id, f"{tid}: COLLECTIVE state missing, skip judging")
            continue
        log(run_id, f"=== judging {tid} ===")
        initials_docs = {a.split("::")[1]: d for a, d in docs.items() if a.startswith("initial::")}

        best, best_info = _best_initial(run_id, tid, initials_docs)
        if best:
            save_judge(run_id, f"bestinitial_{tid}", {"best_alias": best, **best_info})
            log(run_id, f"{tid}: best initial = {best}")

        pairs = []
        if "CURRENT" in docs:
            pairs += [("COLLECTIVE", "CURRENT"), ("CURRENT", "COUNCIL" if "COUNCIL" in docs else "COLLECTIVE")]
        if "COUNCIL" in docs:
            pairs.append(("COLLECTIVE", "COUNCIL"))
        if best:
            pairs += [("COLLECTIVE", f"initial::{best}")]
            if "CURRENT" in docs:
                pairs.append(("CURRENT", f"initial::{best}"))
        for alias in initials_docs:
            if best and alias == best:
                continue
            pairs.append(("COLLECTIVE", f"initial::{alias}"))

        for x, y in pairs:
            if x not in docs or y not in docs:
                continue
            for judge in JUDGES:
                name = f"pair_{tid}_{x.replace('::', '_')}__vs__{y.replace('::', '_')}_{judge}"
                if load_judge(run_id, name) is None:
                    res = pairwise(task, "", docs[x], docs[y], judge, run_id, calls_dir)
                    res.update({"x": x, "y": y})
                    save_judge(run_id, name, res)
                    log(run_id, f"{tid} {x} vs {y} [{judge}]: {res.get('winner')}")

        for judge in JUDGES:
            name = f"novelty_{tid}_{judge}"
            if load_judge(run_id, name) is None:
                res = novelty(task, initials_docs, docs["COLLECTIVE"], judge, run_id, calls_dir)
                save_judge(run_id, name, res)
                n_new = len(res.get("new_elements", []))
                log(run_id, f"{tid} novelty [{judge}]: {n_new} elements, blind_spot={res.get('blind_spot', {}).get('found')}, harm={res.get('harm', {}).get('lost_something_important')}")


# --------------------------------------------------------------- phase: metrics


def phase_metrics(run_id: str) -> dict:
    from metrics import aggregate

    out = aggregate(run_id)
    p = run_dir(run_id) / "metrics.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    log(run_id, f"metrics written: {p}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--phase", default="all", choices=["run", "judge", "metrics", "all"])
    ap.add_argument("--tasks", default=",".join(ALL_TASKS))
    ap.add_argument("--conditions", default=",".join(ALL_CONDITIONS))
    args = ap.parse_args()
    tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]
    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    if args.phase in ("run", "all"):
        phase_run(args.run_id, tasks, conditions)
    if args.phase in ("judge", "all"):
        phase_judge(args.run_id, tasks)
    if args.phase in ("metrics", "all"):
        phase_metrics(args.run_id)


if __name__ == "__main__":
    main()
