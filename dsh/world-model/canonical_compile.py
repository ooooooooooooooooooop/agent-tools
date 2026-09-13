#!/usr/bin/env python3
"""canonical_compile.py — compile canonical L2/L3 state into runtime artifacts.

Outputs (written into the canonical dir):
  briefing.md          GENERATED 7-section onboarding brief (V0.3.1 §7)
  runtime-state.json   projection the plugin reads: identity, lineage head,
                       active body/lease, normative authorities, alerts

Briefing contract:
  - fixed sections 0-6 (Identity / Unresolved First / Reliability Alerts /
    Current Best Models / Corrections & Superseded / Task-Relevant Values /
    Retrieval Pointers)
  - every Current Best Model carries proposition + confidence + scope +
    support refs + strongest counterevidence + falsifier/revision trigger
    (a prior plus the keys to overthrow it — not a conclusion dump)
  - target 4-6 KiB UTF-8, hard cap 8 KiB; overflow trimmed by priority:
    unresolved > alerts > task-relevant models > corrections > values > settled
  - OFF/CORE/FULL all receive the same brief (mode = ritual depth, not on/off)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

TARGET_MAX = 6 * 1024
HARD_CAP = 8 * 1024


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load(p: Path) -> dict:
    if not p.exists():
        return {}
    d = yaml.safe_load(p.read_text(encoding="utf-8"))
    return d if isinstance(d, dict) else {}


def fmt_model(mid: str, m: dict) -> str:
    if not isinstance(m, dict):
        return f"- {mid}: {m}"
    prop = m.get("proposition", "")
    conf = m.get("confidence", "")
    status = m.get("status", "")
    scope = m.get("coverage") or m.get("boundary") or m.get("scope") or ""
    sup = m.get("evidence_refs") or []
    if isinstance(sup, list):
        sup = "; ".join(str(x) for x in sup[:3])
    fals = m.get("falsifier") or ""
    lines = [f"- **{mid}** [{conf or 'n/a'}] {prop}"]
    if status:
        lines.append(f"  status: {status if isinstance(status, str) else json.dumps(status, ensure_ascii=False)}")
    if scope:
        lines.append(f"  scope: {scope}")
    if sup:
        lines.append(f"  support: {sup}")
    counter = m.get("counterevidence") or m.get("counter") or ""
    lines.append(f"  counter: {counter if counter else 'none recorded'}")
    lines.append(f"  revise_if: {fals if fals else 'not specified — treat as weakly held'}")
    return "\n".join(lines)


def compile_briefing(canon: Path) -> str:
    cur = load(canon / "current.yaml")
    gov = load(canon / "governance.yaml")
    lin = load(canon / "lineage.yaml")
    src = load(canon / "sources.yaml")
    loops = load(canon / "open-loops.yaml")
    ops = load(canon / "operators.yaml")

    ident = cur.get("identity") or {}
    w = cur.get("world_model") or {}
    v = cur.get("value_model") or {}

    s0 = ["## 0. Identity",
          f"entity: {ident.get('entity_id','?')} | theory {cur.get('theory_version','?')} | "
          f"schema {cur.get('schema_version','?')} | lineage_head {lin.get('lineage_head','?')}",
          "你是 Personal AI 当班的认知劳工。这份简报是 prior + 推翻 prior 的钥匙，不是脚本。"]

    s1 = ["## 1. Unresolved First"]
    open_loops = [l for l in (loops.get("open_loops") or [])
                  if isinstance(l, dict) and l.get("status") == "open"]
    for l in open_loops[:6]:
        s1.append(f"- [{l.get('priority','?')}] {l.get('id','?')}: {str(l.get('description',''))[:180]}")
    for r in (w.get("known_residuals") or [])[:4]:
        s1.append(f"- residual: {str(r)[:160]}")
    if w.get("open_predictions"):
        s1.append(f"- open_predictions: {w['open_predictions']}")
    if len(s1) == 1:
        s1.append("- (none recorded)")

    s2 = ["## 2. Reliability Alerts"]
    for sid, s in (src.get("sources") or {}).items():
        if not isinstance(s, dict):
            continue
        lim = s.get("known_limits")
        er = s.get("epistemic_reliability") or {}
        weak = [d for d, r in er.items()
                if isinstance(r, dict) and r.get("level") in ("low", "unknown", "provisional")]
        parts = []
        if weak:
            parts.append("weak domains: " + ", ".join(weak[:4]))
        if lim:
            parts.append(str(lim)[:140])
        if parts:
            s2.append(f"- {sid}: {'; '.join(parts)}")
    if len(s2) == 1:
        s2.append("- (no degraded sources recorded)")

    s3 = ["## 3. Current Best Models"]
    models = w.get("models") or {}
    comp = w.get("competing_models") or {}
    for mid, m in list(models.items())[:6]:
        s3.append(fmt_model(mid, m))
    for mid, m in list(comp.items())[:4]:
        if isinstance(m, dict) and "SUPERSEDED" in str(m.get("status", "")):
            continue
        if mid != "note":
            s3.append(fmt_model(mid, m))

    s4 = ["## 4. Corrections & Superseded"]
    for mid, m in list(comp.items()):
        if isinstance(m, dict) and "SUPERSEDED" in str(m.get("status", "")):
            s4.append(f"- {mid}: SUPERSEDED — {str(m.get('status'))[:160]}")
    for sid, s in (src.get("sources") or {}).items():
        for c in (s.get("correction_history") or [])[:4]:
            s4.append(f"- {sid} correction: {str(c)[:160]}")
    if len(s4) == 1:
        s4.append("- (none recorded)")

    s5 = ["## 5. Task-Relevant Values / Constraints"]
    cv = (v.get("current_values") or {})
    for k, val in list(cv.items())[:5]:
        s5.append(f"- {k}: {str(val)[:140]}")
    na = (gov.get("normative_authorities") or {}).get("user", {}).get("scopes") or {}
    if na:
        s5.append("- user normative scopes: " + ", ".join(f"{k}={v2}" for k, v2 in na.items()))

    s6 = ["## 6. Retrieval Pointers",
          "- full state: world_model(op:\"status\"); canonical: current.yaml/governance.yaml/lineage.yaml/sources.yaml",
          "- proposals 治理：canonical 只能经 proposal→评审修改；runtime 直接写会被视为越权",
          "- world_model ops: activate/model/predict/observe/evaluate/update/probe/meta/value/input/persist/status"]

    # priority trim: sections in drop order (keep 0-2 longest)
    sections = [s0, s1, s2, s3, s4, s5, s6]
    hdr = "# PERSONAL AI BRIEF (GENERATED — do not hand-edit)\n" \
          "# classification: INTERNAL | basis: compiled_from_canonical\n\n"
    text = hdr + "\n\n".join("\n".join(s) for s in sections) + "\n"
    b = text.encode("utf-8")
    if len(b) > HARD_CAP:
        # drop lowest-priority whole entries until under cap; never exceed hard cap
        order = [5, 3, 4, 2, 1]  # trim values, models tail, corrections, alerts tail, loops tail
        for idx in order:
            while len(sections[idx]) > 2 and len(text.encode("utf-8")) > TARGET_MAX:
                sections[idx].pop(-1)
                text = hdr + "\n\n".join("\n".join(s) for s in sections) + "\n"
        if len(text.encode("utf-8")) > HARD_CAP:
            text = text.encode("utf-8")[:HARD_CAP - 40].decode("utf-8", "ignore") + \
                "\n…(hard-capped at 8KiB)\n"
    return text


def compile_runtime_state(canon: Path) -> dict:
    cur = load(canon / "current.yaml")
    gov = load(canon / "governance.yaml")
    lin = load(canon / "lineage.yaml")
    src = load(canon / "sources.yaml")
    return {
        "compiled_at": utcnow(),
        "schema_version": cur.get("schema_version"),
        "theory_version": cur.get("theory_version"),
        "identity": cur.get("identity") or {},
        "entity_id": lin.get("entity_id"),
        "lineage_head": lin.get("lineage_head"),
        "continuity_epoch": lin.get("continuity_epoch"),
        "active_body": lin.get("active_body") or {},
        "parent_entity": lin.get("parent_entity"),
        "normative_authorities": gov.get("normative_authorities") or {},
        "u0_root_authorities": (gov.get("u0_constitutional") or {}).get("root_authorities") or {},
        "update_classes": (gov.get("u1_evolvable") or {}).get("update_classes") or {},
        "sources": {k: {kk: s.get(kk) for kk in ("source_type", "roles", "known_limits")}
                    for k, s in (src.get("sources") or {}).items() if isinstance(s, dict)},
        "open_predictions": (cur.get("world_model") or {}).get("open_predictions") or [],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--canonical", required=True)
    ap.add_argument("--check", action="store_true", help="report freshness, write nothing")
    args = ap.parse_args()
    canon = Path(args.canonical)

    briefing = compile_briefing(canon)
    state = compile_runtime_state(canon)
    n = len(briefing.encode("utf-8"))
    report = {"briefing_bytes": n, "target": "4-6KiB", "hard_cap": "8KiB",
              "over_target": n > TARGET_MAX, "over_cap": n > HARD_CAP,
              "entity": state.get("entity_id"), "epoch": state.get("continuity_epoch")}
    if args.check:
        bp = canon / "briefing.md"
        rp = canon / "runtime-state.json"
        yamls = [p for p in canon.glob("*.yaml")]
        newest_yaml = max((p.stat().st_mtime for p in yamls), default=0)
        report["stale"] = {
            "briefing.md": not bp.exists() or bp.stat().st_mtime < newest_yaml,
            "runtime-state.json": not rp.exists() or rp.stat().st_mtime < newest_yaml,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    (canon / "briefing.md").write_text(briefing, encoding="utf-8")
    (canon / "runtime-state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
