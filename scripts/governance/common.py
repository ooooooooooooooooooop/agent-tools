#!/usr/bin/env python3
"""governance/common.py — shared helpers: canonical IO, gov run log, proposals.

Boundary: governance observes/detects/proposes. It never mutates canonical
registry files, never calls paid model APIs for checks, never auto-applies.
Mutation policy: registry/governance-policy.yaml (frozen).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import uuid
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
HOME = Path.home()
REG = REPO / "registry"
STATE = Path(os.environ.get("PERSONAL_AI_STATE", Path.home() / "personal-ai-state"))
INBOX = Path.home() / ".dsh" / ".evolution-inbox" / "proposals"

SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|xox[bap]-|AKIA[0-9A-Z]{16}"
    r"|AIza[0-9A-Za-z_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")


def load_yaml(p: Path):
    return yaml.safe_load(p.read_text(encoding="utf-8-sig"))


def load_canonical() -> dict:
    return {
        "providers": load_yaml(REG / "providers.yaml"),
        "models": load_yaml(REG / "models.yaml"),
        "policy": load_yaml(REG / "routing-policy.yaml"),
        "gateways": load_yaml(REG / "gateways.yaml"),
        "capabilities": load_yaml(REG / "capabilities.yaml"),
        "gov_policy": load_yaml(REG / "governance-policy.yaml"),
    }


def private_gateways() -> dict:
    p = STATE / "registry" / "gateways.yaml"
    return load_yaml(p) if p.is_file() else {"gateways": {}}


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def gov_log(module: str, status: str, findings: list[dict] | int, extra: dict | None = None):
    """Structured governance run log (append-only jsonl, secret-scanned)."""
    row = {"ts": now_iso(), "module": module, "status": status,
           "findings": findings if isinstance(findings, int) else len(findings),
           **(extra or {})}
    blob = json.dumps(row, ensure_ascii=False)
    if SECRET_RE.search(blob):
        raise SystemExit("refuse to log secret-shaped material")
    try:
        root = Path(load_yaml(STATE / "sync" / "this-device.yaml")["backup_root"])
        led = root / "ledger"
        led.mkdir(parents=True, exist_ok=True)
        with (led / "governance.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(blob + "\n")
    except (PermissionError, OSError) as exc:
        # Ledger logging limitation (e.g. sandbox read-only outside workspace)
        # Observability limitation, not a governance check failure
        row["_logging_limitation"] = "OBSERVABILITY_EVIDENCE_LIMITATION"
        row["_logging_error"] = str(exc)
    return row


def propose(ptype: str, evidence: dict, severity: str, affected_ssot: str,
            action: str, safe_auto: bool = False) -> Path:
    """Write an auditable proposal into the evolution inbox. Never auto-applies.
    Dedupes: an open proposal with same type + same subject stays single."""
    INBOX.mkdir(parents=True, exist_ok=True)
    subject = evidence.get("model") or evidence.get("id") or evidence.get("item") \
        or evidence.get("name") or json.dumps(evidence, sort_keys=True)[:80]
    for p in INBOX.glob("gov-*.json"):
        try:
            old = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if old.get("status") == "open" and old.get("type") == ptype:
            oe = old.get("evidence", {})
            old_subject = oe.get("model") or oe.get("id") or oe.get("item") or oe.get("name")
            if old_subject == subject:
                return p  # dedup: same open proposal already exists
    pid = "gov-" + dt.datetime.now().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:8]
    body = {"id": pid, "type": ptype, "evidence": evidence, "severity": severity,
            "affected_ssot": affected_ssot, "recommended_action": action,
            "safe_to_auto_apply": safe_auto, "created_at": now_iso(),
            "status": "open"}
    blob = json.dumps(body, ensure_ascii=False, indent=1)
    if SECRET_RE.search(blob):
        body["evidence"] = {"redacted": True, "note": "evidence contained secret-shaped material"}
        blob = json.dumps(body, ensure_ascii=False, indent=1)
    p = INBOX / f"{pid}.json"
    p.write_text(blob, encoding="utf-8")
    return p
