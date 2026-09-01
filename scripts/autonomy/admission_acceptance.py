#!/usr/bin/env python3
"""admission_acceptance.py — AUTOMATIC EXECUTION PROFILE ADMISSION 验收（A–I）。

运行：python scripts/autonomy/admission_acceptance.py [--live]（--live 额外跑 aic diff 5 harness）
证据要点：用户只描述任务目标 → 自动 TASK ADMISSION → CLASSIFICATION → BINDING → EXECUTION；
UNKNOWN ≠ UNBOUNDED；escalation 受 canonical 约束；五 harness 无 drift。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REG = ROOT / "registry"
sys.path.insert(0, str(ROOT / "scripts" / "autonomy"))

import profile_admission  # noqa: E402


def load_yaml(path: Path) -> dict:
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8-sig"))


CHECKS: list[dict] = []


def check(cid, label, ok, evidence):
    CHECKS.append({"id": cid, "label": label, "ok": bool(ok), "evidence": evidence})


def main() -> int:
    # A. 明确 bug fix → bounded standard
    d = profile_admission.classify("自主修复登录页用户改密后 token 过期的 bug，包含定位根因与回归测试")
    check("A", "明确 bug → AUTONOMOUS_STANDARD（bounded）",
          d["profile"] == "AUTONOMOUS_STANDARD", f"profile={d['profile']} reasons={d['reasons']}")

    # B. 复杂研究 → research
    d = profile_admission.classify("调查一个复杂研究问题：对比三种向量检索方案的召回差异并产出分析报告，多步取证")
    check("B", "复杂研究 → AUTONOMOUS_RESEARCH",
          d["profile"] == "AUTONOMOUS_RESEARCH", f"profile={d['profile']} reasons={d['reasons']}")

    # C. 100 个 judge pair → bulk evaluation + 禁止主 Agent 逐 item
    d = profile_admission.classify("运行 100 个 Judge pair 并汇总：每对评估两个回复，输出对比表与判定统计")
    g = load_yaml(REG / "autonomous-execution-governance.yaml")
    batch_rules = " ".join(load_yaml(REG / "execution-profiles.yaml")["profiles"]
                           ["BULK_EVALUATION"].get("rules", []))
    check("C", "100 Judge pair → BULK_EVALUATION + bulk_workload + 禁止逐 item",
          d["profile"] == "BULK_EVALUATION" and d["bulk_workload"] is True
          and "逐 item" in batch_rules and "manifest" in str(g["batch_execution"]),
          f"profile={d['profile']} bulk={d['bulk_workload']} "
          f"rules 含逐 item 禁令: {'逐 item' in batch_rules}")

    # D. 多阶段质量 campaign → LONG_RUNNING_CAMPAIGN
    d = profile_admission.classify("持续完成多阶段质量 campaign：第一阶段治理数据，第二阶段评估，第三阶段发布，跨会话推进")
    check("D", "多阶段 campaign → LONG_RUNNING_CAMPAIGN",
          d["profile"] == "LONG_RUNNING_CAMPAIGN", f"profile={d['profile']} reasons={d['reasons']}")

    # E. 模糊任务 → 绝不 unbounded（safe default STANDARD）
    d = profile_admission.classify("帮我处理一下")
    check("E", "模糊任务 → safe default（bounded，UNKNOWN ≠ UNBOUNDED）",
          d["profile"] == "AUTONOMOUS_STANDARD" and d["safe_default_used"] is True,
          f"profile={d['profile']} safe_default_used={d['safe_default_used']}")

    # F. admission 先于首次昂贵调用 / 首个执行动作
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, PERSONAL_AI_STATE=tmp,
                   PERSONAL_AI_LEDGER=str(Path(tmp) / "ledger" / "usage.jsonl"))
        py = sys.executable
        ck_py = ROOT / "scripts" / "autonomy" / "checkpoint.py"
        base = [py, str(ck_py)]
        r = subprocess.run(base + ["new", "--task", "admit-f", "--project", "p",
                                   "--objective", "修复搜索排序 bug", "--harness", "dsh",
                                   "--auto-admit"],
                           capture_output=True, text=True, env=env)
        ck = json.loads((Path(tmp) / "checkpoints" / "admit-f.json").read_text(encoding="utf-8-sig"))
        adm = json.loads((Path(tmp) / "checkpoints" / "admit-f.admission.json")
                         .read_text(encoding="utf-8-sig"))
        ledger_rows = []
        lp = Path(tmp) / "ledger" / "usage.jsonl"
        if lp.is_file():
            ledger_rows = [json.loads(x) for x in lp.read_text(encoding="utf-8-sig").splitlines()]
        usage_after = [x for x in ledger_rows if x.get("kind") in ("usage", "checkpoint")]
        admission_rows = [x for x in ledger_rows if x.get("kind") == "admission"]
        check("F", "admission 先于首个执行/消耗（record 早于 usage；绑定后才有 hard policy）",
              r.returncode == 0 and ck["execution_profile"] == "AUTONOMOUS_STANDARD"
              and adm["execution_profile"] == ck["execution_profile"]
              and bool(admission_rows)
              and all(admission_rows[0]["ts"] <= u.get("ts", "9999") for u in usage_after),
              f"auto-admit rc={r.returncode} profile={ck['execution_profile']} "
              f"admission_records={len(admission_rows)} usage_records={len(usage_after)}")

        # G. resume 后累计 budget 不重置
        subprocess.run(base + ["save", "--task", "admit-f", "--usage-json",
                               '{"cached_input_tokens": 1000000, "cost_usd": 5.0}'],
                       capture_output=True, text=True, env=env)
        before = json.loads((Path(tmp) / "checkpoints" / "admit-f.json")
                            .read_text(encoding="utf-8-sig"))
        subprocess.run(base + ["save", "--task", "admit-f", "--resume"], capture_output=True,
                       text=True, env=env)
        after = json.loads((Path(tmp) / "checkpoints" / "admit-f.json")
                           .read_text(encoding="utf-8-sig"))
        check("G", "resume 后累计 budget 不重置",
              before["budget_consumed"] == after["budget_consumed"]
              and after["resume_count"] >= 1
              and after["budget_remaining"]["cost_usd"] < 10,
              f"consumed(cost) before={before['budget_consumed'].get('cost_usd')} "
              f"after={after['budget_consumed'].get('cost_usd')} "
              f"remaining={after['budget_remaining'].get('cost_usd')} resume_count={after['resume_count']}")

        # H. Agent 无法自行改 profile 绕开 budget
        adm_py = ROOT / "scripts" / "autonomy" / "profile_admission.py"
        r1 = subprocess.run([py, str(adm_py), "escalate", "--task", "admit-f",
                             "--target", "LONG_RUNNING_CAMPAIGN", "--reason", "x"],
                            capture_output=True, text=True, env=env)          # reason 太短
        r2 = subprocess.run([py, str(adm_py), "escalate", "--task", "admit-f",
                             "--target", "UNBOUNDED_MODE", "--reason", "let me continue forever"],
                            capture_output=True, text=True, env=env)          # 不存在 profile
        ck_after_rejects = json.loads((Path(tmp) / "checkpoints" / "admit-f.json")
                                      .read_text(encoding="utf-8-sig"))
        both_rejected = r1.returncode == 1 and r2.returncode == 1
        check("H", "Agent 无法自行改 profile 绕开 budget（graph/reason/无 UNBOUNDED profile）",
              both_rejected and "escalation not allowed" in r2.stdout
              and ck_after_rejects["execution_profile"] == "AUTONOMOUS_STANDARD",
              f"weak-reason rc={r1.returncode}; non-graph rc={r2.returncode} "
              f"(msg: {r2.stdout.strip()[:80]}); profile_after_rejects={ck_after_rejects['execution_profile']}")

        # H2. 合法 evidence-driven escalation：成功且不重置累计 usage（budget_consumed 保留）
        consumed_before_esc = ck_after_rejects["budget_consumed"].get("cost_usd")
        r3 = subprocess.run([py, str(adm_py), "escalate", "--task", "admit-f",
                             "--target", "LONG_RUNNING_CAMPAIGN",
                             "--reason", "evidence: 任务演变为多阶段持续质量推进，已出现跨会话语义"],
                            capture_output=True, text=True, env=env)
        ck_after_esc = json.loads((Path(tmp) / "checkpoints" / "admit-f.json")
                                  .read_text(encoding="utf-8-sig"))
        esc_ok = "ok" in r3.stdout and "\"ok\": true" in r3.stdout
        check("H2", "合法 escalation 受 canonical 约束：成功、reason 记录、usage 不重置",
              esc_ok and ck_after_esc["execution_profile"] == "LONG_RUNNING_CAMPAIGN"
              and ck_after_esc["budget_consumed"].get("cost_usd") == consumed_before_esc,
              f"esc rc={r3.returncode} ok={esc_ok} profile={ck_after_esc['execution_profile']} "
              f"consumed(before)={consumed_before_esc} consumed(after)="
              f"{ck_after_esc['budget_consumed'].get('cost_usd')}")

    # I. 五 harness projection 无 drift
    for name in ("dsh", "codex", "claude", "gemini", "switchboard"):
        prc = subprocess.run([sys.executable, str(ROOT / "scripts" / "aic" / "aic.py"),
                              "diff", name], capture_output=True, text=True)
        check("I", f"aic diff {name} = NO DRIFT", prc.returncode == 0,
              prc.stdout.strip()[-200:])

    passed = sum(1 for c in CHECKS if c["ok"])
    print(f"ADMISSION_ACCEPTANCE: {passed}/{len(CHECKS)} passed")
    for c in CHECKS:
        print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['id']} {c['label']}")
        print(f"        {c['evidence'][:300]}")
    overall = "PASS" if passed == len(CHECKS) else "PARTIAL"
    print(f"AUTOMATIC_PROFILE_ADMISSION = {overall}")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())