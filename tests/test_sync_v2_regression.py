"""test_sync_v2_regression.py — 17-Scenario Real Adversarial Sync V3 Regression Test Matrix."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from sync_v2.models import EvidenceLevel, OverallStatus, PlaneStatus, ResourceCategory, SyncPlane
from sync_v2.planes import (
    evaluate_agent_tools_source_plane,
    evaluate_backup_recovery_health,
    evaluate_canonical_state_plane,
    evaluate_deployment_mirror_plane,
    evaluate_dsh_config_plane,
    evaluate_dsh_plugins_plane,
    evaluate_durable_job_health,
    evaluate_mcp_plane,
    evaluate_model_discovery_safety_gate,
    evaluate_presets_plane,
    evaluate_runtime_plane,
    evaluate_session_continuity_health,
    evaluate_skills_plane,
)
from sync_v2.engine import SyncEngine


class FakeSyncRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.td = Path(self.temp_dir.name)
        self.home = self.td / ".dsh"
        self.home.mkdir(parents=True, exist_ok=True)
        self.db_path = self.home / "jobs.db"

        # Populate valid baseline home fixtures for plane checks
        prof_dir = self.home / "profiles" / "web"
        prof_dir.mkdir(parents=True, exist_ok=True)
        (prof_dir / "dsh-runtime-composition.json").write_text(json.dumps({"profileCombinationHash": "hash123"}), encoding="utf-8")
        (prof_dir / "cordis.patch.yml").write_text(
            "# AIC DSH RUNTIME COMPOSITION BEGIN\n- id: include:token-meter-pressure-guard\n# AIC DSH RUNTIME COMPOSITION END\n",
            encoding="utf-8"
        )
        (self.home / "settings.yaml").write_text("agent-default-model:\n  model: deepseek-chat\n", encoding="utf-8")

        # Presets baseline
        preset_dir = self.home / ".agent-presets" / "cc"
        preset_dir.mkdir(parents=True, exist_ok=True)
        (preset_dir / "agent.cordis.yml").write_text("agent:\n  profile: cc\n", encoding="utf-8")

        # Skills baseline
        (self.home / "skills").mkdir(parents=True, exist_ok=True)

        # Storage baseline
        (self.home / "storages").mkdir(parents=True, exist_ok=True)
        (self.home / "storages" / "workspace.json").write_text("{}", encoding="utf-8")

        # Backup baseline
        backup_dir = self.home / "backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        (backup_dir / "backup_20260101.tar").write_text("valid backup data", encoding="utf-8")

        self.contract = {
            "runtime_composition": {
                "managed_rows": {
                    "plugins": [
                        {
                            "id": "include:token-meter-pressure-guard",
                            "source_relative": "dsh/context-pressure-guard/token-meter",
                            "plugin_directory": "dsh-token-meter-pressure-guard",
                            "entry_relative": "lib/index.js",
                        }
                    ]
                }
            }
        }

        self.engine = SyncEngine(home=self.home, repo_root=REPO, db_path=self.db_path)

    def tearDown(self) -> None:
        import gc
        gc.collect()
        self.temp_dir.cleanup()

    # 1. Skills missing -> repair -> REPAIRED / IN_SYNC
    def test_01_skills_missing_repaired(self) -> None:
        res = evaluate_skills_plane(self.home, REPO, repair=True)
        self.assertIn(res.status, (PlaneStatus.IN_SYNC, PlaneStatus.REPAIRED))
        self.assertGreater(len(res.details.get("verified", [])), 0)

    # 2. Skills stale content and stale managed extra detected and repaired, user extra preserved
    def test_02_skills_stale_content_detected_and_repaired(self) -> None:
        # Create an intentionally corrupted/stale installed skill with a stale extra file
        skill_dst = self.home / "skills" / "find-session" / "SKILL.md"
        skill_dst.parent.mkdir(parents=True, exist_ok=True)
        skill_dst.write_text("corrupted stale content", encoding="utf-8")
        stale_file = self.home / "skills" / "find-session" / "old_script.py"
        stale_file.write_text("# legacy deleted script\n", encoding="utf-8")

        # Create a separate user-owned skill folder that is not in skills.json
        user_extra = self.home / "skills" / "my_custom_notes" / "notes.txt"
        user_extra.parent.mkdir(parents=True, exist_ok=True)
        user_extra.write_text("user personal notes\n", encoding="utf-8")

        # Check with repair=True -> should fix stale content, remove stale_file, and preserve user_extra
        res = evaluate_skills_plane(self.home, REPO, repair=True)
        self.assertIn(res.status, (PlaneStatus.IN_SYNC, PlaneStatus.REPAIRED))
        self.assertIn("find-session", res.details.get("verified", []))

        # Verify content was restored to source
        source_skill = REPO / "skills" / "find-session" / "SKILL.md"
        if source_skill.is_file():
            self.assertEqual(skill_dst.read_text(encoding="utf-8"), source_skill.read_text(encoding="utf-8"))

        # Verify stale managed extra file was removed from the managed package
        self.assertFalse(stale_file.exists(), "Stale managed extra file must be removed on repair")

        # Verify separate user-owned extra directory was preserved
        self.assertTrue(user_extra.is_file(), "User custom extra package must be preserved")

    # 3. Skills stale without repair returns PARTIAL
    def test_03_skills_stale_without_repair_returns_partial(self) -> None:
        skill_dst = self.home / "skills" / "find-session" / "SKILL.md"
        skill_dst.parent.mkdir(parents=True, exist_ok=True)
        skill_dst.write_text("corrupted stale content", encoding="utf-8")

        res = evaluate_skills_plane(self.home, REPO, repair=False)
        self.assertEqual(res.status, PlaneStatus.PARTIAL)
        self.assertIn("find-session", res.details.get("stale", []))

    # 4. Plugins missing entry returns PARTIAL
    def test_04_plugins_missing_entry_returns_partial(self) -> None:
        # Plugins directory is empty
        res = evaluate_dsh_plugins_plane(self.home, REPO, self.contract, repair=False)
        self.assertEqual(res.status, PlaneStatus.PARTIAL)
        self.assertIn("include:token-meter-pressure-guard", res.details.get("missing", []))

    # 5. Plugins stale hash detected
    def test_05_plugins_stale_hash_detected(self) -> None:
        plugin_dst = self.home / "profiles" / "web" / "plugins" / "dsh-token-meter-pressure-guard" / "lib" / "index.js"
        plugin_dst.parent.mkdir(parents=True, exist_ok=True)
        plugin_dst.write_text("// stale old build\n", encoding="utf-8")

        res = evaluate_dsh_plugins_plane(self.home, REPO, self.contract, repair=False)
        self.assertEqual(res.status, PlaneStatus.PARTIAL)
        self.assertIn("include:token-meter-pressure-guard", res.details.get("stale", []))

    # 6. MCP source missing returns REVIEW_REQUIRED
    def test_06_mcp_source_missing_returns_review(self) -> None:
        with mock.patch("pathlib.Path.is_file", return_value=False):
            res = evaluate_mcp_plane(self.home, REPO, self.contract)
            self.assertEqual(res.status, PlaneStatus.REVIEW_REQUIRED)
            self.assertIn("源码缺失", res.summary)

    # 7. MCP syntax error returns REVIEW_REQUIRED
    def test_07_mcp_syntax_error_returns_review(self) -> None:
        with mock.patch("subprocess.run") as mock_proc:
            mock_proc.return_value = mock.Mock(returncode=1, stderr=b"SyntaxError")
            res = evaluate_mcp_plane(self.home, REPO, self.contract)
            self.assertEqual(res.status, PlaneStatus.REVIEW_REQUIRED)
            self.assertIn("编译失败", res.summary)

    # 8. Presets missing auto repaired
    def test_08_presets_missing_auto_repaired(self) -> None:
        preset_file = self.home / ".agent-presets" / "cc" / "agent.cordis.yml"
        if preset_file.is_file():
            preset_file.unlink()

        res = evaluate_presets_plane(self.home, REPO, self.contract, repair=True)
        self.assertEqual(res.status, PlaneStatus.IN_SYNC)
        self.assertTrue(preset_file.is_file())

    # 9. Presets corrupt YAML returns REVIEW_REQUIRED
    def test_09_presets_corrupt_yaml_returns_review(self) -> None:
        preset_file = self.home / ".agent-presets" / "cc" / "agent.cordis.yml"
        preset_file.write_text("invalid: [unclosed yaml", encoding="utf-8")

        res = evaluate_presets_plane(self.home, REPO, self.contract, repair=False)
        self.assertEqual(res.status, PlaneStatus.REVIEW_REQUIRED)
        self.assertIn("损坏", res.summary)

    # 10. Config missing files returns REVIEW_REQUIRED
    def test_10_config_missing_files_returns_review(self) -> None:
        settings_file = self.home / "settings.yaml"
        settings_file.unlink()

        res = evaluate_dsh_config_plane(self.home, self.contract)
        self.assertEqual(res.status, PlaneStatus.REVIEW_REQUIRED)
        self.assertIn("缺失", res.summary)

    # 11. Config managed block verified
    def test_11_config_managed_block_verified(self) -> None:
        res = evaluate_dsh_config_plane(self.home, self.contract)
        self.assertEqual(res.status, PlaneStatus.IN_SYNC)
        self.assertGreater(res.details.get("managed_fields_total", 0), 0)

    # 12. Runtime stale active process triggers restart
    def test_12_runtime_stale_active_process_triggers_restart(self) -> None:
        active_proc = {
            "pid": 12345,
            "commandLine": "node.exe bin.js web",
            "startTimeEpoch": 100.0,  # Far in the past (before manifest mtime)
        }
        res = evaluate_runtime_plane(self.home, self.contract, active_proc)
        self.assertEqual(res.status, PlaneStatus.PARTIAL_RESTART_REQUIRED)
        self.assertIn("待重启生效", res.summary)

    # 13. Durable Job corrupt DB returns HEALTH_FAILED (never swallowed!)
    def test_13_durable_job_corrupt_db_returns_health_failed(self) -> None:
        # Write corrupted garbage to jobs.db
        self.db_path.write_text("not a sqlite database garbage content", encoding="utf-8")

        res = evaluate_durable_job_health(self.db_path)
        self.assertEqual(res.status, PlaneStatus.HEALTH_FAILED)
        self.assertIn("异常", res.summary)

    # 14. Session Continuity unattached roots detected
    def test_14_session_continuity_unattached_roots_detected(self) -> None:
        # Create an unattached session directory
        sess_dir = self.home / "sessions" / "session-orphan-999"
        sess_dir.mkdir(parents=True, exist_ok=True)
        (sess_dir / "session.jsonl.zstd").write_text("data", encoding="utf-8")

        res = evaluate_session_continuity_health(self.home)
        self.assertEqual(res.status, PlaneStatus.HEALTH_WARNING)
        self.assertIn("未挂载根会话", res.warnings[0])

    # 15. Backup corrupt 0-byte file returns HEALTH_FAILED
    def test_15_backup_corrupt_empty_file_returns_health_failed(self) -> None:
        backup_file = self.home / "backup" / "backup_corrupted.tar"
        backup_file.write_bytes(b"")  # 0 bytes

        res = evaluate_backup_recovery_health(self.home)
        self.assertEqual(res.status, PlaneStatus.HEALTH_FAILED)
        self.assertIn("0 字节", res.summary)

    # 16. Developer dirty workspace does not block production
    def test_16_developer_dirty_does_not_block_production(self) -> None:
        local_fixture = REPO / "tmp_dev_dirty_regression_fixture.txt"
        try:
            local_fixture.write_text("dev experiment", encoding="utf-8")
            receipt, _ = self.engine.run(check_only=True)
            self.assertIn(receipt.overall, (OverallStatus.PASS, OverallStatus.PASS_NO_CHANGE, OverallStatus.PARTIAL_RESTART_REQUIRED, OverallStatus.PARTIAL))
            self.assertTrue(receipt.metadata.get("developer_workspace_dirty"))
        finally:
            if local_fixture.is_file():
                local_fixture.unlink()

    # 17. Repeated sync idempotence
    def test_17_repeated_sync_idempotence(self) -> None:
        r1, _ = self.engine.run(check_only=True)
        r2, _ = self.engine.run(check_only=True)
        self.assertEqual(r1.overall, r2.overall)


if __name__ == "__main__":
    unittest.main()
