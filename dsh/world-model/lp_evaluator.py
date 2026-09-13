#!/usr/bin/env python3
"""lp_evaluator.py — Learning Progress evaluator (V0.3.1 §9).

LP is epistemic health telemetry, NOT a reward signal.

Hard isolation rules enforced by construction:
  - reads ONLY closed episode files: <stateDir>/runs/*.jsonl + ledger/*.jsonl
    (a run file whose mtime is older than --closed-minutes is treated as closed;
     an actively-appended run is skipped)
  - writes ONLY to <outDir>/learning-metrics-<date>.json
  - no canonical write, no world_model update, no runtime state access
  - rubric is versioned + frozen in this file (RUBRIC_VERSION)

Output is a metric VECTOR — deliberately no single score, so it cannot be
maximized. Self-evaluation (the agent's own verdict) is recorded for
coverage/resolution/process telemetry but never counts as accuracy ground truth.

Leakage guards (V0.3.1 §9): the metrics file must never feed briefing,
runtime current state, value model, gate mode selection, worker reward, or
in-task meta decisions. System influence only via:
  fixed observation window → LP report → governance proposal → review → change
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RUBRIC_VERSION = "LP-RUBRIC-1.0-frozen"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def iter_events(state_dir: Path, closed_minutes: int):
    cutoff = time.time() - closed_minutes * 60
    for d in ("runs", "ledger"):
        pdir = state_dir / d
        if not pdir.is_dir():
            continue
        for f in sorted(pdir.glob("*.jsonl")):
            if d == "runs" and f.stat().st_mtime > cutoff:
                continue  # still-open episode: never read
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ev["_file"] = f.name
                    yield ev


def evaluate(state_dir: Path, closed_minutes: int) -> dict:
    preds = {}
    evals = {}
    counts = {"PREDICTION_CREATED": 0, "PREDICTION_EVALUATED": 0,
              "RAW_EVIDENCE": 0, "OBSERVATION_RECORDED": 0,
              "GUARD_BLOCKED": 0, "PROBE_PLANNED": 0, "META_DECISION": 0,
              "VALUE_DECISION": 0, "MODEL_UPDATED": 0, "BRIEFING_INJECTED": 0,
              "INPUT_ROUTED": 0, "NORMATIVE_DENIED": 0, "CONSTRAINT_ACCEPTED": 0,
              "FORK_DETECTED": 0, "LEASE_DENIED": 0}
    per_session = {}
    for ev in iter_events(state_dir, closed_minutes):
        et = ev.get("event_type")
        if et in counts:
            counts[et] += 1
        sid = ev.get("session_id", "?")
        per_session[sid] = per_session.get(sid, 0) + 1
        if et == "PREDICTION_CREATED":
            preds[ev.get("prediction_id")] = ev
        elif et == "PREDICTION_EVALUATED":
            evals[ev.get("prediction_id")] = ev

    verdicts = {"confirmed": 0, "refuted": 0, "partial": 0, "unknown": 0}
    eval_sources = {"mechanical": 0, "later_reality": 0,
                    "independent_model": 0, "human": 0, "self": 0}
    for pid, ev in evals.items():
        v = (ev.get("payload") or {}).get("verdict", "unknown")
        verdicts[v] = verdicts.get(v, 0) + 1
        src = (ev.get("payload") or {}).get("evaluation_source", "self")
        eval_sources[src] = eval_sources.get(src, 0) + 1

    n_eval = len(evals)
    n_pred = len(preds)
    # accuracy ONLY from non-self evaluation sources
    grounded = {pid: e for pid, e in evals.items()
                if (e.get("payload") or {}).get("evaluation_source", "self") != "self"}
    g_conf = sum(1 for e in grounded.values()
                 if (e.get("payload") or {}).get("verdict") == "confirmed")
    metrics = {
        "accuracy": {"value": (g_conf / len(grounded)) if grounded else None,
                     "n_grounded": len(grounded),
                     "note": "self-evaluations excluded — not ground truth"},
        "calibration": {"value": None, "note": "requires numeric confidence buckets over time — not yet emitted"},
        "coverage": {"value": (n_eval / n_pred) if n_pred else None,
                     "note": "fraction of created predictions that reached evaluation"},
        "specificity": {"with_falsifier": sum(1 for p in preds.values()
                                             if (p.get("payload") or {}).get("falsifier")),
                        "total": n_pred},
        "resolution_rate": {"value": ((verdicts["confirmed"] + verdicts["refuted"]) / n_eval)
                            if n_eval else None},
        "abstention": {"unknown_verdicts": verdicts["unknown"]},
        "decision_relevance": {"value": None,
                               "note": "requires downstream decision outcome linkage — future"},
        "natural_vs_test": {"value": None, "note": "requires task provenance tags — future"},
        "prediction_cost": {"total_predictions": n_pred},
        "sessions": {"closed_episodes": len(per_session),
                     "event_counts": counts},
        "verdicts": verdicts,
        "evaluation_sources": eval_sources,
    }
    return {
        "generated_at": utcnow(),
        "rubric_version": RUBRIC_VERSION,
        "scope_note": "EPISODIC TELEMETRY ONLY — must not feed briefing/runtime/value/gate/reward",
        "metrics": metrics,
        "goodhart_guard": "no single score; vector only; evaluator has no canonical/write/reward access",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True, help="runtime state dir (ledger/runs)")
    ap.add_argument("--out", required=True, help="output dir for learning-metrics")
    ap.add_argument("--closed-minutes", type=int, default=30)
    args = ap.parse_args()
    rep = evaluate(Path(args.state), args.closed_minutes)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    dest = out / f"learning-metrics-{datetime.now():%Y-%m-%d}.json"
    dest.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"wrote": str(dest), "episodes": rep["metrics"]["sessions"]["closed_episodes"],
                      "rubric": RUBRIC_VERSION}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
