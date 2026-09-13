#!/usr/bin/env python3
"""hypothesis_search.py — B: Hypothesis Search (V0.3.1 phase B).

Consumes problem objects — never raw episodes:
    ANOMALY | UNEXPLAINED_RESIDUAL | CHALLENGED_MODEL | CONFLICT |
    DISTILLED_PATTERN

Chain:
  problem object
  → applicability filter (flags → 1–3 operators, never all 8)
  → H4 candidates (each with a DISTINCT falsifiable prediction)
  → discriminative observation/probe plan
  → HYPOTHESIS_PROPOSAL | ABSTAIN

Invariants (web-adjudicated):
  - 8 operators are a search LIBRARY, not a checklist; calling all of them
    on every problem is itself a failure fixture.
  - H4 support can never come from "hypothesis search generated it" — every
    hypothesis stays MODEL_OUTPUT/low confidence until real Observation.
  - ABSTAIN is first-class: sufficient current evidence → no forced novelty;
    exhausted operators → representation/ontology H4 is allowed (B4), not
    endless parameter tuning.
  - proposals/ only. Never writes current.yaml.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HS_VERSION = "HS-1.0"

PROBLEM_KINDS = {"ANOMALY", "UNEXPLAINED_RESIDUAL", "CHALLENGED_MODEL",
                 "CONFLICT", "DISTILLED_PATTERN"}

# Operator library: each yields hypothesis template + discriminative
# prediction template. `flags` list = problem features that make it
# applicable (deterministic applicability filter).
OPERATORS = {
    "omitted_variable": {
        "flags": ["partial_pattern", "residual_unexplained"],
        "hypothesis": "a hidden variable modulates {subject}: outcome differs "
                      "by an unmeasured factor",
        "discriminative_prediction": "stratifying observations by candidate "
                                     "hidden factors separates confirm/refute"},
    "measurement_artifact": {
        "flags": ["source_conflict", "sensor_staleness", "instrument_known_bad"],
        "hypothesis": "{subject} anomaly is a measurement/recording artifact, "
                      "not a world fact",
        "discriminative_prediction": "an independent channel observing the same "
                                     "event will not reproduce the anomaly"},
    "causal_direction": {
        "flags": ["correlation_only", "intervention_free"],
        "hypothesis": "the causal arrow behind {subject} is reversed or "
                      "confounded, not direct",
        "discriminative_prediction": "an intervention/manipulation on the "
                                     "presumed cause changes the outcome only "
                                     "if direction holds"},
    "conditional_mediator": {
        "flags": ["scope_variance", "context_dependent"],
        "hypothesis": "{subject} holds only under a mediating condition "
                      "present in some episodes",
        "discriminative_prediction": "controlling the mediating condition "
                                     "reproduces/blocks the pattern on demand"},
    "time_delay": {
        "flags": ["lag_possible", "async_effects"],
        "hypothesis": "{subject} involves a delayed effect; observations "
                      "arrived before the causal lag closed",
        "discriminative_prediction": "waiting past the hypothesized lag makes "
                                     "the observation consistent"},
    "nonlinear_interaction": {
        "flags": ["magnitude_variance", "dose_dependent"],
        "hypothesis": "{subject} is nonlinear/interaction-driven; linear "
                      "predictions fail at extremes",
        "discriminative_prediction": "observations at extreme parameter values "
                                     "deviate in the predicted direction"},
    "representation_error": {
        "flags": ["persistent_contradiction", "operator_exhausted"],
        "hypothesis": "the problem is framed in the wrong variables — "
                      "{subject} needs a representation change",
        "discriminative_prediction": "re-expressing episodes in alternative "
                                     "variables dissolves the contradiction"},
    "ontology_error": {
        "flags": ["persistent_failure", "operator_exhausted"],
        "hypothesis": "the assumed entities/categories behind {subject} are "
                      "wrong — the hypothesis space itself is wrong",
        "discriminative_prediction": "no model in the current space fits; a "
                                     "newly posited entity predicts a novel "
                                     "observation"},
}


def _features(problem: dict) -> set:
    f = set(problem.get("feature_flags") or [])
    kind = problem.get("type") or problem.get("kind")
    if kind == "CONFLICT":
        f |= {"source_conflict"}
    if kind == "CHALLENGED_MODEL" and problem.get("persistent_failure"):
        f |= {"persistent_failure", "operator_exhausted"}
    if kind == "UNEXPLAINED_RESIDUAL":
        f |= {"residual_unexplained"}
    if problem.get("evidence_sufficient"):
        f.add("evidence_sufficient")
    return f


def applicable_ops(features: set, max_ops: int = 3) -> list[str]:
    hits = [name for name, spec in OPERATORS.items()
            if set(spec["flags"]) & features]
    # ontology/representation only when persistent failure flagged (B4)
    if "operator_exhausted" not in features:
        hits = [h for h in hits
                if h not in ("representation_error", "ontology_error")]
    return hits[:max_ops]


def validate_h4(h: dict, problem: dict) -> tuple[bool, list]:
    """Shared semantic-worker validator (GENERATE_HYPOTHESES mode)."""
    reasons = []
    if not h.get("discriminative_prediction"):
        reasons.append("no discriminative prediction")
    prob_refs = set(problem.get("evidence_refs") or [])
    cited = set(h.get("evidence_refs") or [])
    if prob_refs and not cited <= prob_refs:
        reasons.append("cites evidence outside problem packet")
    prob_scope = set(problem.get("scope") or [])
    if prob_scope and h.get("scope"):
        if not set(h["scope"] if isinstance(h["scope"], list)
                   else [h["scope"]]) <= prob_scope | {"*"}:
            reasons.append("scope outside problem scope")
    if not h.get("falsifier"):
        reasons.append("missing falsifier")
    return (not reasons), reasons


def hypothesize(problem: dict, worker_out: dict | None = None) -> dict:
    """One problem → {hypotheses, abstain}. Never fabricates support.
    worker_out = semantic worker (GENERATE_HYPOTHESES) results; template
    operators provide the deterministic fallback when no worker bound."""
    kind = problem.get("type") or problem.get("kind")
    if kind not in PROBLEM_KINDS:
        return {"abstain": True, "reason": f"unsupported problem kind {kind}"}
    feats = _features(problem)
    if "evidence_sufficient" in feats:
        return {"abstain": True,
                "reason": "evidence already supports current model — "
                          "no forced novelty (B3)"}
    # Semantic worker output is consulted FIRST — its operator choice is the
    # applicability justification. The template path below is the no-worker
    # fallback and keeps its own mechanical applicability gate.
    worker_rejected = []
    if worker_out:
        merged = []
        for h in worker_out:
            ok, reasons = validate_h4(h, problem)
            if not ok:
                worker_rejected.append({"operator": h.get("operator"),
                                        "reasons": reasons})
                continue
            h.setdefault("evidence_type", "MODEL_OUTPUT")
            h.setdefault("confidence", "low")
            merged.append(h)
        # dedup pseudo-competition: identical predictions collapse
        seen_preds, dedup = set(), []
        for h in merged:
            p = h["discriminative_prediction"]
            if p in seen_preds:
                worker_rejected.append({"operator": h.get("operator"),
                                        "reasons": ["duplicate prediction"]})
                continue
            seen_preds.add(p)
            dedup.append(h)
        result = {"abstain": not dedup, "hypotheses": dedup}
        if worker_rejected:
            result["worker_rejected"] = worker_rejected
        if not dedup:
            result["reason"] = "all worker hypotheses failed validation"
        return result

    ops = applicable_ops(feats)
    if not ops:
        return {"abstain": True,
                "reason": "no operator applicable to problem features"}
    subj = problem.get("subject") or problem.get("description") or kind
    out = []
    for op in ops:
        spec = OPERATORS[op]
        h = {
            "hypothesis_id": f"h4-{hashlib.sha256((problem.get('problem_id','?')+op).encode()).hexdigest()[:8]}",
            "operator": op,
            "hypothesis": spec["hypothesis"].format(subject=subj),
            "scope": problem.get("scope") or [subj],
            "discriminative_prediction": spec["discriminative_prediction"],
            "evidence_type": "MODEL_OUTPUT",
            "confidence": "low",
            "note": "support can only come from future real Observation; "
                    "being generated here confers zero evidential weight",
            "falsifier": "the discriminative prediction fails under a "
                         "controlled observation",
        }
        out.append(h)
    return {"abstain": False, "hypotheses": out}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--problems", required=True,
                    help="JSON file or problems/ dir of problem objects")
    ap.add_argument("--canonical", required=True)
    ap.add_argument("--worker", default=None,
                    help="file:<path> — semantic worker GENERATE_HYPOTHESES "
                         "output JSON {problem_id: [h4 objects]}")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--mark", default=None,
                    help="JSON file: {proposal_file, hypothesis_id, state, "
                         "observation_refs, note} — writes a new "
                         "HYPOTHESIS_STATE_UPDATE artifact (original proposal "
                         "is never mutated)")
    args = ap.parse_args()
    if args.mark:
        m = json.loads(Path(args.mark).read_text(encoding="utf-8"))
        if m.get("state") not in ("supported", "weakened", "falsified"):
            raise SystemExit("mark.state must be supported|weakened|falsified")
        canon = Path(args.canonical)
        pdir = canon / "proposals"
        pdir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d")
        name = f"{stamp}-hypothesis-state-{m['hypothesis_id']}.json"
        (pdir / name).write_text(json.dumps({
            "schema_version": "1.1", "kind": "HYPOTHESIS_STATE_UPDATE",
            "timestamp": datetime.now(timezone.utc).isoformat(
                timespec="seconds").replace("+00:00", "Z"),
            "status": "proposed", "update_class": "world_model",
            "classification": {"level": "PRIVATE",
                               "basis": ["derived_from_private_observation"]},
            "payload": {
                "proposal_ref": m.get("proposal_file"),
                "hypothesis_id": m["hypothesis_id"],
                "state": m["state"],
                "observation_refs": m.get("observation_refs", []),
                "note": m.get("note", ""),
                "rule": "supported H4 may become MODEL_PROPOSAL only via "
                        "promotion criteria; falsified stays falsified"}},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"written": [name], "canonical_write":
                          "NONE — proposals only"}, ensure_ascii=False))
        return 0
    src = Path(args.problems)
    if src.is_dir():
        problems = [json.loads(f.read_text(encoding="utf-8"))
                    for f in sorted(src.glob("*.json"))]
    else:
        problems = json.loads(src.read_text(encoding="utf-8"))
    worker = {}
    if args.worker and args.worker.startswith("file:"):
        worker = json.loads(Path(args.worker[5:]).read_text(encoding="utf-8"))
    canon = Path(args.canonical)
    results, written = [], []
    for prob in problems:
        r = hypothesize(prob, worker.get(prob.get("problem_id")))
        entry = {"problem_id": prob.get("problem_id"), **(
            {"abstain": True, "reason": r["reason"]} if r["abstain"] else
            {"hypotheses": len(r["hypotheses"]),
             "operators": [h["operator"] for h in r["hypotheses"]]})}
        if r.get("worker_rejected"):
            entry["worker_rejected"] = r["worker_rejected"]
        results.append(entry)
        if not r["abstain"] and not args.dry_run:
            pdir = canon / "proposals"
            pdir.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d")
            name = f"{stamp}-hypothesis-{prob.get('problem_id', 'x')}.json"
            (pdir / name).write_text(json.dumps({
                "schema_version": "1.1", "kind": "HYPOTHESIS_PROPOSAL",
                "timestamp": datetime.now(timezone.utc).isoformat(
                    timespec="seconds").replace("+00:00", "Z"),
                "status": "proposed", "update_class": "world_model",
                "classification": {"level": "PRIVATE",
                                   "basis": ["derived_from_private_problem"]},
                "payload": {"problem": prob, "h4_candidates": r["hypotheses"],
                            "hs_version": HS_VERSION}},
                ensure_ascii=False, indent=2), encoding="utf-8")
            written.append(name)
    print(json.dumps({"results": results, "written": written,
                      "canonical_write": "NONE — proposals only",
                      "hs_version": HS_VERSION}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
