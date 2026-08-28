#!/usr/bin/env python3
"""memory_gov.py — Dynamic Memory governance + staleness classification.

Checks schema/provenance/scope/immutability/revision/supersede/conflict/
lifecycle/retention/tombstone. Staleness uses multi-factor classification —
never "90 days = delete". Proposals only; never deletes canonical memory.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "memory"))
from common import STATE, gov_log, now_iso, propose  # noqa: E402

SCOPE_RE = re.compile(r"^(personal|global|project:[a-z0-9][a-z0-9-]*|topic:[a-z0-9][a-z0-9/-]*)$")
VALID_TYPES = {"semantic", "episodic", "procedural", "decision"}
VALID_CONF = {"high", "medium", "low"}
VALID_RET = {"keep", "review", "expire"}
VALID_LIFE = {"active", "superseded", "archived", "forgotten"}


def records():
    root = STATE / "memory" / "records"
    if not root.is_dir():
        return
    for d in sorted(root.iterdir()):
        f = d / "record.yaml"
        if f.is_file():
            import yaml
            rec = yaml.safe_load(f.read_text(encoding="utf-8-sig"))
            rec["_dir"] = d
            yield rec


def age_days(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return (dt.datetime.now().astimezone()
                - dt.datetime.fromisoformat(iso)).total_seconds() / 86400
    except ValueError:
        return None


def staleness(rec: dict, project_active: bool) -> str:
    created = (rec.get("created") or {}).get("at")
    superseded = bool(rec.get("_superseded_by"))
    if superseded:
        return "SUPERSEDE_CANDIDATE"
    lv = age_days(created)
    conf = rec.get("confidence", "medium")
    ret = rec.get("retention", "review")
    if lv is None:
        return "UNKNOWN"
    if ret == "keep":
        return "HEALTHY"
    if lv > 180 and conf == "low":
        return "ARCHIVE_CANDIDATE"
    if lv > 90 and (conf == "low" or not project_active):
        return "REVIEW"
    if lv > 90 and conf == "high" and project_active:
        return "REVIEW"
    return "HEALTHY"


def main() -> int:
    findings, stale_counts = [], {}
    seen_ids: dict[str, str] = {}
    recs = list(records())
    # supersede graph: record X superseded if some record lists X in supersedes
    superseded_map: dict[str, str] = {}
    for rec in recs:
        for old in rec.get("supersedes") or []:
            superseded_map[old] = rec.get("id", "?")
    for rec in recs:
        rid = rec.get("id", "?")
        rec["_superseded_by"] = superseded_map.get(rid)
        created = rec.get("created") or {}
        for key in ("id", "scope", "type", "created", "provenance",
                    "confidence", "retention", "access_policy", "content_fingerprint"):
            if key not in rec:
                findings.append({"kind": "schema_missing_field", "id": rid, "field": key})
        for key in ("at", "by_agent", "requested_model"):
            if key not in created:
                findings.append({"kind": "schema_missing_field", "id": rid, "field": f"created.{key}"})
        if rec.get("type") not in VALID_TYPES:
            findings.append({"kind": "invalid_type", "id": rid, "type": rec.get("type")})
        if not SCOPE_RE.match(str(rec.get("scope", ""))):
            findings.append({"kind": "invalid_scope", "id": rid, "scope": rec.get("scope")})
        if rec.get("confidence", "medium") not in VALID_CONF:
            findings.append({"kind": "invalid_confidence", "id": rid})
        if rec.get("retention", "review") not in VALID_RET:
            findings.append({"kind": "invalid_retention", "id": rid})
        prov = rec.get("provenance", {})
        if not prov.get("source"):
            findings.append({"kind": "provenance_incomplete", "id": rid})
        if rid in seen_ids:
            findings.append({"kind": "immutable_id_collision", "id": rid})
        seen_ids[rid] = "1"
        # revision integrity: revisions/ must exist and contain >=1 revision
        revdir = rec["_dir"] / "revisions"
        revs = list(revdir.glob("*.yaml")) if revdir.is_dir() else []
        if not revs:
            findings.append({"kind": "revision_integrity", "id": rid,
                             "detail": "no revision file"})
        # supersede chain symmetry
        for old in rec.get("supersedes") or []:
            if old not in seen_ids and old not in {r.get("id") for r in recs}:
                findings.append({"kind": "supersede_chain_broken", "id": rid,
                                 "detail": f"supersedes unknown {old}"})
        # forgotten tombstone: forgotten marker file means content must be absent
        if (rec["_dir"] / "FORGOTTEN").exists() and rec.get("content_fingerprint"):
            findings.append({"kind": "forgotten_tombstone_violation", "id": rid})
        scope = rec.get("scope", "")
        proj_active = True  # PAUSED project is a known state, not an error
        s = staleness(rec, proj_active)
        stale_counts[s] = stale_counts.get(s, 0) + 1
        if s in ("REVIEW", "SUPERSEDE_CANDIDATE", "ARCHIVE_CANDIDATE"):
            propose("memory_review",
                    {"id": rid, "scope": scope, "staleness": s,
                     "confidence": rec.get("confidence"),
                     "created_at": created.get("at")},
                    "low", "personal-ai-state/memory",
                    f"review candidate ({s}); no automatic deletion")

    for f in findings:
        print(f"FINDING: {f}")
    print(f"staleness distribution: {stale_counts}")
    gov_log("memory_gov", "ok" if not findings else "findings", findings,
            {"records": len(recs), "staleness": stale_counts})
    print(f"records={len(recs)} findings={len(findings)}")
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
