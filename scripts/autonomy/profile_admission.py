#!/usr/bin/env python3
"""profile_admission.py — Personal AI Automatic Execution Profile Admission.

用户只描述任务目标；本脚本（Personal AI 资产，harness 中立）自动完成：
  TASK ADMISSION → PROFILE CLASSIFICATION → HARD POLICY BINDING → (EXECUTION 由 governor)

- 规则数据唯一来源：registry/autonomous-execution-governance.yaml#profile_admission（canonical）。
- 项目禁止复制 classifier；项目只消费 admission 结果。
- 安全：UNKNOWN ≠ UNBOUNDED —— 无法分类的 autonomous task 进入 safe default
  （AUTONOMOUS_STANDARD），绝不进入无约束观察模式。
- 结果 durable：personal-ai-state/checkpoints/<task_id>.admission.json + Usage Ledger
  kind=admission / escalation。

用法：
  profile_admission.py run --task <id> --project <id> --objective "<任务目标>" [--declare '<json>'] [--harness dsh]
  profile_admission.py status --task <id>
  profile_admission.py escalate --task <id> --target <profile> --reason "<evidence 描述>"

调用方：DSH governor 插件（autoAdmit）、checkpoint.py --auto-admit、harness adapter、编排器。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REG = ROOT / "registry"
SAFE_DEFAULT = "AUTONOMOUS_STANDARD"

# 信号模式（提取器；权重与决策规则在 canonical#profile_admission，不在此复制分值）
_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "bulk_repetition": [
        re.compile(r"(\d{1,4})\s*(个|对|组|份|条|批|轮|篇|次|条)"),
        re.compile(r"[×xX]\s*(\d{1,4})"),
        re.compile(r"每[个篇条份]"),
        re.compile(r"(全部|所有)\s*\d"),
        re.compile(r"(批量|batched|parallel)"),
    ],
    "bulk_verb": [
        re.compile(r"\b(judge|judges|generate|generate|transform|extract|compare|comparison"
                   r"|translate|rewrite|summarize|evaluate|score)\b", re.I),
        re.compile(r"(评估|评审|打分|评分|生成|转换|抽取|对比|翻译|改写|汇总)"),
    ],
    "campaign": [
        re.compile(r"(阶段|phase|milestone|里程碑|长期|持续|跨会话|多阶段|多轮|resume|campaign)", re.I),
    ],
    "research": [
        re.compile(r"\b(research|investigate|explore|analysis|audit|review)\b", re.I),
        re.compile(r"(调查|研究|分析|调研|对比实验|审计|报告)"),
    ],
    "reasoning_critical": [
        re.compile(r"\b(architecture|design|adjudicate|decision|trade-off|critical|schema)\b", re.I),
        re.compile(r"(架构|设计|裁决|决策|权衡|高风险)"),
    ],
    "interactive": [
        re.compile(r"\b(step by step|confirm each|ask me)\b", re.I),
        re.compile(r"(逐步|每步|确认|等我|询问我)"),
    ],
}


def load_yaml(path: Path) -> dict:
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8-sig"))


def admission_policy() -> dict:
    gov = load_yaml(REG / "autonomous-execution-governance.yaml")
    return gov["profile_admission"]


def _magnitude(text: str) -> int:
    """从重复结构中提取最大数量（如 '100 个' → 100）；无则 0。"""
    best = 0
    for m in re.finditer(r"(\d{1,4})\s*(个|对|组|份|条|批|轮|篇|次)", text):
        try:
            best = max(best, int(m.group(1)))
        except ValueError:
            pass
    for m in re.finditer(r"[×xX]\s*(\d{1,4})", text):
        try:
            best = max(best, int(m.group(1)))
        except ValueError:
            pass
    return best


def extract_signals(objective: str, declare: dict | None) -> dict:
    declare = declare or {}
    counts: dict[str, int] = {}
    for sid, pats in _PATTERNS.items():
        counts[sid] = sum(1 for p in pats for _ in p.finditer(objective))
    if declare.get("batch_size"):
        counts["bulk_repetition"] = max(counts["bulk_repetition"], 1)
    if declare.get("bulk") is True:
        counts["bulk_repetition"] = max(counts["bulk_repetition"], 5)
    if declare.get("campaign") is True:
        counts["campaign"] = max(counts["campaign"], 2)
    if declare.get("research") is True:
        counts["research"] = max(counts["research"], 1)
    if declare.get("reasoning") is True:
        counts["reasoning_critical"] = max(counts["reasoning_critical"], 2)
    if declare.get("autonomy") == "human":
        counts["interactive"] = max(counts["interactive"], 2)
    mag = max(_magnitude(objective), int(declare.get("batch_size") or 0))
    expected_calls = declare.get("expected_provider_calls")
    short_scope = (expected_calls is not None and int(expected_calls) <= 24) or (
        expected_calls is None and len(objective) <= 200)
    if short_scope:
        counts["scope_small"] = 1
    return {"counts": counts, "magnitude": mag, "expected_calls": expected_calls,
            "short_scope": short_scope}


def classify(objective: str, declare: dict | None = None) -> dict:
    """确定性分类。返回 {profile, confidence, bulk_workload, reasons, signals}。"""
    policy = admission_policy()
    sig = extract_signals(objective, declare)
    c = sig["counts"]
    mag = sig["magnitude"]
    reasons: list[str] = []
    bulk_workload = False

    interactive = c.get("interactive", 0)
    campaign = c.get("campaign", 0)
    bulk_rep = c.get("bulk_repetition", 0)
    bulk_verb = c.get("bulk_verb", 0)
    reasoning = c.get("reasoning_critical", 0)
    research = c.get("research", 0)
    scope_small = sig["short_scope"]

    if interactive >= 2 and campaign == 0 and not (bulk_rep >= 2 or mag >= 10):
        profile, confidence = "INTERACTIVE", "high"
        reasons.append(f"interactive={interactive} (>=2) 且无 bulk/campaign 结构")
    elif campaign >= 2 or (declare or {}).get("campaign") is True:
        profile, confidence = "LONG_RUNNING_CAMPAIGN", "high" if campaign >= 2 else "medium"
        reasons.append(f"campaign={campaign}（多阶段/长期/cross-session 语义）")
        if bulk_rep >= 2 or mag >= 10:
            bulk_workload = True
            reasons.append("campaign 内含同构批量负载 → batch 子任务")
    elif bulk_rep >= 5 or mag >= 10 or (bulk_rep >= 2 and bulk_verb >= 1):
        profile, confidence = "BULK_EVALUATION", "high" if (bulk_rep >= 5 or mag >= 10) else "medium"
        bulk_workload = True
        reasons.append(f"bulk_repetition={bulk_rep}, magnitude={mag}, bulk_verb={bulk_verb} "
                       f"→ 同构批量负载（强制 batch execution）")
    elif reasoning >= 2 and scope_small:
        profile, confidence = "CRITICAL_REASONING", "high"
        reasons.append(f"reasoning_critical={reasoning} (>=2) 且 scope 小（高价值裁决类）")
    elif reasoning >= 1 and scope_small:
        profile, confidence = "CRITICAL_REASONING", "medium"
        reasons.append(f"reasoning_critical={reasoning} 且 scope 小")
    elif research >= 1:
        profile, confidence = "AUTONOMOUS_RESEARCH", "high" if research >= 2 else "medium"
        reasons.append(f"research={research}（多步调查/分析）")
    else:
        profile, confidence = SAFE_DEFAULT, "low"
        reasons.append("无决定性结构信号 → safe default AUTONOMOUS_STANDARD（UNKNOWN ≠ UNBOUNDED）")

    return {
        "profile": profile,
        "confidence": confidence,
        "bulk_workload": bulk_workload,
        "reasons": reasons,
        "signals": sig,
        "objective": objective[:2000],
        "safe_default_used": profile == SAFE_DEFAULT,
    }


def admission_path(task_id: str) -> Path:
    state = Path(os.environ.get("PERSONAL_AI_STATE", Path.home() / "personal-ai-state"))
    d = state / "checkpoints"
    d.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in task_id)
    return d / f"{safe}.admission.json"


def write_admission(task_id: str, project_id: str, decision: dict, harness: str | None) -> Path:
    rec = {
        "schema_version": 1,
        "task_id": task_id,
        "project_id": project_id,
        "harness": harness,
        "execution_profile": decision["profile"],
        "confidence": decision["confidence"],
        "bulk_workload": decision["bulk_workload"],
        "reasons": decision["reasons"],
        "profile_source": "automatic_admission",
        "admitted_before_first_call": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    path = admission_path(task_id)
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sys.path.insert(0, str(ROOT / "scripts" / "autonomy"))
    try:
        import usage_ledger
        usage_ledger.append_record({
            "kind": "admission",
            "task_id": task_id,
            "project_id": project_id,
            "harness": harness,
            "execution_profile": decision["profile"],
            "flags": ["auto_admission", f"confidence={decision['confidence']}",
                      "bulk_workload=true" if decision["bulk_workload"] else "bulk_workload=false"],
            "event_context": {"reasons": decision["reasons"]},
        })
    except Exception:  # noqa: BLE001
        pass
    return path


def read_admission(task_id: str) -> dict | None:
    path = admission_path(task_id)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _checkpoint_file(task_id: str) -> Path:
    state = Path(os.environ.get("PERSONAL_AI_STATE", Path.home() / "personal-ai-state"))
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in task_id)
    return state / "checkpoints" / f"{safe}.json"


def _consumed_ratio(task_id: str) -> float | None:
    ck = _checkpoint_file(task_id)
    if not ck.is_file():
        return None
    data = json.loads(ck.read_text(encoding="utf-8-sig"))
    eps = load_yaml(REG / "execution-profiles.yaml")["profiles"]
    r = data.get("budget_remaining", {})
    consumed = data.get("budget_consumed", {})
    profile = data.get("execution_profile")
    if not profile or profile not in eps:
        return None
    caps = eps[profile]["budgets"]["task"]
    denom = caps.get("cost_usd")
    if not denom:
        return None
    used = consumed.get("cost_usd", 0.0)
    return float(used) / float(denom)


def escalate(task_id: str, target: str, reason: str) -> dict:
    policy = admission_policy()
    adm = read_admission(task_id)
    if not adm:
        return {"ok": False, "reason": "no admission record; run `admission` first"}
    current = adm["execution_profile"]
    graph = policy["escalation"]["graph"]
    if current not in graph or target not in graph[current]:
        return {"ok": False, "reason": f"escalation not allowed: {current} → {target} "
                                       f"(graph 约束，见 canonical#profile_admission.escalation.graph)"}
    if not reason or len(reason.strip()) < 10:
        return {"ok": False, "reason": "reason 必须是非空 evidence 描述（>=10 字符）"}
    ratio = _consumed_ratio(task_id)
    if ratio is not None and ratio >= 0.9:
        return {"ok": False, "reason": f"consumed {ratio:.0%} >= 90%：拒绝 widening（剩余预算不足以支撑新 profile）"}
    ck = _checkpoint_file(task_id)
    if not ck.is_file():
        return {"ok": False, "reason": "checkpoint 缺失：先建任务 checkpoint（checkpoint.py new）"}
    data = json.loads(ck.read_text(encoding="utf-8-sig"))
    data["execution_profile"] = target
    data["timestamp"] = datetime.now(timezone.utc).isoformat()
    ck.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    adm["execution_profile"] = target
    adm["escalation"] = {"from": current, "to": target, "reason": reason,
                          "consumed_ratio": ratio,
                          "timestamp": datetime.now(timezone.utc).isoformat()}
    admission_path(task_id).write_text(
        json.dumps(adm, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sys.path.insert(0, str(ROOT / "scripts" / "autonomy"))
    try:
        import usage_ledger
        usage_ledger.append_record({
            "kind": "escalation",
            "task_id": task_id,
            "project_id": adm["project_id"],
            "harness": adm.get("harness"),
            "execution_profile": target,
            "flags": ["profile_change", "widening" if _is_widening(current, target) else "narrowing"],
            "event_context": {"from": current, "to": target, "reason": reason},
        })
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "from": current, "to": target, "reason": reason}


def _is_widening(a: str, b: str) -> bool:
    # 以 task 预算上限粗略判定：更宽松（cap 更大）视为 widening
    eps = load_yaml(REG / "execution-profiles.yaml")["profiles"]
    ca = eps.get(a, {}).get("budgets", {}).get("task", {}).get("provider_calls", 0)
    cb = eps.get(b, {}).get("budgets", {}).get("task", {}).get("provider_calls", 0)
    return (cb or 0) > (ca or 0)


def cmd_run(args) -> int:
    decision = classify(args.objective, json.loads(args.declare) if args.declare else None)
    path = write_admission(args.task, args.project, decision, args.harness)
    print(json.dumps({"admitted": True, **decision, "admission_file": str(path)},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_status(args) -> int:
    adm = read_admission(args.task)
    if not adm:
        print(json.dumps({"admitted": False}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(adm, ensure_ascii=False, indent=2))
    return 0


def cmd_escalate(args) -> int:
    result = escalate(args.task, args.target, args.reason)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def main() -> int:
    p = argparse.ArgumentParser(prog="profile_admission", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    pr = sub.add_parser("run")
    pr.add_argument("--task", required=True)
    pr.add_argument("--project", required=True)
    pr.add_argument("--objective", required=True)
    pr.add_argument("--declare")
    pr.add_argument("--harness", default=None)
    pr.set_defaults(func=cmd_run)
    ps = sub.add_parser("status")
    ps.add_argument("--task", required=True)
    ps.set_defaults(func=cmd_status)
    pe = sub.add_parser("escalate")
    pe.add_argument("--task", required=True)
    pe.add_argument("--target", required=True)
    pe.add_argument("--reason", required=True)
    pe.set_defaults(func=cmd_escalate)
    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())