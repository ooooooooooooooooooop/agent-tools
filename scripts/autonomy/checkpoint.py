#!/usr/bin/env python3
"""checkpoint.py — Personal AI durable Checkpoint tool（harness-neutral）。

- Checkpoint 属于 Personal AI durable state：personal-ai-state/checkpoints/<task_id>.json
  （PERSONAL_AI_STATE 可覆盖），永不只存在于某 Harness session history。
- Schema：registry/checkpoint-schema.yaml（v1；实例为 JSON）。
- resume 契约：读 checkpoint（next_executable_action / current_state / budget_remaining），
  严禁回放原 conversation；任务累计预算跨 resume 不重置。
- 写入即追加一条 Usage Ledger kind=checkpoint 记录（--no-ledger 可关闭）。

用法：
  checkpoint.py new   --task <id> --project <id> --objective ... --profile <name> --harness <name> [--campaign ...]
  checkpoint.py save  --task <id> [--from-json <file>|--actions ... --next ... --stop-reason ... ...]
  checkpoint.py load <task_id>
  checkpoint.py resume <task_id>
  checkpoint.py list
  checkpoint.py validate <task_id>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REG = ROOT / "registry"
SCHEMA_VERSION = 1

STOP_REASONS = {"completed", "checkpoint", "budget_limit", "loop_breaker",
                "error", "human_interrupt", "local_shutdown"}


def load_yaml(path: Path) -> dict:
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8-sig"))


def state_root() -> Path:
    return Path(os.environ.get("PERSONAL_AI_STATE", Path.home() / "personal-ai-state"))


def checkpoints_dir() -> Path:
    d = state_root() / "checkpoints"
    d.mkdir(parents=True, exist_ok=True)
    return d


def protocol_hash() -> str:
    schema_raw = (REG / "checkpoint-schema.yaml").read_bytes()
    gov = load_yaml(REG / "autonomous-execution-governance.yaml")
    digest_input = schema_raw + f"\nschema_version={gov.get('schema_version', 1)}\n".encode("utf-8")
    return hashlib.sha256(digest_input).hexdigest()


def profile_budget(profile: str) -> dict:
    eps = load_yaml(REG / "execution-profiles.yaml")
    p = eps.get("profiles", {}).get(profile)
    if not p:
        raise ValueError(f"unknown execution_profile: {profile}")
    b = p.get("budgets", {})
    return {"task": b.get("task", {}), "session": b.get("session", {})}


def _remaining(consumed: dict, limits: dict) -> dict:
    out = {}
    for k, cap in limits.items():
        used = consumed.get(k) or 0
        out[k] = None if cap is None else max(0.0, float(cap) - float(used))
    return out


def _ledger_append(kind: str, task_id: str, project_id: str, **kw) -> None:
    sys.path.insert(0, str(ROOT / "scripts" / "autonomy"))
    try:
        import usage_ledger
        usage_ledger.append_record({
            "kind": kind, "task_id": task_id, "project_id": project_id, **kw,
        })
    except Exception:  # noqa: BLE001 — ledger failure must not break checkpointing
        pass


def ckpt_path(task_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in task_id)
    return checkpoints_dir() / f"{safe}.json"


def load_checkpoint(task_id: str) -> dict:
    path = ckpt_path(task_id)
    if not path.is_file():
        raise FileNotFoundError(f"no checkpoint for task {task_id} at {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_checkpoint(data: dict) -> Path:
    path = ckpt_path(str(data["task_id"]))
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def validate_checkpoint(data: dict) -> list[str]:
    errors = []
    for f in ("schema_version", "protocol_hash", "task_id", "project_id", "objective",
              "harness", "execution_profile", "completed_actions", "current_state",
              "durable_artifacts", "evidence", "unresolved_blockers",
              "next_executable_action", "model_usage", "budget_consumed",
              "budget_remaining", "stop_reason", "resume_count", "timestamp"):
        if f not in data:
            errors.append(f"missing field: {f}")
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version != {SCHEMA_VERSION}")
    if data.get("protocol_hash") != protocol_hash():
        errors.append("protocol_hash mismatch (schema/gov policy changed?)")
    if data.get("stop_reason") not in STOP_REASONS:
        errors.append(f"stop_reason not in {sorted(STOP_REASONS)}")
    return errors


def cmd_new(args) -> int:
    task_id, project_id = args.task, args.project
    if ckpt_path(task_id).is_file():
        print(f"EXISTS: {ckpt_path(task_id)} (use save to continue)")
        return 2
    profile = args.profile
    if not profile and args.auto_admit:
        # Automatic Profile Admission：用户无感知；classifier 是 Personal AI 资产
        sys.path.insert(0, str(ROOT / "scripts" / "autonomy"))
        import profile_admission
        decision = profile_admission.classify(args.objective)
        profile = decision["profile"]
        profile_admission.write_admission(task_id, project_id, decision, args.harness)
        print(f"[auto-admit] {task_id} → {profile} "
              f"(confidence={decision['confidence']}, bulk_workload={decision['bulk_workload']}) "
              f"reasons={decision['reasons']}")
    if not profile:
        print("INVALID: --profile 或 --auto-admit 必须提供其一")
        return 1
    try:
        limits = profile_budget(profile)
    except ValueError as exc:
        print(f"INVALID: {exc}")
        return 1
    data = {
        "schema_version": SCHEMA_VERSION,
        "protocol_hash": protocol_hash(),
        "task_id": task_id,
        "project_id": project_id,
        "campaign_id": args.campaign,
        "objective": args.objective,
        "harness": args.harness,
        "execution_profile": profile,
        "completed_actions": [],
        "current_state": {},
        "durable_artifacts": [],
        "evidence": [],
        "unresolved_blockers": [],
        "next_executable_action": args.next_action or "re-read objective and start",
        "model_usage": {"calls_by_model": {}, "input_tokens": 0,
                        "cached_input_tokens": 0, "output_tokens": 0},
        "budget_consumed": {"provider_calls": 0, "agent_turns": 0,
                            "input_tokens": 0, "cached_input_tokens": 0,
                            "output_tokens": 0, "cost_usd": 0.0, "runtime_min": 0},
        "budget_remaining": _remaining(
            {"provider_calls": 0, "agent_turns": 0, "input_tokens": 0,
             "cached_input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
             "runtime_min": 0},
            {**limits["task"], "agent_turns": ((limits["session"] or {}).get("agent_turns")
                                               or 0)}),
        "stop_reason": "checkpoint",
        "resume_count": 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    path = write_checkpoint(data)
    if not args.no_ledger:
        _ledger_append("checkpoint", task_id, project_id, harness=args.harness,
                       execution_profile=profile)
    print(f"checkpoint created -> {path}")
    return 0


def cmd_save(args) -> int:
    task_id = args.task
    try:
        data = load_checkpoint(task_id)
    except FileNotFoundError as exc:
        print(f"INVALID: {exc} (run `new` first)")
        return 1
    if args.from_json:
        merge = json.loads(Path(args.from_json).read_text(encoding="utf-8-sig"))
    else:
        merge = {}
    if args.actions:
        merge["completed_actions"] = [a for a in args.actions.split("|||") if a]
    if args.next:
        merge["next_executable_action"] = args.next
    if args.stop_reason:
        merge["stop_reason"] = args.stop_reason
    if args.state_json:
        merge["current_state"] = json.loads(args.state_json)
    if args.usage_json:
        merge["model_usage"] = {**data.get("model_usage", {}), **json.loads(args.usage_json)}
    for field, cli_attr in (("completed_actions", "actions"), ("durable_artifacts", "artifacts"),
                            ("evidence", "evidence"), ("unresolved_blockers", "blockers")):
        vals = [v for v in (getattr(args, cli_attr) or "").split(",") if v]
        if vals:
            existing = list(data.get(field, []))
            merge[field] = existing + [v for v in vals if v not in existing]
    data.update({k: v for k, v in merge.items() if v is not None})

    # budget accounting from usage_json (optional)
    usage = data.get("model_usage", {})
    consumed = data.get("budget_consumed", {})
    consumed.update({
        "provider_calls": consumed.get("provider_calls", 0),
        "agent_turns": consumed.get("agent_turns", 0),
        "input_tokens": usage.get("input_tokens", consumed.get("input_tokens", 0)),
        "cached_input_tokens": usage.get("cached_input_tokens", consumed.get("cached_input_tokens", 0)),
        "output_tokens": usage.get("output_tokens", consumed.get("output_tokens", 0)),
        "cost_usd": usage.get("cost_usd", consumed.get("cost_usd", 0.0)),
    })
    try:
        limits = profile_budget(data["execution_profile"])
    except ValueError as exc:
        print(f"INVALID: {exc}")
        return 1
    data["budget_remaining"] = _remaining(
        consumed, {**limits["task"], "agent_turns": ((limits["session"] or {}).get("agent_turns") or 0)})
    data["resume_count"] = data.get("resume_count", 0)
    if args.resume:
        data["resume_count"] += 1
    data["timestamp"] = datetime.now(timezone.utc).isoformat()

    errors = validate_checkpoint(data)
    if errors:
        print("INVALID:")
        for e in errors:
            print(f"  - {e}")
        return 1
    path = write_checkpoint(data)
    if not args.no_ledger:
        _ledger_append("checkpoint", task_id, data.get("project_id", ""),
                       harness=data.get("harness"),
                       execution_profile=data.get("execution_profile"),
                       progress_events=1)
    print(f"checkpoint saved -> {path}")
    return 0


def cmd_load(args) -> int:
    try:
        data = load_checkpoint(args.task)
    except FileNotFoundError as exc:
        print(f"INVALID: {exc}")
        return 1
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def cmd_resume(args) -> int:
    try:
        data = load_checkpoint(args.task)
    except FileNotFoundError as exc:
        print(f"INVALID: {exc}")
        return 1
    errors = validate_checkpoint(data)
    print(json.dumps({
        "task_id": data["task_id"], "resumable": not errors,
        "errors": errors,
        "objective": data["objective"],
        "execution_profile": data["execution_profile"],
        "harness": data["harness"],
        "completed_actions": len(data["completed_actions"]),
        "current_state": data["current_state"],
        "next_executable_action": data["next_executable_action"],
        "unresolved_blockers": data["unresolved_blockers"],
        "budget_consumed": data["budget_consumed"],
        "budget_remaining": data["budget_remaining"],
        "stop_reason": data["stop_reason"],
        "resume_count": data["resume_count"],
        "timestamp": data["timestamp"],
    }, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


def cmd_list(_args) -> int:
    rows = []
    for path in sorted(checkpoints_dir().glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            rows.append({"task_id": data.get("task_id"), "project_id": data.get("project_id"),
                         "execution_profile": data.get("execution_profile"),
                         "stop_reason": data.get("stop_reason"),
                         "resume_count": data.get("resume_count"),
                         "timestamp": data.get("timestamp")})
        except Exception:  # noqa: BLE001
            rows.append({"file": path.name, "error": "unparseable"})
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def cmd_validate(args) -> int:
    try:
        data = load_checkpoint(args.task)
    except FileNotFoundError as exc:
        print(f"INVALID: {exc}")
        return 1
    errors = validate_checkpoint(data)
    if errors:
        print("INVALID:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("VALID")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="checkpoint", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    pn = sub.add_parser("new")
    pn.add_argument("--task", required=True)
    pn.add_argument("--project", required=True)
    pn.add_argument("--campaign")
    pn.add_argument("--objective", required=True)
    pn.add_argument("--harness", required=True, choices=["dsh", "codex", "claude", "gemini", "switchboard"])
    pn.add_argument("--profile")
    pn.add_argument("--auto-admit", action="store_true",
                    help="无 --profile 时自动 profile admission（classifier）")
    pn.add_argument("--next-action")
    pn.add_argument("--no-ledger", action="store_true")
    pn.set_defaults(func=cmd_new)
    ps = sub.add_parser("save")
    ps.add_argument("--task", required=True)
    ps.add_argument("--from-json")
    ps.add_argument("--actions")
    ps.add_argument("--next")
    ps.add_argument("--stop-reason", choices=sorted(STOP_REASONS))
    ps.add_argument("--state-json")
    ps.add_argument("--usage-json")
    ps.add_argument("--artifacts")
    ps.add_argument("--evidence")
    ps.add_argument("--blockers")
    ps.add_argument("--resume", action="store_true")
    ps.add_argument("--no-ledger", action="store_true")
    ps.set_defaults(func=cmd_save)
    for name, fn in (("load", cmd_load), ("validate", cmd_validate), ("resume", cmd_resume)):
        pp = sub.add_parser(name)
        pp.add_argument("task")
        pp.set_defaults(func=fn)
    pl = sub.add_parser("list")
    pl.set_defaults(func=cmd_list)
    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())