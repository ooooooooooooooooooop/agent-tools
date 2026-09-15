#!/usr/bin/env python3
"""u1_accept.py — U1 governance apply path (V0.3.1).

MODEL_PROPOSAL / promoted HYPOTHESIS -> U1 decision -> canonical current.yaml.

Rules:
  - canonical write happens ONLY here (proposals/ledger/distiller never write)
  - every apply writes a byte-exact backup first (rollback-capable)
  - accepted models enter with epistemic_status 'provisional' — real
    Observation promotes/demotes them later, not the proposal itself
  - provenance chain preserved: proposal -> hypothesis/problem/packet/episodes
  - proposal file status updated (its lifecycle), original payload untouched
"""
import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

U1_VERSION = "U1-1.0"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--canonical", required=True)
    ap.add_argument("--proposal", required=True,
                    help="proposal file inside canonical/proposals/")
    ap.add_argument("--decision", required=True,
                    choices=["accept", "provisional", "reject"])
    ap.add_argument("--reason", default="")
    ap.add_argument("--authority", default="u1-review",
                    help="accepting authority ref (must exist pre-change)")
    args = ap.parse_args()

    canon = Path(args.canonical)
    pfile = canon / "proposals" / args.proposal
    prop = json.loads(pfile.read_text(encoding="utf-8"))
    if prop.get("kind") not in ("MODEL_PROPOSAL",):
        raise SystemExit(f"U1 apply supports MODEL_PROPOSAL, got {prop.get('kind')}")
    cand = (prop.get("payload") or {}).get("candidate") or {}
    mid = cand.get("candidate_id")
    if not mid:
        raise SystemExit("proposal has no candidate_id")

    if args.decision == "reject":
        prop["status"] = "rejected"
        prop["decision"] = {"decision": "reject", "reason": args.reason,
                            "authority": args.authority, "ts": utcnow()}
        pfile.write_text(json.dumps(prop, ensure_ascii=False, indent=2),
                         encoding="utf-8")
        print(json.dumps({"decision": "reject", "canonical_write": "NONE"}))
        return 0

    cur_path = canon / "current.yaml"
    cur = yaml.safe_load(cur_path.read_text(encoding="utf-8"))
    # rollback backup — byte-exact, before any mutation
    bdir = canon / "history" / f"pre-u1-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    bdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cur_path, bdir / "current.yaml")

    models = cur.setdefault("world_model", {}).setdefault("models", {})
    status = "provisional" if args.decision in ("accept", "provisional") else "?"
    models[mid] = {
        "proposition": cand.get("proposition"),
        "epistemic_status": status,
        "operational_status": "usable",
        "scope": cand.get("scope"),
        "scope_conditions": cand.get("scope_conditions"),
        "confidence": cand.get("confidence", "low"),
        "confidence_basis": cand.get("confidence_basis"),
        "evidence_refs": (cand.get("supporting") or {}).get("evidence_refs", []),
        "episode_refs": (cand.get("supporting") or {}).get("episode_refs", []),
        "counterevidence": cand.get("counterexamples", []),
        "known_exceptions": cand.get("known_exceptions", []),
        "falsifier": cand.get("falsifier"),
        "promotion_criteria": cand.get("promotion_criteria"),
        "access": cand.get("access") or {"level": "PRIVATE",
                                         "basis": ["taint_inheritance"]},
        "provenance": {
            "proposal": args.proposal,
            "decision": args.decision,
            "authority": args.authority,
            "ts": utcnow(),
            "promoted_from": cand.get("promoted_from"),
        },
        "update_history": f"U1 {args.decision}: {args.reason}"[:400],
    }
    cur_path.write_text(yaml.safe_dump(cur, allow_unicode=True,
                                       sort_keys=False), encoding="utf-8")

    prop["status"] = "accepted" if args.decision == "accept" else "provisional"
    prop["decision"] = {"decision": args.decision, "reason": args.reason,
                        "authority": args.authority, "ts": utcnow(),
                        "applied_model": mid, "rollback": str(bdir)}
    pfile.write_text(json.dumps(prop, ensure_ascii=False, indent=2),
                     encoding="utf-8")

    mlog = canon / "history" / "model-updates.jsonl"
    with mlog.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": utcnow(), "kind": "U1_APPLY", "model_id": mid,
            "decision": args.decision, "proposal": args.proposal,
            "authority": args.authority, "rollback": str(bdir),
            "schema_version": "1.2"}, ensure_ascii=False) + "\n")

    print(json.dumps({"decision": args.decision, "model_id": mid,
                      "epistemic_status": status, "rollback": str(bdir),
                      "u1_version": U1_VERSION}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
