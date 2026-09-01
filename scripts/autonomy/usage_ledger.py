#!/usr/bin/env python3
"""usage_ledger.py — Personal AI Usage Ledger（canonical schema v1，JSONL append-only）。

- 位置：~/.personal-ai/ledger/usage.jsonl（PERSONAL_AI_LEDGER 可覆盖；设备 durable runtime，
  随既有 durability 备份；不进入 git canonical）。
- 记录：task/project/campaign/harness/session/model/provider/calls/input/cached/output/
  cost/artifacts/progress/event_context（schema: registry/usage-ledger-schema.yaml）。
- 检测：COST_PER_PROGRESS、runaway session（对照 execution-profiles.yaml 预算）。
- 用法见 --help；供各 harness adapter / checkpoint.py / 治理脚本写入与查询。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REG = ROOT / "registry"
SCHEMA_VERSION = 1

RECORD_KINDS = {"usage", "progress", "checkpoint", "budget_stop",
                "circuit_break", "completion", "exemption"}
REQUIRED = ("kind", "task_id", "project_id")


def ledger_path() -> Path:
    p = Path(os.environ.get("PERSONAL_AI_LEDGER",
                            Path.home() / ".personal-ai" / "ledger" / "usage.jsonl"))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _dflt(rec: dict) -> dict:
    rec.setdefault("schema_version", SCHEMA_VERSION)
    rec.setdefault("ts", datetime.now(timezone.utc).isoformat())
    for key, zero in (("calls_delta", 0), ("input_tokens", 0),
                      ("cached_input_tokens", 0), ("output_tokens", 0),
                      ("progress_events", 0)):
        rec.setdefault(key, zero)
    for key in ("campaign_id", "harness", "session_id", "execution_profile",
                "model", "provider", "cost_usd_est", "artifacts_produced",
                "flags", "event_context"):
        rec.setdefault(key, None if key in ("campaign_id", "harness", "session_id",
                                            "execution_profile", "model", "provider",
                                            "cost_usd_est") else ([] if key in
                                                                  ("artifacts_produced", "flags") else None))
    return rec


def validate(rec: dict) -> list[str]:
    errors = []
    if rec.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version != 1")
    if rec.get("kind") not in RECORD_KINDS:
        errors.append(f"kind not in {sorted(RECORD_KINDS)}")
    for f in REQUIRED:
        if not rec.get(f):
            errors.append(f"missing required {f}")
    for num in ("calls_delta", "input_tokens", "cached_input_tokens",
                "output_tokens", "progress_events"):
        v = rec.get(num)
        if v is not None and (not isinstance(v, (int, float)) or v < 0):
            errors.append(f"bad {num}={v!r}")
    return errors


def append_record(rec: dict, path: Path | None = None) -> Path:
    rec = _dflt(rec)
    errors = validate(rec)
    if errors:
        raise ValueError(" ; ".join(errors))
    path = path or ledger_path()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


def read_records(path: Path | None = None) -> list[dict]:
    path = path or ledger_path()
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def load_profile_budgets() -> dict:
    import yaml
    eps = yaml.safe_load((REG / "execution-profiles.yaml").read_text(encoding="utf-8-sig"))
    return {name: p.get("budgets", {}).get("task", {}) for name, p in eps.get("profiles", {}).items()}


def summarize_task(records: list[dict], task_id: str) -> dict:
    rows = [r for r in records if r.get("task_id") == task_id]
    out = {"task_id": task_id, "records": len(rows),
           "provider_calls": sum(r.get("calls_delta") or 0 for r in rows),
           "input_tokens": sum(r.get("input_tokens") or 0 for r in rows),
           "cached_input_tokens": sum(r.get("cached_input_tokens") or 0 for r in rows),
           "output_tokens": sum(r.get("output_tokens") or 0 for r in rows),
           "cost_usd_est": sum(r.get("cost_usd_est") or 0 for r in rows),
           "progress_events": sum(r.get("progress_events") or 0 for r in rows),
           "last_stop": next((r.get("ts") for r in reversed(rows)
                              if r.get("kind") in ("budget_stop", "circuit_break", "completion")), None)}
    fl = [f for r in rows for f in (r.get("flags") or [])]
    out["flags"] = sorted(set(fl))
    return out


def cost_per_progress(task: dict) -> float | None:
    if task["progress_events"] <= 0:
        return None
    return task["cost_usd_est"] / task["progress_events"]


def runaway_check(task: dict, budgets: dict) -> list[str]:
    profile_task = budgets.get("LONG_RUNNING_CAMPAIGN", {})
    hits = []
    if task["provider_calls"] > (profile_task.get("provider_calls") or 1 << 60):
        hits.append("provider_calls_exceeded")
    if task["cost_usd_est"] > (profile_task.get("cost_usd") or 1 << 60):
        hits.append("cost_exceeded")
    total = task["input_tokens"] + task["cached_input_tokens"]
    if total > 0 and task["cached_input_tokens"] / total > 0.85 and "compacted" not in task["flags"]:
        hits.append("cached_ratio_high_no_compact")
    return hits


def cmd_append(args) -> int:
    rec = {
        "kind": args.kind, "task_id": args.task, "project_id": args.project,
        "campaign_id": args.campaign, "harness": args.harness,
        "session_id": args.session, "execution_profile": args.profile,
        "model": args.model, "provider": args.provider,
        "calls_delta": args.calls, "input_tokens": args.input,
        "cached_input_tokens": args.cached, "output_tokens": args.output,
        "cost_usd_est": args.cost,
        "artifacts_produced": [a for a in (args.artifacts or "").split(",") if a],
        "progress_events": args.progress,
        "flags": [f for f in (args.flags or "").split(",") if f],
        "event_context": json.loads(args.context) if args.context else None,
    }
    try:
        path = append_record(rec)
    except ValueError as exc:
        print(f"INVALID: {exc}")
        return 1
    print(f"appended -> {path}")
    return 0


def cmd_query(args) -> int:
    records = read_records()
    rows = [r for r in records
            if (not args.task or r.get("task_id") == args.task)
            and (not args.project or r.get("project_id") == args.project)]
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def cmd_cost_per_progress(args) -> int:
    records = read_records()
    task = summarize_task(records, args.task)
    cpp = cost_per_progress(task)
    print(json.dumps({**task, "cost_per_progress": cpp}, ensure_ascii=False, indent=2))
    return 0


def cmd_runaway(args) -> int:
    records = read_records()
    budgets = load_profile_budgets()
    tasks = {}
    for r in records:
        tasks.setdefault(r.get("task_id"), []).append(r)
    findings = []
    for tid, rows in tasks.items():
        task = summarize_task(rows, tid)
        hits = runaway_check(task, budgets)
        if hits:
            findings.append({"task_id": tid, "runaway": hits, "summary": task})
    if not findings:
        print("NO RUNAWAY")
        return 0
    print(json.dumps(findings, ensure_ascii=False, indent=2))
    return 1


def main() -> int:
    p = argparse.ArgumentParser(prog="usage_ledger", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    pa = sub.add_parser("append")
    pa.add_argument("--kind", required=True, choices=sorted(RECORD_KINDS))
    pa.add_argument("--task", required=True)
    pa.add_argument("--project", required=True)
    pa.add_argument("--campaign")
    pa.add_argument("--harness")
    pa.add_argument("--session")
    pa.add_argument("--profile")
    pa.add_argument("--model")
    pa.add_argument("--provider")
    pa.add_argument("--calls", type=int, default=0)
    pa.add_argument("--input", type=int, default=0)
    pa.add_argument("--cached", type=int, default=0)
    pa.add_argument("--output", type=int, default=0)
    pa.add_argument("--cost", type=float, default=None)
    pa.add_argument("--artifacts")
    pa.add_argument("--progress", type=int, default=0)
    pa.add_argument("--flags")
    pa.add_argument("--context")
    pa.set_defaults(func=cmd_append)
    pq = sub.add_parser("query")
    pq.add_argument("--task")
    pq.add_argument("--project")
    pq.set_defaults(func=cmd_query)
    pcpp = sub.add_parser("cost-per-progress")
    pcpp.add_argument("--task", required=True)
    pcpp.set_defaults(func=cmd_cost_per_progress)
    pr = sub.add_parser("runaway")
    pr.set_defaults(func=cmd_runaway)
    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())