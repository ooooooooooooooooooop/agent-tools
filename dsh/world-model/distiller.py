#!/usr/bin/env python3
"""distiller.py — Model Distillation & Lifecycle (V0.3.1 phase A).

A1 deterministic pattern/evidence packet detection (PASS'd adjudication)
+ A2 semantic abstraction layer with strict grounding:

  closed episodes
  → deterministic evidence packets (signature grouping, independence)
  → abstractor (LLM / file exchange / template-for-tests / none→ABSTAIN)
  → grounding validator (claims must chain back to packet evidence)
  → MODEL_PROPOSAL (proposals/ only — never current.yaml)
  → U1 governance

Invariants (web-adjudicated):
  - LLM/abstractor output is always MODEL_OUTPUT: never adds evidence weight.
  - ABSTAIN is a first-class outcome.
  - Lifecycle is two orthogonal axes:
        epistemic_status : candidate|active|challenged|superseded|retracted
        lifecycle_status : fresh|stale|archived|retired
  - stale = per-model dependency fingerprint change (not global rev, not
    pure time). Models declare `dependencies: {dep_key: recorded_value}`;
    only changes to declared deps trigger stale.
  - merge is a governance operation preserving merged_from + unions;
    originals superseded/archived, never deleted.
  - rejected candidates suppressed by candidate_family_fingerprint
    (subject+relation+scope+evidence sig+model relation), not just literal
    proposition hash — synonym rewording cannot bypass rejection.

Abstractor backends (--abstractor):
  none              default: every packet abstains (honest no-LLM boundary)
  file:<path>       JSON map {packet_id: candidate fields | {"abstain":...}}
                    — the exchange format a real model worker fills
  template          deterministic literal proposition (tests only)
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

DISTILLER_VERSION = "DISTILLER-1.1"

DIRECT = "DIRECT_OBSERVATION"
DERIVED = "DERIVED_INTERPRETATION"
MODEL_OUT = "MODEL_OUTPUT"
HISTORICAL = "HISTORICAL_REPORT"

ACCESS_RANK = {"PUBLIC": 0, "SANITIZED": 1, "INTERNAL": 2, "PRIVATE": 3}
RELATIONS = {"NEW", "DUPLICATE_OF", "REFINES", "GENERALIZES",
             "CONTRADICTS", "COMPETES_WITH", "REVIVES_SUPERSEDED"}

EVIDENCE_TYPE_BY_EVENT = {
    "RAW_EVIDENCE": DIRECT,
    "OBSERVATION_RECORDED": DIRECT,
    "PREDICTION_EVALUATED": DIRECT,      # refined by evaluation_source
    "TOOL_RESULT": DIRECT,
    "INPUT_ROUTED": HISTORICAL,
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
    """Same closed-episode rule as lp_evaluator: an actively-appended run
    file is never read."""
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
    if ev.get("event_type") == "PREDICTION_EVALUATED":
        src = (ev.get("payload") or {}).get("evaluation_source", "self")
        return DIRECT if src != "self" else MODEL_OUT
    return EVIDENCE_TYPE_BY_EVENT.get(ev.get("event_type"), DERIVED)


def signature_of(pred_ev: dict) -> str:
    mid = pred_ev.get("model_id")
    if mid:
        return f"model:{mid}"
    stmt = ((pred_ev.get("payload") or {}).get("statement")
            or pred_ev.get("happened") or "")
    toks = re.findall(r"[a-zA-Z一-鿿0-9_]+", stmt.lower())
    grams = sorted(set(tuple(toks[i:i + 3]) for i in range(max(0, len(toks) - 2))))
    return "text:" + hashlib.sha256(json.dumps(grams).encode()).hexdigest()[:12]


def access_taint(events: list[dict]) -> str:
    lvl = "PRIVATE"
    for ev in events:
        a = ev.get("access")
        l = a.get("level") if isinstance(a, dict) else a
        if l and ACCESS_RANK.get(l, 3) > ACCESS_RANK[lvl]:
            lvl = l
    return lvl


def _tokens(s: str) -> set:
    return set(re.findall(r"[a-zA-Z一-鿿0-9_]+", (s or "").lower()))


def _refs(evs: list[dict]) -> list[str]:
    return [e.get("prediction_id") or e.get("event_id") or "?" for e in evs]


# ---------------- A1: evidence packets ----------------

def build_packets(state_dir: Path, closed_minutes: int,
                  existing_models: dict) -> list[dict]:
    """Group closed-episode prediction lifecycles into evidence packets.
    Only signatures with ≥2 independent grounded groups become packets;
    self/MODEL_OUTPUT echoes are counted but never counted as support."""
    preds, evals, obs = {}, {}, {}
    for ev in iter_closed_events(state_dir, closed_minutes):
        et = ev.get("event_type")
        if et == "PREDICTION_CREATED":
            ev["_sig"] = signature_of(ev)
            preds[ev.get("prediction_id")] = ev
        elif et == "PREDICTION_EVALUATED":
            evals[ev.get("prediction_id")] = ev
        elif et == "OBSERVATION_RECORDED":
            obs.setdefault(ev.get("prediction_id"), []).append(ev)

    by_sig: dict[str, dict] = {}
    for pid, pev in preds.items():
        g = by_sig.setdefault(pev["_sig"], {"preds": [], "evals": [], "obs": []})
        g["preds"].append(pev)
        if pid in evals:
            g["evals"].append(evals[pid])
        g["obs"].extend(obs.get(pid, []))

    packets = []
    for sig, g in by_sig.items():
        grounded = [e for e in g["evals"] if evidence_type(e) == DIRECT]
        echoes = len(g["evals"]) - len(grounded)
        groups = {}
        for e in grounded:
            grp = f"{e.get('session_id')}:{(e.get('payload') or {}).get('evaluation_source', '?')}"
            groups.setdefault(grp, []).append(e)
        if len(groups) < 2:
            continue
        verdicts = {"confirmed": [], "refuted": [], "partial": [], "unknown": []}
        for e in grounded:
            verdicts.setdefault(
                (e.get("payload") or {}).get("verdict", "unknown"), []).append(e)
        all_evs = g["preds"] + g["evals"] + g["obs"]
        stmt = ((g["preds"][0].get("payload") or {}).get("statement")
                or g["preds"][0].get("happened") or sig)
        scopes = sorted({(e.get("payload") or {}).get("scope")
                         for e in all_evs if (e.get("payload") or {}).get("scope")})
        packet = {
            "packet_id": f"pkt-{hashlib.sha256(sig.encode()).hexdigest()[:10]}",
            "signature": sig,
            "observed_statement": stmt,
            "observed_scopes": scopes,
            "verdicts": {k: _refs(v) for k, v in verdicts.items() if v},
            "supporting": {
                "episode_refs": sorted({e.get("session_id") for e in grounded
                                        if e.get("session_id")}),
                "evidence_refs": _refs(grounded),
                "source_refs": sorted({(e.get("payload") or {}).get(
                    "evaluation_source", "?") for e in grounded}),
                "independence_groups": sorted(groups.keys()),
                "evidence_types": sorted({evidence_type(e) for e in grounded}),
            },
            "model_output_echoes_ignored": echoes,
            "conflict": bool(verdicts.get("confirmed") and verdicts.get("refuted")),
            "access": {"level": access_taint(all_evs),
                       "basis": ["taint_inheritance"]},
            "relation_hint": _relation(stmt, existing_models),
            "evidence_tokens": sorted(_tokens(stmt) | {
                t for e in all_evs for t in _tokens(json.dumps(
                    e.get("payload") or {}, ensure_ascii=False))}),
        }
        packets.append(packet)
    return packets


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


# ---------------- A2: abstraction + grounding ----------------

def family_fingerprint(c: dict) -> str:
    """Dedup key robust to rewording: normalized subject + relation + scope +
    evidence signature + existing-model relation."""
    subj = " ".join(sorted(_tokens(c.get("proposition", ""))))[:200]
    evsig = hashlib.sha256(json.dumps(
        sorted((c.get("supporting") or {}).get("evidence_refs", []))
    ).encode()).hexdigest()[:10]
    raw = "|".join([
        subj, c.get("abstraction_level", "L2"), str(c.get("scope", "")),
        evsig, str(c.get("existing_model_relation", ""))])
    return "fam-" + hashlib.sha256(raw.encode()).hexdigest()[:12]


def template_abstractor(packet: dict) -> dict:
    """Degenerate abstractor: literal proposition from observed statement.
    For tests / last resort. Marks confidence_basis=template."""
    if packet["conflict"]:
        return {"abstain": True,
                "abstain_reason": "conflict requires scoped competing propositions"}
    return {
        "proposition": f"Recurring confirmed pattern: {packet['observed_statement']}",
        "abstraction_level": "L2",
        "scope": packet["observed_scopes"] or [packet["signature"]],
        "confidence": "low",
        "competing_explanations": ["shared hidden cause not yet ruled out"],
        "existing_model_relation": packet["relation_hint"]["relation"],
        "falsifier": "a grounded refuting observation within declared scope",
        "promotion_criteria": "one additional independent DIRECT_OBSERVATION "
                              "in a different environment or scope",
        "known_exceptions": packet["verdicts"].get("partial", []),
        "confidence_basis": "template",
    }


def validate_abstraction(cand: dict, packet: dict) -> tuple[bool, list]:
    """Grounding validator: every material claim must chain back to the
    evidence packet. Returns (accepted, reasons)."""
    reasons = []
    if cand.get("abstain"):
        return True, ["abstain"]
    prop = cand.get("proposition", "")
    if not prop:
        reasons.append("empty proposition")
    # scope: must not exceed observed scopes (fixture E)
    obs_scopes = set(packet.get("observed_scopes") or [packet["signature"]])
    cand_scopes = set(cand.get("scope") or obs_scopes)
    if packet.get("observed_scopes") and not cand_scopes <= obs_scopes | {"*"}:
        if cand.get("scope") and cand["scope"] != ["*"]:
            reasons.append(f"scope overgeneralization: {cand_scopes - obs_scopes}")
    # ungrounded causal claim (fixture G): candidate may reference only
    # evidence refs present in packet
    pkt_refs = set(packet["supporting"]["evidence_refs"])
    cited = set((cand.get("supporting") or {}).get("evidence_refs", []))
    if not cited <= pkt_refs:
        reasons.append("cites evidence outside packet")
    # counterexamples present → must carry known_exceptions or conditioned scope
    if packet["verdicts"].get("refuted") or packet["verdicts"].get("partial"):
        if not (cand.get("known_exceptions") or cand.get("scope_conditions")):
            reasons.append("packet has counterexamples; candidate lacks "
                           "known_exceptions/scope_conditions (fixture F)")
    if cand.get("existing_model_relation") not in RELATIONS:
        reasons.append("invalid existing_model_relation")
    if not cand.get("falsifier"):
        reasons.append("missing falsifier")
    if not cand.get("promotion_criteria"):
        reasons.append("missing promotion_criteria")
    return (not reasons), reasons


def assemble_candidate(cand: dict, packet: dict) -> dict:
    """Merge abstractor output with packet grounding; LLM output stays
    MODEL_OUTPUT — it never adds evidence weight."""
    sup = dict(packet["supporting"])
    cited = (cand.get("supporting") or {}).get("evidence_refs")
    if cited:
        sup["evidence_refs"] = sorted(set(cited) & set(sup["evidence_refs"]))
    full = {
        "candidate_id": f"cand-{hashlib.sha256(packet['packet_id'].encode()).hexdigest()[:8]}",
        "proposition": cand["proposition"],
        "abstraction_level": cand.get("abstraction_level", "L2"),
        "scope": cand.get("scope") or packet["observed_scopes"] or [packet["signature"]],
        "scope_conditions": cand.get("scope_conditions"),
        "assumptions": cand.get("assumptions", []),
        "supporting": sup,
        "counterexamples": packet["verdicts"].get("refuted", []) +
                           packet["verdicts"].get("partial", []),
        "known_exceptions": cand.get("known_exceptions", []),
        "confidence": cand.get("confidence", "low"),
        "confidence_basis": cand.get("confidence_basis", "abstractor"),
        "competing_explanations": cand.get("competing_explanations", []),
        "novelty_vs_existing": packet["relation_hint"],
        "existing_model_relation": cand.get(
            "existing_model_relation", packet["relation_hint"]["relation"]),
        "falsifier": cand["falsifier"],
        "promotion_criteria": cand["promotion_criteria"],
        "access": packet["access"],
        "independence_summary": {
            "groups": packet["supporting"]["independence_groups"],
            "n_independent": len(packet["supporting"]["independence_groups"]),
            "model_output_echoes_ignored": packet["model_output_echoes_ignored"],
        },
        "distiller_version": DISTILLER_VERSION,
    }
    full["family_fingerprint"] = family_fingerprint(full)
    return full


def load_abstractor(spec: str):
    if spec == "none":
        return lambda p: {"abstain": True, "abstain_reason": "no abstractor bound"}
    if spec == "template":
        return template_abstractor
    if spec.startswith("file:"):
        data = json.loads(Path(spec[5:]).read_text(encoding="utf-8"))
        return lambda p: data.get(p["packet_id"],
                                  {"abstain": True,
                                   "abstain_reason": "abstractor returned nothing"})
    raise ValueError(f"unknown abstractor: {spec}")


# ---------------- problem-object builder (A→B bridge) ----------------

PROBLEM_FIELDS = ("problem_id", "type", "trigger_event", "affected_model_ids",
                  "residual_refs", "evidence_refs", "scope",
                  "attempted_updates", "why_existing_models_insufficient",
                  "access")


def build_problems(canon: Path, packets: list[dict]) -> list[dict]:
    """Mechanical problem-object builder. Sources are FIXED — the model never
    decides 'is this a problem':
      CONFLICT            ← packet with independent-evidence conflict
      CHALLENGED_MODEL    ← active model carrying valid counterevidence
      UNEXPLAINED_RESIDUAL← evaluated predictions no current model explains
      DISTILLED_PATTERN   ← grounded pattern no existing model absorbs
    Gating: if an ordinary parameter/structure update was never attempted and
    no reason given, the problem is suppressed (not silently dropped —
    reported as gated)."""
    problems, gated = [], []
    cur = _load_yaml(canon / "current.yaml")
    models = ((cur.get("world_model") or {}).get("models") or {})
    for pkt in packets:
        if pkt["conflict"]:
            problems.append({
                "problem_id": "prob-" + pkt["packet_id"][4:],
                "type": "CONFLICT", "trigger_event": pkt["packet_id"],
                "affected_model_ids": [],
                "residual_refs": pkt["verdicts"].get("refuted", []),
                "evidence_refs": pkt["supporting"]["evidence_refs"],
                "scope": pkt["observed_scopes"] or [pkt["signature"]],
                "attempted_updates": [],
                "why_existing_models_insufficient":
                    "independent grounded evidence conflicts — no current "
                    "model covers both verdicts",
                "access": pkt["access"]})
        elif not _relation_absorbed(pkt, models):
            problems.append({
                "problem_id": "prob-" + pkt["packet_id"][4:],
                "type": "DISTILLED_PATTERN", "trigger_event": pkt["packet_id"],
                "affected_model_ids": [],
                "residual_refs": [],
                "evidence_refs": pkt["supporting"]["evidence_refs"],
                "scope": pkt["observed_scopes"] or [pkt["signature"]],
                "attempted_updates": [],
                "why_existing_models_insufficient":
                    "grounded pattern has no absorbing existing model",
                "access": pkt["access"]})
    for mid, m in models.items():
        if not isinstance(m, dict):
            continue
        challenged = (m.get("epistemic_status") == "challenged"
                      or (m.get("counterevidence_refs") or []))
        if not challenged:
            continue
        attempted = m.get("attempted_updates") or []
        why = m.get("why_existing_models_insufficient")
        prob = {
            "problem_id": f"prob-chal-{mid}",
            "type": "CHALLENGED_MODEL", "trigger_event": mid,
            "affected_model_ids": [mid],
            "residual_refs": m.get("counterevidence_refs") or [],
            "evidence_refs": m.get("counterevidence_refs") or [],
            "scope": [m.get("scope", "?")],
            "attempted_updates": attempted,
            "why_existing_models_insufficient": why,
            "access": m.get("access") or {"level": "PRIVATE",
                                          "basis": ["model_entry"]}}
        if not attempted and not why:
            gated.append({"problem_id": prob["problem_id"],
                          "reason": "no attempted_updates and no "
                                    "insufficiency justification — ordinary "
                                    "update must be tried first"})
            continue
        problems.append(prob)
    return problems, gated


def _relation_absorbed(pkt: dict, models: dict) -> bool:
    return pkt["relation_hint"]["relation"] in ("DUPLICATE_OF", "REFINES")


# ---------------- lifecycle governance ----------------

def current_env(canon: Path) -> dict:
    """Environment fingerprint per dependency key (per-source, per-schema,
    per-policy) — not a single global rev."""
    env = {}
    cur = _load_yaml(canon / "current.yaml")
    ident = cur.get("identity") or {}
    env["schema:world-model"] = str(ident.get("schema_version", "?"))
    gov = _load_yaml(canon / "governance.yaml")
    env["policy:governance"] = str(gov.get("policy_version", "?"))
    srcs = _load_yaml(canon / "sources.yaml").get("sources") or {}
    for sid, s in srcs.items():
        rev = (s.get("revision") or s.get("updated_at")
               or len(s.get("correction_history") or [])
               if isinstance(s, dict) else 0)
        env[f"source:{sid}"] = str(rev)
    return env


def lifecycle_scan(canon: Path) -> list[dict]:
    """LIFECYCLE_PROPOSALs only — never mutates models.
    stale iff a dependency the model actually declared changed."""
    cur = _load_yaml(canon / "current.yaml")
    models = ((cur.get("world_model") or {}).get("models") or {})
    env = current_env(canon)
    props = []
    seen = {}
    for mid, m in models.items():
        if not isinstance(m, dict):
            continue
        st = _tokens(m.get("proposition", ""))
        for other, o in seen.items():
            j = len(st & o) / max(1, len(st | o))
            if j >= 0.6:
                props.append({
                    "kind": "MERGE_PROPOSAL", "merged_from": [other, mid],
                    "policy": "support/counterexample/scope union; originals → "
                              "superseded+archived, never deleted",
                    "jaccard": round(j, 2)})
        seen[mid] = st
        deps = m.get("dependencies") or {}
        changed = [f"{k}:{v}→{env[k]}" for k, v in deps.items()
                   if k in env and str(env[k]) != str(v)]
        if changed:
            props.append({
                "kind": "LIFECYCLE_PROPOSAL", "model_id": mid,
                "lifecycle_status": "stale",
                "reason": "declared dependencies changed: " + "; ".join(changed),
                "note": "only model-declared deps trigger stale — "
                        "unrelated revs and time alone do not"})
    return props


def rejected_families(proposals_dir: Path) -> set:
    out = set()
    if proposals_dir.is_dir():
        for f in proposals_dir.glob("*.json"):
            try:
                p = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if p.get("status") == "rejected":
                cand = (p.get("payload") or {}).get("candidate") or {}
                fp = cand.get("family_fingerprint")
                if fp:
                    out.add(fp)
                elif cand.get("proposition"):
                    out.add("legacy-" + hashlib.sha256(
                        cand["proposition"].encode()).hexdigest()[:12])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--canonical", required=True)
    ap.add_argument("--closed-minutes", type=int, default=30)
    ap.add_argument("--abstractor", default="none",
                    help="none|template|file:<path to LLM abstraction JSON>")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    canon = Path(args.canonical)
    existing = ((_load_yaml(canon / "current.yaml").get("world_model") or {})
                .get("models") or {})
    packets = build_packets(Path(args.state), args.closed_minutes, existing)
    abstractor = load_abstractor(args.abstractor)
    rej = rejected_families(canon / "proposals")

    accepted, abstained, rejected = [], [], []
    seen_fam = set()
    for pkt in packets:
        out = abstractor(pkt)
        if out.get("abstain"):
            abstained.append({"packet": pkt["packet_id"],
                              "reason": out.get("abstain_reason", "abstain")})
            continue
        ok, reasons = validate_abstraction(out, pkt)
        if not ok:
            rejected.append({"packet": pkt["packet_id"], "reasons": reasons})
            continue
        cand = assemble_candidate(out, pkt)
        fp = cand["family_fingerprint"]
        if fp in rej:
            rejected.append({"packet": pkt["packet_id"],
                             "reasons": ["family previously rejected"]})
            continue
        if fp in seen_fam:
            rejected.append({"packet": pkt["packet_id"],
                             "reasons": ["duplicate family in batch (fixture D)"]})
            continue
        seen_fam.add(fp)
        accepted.append(cand)

    life = lifecycle_scan(canon)
    problems, gated = build_problems(canon, packets)
    written = []
    if not args.dry_run:
        pdir = canon / "proposals"
        pdir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d")
        probdir = canon / "problems"
        probdir.mkdir(exist_ok=True)
        for pr in problems:
            (probdir / f"{pr['problem_id']}.json").write_text(
                json.dumps(pr, ensure_ascii=False, indent=2), encoding="utf-8")
        for c in accepted:
            p = pdir / f"{stamp}-model-proposal-{c['candidate_id']}.json"
            p.write_text(json.dumps({
                "schema_version": "1.2", "kind": "MODEL_PROPOSAL",
                "timestamp": utcnow(), "status": "proposed",
                "update_class": "world_model",
                "classification": c["access"],
                "payload": {"candidate": c}}, ensure_ascii=False, indent=2),
                encoding="utf-8")
            written.append(p.name)
        for i, lp in enumerate(life):
            p = pdir / f"{stamp}-lifecycle-{i:03d}.json"
            p.write_text(json.dumps({
                "schema_version": "1.2", "kind": lp["kind"],
                "timestamp": utcnow(), "status": "proposed",
                "update_class": "world_model",
                "classification": {"level": "PRIVATE",
                                   "basis": ["derived_from_private_canonical"]},
                "payload": lp}, ensure_ascii=False, indent=2), encoding="utf-8")
            written.append(p.name)

    print(json.dumps({
        "packets": len(packets), "candidates": len(accepted),
        "abstained": abstained, "validator_rejected": rejected,
        "rejected_families_known": len(rej),
        "problems_emitted": len(problems), "problems_gated": gated,
        "lifecycle_proposals": len(life), "written": written,
        "canonical_write": "NONE — proposals only",
        "distiller_version": DISTILLER_VERSION}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
