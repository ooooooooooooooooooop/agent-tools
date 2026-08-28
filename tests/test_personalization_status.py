#!/usr/bin/env python3
"""test_personalization_status.py — personal-ai-operations-review Personalization 域 smoke tests。

覆盖任务要求的五个场景：
  Healthy / Known blocker only / Personalization regression / Scope leakage / Over-personalization
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills" / "personal-ai-operations-review" / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "governance"))

import personalization_status as ps  # noqa: E402
import personal_status as pss  # noqa: E402


def write_jsonl(path: Path, rows: list[dict]) -> str:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                    encoding="utf-8")
    return str(path)


def healthy_messages(d: Path) -> str:
    return write_jsonl(d / "msgs.jsonl", [
        {"session": "s1", "text": "帮我看一下这个脚本有没有问题"},
        {"session": "s1", "text": "可以，继续"},
        {"session": "s2", "text": "目前项目总目标是什么"},
        {"session": "s2", "text": "好的"},
    ])


class TestHealthyScenario(unittest.TestCase):
    def test_healthy(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            r = ps.evaluate(healthy_messages(d), None, str(d / "no-report.md"))
            self.assertEqual(r["status"], "HEALTHY", r["reason"])
            self.assertEqual(r["metrics"]["scope_leakage"], "UNKNOWN")  # 无注入数据源，诚实标记

    def test_healthy_overall_no_action(self):
        domains = {"Infrastructure": ("HEALTHY", ""), "Personalization": ("HEALTHY", ""),
                   "Durability": ("HEALTHY", ""), "Governance": ("HEALTHY", ""),
                   "Proposals": ("HEALTHY", "")}
        self.assertEqual(pss.classify_action(domains, []), "NO ACTION")


class TestKnownBlockerOnly(unittest.TestCase):
    def test_known_blockers_not_action_required(self):
        domains = {"Infrastructure": ("HEALTHY", ""), "Personalization": ("HEALTHY", ""),
                   "Durability": ("HEALTHY", ""), "Governance": ("HEALTHY", ""),
                   "Proposals": ("HEALTHY", "")}
        action = pss.classify_action(domains, pss.KNOWN_EXTERNAL_BLOCKERS)
        self.assertEqual(action, "EXTERNAL BLOCKER")
        self.assertNotEqual(action, "ACTION REQUIRED")


class TestPersonalizationRegression(unittest.TestCase):
    def test_repeat_correction_worsening(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            report = d / "report.md"
            report.write_text("Correction Rate ≈ 6.2% / user message\n"
                              "Repeat Correction Rate | 6/56 会话出现同会话重复纠正\n",
                              encoding="utf-8")
            msgs = [
                {"session": "s1", "text": "说了省token你到底想干嘛"},
                {"session": "s2", "text": "又说省token，怎么又浪费"},
                {"session": "s2", "text": "省token 又忘了"},
                {"session": "s3", "text": "不要问，直接做"},
                {"session": "s4", "text": "能不能干？一直在问"},
                {"session": "s5", "text": "太啰嗦了"},
            ]
            mp = write_jsonl(d / "msgs.jsonl", msgs)
            r = ps.evaluate(mp, None, str(report))
            self.assertEqual(r["status"], "DEGRADED")
            self.assertTrue(r["reason"])
            self.assertGreater(r["metrics"]["repeat_personalization_failures"], 0)
            domains = {"Infrastructure": ("HEALTHY", ""), "Personalization": ("DEGRADED", ""),
                       "Durability": ("HEALTHY", ""), "Governance": ("HEALTHY", ""),
                       "Proposals": ("HEALTHY", "")}
            self.assertEqual(pss.classify_action(domains, []), "REVIEW")


class TestScopeLeakage(unittest.TestCase):
    def test_project_scope_leakage_action_required(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            ev = write_jsonl(d / "sel.jsonl", [
                {"task_scope": "project:novel-main",
                 "injected_scopes": ["global", "project:skills"], "preference_count": 3},
            ])
            r = ps.evaluate(None, ev, str(d / "missing.md"))  # 无消息数据也能判定泄漏
            self.assertEqual(r["status"], "ACTION REQUIRED")
            self.assertEqual(len(r["selection"]["scope_leakage"]), 1)
            domains = {"Infrastructure": ("HEALTHY", ""),
                       "Personalization": ("ACTION REQUIRED", "scope leakage"),
                       "Durability": ("HEALTHY", ""), "Governance": ("HEALTHY", ""),
                       "Proposals": ("HEALTHY", "")}
            self.assertEqual(pss.classify_action(domains, pss.KNOWN_EXTERNAL_BLOCKERS),
                             "ACTION REQUIRED")


class TestOverPersonalization(unittest.TestCase):
    def test_simple_task_over_injection_degraded(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            ev = write_jsonl(d / "sel.jsonl", [
                {"task_scope": "simple", "injected_scopes": ["global"],
                 "preference_count": 9},
            ])
            mp = healthy_messages(d)
            r = ps.evaluate(mp, ev, str(d / "missing.md"))
            self.assertEqual(r["status"], "DEGRADED")
            self.assertIn("超额 preference", r["reason"])


class TestMissingData(unittest.TestCase):
    def test_no_messages_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            r = ps.evaluate(str(Path(td) / "none.jsonl"), None, str(Path(td) / "none.md"))
            self.assertEqual(r["status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
