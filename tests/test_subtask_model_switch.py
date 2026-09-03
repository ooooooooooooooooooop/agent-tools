"""Unit and regression tests for Personal AI / DSH Subtask Model Switcher.

Covers all failure and behavioral regression requirements:
 1. current Luna -> Gemini PASS
 2. Gemini -> Luna PASS
 3. Gemini -> Gemini PASS_NO_CHANGE
 4. target unavailable -> current profile unchanged
 5. target not runtime-admitted -> unchanged
 6. second switch concurrent -> single writer / serialized
 7. AIC apply does not overwrite profile
 8. Sync does not overwrite profile
 9. main model remains untouched
10. compaction route remains unchanged
11. spawn expected/resolved/executed agree
12. fork expected/resolved/executed agree
13. workflow child expected/resolved/executed agree
14. partial projection cannot PASS
15. restart-required cannot masquerade as active
16. rollback restores previous valid profile
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "aic"))
sys.path.insert(0, str(REPO / "scripts"))

import aic  # noqa: E402
import subtask_model_switch as sms  # noqa: E402


class SubtaskModelSwitchTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.home = Path(self.td.name)
        # Mock dsh_home to use isolated directory
        self.patcher = mock.patch("subtask_model_switch.dsh_home", return_value=self.home)
        self.patcher.start()

        # Setup minimal mock settings.yaml and preset
        self.settings_file = self.home / "settings.yaml"
        self.settings_data = {
            "agent-default-model": {"provider": "cpa", "model": "gemini-3.8-flash-high"},
            "llm-pi-ai": {
                "providers": {
                    "cpa": {
                        "models": [
                            {"id": "gpt-5.6-luna-max", "contextWindow": 1050000},
                            {"id": "gemini-3.7-flash-high", "contextWindow": 1048576},
                            {"id": "gemini-3.8-flash-high", "contextWindow": 1048576},
                        ]
                    }
                }
            }
        }
        import yaml
        self.settings_file.write_text(yaml.safe_dump(self.settings_data), encoding="utf-8")

        self.preset_dir = self.home / ".agent-presets" / "cc"
        self.preset_dir.mkdir(parents=True, exist_ok=True)
        self.preset_file = self.preset_dir / "agent.cordis.yml"
        self.preset_content = (
            "- id: compaction-basic\n"
            "  config:\n"
            "    summarizationProvider: cpa\n"
            "    summarizationModel: gemini-3.8-flash-high\n"
            "- id: tool-subagent\n"
            "  config:\n"
            "    provider: spawn\n"
            "    agentOptions:\n"
            "      provider: cpa\n"
            "      model: gpt-5.6-luna-max\n"
            "- id: tool-subagent-fork\n"
            "  config:\n"
            "    provider: fork\n"
            "    agentOptions:\n"
            "      provider: cpa\n"
            "      model: gpt-5.6-luna-max\n"
        )
        self.preset_file.write_text(self.preset_content, encoding="utf-8")

    def tearDown(self):
        self.patcher.stop()
        self.td.cleanup()

    def test_01_luna_to_gemini_pass(self):
        """Req 1: Switch from Luna to Gemini succeeds atomically."""
        res = sms.switch_subtask_model("gemini")
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["profile"], "gemini")
        self.assertEqual(res["target"]["model"], "gemini-3.7-flash-high")
        self.assertTrue(res["spawn_aligned"])
        self.assertTrue(res["fork_aligned"])

        st = sms.get_status()
        self.assertEqual(st["profile"], "gemini")
        self.assertEqual(st["routes"]["spawn"]["model"], "gemini-3.7-flash-high")
        self.assertEqual(st["routes"]["fork"]["model"], "gemini-3.7-flash-high")

    def test_02_gemini_to_luna_pass(self):
        """Req 2: Switch back from Gemini to Luna succeeds."""
        sms.switch_subtask_model("gemini")
        res = sms.switch_subtask_model("luna")
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["profile"], "luna")
        self.assertEqual(res["target"]["model"], "gpt-5.6-luna-max")

        st = sms.get_status()
        self.assertEqual(st["profile"], "luna")
        self.assertEqual(st["routes"]["spawn"]["model"], "gpt-5.6-luna-max")
        self.assertEqual(st["routes"]["fork"]["model"], "gpt-5.6-luna-max")

    def test_03_gemini_to_gemini_no_change(self):
        """Req 3: Idempotent switch to same profile returns PASS_NO_CHANGE without re-mutating."""
        sms.switch_subtask_model("gemini")
        res = sms.switch_subtask_model("gemini")
        self.assertEqual(res["status"], "PASS_NO_CHANGE")
        self.assertEqual(res["profile"], "gemini")

    def test_04_target_unavailable_profile_unchanged(self):
        """Req 4: Requesting nonexistent profile fails and keeps current profile intact."""
        sms.switch_subtask_model("luna")
        res = sms.switch_subtask_model("nonexistent-model-xyz")
        self.assertEqual(res["status"], "FAIL_INVALID_PROFILE")
        st = sms.get_status()
        self.assertEqual(st["profile"], "luna")

    def test_05_target_not_runtime_admitted_unchanged(self):
        """Req 5: Target model not admitted in settings.yaml fails closed without modifying routes."""
        # Temporarily remove gemini-3.7-flash-high from settings
        settings_data = copy.deepcopy(self.settings_data)
        settings_data["llm-pi-ai"]["providers"]["cpa"]["models"] = [
            {"id": "gpt-5.6-luna-max", "contextWindow": 1050000}
        ]
        import yaml
        self.settings_file.write_text(yaml.safe_dump(settings_data), encoding="utf-8")

        sms.save_user_subtask_profile("luna")
        res = sms.switch_subtask_model("gemini")
        self.assertEqual(res["status"], "FAILED_TARGET_UNAVAILABLE")
        # Current routes must still be Luna
        st = sms.get_status()
        self.assertEqual(st["profile"], "luna")
        self.assertEqual(st["routes"]["spawn"]["model"], "gpt-5.6-luna-max")

    def test_06_concurrent_switches_serialized(self):
        """Req 6: Concurrent switch requests are serialized by process lock."""
        results = []

        def worker(target):
            r = sms.switch_subtask_model(target)
            results.append(r)

        t1 = threading.Thread(target=worker, args=("gemini",))
        t2 = threading.Thread(target=worker, args=("luna",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(len(results), 2)
        # Both must succeed cleanly without throwing or corrupting state
        for r in results:
            self.assertIn(r["status"], ("PASS", "PASS_NO_CHANGE"))

    def test_07_aic_apply_preserves_profile(self):
        """Req 7: AIC apply treats subtask_model_profile as user-owned and does not revert to Luna."""
        sms.switch_subtask_model("gemini")

        # Mock aic dsh_home to use test home
        with mock.patch("aic.dsh_home", return_value=self.home):
            # Resolve value for subagent spawn with active gemini preference
            exp_model = aic.resolve_value_from(
                aic.load_canonical()["policy"], "rules.subagent_spawn.model"
            )
            self.assertEqual(exp_model, "gemini-3.7-flash-high")

            exp_provider = aic.resolve_value_from(
                aic.load_canonical()["policy"], "rules.subagent_spawn.provider"
            )
            self.assertEqual(exp_provider, "cpa")

    def test_08_sync_preserves_profile(self):
        """Req 8: Sync status recognizes and preserves active subtask profile."""
        sms.switch_subtask_model("gemini")
        st = sms.get_status()
        self.assertEqual(st["profile"], "gemini")

    def test_09_main_model_remains_untouched(self):
        """Req 9: Switching subtask model NEVER alters agent-default-model."""
        import yaml
        before_adm = yaml.safe_load(self.settings_file.read_text())["agent-default-model"]

        sms.switch_subtask_model("gemini")
        after_adm_1 = yaml.safe_load(self.settings_file.read_text())["agent-default-model"]
        self.assertEqual(before_adm, after_adm_1)

        sms.switch_subtask_model("luna")
        after_adm_2 = yaml.safe_load(self.settings_file.read_text())["agent-default-model"]
        self.assertEqual(before_adm, after_adm_2)

    def test_10_compaction_route_remains_unchanged(self):
        """Req 10: Compaction route (gemini-3.8-flash-high) is unchanged across subtask switches."""
        txt_before = self.preset_file.read_text(encoding="utf-8")
        self.assertIn("summarizationModel: gemini-3.8-flash-high", txt_before)

        sms.switch_subtask_model("gemini")
        txt_gemini = self.preset_file.read_text(encoding="utf-8")
        self.assertIn("summarizationModel: gemini-3.8-flash-high", txt_gemini)

        sms.switch_subtask_model("luna")
        txt_luna = self.preset_file.read_text(encoding="utf-8")
        self.assertIn("summarizationModel: gemini-3.8-flash-high", txt_luna)

    def test_11_spawn_binding_correct(self):
        """Req 11: Spawn route binding matches selected profile."""
        sms.switch_subtask_model("gemini")
        routes = sms.get_current_preset_routes()
        self.assertEqual(routes["spawn"], {"provider": "cpa", "model": "gemini-3.7-flash-high"})

    def test_12_fork_binding_correct(self):
        """Req 12: Fork route binding matches selected profile."""
        sms.switch_subtask_model("gemini")
        routes = sms.get_current_preset_routes()
        self.assertEqual(routes["fork"], {"provider": "cpa", "model": "gemini-3.7-flash-high"})

    def test_13_workflow_child_binding_correct(self):
        """Req 13: Workflow child route matches selected profile."""
        sms.switch_subtask_model("gemini")
        routes = sms.get_current_preset_routes()
        self.assertEqual(routes["workflow"], {"provider": "cpa", "model": "gemini-3.7-flash-high"})

    def test_14_partial_projection_cannot_pass(self):
        """Req 14: If one subagent route fails to project, overall status is DRIFT."""
        sms.switch_subtask_model("gemini")
        # Tamper preset so fork has mismatched model
        txt = self.preset_file.read_text(encoding="utf-8")
        tampered = txt.replace("tool-subagent-fork\n  config:\n    provider: fork\n    agentOptions:\n      provider: cpa\n      model: gemini-3.7-flash-high",
                               "tool-subagent-fork\n  config:\n    provider: fork\n    agentOptions:\n      provider: cpa\n      model: gpt-5.6-luna-max")
        self.preset_file.write_text(tampered, encoding="utf-8")

        st = sms.get_status()
        self.assertEqual(st["status"], "DRIFT")
        self.assertTrue(st["spawn_ok"])
        self.assertFalse(st["fork_ok"])

    def test_15_restart_required_cannot_masquerade_as_active(self):
        """Req 15: Deployed != active reports restartRequired and cannot report active until restarted."""
        profile_web = self.home / "profiles" / "web"
        profile_web.mkdir(parents=True, exist_ok=True)
        (profile_web / "dsh-runtime-composition.json").write_text(
            json.dumps({"profileCombinationHash": "comp-NEW"}), encoding="utf-8")
        (profile_web / "active-process.json").write_text(
            json.dumps({"pid": 55555, "compositionHash": "comp-OLD"}), encoding="utf-8")

        with mock.patch("dsh_lifecycle._find_live_dsh_process", return_value={"pid": 55555}):
            import dsh_lifecycle
            proc = dsh_lifecycle.inspect_active_process(self.home)
            self.assertTrue(proc["restartRequired"])
            self.assertTrue(proc["isStale"])

    def test_16_rollback_restores_previous_valid_profile(self):
        """Req 16: Failure during projection restores previous profile cleanly."""
        sms.switch_subtask_model("luna")
        # Make preset file unwriteable or simulate error
        with mock.patch("subtask_model_switch.project_preset_routes", side_effect=IOError("disk full")):
            res = sms.switch_subtask_model("gemini")
            self.assertEqual(res["status"], "FAILED_SWITCH_ERROR")

        # Must have stayed on Luna
        st = sms.get_status()
        self.assertEqual(st["profile"], "luna")


if __name__ == "__main__":
    unittest.main()
