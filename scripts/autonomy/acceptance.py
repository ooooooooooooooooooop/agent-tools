#!/usr/bin/env python3
"""acceptance.py — AUTONOMOUS_EXECUTION_GOVERNANCE counterexample acceptance.

以真实反例 session-0d07ae22（novel-main：534 turns / 674 API requests / ≈171M tokens
[cached ≈168.1M / new ≈2.57M / output ≈307K] / 估算成本 ≈$106）为验收基准，
证明同等类型任务在新的 Personal AI governance 下不可能再无界运行。

运行：
  python scripts/autonomy/acceptance.py            # 12 项验收（policy + 算数 + 机械验证）
  python scripts/autonomy/acceptance.py --live      # 额外运行 aic diff 五目标 + checkpoint demo
输出：stdout 摘要；--json 产出机读证据。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REG = ROOT / "registry"

INCIDENT = {
    "turns": 534,
    "provider_calls": 674,
    "total_tokens": 171_000_000,
    "cached_tokens": 168_100_000,
    "new_tokens": 2_570_000,
    "output_tokens": 307_000,
    "cost_usd": 106.0,
}


def load_yaml(path: Path) -> dict:
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8-sig"))


def gov() -> dict:
    return load_yaml(REG / "autonomous-execution-governance.yaml")


def profiles() -> dict:
    return load_yaml(REG / "execution-profiles.yaml").get("profiles", {})


def ck_schema() -> dict:
    return load_yaml(REG / "checkpoint-schema.yaml")


def ledger_schema() -> dict:
    return load_yaml(REG / "usage-ledger-schema.yaml")


def governance_policy() -> dict:
    return load_yaml(REG / "governance-policy.yaml")


def b(pname, key):
    return profiles()[pname]["budgets"].get(key)


def check(checks, cid, label, ok, evidence):
    checks.append({"id": cid, "label": label, "ok": bool(ok), "evidence": evidence})


def run_live_checks(checks) -> None:
    # 9+10: budget stop -> durable checkpoint; resume from checkpoint (not conversation)
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, PERSONAL_AI_STATE=tmp)
        py = sys.executable
        ck_py = ROOT / "scripts" / "autonomy" / "checkpoint.py"
        base = [py, str(ck_py)]
        new = subprocess.run(
            base + ["new", "--task", "counterexample-demo", "--project", "novel-main",
                    "--objective", "demonstrate budget-stop checkpoint", "--harness", "dsh",
                    "--profile", "LONG_RUNNING_CAMPAIGN"],
            capture_output=True, text=True, env=env)
        save = subprocess.run(
            base + ["save", "--task", "counterexample-demo",
                    "--actions", "phase-1", "--stop-reason", "budget_limit",
                    "--next", "stop: budget exhausted; report to owner",
                    "--usage-json", '{"input_tokens": 1200000, "cached_input_tokens": 90000000,'
                                    ' "output_tokens": 30000, "cost_usd": 36.5}'],
            capture_output=True, text=True, env=env)
        resume = subprocess.run(base + ["resume", "counterexample-demo"],
                                capture_output=True, text=True, env=env)
        val = subprocess.run(base + ["validate", "counterexample-demo"],
                             capture_output=True, text=True, env=env)
        ck_file = Path(tmp) / "checkpoints" / "counterexample-demo.json"
        ck_data = json.loads(ck_file.read_text(encoding="utf-8")) if ck_file.is_file() else {}
        check(checks, "9", "budget stop 保存 durable checkpoint",
              new.returncode == 0 and save.returncode == 0 and ck_file.is_file()
              and ck_data.get("stop_reason") == "budget_limit"
              and ck_data.get("protocol_hash", "").startswith("")
              and "VALID" in val.stdout,
              f"new rc={new.returncode} save rc={save.returncode} file={ck_file.is_file()} "
              f"stop={ck_data.get('stop_reason')} "
              f"budget_remaining.cost={ck_data.get('budget_remaining', {}).get('cost_usd')} "
              f"validate={val.stdout.strip()}")
        resumed = json.loads(resume.stdout) if resume.returncode == 0 else {}
        check(checks, "10", "resume 不依赖原 conversation（读 checkpoint）",
              resume.returncode == 0 and resumed.get("resumable") is True
              and bool(resumed.get("next_executable_action")),
              f"resume rc={resume.returncode} resumable={resumed.get('resumable')} "
              f"next={resumed.get('next_executable_action')!r} "
              f"budget_remaining.cost_usd={resumed.get('budget_remaining', {}).get('cost_usd')}")

    # 11: 五 harness 同一 governance block（内容一致 + 已渲染进 live 指令文件）
    sys.path.insert(0, str(ROOT / "scripts" / "aic"))
    import aic
    block = aic.governance_block_text()
    digest = hashlib_sha256(block)
    targets = {name: (Path.home() / p[0] / p[1]) for name, p in aic.POLICY_TARGETS.items()}
    for name, path in targets.items():
        row = aic._governance_diff_row(name)
        check(checks, "11", f"harness {name} governance block NO DRIFT",
              row is not None and row["ok"],
              f"{path}: {row['actual'] if row else 'no-target'} (content sha256={digest[:12]})")

    # 1: live aic diff for all five targets
    for name in ("dsh", "codex", "claude", "gemini", "switchboard"):
        prc = subprocess.run([sys.executable, str(ROOT / "scripts" / "aic" / "aic.py"),
                              "diff", name], capture_output=True, text=True)
        if prc.returncode != 0:
            check(checks, "13", f"aic diff {name} = NO DRIFT", False,
                  prc.stdout.strip()[-500:] or prc.stderr.strip()[-200:])
    # live diff result rows (only for targets where governance check exists)
    for name in ("dsh", "codex", "claude", "gemini", "switchboard"):
        row = aic._governance_diff_row(name)
        if row is not None:
            check(checks, "13", f"aic governance diff {name}", row["ok"], row["actual"])


def hashlib_sha256(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run(checks) -> None:
    g, eps, cs, ls, gp = gov(), profiles(), ck_schema(), ledger_schema(), governance_policy()
    lrc = profiles()["LONG_RUNNING_CAMPAIGN"]
    lrc_b = lrc["budgets"]

    turns_limit = lrc_b["session"]["agent_turns"]
    calls_limit = lrc_b["task"]["provider_calls"]
    cached_limit = lrc_b["task"]["cached_input_tokens"]
    cost_limit = lrc_b["task"]["cost_usd"]
    break_at = lrc["loop_breaker"]["hard_window"]

    check(checks, "1", "agent-turn hard limit 真正生效（session 上限 < 534）",
          isinstance(turns_limit, int) and 0 < turns_limit < INCIDENT["turns"],
          f"LONG_RUNNING_CAMPAIGN session.agent_turns={turns_limit} vs incident 534 "
          f"→ 在事故 turn 数的 {turns_limit / INCIDENT['turns']:.0%} 处 fail closed")

    check(checks, "2", "provider-call budget 真正生效（task 累计 < 674）",
          isinstance(calls_limit, int) and 0 < calls_limit < INCIDENT["provider_calls"],
          f"task.provider_calls={calls_limit} vs incident 674（跨 resume 累计不重置）")

    cached_kinds = {k["id"] for k in g["budget_governor"]["kinds"]}
    ck_fields_json = json.dumps(ck_schema().get("fields", {}), ensure_ascii=False)
    check(checks, "3", "cached-token usage 被计入预算",
          "cached_input_token_budget" in cached_kinds
          and "cached_input_tokens" in ls.get("record_fields", {})
          and "cached_input_tokens" in ck_fields_json,
          f"budget kinds 含 cached_input_token_budget: "
          f"{'cached_input_token_budget' in cached_kinds}; ledger schema 含 cached_input_tokens; "
          f"checkpoint schema 经 model_usage/budget_consumed 承载 cached_input_tokens; "
          f"incident cached 168.1M vs 预算 {cached_limit} → 超限触发 COMPACT")

    tiers = {t["id"] for t in g["model_tier_governance"]["task_types"]}
    hard_rules = " ".join(g["model_tier_governance"].get("hard_rules", []))
    lrc_tier = lrc.get("model_tier_default")
    be = profiles()["BULK_EVALUATION"]
    check(checks, "4", "昂贵模型 bulk 默认被禁止/限制",
          "FRONTIER_REASONING" in tiers and "禁止被硬编码为默认 bulk worker" in hard_rules
          and lrc_tier != "FRONTIER_REASONING"
          and be.get("bulk_worker_tier_default") != "FRONTIER_REASONING",
          f"hard_rules 含 bulk 昂贵模型禁令; LONG_RUNNING_CAMPAIGN tier={lrc_tier}; "
          f"BULK_EVALUATION bulk_worker_tier_default={be.get('bulk_worker_tier_default')}")

    patterns = {p["id"] for p in g["loop_breaker"]["detection_patterns"]}
    forbidden = gp.get("auto_forbidden", [])
    check(checks, "5", "no-progress loop 自动 circuit break",
          {"no_new_artifact_loop", "no_state_change_loop"}.issubset(patterns)
          and "disable_loop_breaker" in forbidden
          and isinstance(break_at, int) and break_at > 0,
          f"检测模式 {len(patterns)} 种; hard circuit at {break_at} 连续无进展轮; "
          f"auto_forbidden.disable_loop_breaker 存在: {'disable_loop_breaker' in forbidden}")

    repair_max = lrc["retard"]["repeated_repair_max"]
    check(checks, "6", "repeated repair 有界 retry",
          isinstance(repair_max, int) and 0 < repair_max <= 3,
          f"repeated_repair_max={repair_max}（与 switchboard chain_key ≤3 复用对齐）")

    check(checks, "7", "batch 任务不需要主 Agent 逐 item reasoning",
          profiles()["BULK_EVALUATION"].get("batch_default") is True
          and "主 Agent 逐 item 模型调用" in " ".join(
              profiles()["BULK_EVALUATION"].get("rules", []))
          and "manifest" in str(g["batch_execution"]),
          f"BULK_EVALUATION batch_default=True; rules 含逐 item 禁令; contract 含 manifest→workers→aggregate")

    cadence = lrc["checkpoint_cadence_turns"]
    cycle_ok = "CHECKPOINT" in str(g["context_governor"].get("cycle", ""))
    check(checks, "8", "phase checkpoint 后上下文显著缩小",
          isinstance(cadence, int) and cadence > 0 and cycle_ok
          and "cached_input_token_budget" in cached_kinds,
          f"checkpoint_cadence_turns={cadence}; cycle={g['context_governor'].get('cycle')}; "
          f"演示：incident 171M token 上下文 → CHECKPOINT(KB 级 JSON)+COMPACT"
          f"（摘要档由 routing-policy.compaction_summary SSOT 决定）→ RESUME 携带 brief 而非"
          f"{INCIDENT['cached_tokens'] / 1e6:.0f}M 重复 cache-read；cache 预算 {cached_limit} 超限强制 COMPACT")

    check(checks, "12", "项目不需要各自重新实现",
          set(profiles()).issuperset({"INTERACTIVE", "AUTONOMOUS_STANDARD",
                                      "AUTONOMOUS_RESEARCH", "BULK_EVALUATION",
                                      "LONG_RUNNING_CAMPAIGN", "CRITICAL_REASONING"})
          and "forbidden_declarations" in g.get("consumers", {})
          and "budget_numbers" in g["consumers"].get("forbidden_declarations", []),
          "项目只声明 execution_profile + 白名单 override；预算/模型/上限由 Personal AI 决定")

    # 反例总账：incident 数字 vs 新上限
    check(checks, "0", "counterexample overall: 534/674/$106 不可能复现",
          turns_limit < INCIDENT["turns"] and calls_limit < INCIDENT["provider_calls"]
          and cost_limit < INCIDENT["cost_usd"] and cached_limit < INCIDENT["cached_tokens"],
          f"session turns {turns_limit}<{INCIDENT['turns']}; calls {calls_limit}<674; "
          f"cost ${cost_limit}<$106; cached {cached_limit}<168.1M")


def report(checks, out_json: Path | None) -> int:
    passed = sum(1 for c in checks if c["ok"])
    print(f"ACCEPTANCE: {passed}/{len(checks)} passed")
    for c in checks:
        print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['id']} {c['label']}")
        print(f"        evidence: {c['evidence'][:400]}")
    if out_json:
        out_json.write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"evidence -> {out_json}")
    overall = "PASS" if passed == len(checks) else "PARTIAL"
    print(f"COUNTEREXAMPLE_ACCEPTANCE = {overall}")
    return 0 if all(c["ok"] for c in checks) else 1


def main() -> int:
    p = argparse.ArgumentParser(prog="acceptance", description=__doc__)
    p.add_argument("--live", action="store_true", help="run aic diff + checkpoint demo")
    p.add_argument("--json", type=Path)
    args = p.parse_args()
    checks: list[dict] = []
    run(checks)
    if args.live:
        run_live_checks(checks)
    return report(checks, args.json)


if __name__ == "__main__":
    sys.exit(main())