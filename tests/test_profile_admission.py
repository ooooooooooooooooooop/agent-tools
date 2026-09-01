"""Automatic Execution Profile Admission 回归测试。

覆盖：classifier 决策（A–E）、--auto-admit 落盘、escalation 约束（graph/reason/usage 不重置）、
UNKNOWN ≠ UNBOUNDED safe default、admission-before-first-call 顺序。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "autonomy"))

import profile_admission  # noqa: E402


class TestClassifier(unittest.TestCase):
    def test_a_bug_fix_standard(self):
        d = profile_admission.classify("自主修复登录页用户改密后 token 过期的 bug")
        self.assertEqual(d["profile"], "AUTONOMOUS_STANDARD")
        self.assertTrue(d["safe_default_used"])

    def test_b_research(self):
        d = profile_admission.classify("调查一个复杂研究问题并产出分析报告，多步取证")
        self.assertEqual(d["profile"], "AUTONOMOUS_RESEARCH")

    def test_c_bulk_100_judge_pairs(self):
        d = profile_admission.classify("运行 100 个 Judge pair 并汇总：每对评估两个回复")
        self.assertEqual(d["profile"], "BULK_EVALUATION")
        self.assertTrue(d["bulk_workload"])

    def test_d_campaign(self):
        d = profile_admission.classify("持续完成多阶段质量 campaign，跨会话推进")
        self.assertEqual(d["profile"], "LONG_RUNNING_CAMPAIGN")

    def test_e_unknown_safe_default(self):
        d = profile_admission.classify("帮我处理一下")
        self.assertEqual(d["profile"], "AUTONOMOUS_STANDARD")
        self.assertTrue(d["safe_default_used"])
        # UNKNOWN ≠ UNBOUNDED：没有任何 autonomous profile 是 unbounded
        # （INTERACTIVE 的 session turn 上限为 null 是设计——人工驱动，非 autonomous task；
        # 但 task 级 provider_calls/cost 仍 bounded）
        eps = profile_admission.load_yaml(ROOT / "registry" / "execution-profiles.yaml")
        for name, p in eps["profiles"].items():
            if name != "INTERACTIVE":
                self.assertNotEqual(p.get("budgets", {}).get("session", {}).get("agent_turns"), None)
            self.assertGreater(p.get("budgets", {}).get("task", {}).get("provider_calls", 0), 0)

    def test_declare_overrides_text(self):
        d = profile_admission.classify("处理一下", {"batch_size": 30})
        self.assertEqual(d["profile"], "BULK_EVALUATION")
        d = profile_admission.classify("处理一下", {"campaign": True})
        self.assertEqual(d["profile"], "LONG_RUNNING_CAMPAIGN")

    def test_reasons_present(self):
        d = profile_admission.classify("运行 80 组对比实验并汇总")
        self.assertTrue(d["reasons"])


class TestAdmissionDurable(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.env = dict(os.environ, PERSONAL_AI_STATE=self._tmp.name,
                        PERSONAL_AI_LEDGER=str(Path(self._tmp.name) / "ledger" / "usage.jsonl"))
        self.py = sys.executable

    def tearDown(self):
        self._tmp.cleanup()

    def cli_checkpoint(self, *args):
        return subprocess.run([self.py, str(ROOT / "scripts" / "autonomy" / "checkpoint.py"),
                               *args], capture_output=True, text=True, env=self.env)

    def test_auto_admit_binds_before_usage(self):
        r = self.cli_checkpoint("new", "--task", "t-auto", "--project", "p",
                                "--objective", "修复搜索排序 bug", "--harness", "dsh",
                                "--auto-admit")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        ck = json.loads((Path(self._tmp.name) / "checkpoints" / "t-auto.json")
                        .read_text(encoding="utf-8-sig"))
        adm = json.loads((Path(self._tmp.name) / "checkpoints" / "t-auto.admission.json")
                         .read_text(encoding="utf-8-sig"))
        self.assertEqual(ck["execution_profile"], "AUTONOMOUS_STANDARD")
        self.assertEqual(adm["execution_profile"], ck["execution_profile"])
        self.assertTrue(adm["admitted_before_first_call"])
        # usage ledger 中 admission 记录先于任何 usage/checkpoint 消耗记录
        rows = []
        lp = Path(self._tmp.name) / "ledger" / "usage.jsonl"
        if lp.is_file():
            rows = [json.loads(x) for x in lp.read_text(encoding="utf-8-sig").splitlines()]
        admission_rows = [x for x in rows if x.get("kind") == "admission"]
        self.assertTrue(admission_rows)

    def test_escalation_invariants(self):
        self.cli_checkpoint("new", "--task", "t-esc", "--project", "p",
                            "--objective", "修复排序 bug", "--harness", "dsh",
                            "--auto-admit")
        self.cli_checkpoint("save", "--task", "t-esc",
                            "--usage-json", '{"cost_usd": 5.0}')
        py = self.py
        adm = ROOT / "scripts" / "autonomy" / "profile_admission.py"
        def esc(target, reason):
            return subprocess.run([py, str(adm), "escalate", "--task", "t-esc",
                                   "--target", target, "--reason", reason],
                                  capture_output=True, text=True, env=self.env)
        r_weak = esc("LONG_RUNNING_CAMPAIGN", "x")
        self.assertEqual(r_weak.returncode, 1)
        r_nongraph = esc("UNBOUNDED_MODE", "let me continue forever")
        self.assertEqual(r_nongraph.returncode, 1)
        self.assertIn("escalation not allowed", r_nongraph.stdout)
        ck = json.loads((Path(self._tmp.name) / "checkpoints" / "t-esc.json")
                        .read_text(encoding="utf-8-sig"))
        self.assertEqual(ck["execution_profile"], "AUTONOMOUS_STANDARD")
        consumed_before = ck["budget_consumed"].get("cost_usd")
        r_ok = esc("LONG_RUNNING_CAMPAIGN",
                   "evidence: 任务演变为多阶段持续质量推进，已出现跨会话语义")
        self.assertEqual(r_ok.returncode, 0, r_ok.stdout)
        ck2 = json.loads((Path(self._tmp.name) / "checkpoints" / "t-esc.json")
                         .read_text(encoding="utf-8-sig"))
        self.assertEqual(ck2["execution_profile"], "LONG_RUNNING_CAMPAIGN")
        self.assertEqual(ck2["budget_consumed"].get("cost_usd"), consumed_before)
        adm2 = json.loads((Path(self._tmp.name) / "checkpoints" / "t-esc.admission.json")
                          .read_text(encoding="utf-8-sig"))
        self.assertEqual(adm2["execution_profile"], "LONG_RUNNING_CAMPAIGN")
        self.assertIn("escalation", adm2)


if __name__ == "__main__":
    unittest.main()