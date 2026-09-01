"""AUTONOMOUS_EXECUTION_GOVERNANCE 回归测试（canonical + tools + 反例验收子集）。

覆盖：
- canonical policy 完整性（预算 kinds / hook 矩阵 / profiles / schema 文件）
- aic _validate_governance 一致性
- checkpoint.py 机制（new/save/resume/validate，temp PERSONAL_AI_STATE）
- usage_ledger.py 机制（append/query/cost-per-progress/runaway，temp ledger）
- 反例（session-0d07ae22）算术验收（turns/calls/cost/cached 全部不可能复现）
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
REG = ROOT / "registry"
sys.path.insert(0, str(ROOT / "scripts" / "autonomy"))
sys.path.insert(0, str(ROOT / "scripts" / "aic"))

import aic  # noqa: E402
import usage_ledger  # noqa: E402


def load_yaml(path: Path) -> dict:
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8-sig"))


class TestCanonicalPolicy(unittest.TestCase):
    def test_budget_kinds_complete(self):
        g = load_yaml(REG / "autonomous-execution-governance.yaml")
        kinds = {k["id"] for k in g["budget_governor"]["kinds"]}
        expected = {"task_budget", "session_budget", "provider_call_budget",
                    "agent_turn_budget", "model_tier_budget", "input_token_budget",
                    "cached_input_token_budget", "output_token_budget",
                    "cost_budget", "runtime_budget"}
        self.assertEqual(kinds, expected)
        self.assertIn("hard_enforce", g["budget_governor"]["semantics"])

    def test_hook_matrix_8_hooks_x_5_harnesses(self):
        g = load_yaml(REG / "autonomous-execution-governance.yaml")
        hooks = g["harness_hook_matrix"]["hooks"]
        self.assertEqual(len(hooks), 8)
        rows = {r["harness"]: r for r in g["harness_hook_matrix"]["rows"]}
        self.assertEqual(set(rows), {"dsh", "codex", "claude", "gemini", "switchboard"})
        for h, row in rows.items():
            missing = [x for x in hooks if x not in row["mechanisms"]]
            self.assertEqual(missing, [], f"{h} missing hooks {missing}")

    def test_profiles_turn_call_bounds_vs_incident(self):
        eps = load_yaml(REG / "execution-profiles.yaml")
        lrc = eps["profiles"]["LONG_RUNNING_CAMPAIGN"]
        self.assertEqual(lrc["budgets"]["session"]["agent_turns"], 64)
        self.assertLess(lrc["budgets"]["session"]["agent_turns"], 534)  # incident turns
        self.assertEqual(lrc["budgets"]["task"]["provider_calls"], 200)
        self.assertLess(lrc["budgets"]["task"]["provider_calls"], 674)  # incident calls
        self.assertLess(lrc["budgets"]["task"]["cost_usd"], 106)  # incident cost
        self.assertLess(lrc["budgets"]["task"]["cached_input_tokens"], 168_100_000)
        self.assertEqual(len(eps["profiles"]), 6)

    def test_generated_instructions_present(self):
        g = load_yaml(REG / "autonomous-execution-governance.yaml")
        self.assertIn("AUTONOMOUS_EXECUTION_GOVERNANCE", g["generated_instructions"])
        self.assertIn("execution_profile", g["generated_instructions"])

    def test_checkpoint_and_ledger_schemas(self):
        ck = load_yaml(REG / "checkpoint-schema.yaml")
        self.assertEqual(ck["schema_version"], 1)
        for f in ("task_id", "project_id", "objective", "harness",
                  "execution_profile", "next_executable_action", "budget_consumed",
                  "budget_remaining", "stop_reason", "resume_count", "timestamp"):
            self.assertIn(f, ck["fields"])
        ul = load_yaml(REG / "usage-ledger-schema.yaml")
        self.assertIn("cached_input_tokens", ul["record_fields"])
        self.assertIn("cost_per_progress", ul["detectors"])

    def test_governance_policy_freeze_additions(self):
        gp = load_yaml(REG / "governance-policy.yaml")
        self.assertIn("raise_execution_budget", gp["auto_forbidden"])
        self.assertIn("disable_loop_breaker", gp["auto_forbidden"])
        self.assertIn("bypass_budget_limit", gp["auto_forbidden"])
        self.assertIn("budget_enforcement", gp["auto_allowed"])
        self.assertIn("checkpoint_write", gp["auto_allowed"])

    def test_aic_validate_governance_clean(self):
        errors = aic._validate_governance()
        self.assertEqual(errors, [])


class TestCheckpointTool(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._state = str(Path(self._tmp.name))
        self.env = dict(os.environ, PERSONAL_AI_STATE=self._state,
                        PERSONAL_AI_LEDGER=str(Path(self._tmp.name) / "ledger" / "usage.jsonl"))
        self.py = sys.executable
        self.base = [self.py, str(ROOT / "scripts" / "autonomy" / "checkpoint.py")]

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args):
        return subprocess.run(self.base + list(args), capture_output=True,
                              text=True, env=self.env)

    def test_new_save_resume_validate(self):
        r1 = self.run_cli("new", "--task", "t1", "--project", "p1",
                          "--objective", "obj", "--harness", "dsh",
                          "--profile", "LONG_RUNNING_CAMPAIGN")
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
        r2 = self.run_cli("save", "--task", "t1", "--actions", "a1",
                          "--stop-reason", "budget_limit", "--next", "stop",
                          "--usage-json",
                          '{"cached_input_tokens": 90000000, "cost_usd": 36.5}')
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        rv = self.run_cli("validate", "t1")
        self.assertEqual(rv.returncode, 0, rv.stdout)
        ck = json.loads((Path(self._state) / "checkpoints" / "t1.json")
                        .read_text(encoding="utf-8"))
        self.assertEqual(ck["stop_reason"], "budget_limit")
        self.assertEqual(ck["budget_remaining"]["cost_usd"], 3.5)
        rr = self.run_cli("resume", "t1")
        resumed = json.loads(rr.stdout)
        self.assertTrue(resumed["resumable"])
        self.assertEqual(resumed["next_executable_action"], "stop")

    def test_resume_count_increments(self):
        self.run_cli("new", "--task", "t2", "--project", "p2",
                     "--objective", "o", "--harness", "codex",
                     "--profile", "AUTONOMOUS_STANDARD")
        self.run_cli("save", "--task", "t2", "--resume")
        ck = json.loads((Path(self._state) / "checkpoints" / "t2.json")
                        .read_text(encoding="utf-8"))
        self.assertEqual(ck["resume_count"], 1)

    def test_invalid_profile_rejected(self):
        r = self.run_cli("new", "--task", "t3", "--project", "p3",
                         "--objective", "o", "--harness", "dsh",
                         "--profile", "NOT_A_PROFILE")
        self.assertEqual(r.returncode, 1)


class TestUsageLedger(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ledger = Path(self._tmp.name) / "usage.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_append_query_cost_per_progress_runaway(self):
        usage_ledger.append_record({"kind": "usage", "task_id": "x",
                                    "project_id": "p", "calls_delta": 5,
                                    "input_tokens": 200, "cached_input_tokens": 100,
                                    "cost_usd_est": 2.0,
                                    "progress_events": 2}, path=self.ledger)
        usage_ledger.append_record({"kind": "usage", "task_id": "x",
                                    "project_id": "p", "calls_delta": 3,
                                    "cost_usd_est": 1.0, "progress_events": 0},
                                   path=self.ledger)
        records = usage_ledger.read_records(self.ledger)
        self.assertEqual(len(records), 2)
        task = usage_ledger.summarize_task(records, "x")
        self.assertEqual(task["provider_calls"], 8)
        self.assertEqual(task["cost_usd_est"], 3.0)
        self.assertEqual(usage_ledger.cost_per_progress(task), 3.0 / 2)
        self.assertEqual(usage_ledger.runaway_check(task, {}), [])

    def test_invalid_record_rejected(self):
        with self.assertRaises(ValueError):
            usage_ledger.append_record({"kind": "bogus", "task_id": "", "project_id": ""},
                                       path=self.ledger)

    def test_cached_ratio_runaway(self):
        usage_ledger.append_record({"kind": "usage", "task_id": "r",
                                    "project_id": "p",
                                    "cached_input_tokens": 900, "input_tokens": 100},
                                   path=self.ledger)
        records = usage_ledger.read_records(self.ledger)
        task = usage_ledger.summarize_task(records, "r")
        hits = usage_ledger.runaway_check(task, {})
        self.assertIn("cached_ratio_high_no_compact", hits)


class TestCounterexampleArithmetic(unittest.TestCase):
    INCIDENT_TURNS = 534
    INCIDENT_CALLS = 674
    INCIDENT_COST = 106.0
    INCIDENT_CACHED = 168_100_000

    def test_incident_impossible_under_campaign(self):
        eps = load_yaml(REG / "execution-profiles.yaml")
        lrc = eps["profiles"]["LONG_RUNNING_CAMPAIGN"]["budgets"]
        self.assertLess(lrc["session"]["agent_turns"], self.INCIDENT_TURNS)
        self.assertLess(lrc["task"]["provider_calls"], self.INCIDENT_CALLS)
        self.assertLess(lrc["task"]["cost_usd"], self.INCIDENT_COST)
        self.assertLess(lrc["task"]["cached_input_tokens"], self.INCIDENT_CACHED)

    def test_plugin_guard_math_mirror(self):
        # 与 dsh/autonomous-execution-governor 纯逻辑同一阈值（node 侧另有断言）
        eps = load_yaml(REG / "execution-profiles.yaml")
        lrc = eps["profiles"]["LONG_RUNNING_CAMPAIGN"]
        hard = lrc["loop_breaker"]["hard_window"]
        self.assertEqual(lrc["retard"]["repeated_repair_max"], 3)
        self.assertIsInstance(hard, int)
        self.assertGreater(hard, 0)


if __name__ == "__main__":
    unittest.main()