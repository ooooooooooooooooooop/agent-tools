#!/usr/bin/env python3
"""distiller.py — Model Distillation & Lifecycle (V0.3.1 phase A).

L0/L1 closed episodes → candidate L2/L3 MODEL_PROPOSALs, plus lifecycle
governance proposals for existing models.

Hard invariants (web-adjudicated):
  - NEVER writes current.yaml / canonical models directly. Output is
    proposals/ only; promotion runs through existing U1 governance.
  - Evidence independence is first-class: MODEL_OUTPUT / DERIVED_INTERPRETATION
    echoes may help FIND a pattern but never raise real-evidence strength.
  - Lifecycle is two orthogonal axes:
        epistemic_status : candidate|active|challenged|superseded|retracted
        lifecycle_status : fresh|stale|archived|retired
    duplicate/conflict/low-value are relations/operations, not states.
  - stale is dependency-fingerprint based (env/version/scope/source), not
    pure time.
  - merge preserves provenance (merged_from + support/counterexample union);
    originals go superseded/archived, never deleted.
  - rejected candidates are not re-proposed without genuinely new evidence.

Candidate schema carries: candidate_id, abstraction_level, proposition, scope,
supporting {episode_refs, evidence_refs, source_refs, independence_groups,
evidence_types}, counterexamples, known_exceptions, confidence,
competing_explanation, novelty_vs_existing, existing_model_relation,
promotion_criteria, falsifier, access{level,basis}, distiller_version.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DISTILLER_VERSION = "DISTILLER-1.0"

DIRECT = "DIRECT_OBSERVATION"
DERIVED = "DERIVED_INTERPRETATION"
MODEL_OUT = "MODEL_OUTPUT"
HISTORICAL = "HISTORICAL_REPORT"

ACCESS_RANK = {"PUBLIC": 0, "SANITIZED": 1, "INTERNAL": 2, "PRIVATE": 3}

EVIDENCE_TYPE_BY_EVENT = {
    "RAW_EVIDENCE": DIRECT,
    "OBSERVATION_RECORDED": DIRECT,
    "PREDICTION_EVALUATED": DIRECT,      # refined by evaluation_source below
    "TOOL_RESULT": DIRECT,
    "INPUT_ROUTED": HISTORICAL,          # source claim, not our observation
    "MODEL_CREATED": MODEL_OUT,
    "MODEL_UPDATED": MODEL_OUT,
    "META_DECISION": DERIVED,
    "VALUE_DECISION": DERIVED,
    "PROBE_PLANNED": DERIVED,
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_yaml(p: Path) -> dict:
    try:
        import yaml
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def iter_closed_events(state_dir: Path, closed_minutes: int):
    """Same closed-episode rule as lp_evaluator: an actively-appended run file
    is never read."""
    cutoff = time.time() - closed_minutes * 60
    for d in ("runs", "ledger"):
        pdir = state_dir / d
        if not pdir.is_dir():
            continue
        for f in sorted(pdir.glob("*.jsonl")):
            if d == "runs" and f.stat().st_mtime > cutoff:
                continue
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


def evidence_type(ev: dict) -> str:
    et = ev.get("event_type")
    if et == "PREDICTION_EVALUATED":
        src = (ev.get("payload") or {}).get("evaluation_source", "self")
        return DIRECT if src != "self" else MODEL_OUT
    return EVIDENCE_TYPE_BY_EVENT.get(et, DERIVED)


def signature_of(pred_ev: dict) -> str:
    """Grouping key: model_id when bound, else normalized statement n-grams."""
    mid = pred_ev.get("model_id")
    if mid:
        return f"model:{mid}"
    stmt = ((pred_ev.get("payload") or {}).get("statement")
            or pred_ev.get("happened") or "")
    toks = re.findall(r"[a-zA-Z一-鿿0-9_]+", stmt.lower())
    grams = sorted(set(tuple(toks[i:i + 3]) for i in range(max(0, len(toks) - 2))))
    h = hashlib.sha256(json.dumps(grams).encode()).hexdigest()[:12]
    return f"text:{h}"


def access_taint(events: list[dict]) -> str:
    """Derivative inherits strictest access level of its evidence."""
    lvl = "PRIVATE"  # ledger episodes default PRIVATE
    for ev in events:
        a = ev.get("access") or {}
        l = a.get("level")
        if l and ACCESS_RANK.get(l, 3) > ACCESS_RANK[lvl]:
            lvl = l
    return lvl


def collect_signatures(state_dir: Path, closed_minutes: int) -> dict:
    """Group prediction lifecycle events by signature."""
    preds, evals, obs = {}, {}, {}
    models_echo = {}
    for ev in iter_closed_events(state_dir, closed_minutes):
        et = ev.get("event_type")
        if et == "PREDICTION_CREATED":
            sig = signature_of(ev)
            ev["_sig"] = sig
            preds[ev.get("prediction_id")] = ev
        elif et == "PREDICTION_EVALUATED":
            evals[ev.get("prediction_id")] = ev
        elif et == "OBSERVATION_RECORDED":
            obs.setdefault(ev.get("prediction_id"), []).append(ev)
        elif et in ("MODEL_CREATED", "MODEL_UPDATED"):
            models_echo.setdefault(ev.get("session_id"), []).append(ev)
    return {"preds": preds, "evals": evals, "obs": obs,
            "model_echoes": models_echo}


def build_candidates(sig_groups: dict, existing_models: dict) -> list[dict]:
    """For each signature with ≥2 independent grounded verdicts, build a
    candidate. Conflicting independent evidence → COMPETES_WITH pair, never
    a merged average."""
    preds, evals, obs = (sig_groups[k] for k in ("preds", "evals", "obs"))
    by_sig: dict[str, dict] = {}
    for pid, pev in preds.items():
        sig = pev["_sig"]
        g = by_sig.setdefault(sig, {"preds": [], "evals": [], "obs": []})
        g["preds"].append(pev)
        if pid in evals:
            g["evals"].append(evals[pid])
        g["obs"].extend(obs.get(pid, []))

    candidates = []
    for sig, g in by_sig.items():
        # grounded = non-self evaluations only
        grounded = [e for e in g["evals"] if evidence_type(e) == DIRECT]
        echo_count = len(g["evals"]) - len(grounded)
        groups = {}
        for e in grounded:
            # independence: session + evaluation source
            grp = f"{e.get('session_id')}:{(e.get('payload') or {}).get('evaluation_source', '?')}"
            groups.setdefault(grp, []).append(e)
        if len(groups) < 2:
            continue  # fixture B: echoes / single-source never graduate
        verdicts = {"confirmed": [], "refuted": [], "partial": [], "unknown": []}
        for e in grounded:
            verdicts.setdefault(
                (e.get("payload") or {}).get("verdict", "unknown"), []).append(e)
        all_evs = g["preds"] + g["evals"] + g["obs"]
        taint = access_taint(all_evs)
        common = {
            "abstraction_level": "L2",
            "scope": sig,
            "independence_summary": {
                "groups": sorted(groups.keys()),
                "n_independent": len(groups),
                "model_output_echoes_ignored": echo_count,
            },
            "access": {"level": taint, "basis": ["taint_inheritance"]},
            "distiller_version": DISTILLER_VERSION,
        }
        conf, refut = verdicts.get("confirmed", []), verdicts.get("refuted", [])
        stmt = ((g["preds"][0].get("payload") or {}).get("statement")
                or g["preds"][0].get("happened") or sig)
        if conf and refut:
            # fixture C: conflicting independent evidence → COMPETES_WITH pair
            for side, evs, prop in (
                    ("supports", conf, f"{stmt} — holds in observed scope"),
                    ("refutes", refut, f"{stmt} — fails in observed scope")):
                cand = dict(common)
                cand.update({
                    "candidate_id": f"cand-{hashlib.sha256((sig + side).encode()).hexdigest()[:8]}",
                    "proposition": prop,
                    "supporting": _support(evs),
                    "counterexamples": _refs(verdicts.get(
                        "refuted" if side == "supports" else "confirmed", [])),
                    "known_exceptions": [r for e in verdicts.get("partial", [])
                                         for r in _refs([e])],
                    "confidence": "low",
                    "competing_explanation": "see COMPETES_WITH sibling candidate",
                    "novelty_vs_existing": _relation(stmt, existing_models),
                    "existing_model_relation": "COMPETES_WITH",
                    "promotion_criteria": "discriminative observation resolving "
                                          "which scope conditions separate the "
                                          "two verdicts",
                    "falsifier": "a scope-controlled observation matching the "
                                 "opposing verdict",
                })
                candidates.append(cand)
        elif len(conf) >= 2:
            # fixture A: ≥2 independent confirmations → pattern candidate
            cand = dict(common)
            cand.update({
                "candidate_id": f"cand-{hashlib.sha256(sig.encode()).hexdigest()[:8]}",
                "proposition": f"Recurring confirmed pattern: {stmt}",
                "supporting": _support(conf),
                "counterexamples": _refs(refut + verdicts.get("partial", [])),
                "known_exceptions": _refs(verdicts.get("partial", [])),
                "confidence": "medium" if len(groups) >= 3 else "low",
                "competing_explanation": "shared hidden cause not yet ruled out",
                "novelty_vs_existing": _relation(stmt, existing_models),
                "existing_model_relation": _relation(stmt, existing_models)["relation"],
                "promotion_criteria": "one additional independent DIRECT_OBSERVATION "
                                      "in a different environment or scope",
                "falsifier": "a grounded refuting observation within declared scope",
            })
            candidates.append(cand)
    return candidates


def _refs(evs: list[dict]) -> list[str]:
    return [e.get("prediction_id") or e.get("event_id") or "?" for e in evs]


def _support(evs: list[dict]) -> dict:
    return {
        "episode_refs": sorted({e.get("session_id") for e in evs if e.get("session_id")}),
        "evidence_refs": _refs(evs),
        "source_refs": sorted({(e.get("payload") or {}).get("evaluation_source", "?")
                               for e in evs}),
        "independence_groups": sorted({
            f"{e.get('session_id')}:{(e.get('payload') or {}).get('evaluation_source', '?')}"
            for e in evs}),
        "evidence_types": sorted({evidence_type(e) for e in evs}),
    }


def _tokens(s: str) -> set:
    return set(re.findall(r"[a-zA-Z一-鿿0-9_]+", (s or "").lower()))


def _relation(stmt: str, existing_models: dict) -> dict:
    st = _tokens(stmt)
    best, best_j = None, 0.0
    for mid, m in (existing_models or {}).items():
        prop = m.get("proposition") if isinstance(m, dict) else str(m)
        j = len(st & _tokens(prop)) / max(1, len(st | _tokens(prop)))
        if j > best_j:
            best, best_j = mid, j
    if best_j >= 0.6:
        return {"relation": "DUPLICATE_OF", "of": best, "jaccard": round(best_j, 2)}
    if best_j >= 0.3:
        return {"relation": "REFINES", "of": best, "jaccard": round(best_j, 2)}
    return {"relation": "NEW"}


# ---------------- lifecycle governance ----------------

def dep_fingerprint(canon: Path) -> str:
    """Dependency fingerprint: schema + sources freshness + governance rev."""
    h = hashlib.sha256()
    cur = _load_yaml(canon / "current.yaml")
    h.update(str(cur.get("identity", {}).get("schema_version", "?")).encode())
    srcs = _load_yaml(canon / "sources.yaml")
    h.update(json.dumps(sorted(srcs.keys()), default=str).encode())
    gov = _load_yaml(canon / "governance.yaml")
    h.update(str(gov.get("policy_version", "?")).encode())
    return h.hexdigest()[:16]


def lifecycle_scan(canon: Path) -> list[dict]:
    """Emit LIFECYCLE_PROPOSALs: stale marking (dependency fingerprint),
    duplicate merge (with full provenance). Never mutates models."""
    cur = _load_yaml(canon / "current.yaml")
    wm = (cur.get("world_model") or {})
    models = wm.get("models") or {}
    fp = dep_fingerprint(canon)
    props = []
    seen = {}
    for mid, m in models.items():
        if not isinstance(m, dict):
            continue
        m.setdefault("epistemic_status", "active")
        # duplicate detection → MERGE proposal (operation, not state)
        st = _tokens(m.get("proposition", ""))
        for other, o in seen.items():
            j = len(st & o) / max(1, len(st | o))
            if j >= 0.6:
                props.append({
                    "kind": "MERGE_PROPOSAL",
                    "merged_from": [other, mid],
                    "policy": "support/counterexample/scope union; originals → "
                              "superseded+archived, never deleted",
                    "jaccard": round(j, 2)})
        seen[mid] = st
        # stale: model's recorded fingerprint differs from current
        rec_fp = (m.get("dependency_fingerprint") or {}).get("value")
        if rec_fp and rec_fp != fp:
            props.append({
                "kind": "LIFECYCLE_PROPOSAL",
                "model_id": mid,
                "lifecycle_status": "stale",
                "reason": "dependency fingerprint changed "
                          f"({rec_fp} → {fp}) — revalidation required",
                "note": "time alone does not stale a model"})
    return props


def rejected_hashes(proposals_dir: Path) -> set:
    out = set()
    if proposals_dir.is_dir():
        for f in proposals_dir.glob("*.json"):
            try:
                p = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if p.get("status") == "rejected":
                cand = (p.get("payload") or {}).get("candidate") or {}
                prop = cand.get("proposition") or ""
                if prop:
                    out.add(hashlib.sha256(prop.encode()).hexdigest())
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--canonical", required=True)
    ap.add_argument("--closed-minutes", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    canon = Path(args.canonical)
    existing = ((_load_yaml(canon / "current.yaml").get("world_model") or {})
                .get("models") or {})
    groups = collect_signatures(Path(args.state), args.closed_minutes)
    cands = build_candidates(groups, existing)
    rej = rejected_hashes(canon / "proposals")
    cands = [c for c in cands
             if hashlib.sha256(c["proposition"].encode()).hexdigest() not in rej]
    life = lifecycle_scan(canon)

    written = []
    if not args.dry_run:
        pdir = canon / "proposals"
        pdir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d")
        for c in cands:
            p = pdir / f"{stamp}-model-proposal-{c['candidate_id']}.json"
            p.write_text(json.dumps({
                "schema_version": "1.1", "kind": "MODEL_PROPOSAL",
                "timestamp": utcnow(), "status": "proposed",
                "update_class": "world_model",
                "classification": c["access"],
                "payload": {"candidate": c}}, ensure_ascii=False, indent=2),
                encoding="utf-8")
            written.append(p.name)
        for i, lp in enumerate(life):
            p = pdir / f"{stamp}-lifecycle-{i:03d}.json"
            p.write_text(json.dumps({
                "schema_version": "1.1", "kind": lp["kind"],
                "timestamp": utcnow(), "status": "proposed",
                "update_class": "world_model",
                "classification": {"level": "PRIVATE",
                                   "basis": ["derived_from_private_canonical"]},
                "payload": lp}, ensure_ascii=False, indent=2), encoding="utf-8")
            written.append(p.name)

    print(json.dumps({
        "candidates": len(cands), "rejected_resubmissions_filtered": len(rej),
        "lifecycle_proposals": len(life), "written": written,
        "canonical_write": "NONE — proposals only",
        "distiller_version": DISTILLER_VERSION}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
