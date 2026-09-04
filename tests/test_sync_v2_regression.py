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

from sync_v2.models import EvidenceLevel, OverallStatus, PlaneStatus, ResourceCategory, ResourceRecord, SyncPlane
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
from sync_v2.receipt import render_human_receipt


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
        (self.home / "storages" / "session_projcache.json").write_text(
            json.dumps({"unit": {"name": "session_projcache", "version": 1}, "tables": {"sessions": {}}}),
            encoding="utf-8")

        # Machine backup-policy baseline (temp state repo -> temp backup_root)
        self.state_repo = self.td / "personal-ai-state"
        (self.state_repo / "sync").mkdir(parents=True, exist_ok=True)
        self.backup_root = self.td / "ai-backup"
        (self.backup_root / "ledger").mkdir(parents=True, exist_ok=True)
        (self.state_repo / "sync" / "this-device.yaml").write_text(
            "device_id: TESTDEVICE\n"
            f"backup_root: {self.backup_root}\n"
            "rpo_targets_hours:\n  sessions: 26\n  broker: 26\n  configs: 168\n  jobs: 26\n  repos: 26\n",
            encoding="utf-8")
        self._write_ledger_verified()

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

        self.engine = SyncEngine(home=self.home, repo_root=REPO, db_path=self.db_path,
                                 state_repo=self.state_repo)

    # -- fixture helpers -------------------------------------------------
    def _write_ledger_verified(self, stale: bool = False) -> None:
        """Write a fully healthy ledger (fresh or stale) + matching artifacts."""
        import datetime as dt
        base = dt.datetime.now().astimezone() - (dt.timedelta(hours=96) if stale
                                                 else dt.timedelta(hours=1))
        ts = base.isoformat(timespec="seconds")
        rows = [
            {"job": "backup_sessions", "dataset": "sessions", "finished_at": ts,
             "status": "ok", "integrity_status": "verified"},
            {"job": "backup_broker", "dataset": "broker:broker", "finished_at": ts,
             "status": "ok", "integrity_status": "verified"},
            {"job": "backup_configs", "dataset": "configs", "finished_at": ts,
             "status": "ok", "integrity_status": "verified"},
            {"job": "backup_jobs", "dataset": "jobs", "finished_at": ts,
             "status": "ok", "integrity_status": "verified"},
            {"job": "restore_check", "dataset": "all", "finished_at": ts,
             "status": "ok", "integrity_status": "verified"},
        ]
        led = self.backup_root / "ledger" / "runs.jsonl"
        led.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        for sub, name, payload in (("sessions", "daily-x", None),
                                   ("configs", "daily-x", None),
                                   ("broker", "broker-x.sqlite", b"sqlite"),
                                   ("jobs", "jobs-x.sqlite", b"sqlite")):
            d = self.backup_root / sub / name
            if payload is None:
                d.mkdir(parents=True, exist_ok=True)
            else:
                d.parent.mkdir(parents=True, exist_ok=True)
                d.write_bytes(payload)

    def _mk_root(self, sid: str, proj_title=None, proj_created=None, events=None):
        """Create a physical root session + projcache entry.

        events: None -> write undecodable garbage; list -> zstd-encoded JSONL.
        """
        d = self.home / "sessions" / sid
        d.mkdir(parents=True, exist_ok=True)
        f = d / "session.jsonl.zstd"
        if events is None:
            f.write_bytes(b"not-a-valid-zstd-stream")
        else:
            import zstandard as zstd
            raw = "\n".join(json.dumps(e) for e in events).encode()
            f.write_bytes(zstd.ZstdCompressor().compress(raw))
        pc_path = self.home / "storages" / "session_projcache.json"
        pc = json.loads(pc_path.read_text(encoding="utf-8"))
        entry = {"identity": {"createdAt": proj_created or 1788282000000,
                              "cwd": str(self.td)},
                 "rows": {}}
        if proj_title is not None:
            entry["rows"]["title"] = {"ver": 1, "seq": 1, "val": proj_title}
        pc["tables"]["sessions"][sid] = entry
        pc_path.write_text(json.dumps(pc), encoding="utf-8")
        return f

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

    # 14. Unreadable/garbage unattached root -> UNKNOWN -> HEALTH_WARNING
    def test_14_session_unreadable_root_is_unknown_warning(self) -> None:
        self._mk_root("session-orphan-999")  # garbage file, no projcache title

        res = evaluate_session_continuity_health(self.home)
        self.assertEqual(res.status, PlaneStatus.HEALTH_WARNING)
        self.assertEqual(res.details["unknown_unattached_count"], 1)
        self.assertEqual(res.details["unexpected_unattached_count"], 0)

    # 15. Backup policy unreachable -> HEALTH_FAILED
    def test_15_backup_policy_missing_returns_health_failed(self) -> None:
        missing = self.td / "no-such-state-repo"
        res = evaluate_backup_recovery_health(self.home, state_repo=missing)
        self.assertEqual(res.status, PlaneStatus.HEALTH_FAILED)
        self.assertIn("BACKUP_POLICY_UNREACHABLE", res.blockers[0])

    # 15b. Fresh verified ledger + artifacts -> HEALTHY with decomposed signals
    def test_15b_backup_fresh_ledger_is_healthy(self) -> None:
        res = evaluate_backup_recovery_health(self.home, state_repo=self.state_repo)
        self.assertEqual(res.status, PlaneStatus.HEALTHY)
        self.assertEqual(res.details["BACKUP_FRESHNESS_STATE"], "PASS")
        self.assertEqual(res.details["BACKUP_INTEGRITY_STATE"], "PASS")
        self.assertEqual(res.details["RESTORE_EVIDENCE"]["status"], "PASS")
        self.assertIn(res.details["FULL_DR_READINESS"], ("INCOMPLETE", "MISSING"))

    # 15c. Stale ledger beyond RPO -> HEALTH_WARNING
    def test_15c_backup_stale_ledger_warns(self) -> None:
        self._write_ledger_verified(stale=True)
        res = evaluate_backup_recovery_health(self.home, state_repo=self.state_repo)
        self.assertEqual(res.status, PlaneStatus.HEALTH_WARNING)
        self.assertEqual(res.details["BACKUP_FRESHNESS_STATE"], "WARNING")

    # 15d. Missing artifact dir -> INTEGRITY warning
    def test_15d_backup_missing_artifact_warns(self) -> None:
        import shutil
        shutil.rmtree(self.backup_root / "jobs")
        res = evaluate_backup_recovery_health(self.home, state_repo=self.state_repo)
        self.assertEqual(res.status, PlaneStatus.HEALTH_WARNING)
        self.assertEqual(res.details["BACKUP_INTEGRITY_STATE"], "WARNING")
        self.assertFalse(res.details["BACKUP_INTEGRITY"]["jobs"])

    # 15e. Failed latest restore_check -> RESTORE_EVIDENCE warning
    def test_15e_failed_restore_check_warns(self) -> None:
        led = self.backup_root / "ledger" / "runs.jsonl"
        rows = [json.loads(l) for l in led.read_text(encoding="utf-8").splitlines() if l.strip()]
        rows.append({"job": "restore_check", "dataset": "all",
                     "finished_at": "2999-01-01T00:00:00+08:00",
                     "status": "error", "integrity_status": "failed"})
        led.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        res = evaluate_backup_recovery_health(self.home, state_repo=self.state_repo)
        self.assertEqual(res.status, PlaneStatus.HEALTH_WARNING)
        self.assertEqual(res.details["RESTORE_EVIDENCE"]["status"], "WARNING")

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


class SessionResidueSemanticsTests(unittest.TestCase):
    """Session health semantics: EXPECTED / UNEXPECTED / UNKNOWN + identity."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.td = Path(self.temp_dir.name)
        self.home = self.td / ".dsh"
        (self.home / "sessions").mkdir(parents=True, exist_ok=True)
        (self.home / "storages").mkdir(parents=True, exist_ok=True)
        (self.home / "storages" / "workspace.json").write_text(
            json.dumps({"unit": {"name": "workspace", "version": 2},
                        "global": {"initialized": True, "workspaceIds": ["ws1"],
                                   "archivedSessionIds": []},
                        "tables": {"workspaces": {"ws1": {
                            "path": str(self.td / "proj"), "title": "proj",
                            "sessionIds": ["session-attached-1"]}}}}), encoding="utf-8")
        (self.home / "storages" / "session_projcache.json").write_text(
            json.dumps({"tables": {"sessions": {}}}), encoding="utf-8")
        self._batch_ms = 1788282000000  # shared fixture creation time
        self._mk("session-attached-1", title="real attached user session",
                 events=self._header_events(text="real work"))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _mk(self, sid, title=None, events=None, created=None):
        d = self.home / "sessions" / sid
        d.mkdir(parents=True, exist_ok=True)
        f = d / "session.jsonl.zstd"
        if events is None:
            f.write_bytes(b"garbage")
        else:
            import zstandard as zstd
            raw = ("\n".join(json.dumps(e) for e in events) + "\n").encode()
            f.write_bytes(zstd.ZstdCompressor().compress(raw))
        pc = json.loads((self.home / "storages" / "session_projcache.json")
                        .read_text(encoding="utf-8"))
        entry = {"identity": {"createdAt": created or self._batch_ms,
                              "cwd": str(self.td / "proj")}, "rows": {}}
        if title is not None:
            entry["rows"]["title"] = {"ver": 1, "seq": 1, "val": title}
        pc["tables"]["sessions"][sid] = entry
        (self.home / "storages" / "session_projcache.json").write_text(
            json.dumps(pc), encoding="utf-8")

    def _header_events(self, text=None, tool=False):
        ev = [{"type": "session", "version": 0, "id": "x", "createdAt": self._batch_ms,
               "cwd": str(self.td / "proj"), "delegationDepth": 0, "agentPreset": "cc"},
              {"type": "permission/preset", "seq": 0, "time": self._batch_ms,
               "data": {"preset": "workspace-write"}}]
        if text is not None:
            ev.append({"type": "user/message", "seq": 1, "time": self._batch_ms,
                       "data": {"content": [{"type": "text", "text": text}]},
                       "role": "user"})
            ev.append({"type": "turn/start", "seq": 2, "time": self._batch_ms,
                       "data": {"turn": 1}})
        if tool:
            ev.append({"type": "tool/call", "seq": 3, "time": self._batch_ms,
                       "data": {"name": "Bash"}})
        return ev

    def test_synthetic_fixture_is_expected(self) -> None:
        self._mk("session-fixture-1", title="Reply With Exactly OK",
                 events=self._header_events(text="Reply with exactly OK."))
        res = evaluate_session_continuity_health(self.home)
        self.assertEqual(res.status, PlaneStatus.HEALTHY)
        self.assertEqual(res.details["expected_unattached_count"], 1)
        self.assertEqual(res.details["unexpected_unattached_count"], 0)

    def test_empty_shell_correlated_with_fixture_is_expected(self) -> None:
        # Confirmed fixture first, then a header-only shell 3 minutes later.
        self._mk("session-fixture-1", title="Reply With Exactly OK",
                 events=self._header_events(text="Reply with exactly OK."))
        self._mk("session-shell-2", title=None,
                 events=self._header_events(), created=self._batch_ms + 3 * 60 * 1000)
        res = evaluate_session_continuity_health(self.home)
        self.assertEqual(res.status, PlaneStatus.HEALTHY)
        self.assertEqual(res.details["expected_unattached_count"], 2)

    def test_empty_shell_without_correlation_is_unknown(self) -> None:
        self._mk("session-shell-lonely", title=None, events=self._header_events(),
                 created=self._batch_ms + 24 * 60 * 60 * 1000)
        res = evaluate_session_continuity_health(self.home)
        self.assertEqual(res.status, PlaneStatus.HEALTH_WARNING)
        self.assertEqual(res.details["unknown_unattached_count"], 1)

    def test_real_user_content_root_is_unexpected(self) -> None:
        self._mk("session-realuser-1", title="My novel chapter draft",
                 events=self._header_events(text="帮我续写第三章"))
        res = evaluate_session_continuity_health(self.home)
        self.assertEqual(res.status, PlaneStatus.HEALTH_WARNING)
        self.assertEqual(res.details["unexpected_unattached_count"], 1)

    def test_real_user_root_with_tool_calls_is_unexpected(self) -> None:
        self._mk("session-realuser-2", title=None,
                 events=self._header_events(text="run the tests", tool=True))
        res = evaluate_session_continuity_health(self.home)
        self.assertEqual(res.status, PlaneStatus.HEALTH_WARNING)
        self.assertEqual(res.details["unexpected_unattached_count"], 1)

    def test_identity_mismatch_is_failed(self) -> None:
        # Physical session not present in the runtime index.
        d = self.home / "sessions" / "session-phys-only"
        d.mkdir(parents=True, exist_ok=True)
        (d / "session.jsonl.zstd").write_bytes(b"garbage")
        res = evaluate_session_continuity_health(self.home, runtime_enumerable_override={"session-attached-1"})
        self.assertEqual(res.status, PlaneStatus.HEALTH_FAILED)
        self.assertIn("PHYSICAL_RUNTIME_IDENTITY_MISMATCH", res.blockers)

    def test_attached_roots_not_flagged(self) -> None:
        self._mk("session-attached-1", title="real attached user session",
                 events=self._header_events(text="real work"))
        res = evaluate_session_continuity_health(self.home)
        self.assertEqual(res.status, PlaneStatus.HEALTHY)
        self.assertEqual(res.details["unexpected_unattached_count"], 0)
        self.assertEqual(res.details["attached_root_count"], 1)

    def test_injected_context_frames_do_not_count_as_user_content(self) -> None:
        # Harness injects <system-reminder> / runtime-context snapshots over the
        # user/message channel — they must not upgrade a synthetic fixture to
        # UNEXPECTED "real user content".
        ev = self._header_events(text="Reply with exactly OK.")
        ev.append({"type": "user/message", "seq": 3, "time": self._batch_ms,
                   "data": {"content": [{"type": "text",
                                         "text": "<system-reminder>\nworkspace instructions"}]},
                   "role": "user"})
        ev.append({"type": "user/message", "seq": 4, "time": self._batch_ms,
                   "data": {"content": [{"type": "text",
                                         "text": "Current runtime context. This snapshot supersedes"}]},
                   "role": "user"})
        self._mk("session-fixture-injected", title="Reply With Exactly OK", events=ev)
        res = evaluate_session_continuity_health(self.home)
        self.assertEqual(res.status, PlaneStatus.HEALTHY)
        self.assertEqual(res.details["expected_unattached_count"], 1)
        self.assertEqual(res.details["unexpected_unattached_count"], 0)

    def test_llm_retitled_fixture_still_expected(self) -> None:
        # Runtime projcache title is the LLM retitle; synthetic evidence lives
        # only in the first session/title event and the user prompt.
        ev = self._header_events(text="Reply with exactly OPENCODE_RECOVERY_SMOKE_OK")
        ev.append({"type": "session/title", "seq": 5, "time": self._batch_ms,
                   "data": {"title": "Reply with exactly OPENCODE_RECOVERY_SMO",
                            "source": {"kind": "llm"}}})
        self._mk("session-fixture-retitled", title="OpenCode recovery smoke test",
                 events=ev)
        res = evaluate_session_continuity_health(self.home)
        self.assertEqual(res.status, PlaneStatus.HEALTHY)
        self.assertEqual(res.details["expected_unattached_count"], 1)
        self.assertEqual(res.details["unknown_unattached_count"], 0)

    def test_shell_within_60min_of_fixture_is_expected(self) -> None:
        # Live drill batches span >15min; 60min window covers them.
        self._mk("session-fixture-1", title="Reply With Exactly OK",
                 events=self._header_events(text="Reply with exactly OK."))
        self._mk("session-shell-2", title=None,
                 events=self._header_events(), created=self._batch_ms + 45 * 60 * 1000)
        res = evaluate_session_continuity_health(self.home)
        self.assertEqual(res.status, PlaneStatus.HEALTHY)
        self.assertEqual(res.details["expected_unattached_count"], 2)
        self.assertEqual(res.details["unknown_unattached_count"], 0)

    def test_shell_correlates_with_event_only_fixture(self) -> None:
        # Fixture's proj title is an LLM retitle (not synthetic), so pass-1
        # windows are empty; the two-pass correlation must still classify the
        # header-only shell as EXPECTED_EMPTY_ABORTED.
        ev = self._header_events(text="Reply with exactly OPENCODE_RECOVERY_SMOKE_OK")
        ev.append({"type": "session/title", "seq": 5, "time": self._batch_ms,
                   "data": {"title": "Reply with exactly OPENCODE_RECOVERY_SMO",
                            "source": {"kind": "llm"}}})
        self._mk("session-fixture-retitled", title="OpenCode recovery smoke test",
                 events=ev)
        self._mk("session-shell-2", title=None,
                 events=self._header_events(), created=self._batch_ms + 20 * 60 * 1000)
        res = evaluate_session_continuity_health(self.home)
        self.assertEqual(res.status, PlaneStatus.HEALTHY)
        self.assertEqual(res.details["expected_unattached_count"], 2)
        self.assertEqual(res.details["unknown_unattached_count"], 0)


class SyncV3TruthAndHealthAdjudicationTests(unittest.TestCase):
    """Specific regression test matrix covering the 11 truth & health adjudication requirements:
    1. projcache sparse != session loss
    2. physical/runtime identity equality
    3. true physical/runtime mismatch
    4. child lineage semantics
    5. unexpected unattached root
    6. health warning affects overall
    7. health failure affects overall
    8. warning prevents '无需操作' false statement
    9. failure prevents '无需操作'
    10. jobs no backup -> warning
    11. jobs backup verified -> local backup health PASS
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.td = Path(self.temp_dir.name)
        self.home = self.td / ".dsh"
        self.home.mkdir(parents=True, exist_ok=True)
        self.db_path = self.home / "jobs.db"
        (self.home / "sessions").mkdir(parents=True, exist_ok=True)
        (self.home / "storages").mkdir(parents=True, exist_ok=True)
        (self.home / "storages" / "workspace.json").write_text(
            json.dumps({"unit": {"name": "workspace", "version": 2},
                        "global": {"initialized": True, "workspaceIds": ["ws1"], "archivedSessionIds": []},
                        "tables": {"workspaces": {"ws1": {"path": str(self.td / "repo"), "title": "repo", "sessionIds": []}}}}),
            encoding="utf-8",
        )
        (self.home / "storages" / "session_projcache.json").write_text(
            json.dumps({"tables": {"sessions": {}}}), encoding="utf-8"
        )

        # Baseline sync fixtures for engine runs
        prof_dir = self.home / "profiles" / "web"
        prof_dir.mkdir(parents=True, exist_ok=True)
        (prof_dir / "dsh-runtime-composition.json").write_text(json.dumps({"profileCombinationHash": "hash123"}), encoding="utf-8")
        (prof_dir / "cordis.patch.yml").write_text(
            "# AIC DSH RUNTIME COMPOSITION BEGIN\n- id: include:token-meter-pressure-guard\n# AIC DSH RUNTIME COMPOSITION END\n",
            encoding="utf-8"
        )
        (self.home / "settings.yaml").write_text("agent-default-model:\n  model: deepseek-chat\n", encoding="utf-8")
        preset_dir = self.home / ".agent-presets" / "cc"
        preset_dir.mkdir(parents=True, exist_ok=True)
        (preset_dir / "agent.cordis.yml").write_text("agent:\n  profile: cc\n", encoding="utf-8")
        (self.home / "skills").mkdir(parents=True, exist_ok=True)

        self.state_repo = self.td / "personal-ai-state"
        (self.state_repo / "sync").mkdir(parents=True, exist_ok=True)
        self.backup_root = self.td / "ai-backup"
        (self.backup_root / "ledger").mkdir(parents=True, exist_ok=True)
        (self.state_repo / "sync" / "this-device.yaml").write_text(
            f"device_id: TEST\nbackup_root: {self.backup_root}\n", encoding="utf-8"
        )
        self._write_verified_backup_ledger()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_verified_backup_ledger(self, include_jobs: bool = True) -> None:
        import datetime as dt
        base = dt.datetime.now().astimezone() - dt.timedelta(hours=1)
        ts = base.isoformat(timespec="seconds")
        rows = [
            {"job": "backup_sessions", "dataset": "sessions", "finished_at": ts,
             "status": "ok", "integrity_status": "verified"},
            {"job": "backup_broker", "dataset": "broker:broker", "finished_at": ts,
             "status": "ok", "integrity_status": "verified"},
            {"job": "backup_configs", "dataset": "configs", "finished_at": ts,
             "status": "ok", "integrity_status": "verified"},
            {"job": "backup_repos", "dataset": "repos", "finished_at": ts,
             "status": "ok", "integrity_status": "verified"},
            {"job": "restore_check", "dataset": "all", "finished_at": ts,
             "status": "ok", "integrity_status": "verified"},
        ]
        if include_jobs:
            rows.append({"job": "backup_jobs", "dataset": "jobs", "finished_at": ts,
                         "status": "ok", "integrity_status": "verified"})

        led = self.backup_root / "ledger" / "runs.jsonl"
        led.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

        for sub, name, payload in (("sessions", "daily-x", None),
                                   ("configs", "daily-x", None),
                                   ("broker", "broker-x.sqlite", b"sqlite")):
            d = self.backup_root / sub / name
            if payload is None:
                d.mkdir(parents=True, exist_ok=True)
            else:
                d.parent.mkdir(parents=True, exist_ok=True)
                d.write_bytes(payload)
        if include_jobs:
            d = self.backup_root / "jobs" / "jobs-x.sqlite"
            d.parent.mkdir(parents=True, exist_ok=True)
            d.write_bytes(b"sqlite")

    def _create_session_file(self, sid: str, events: list) -> Path:
        import zstandard as zstd
        s_dir = self.home / "sessions" / f"--test--/{sid}"
        s_dir.mkdir(parents=True, exist_ok=True)
        z_file = s_dir / "session.jsonl.zstd"
        raw = ("\n".join(json.dumps(e) for e in events) + "\n").encode("utf-8")
        z_file.write_bytes(zstd.ZstdCompressor().compress(raw))
        return z_file

    def _attach_sessions_to_workspace(self, sids: list) -> None:
        ws_file = self.home / "storages" / "workspace.json"
        data = json.loads(ws_file.read_text(encoding="utf-8"))
        data["tables"]["workspaces"]["ws1"]["sessionIds"] = sids
        ws_file.write_text(json.dumps(data), encoding="utf-8")

    def _set_projcache_entries(self, sids: list) -> None:
        pc_file = self.home / "storages" / "session_projcache.json"
        data = json.loads(pc_file.read_text(encoding="utf-8"))
        for sid in sids:
            data["tables"]["sessions"][sid] = {"identity": {"createdAt": 1000, "cwd": str(self.td / "repo")}, "rows": {}}
        pc_file.write_text(json.dumps(data), encoding="utf-8")

    def test_01_projcache_sparse_does_not_cause_session_loss(self) -> None:
        # 5 physical sessions on disk, all attached in workspace.json
        sids = [f"session-phys-{i}" for i in range(5)]
        for sid in sids:
            self._create_session_file(sid, [{"type": "session", "id": sid, "delegationDepth": 0}])
        self._attach_sessions_to_workspace(sids)
        # Only 1 in projcache (sparse cache!)
        self._set_projcache_entries([sids[0]])

        res = evaluate_session_continuity_health(self.home)
        self.assertEqual(res.details["physical_count"], 5)
        self.assertEqual(res.details["runtime_enumerable_count"], 5)
        self.assertEqual(res.details["projcache_count"], 1)
        self.assertFalse(res.details["projcache_used_as_runtime_truth"])
        self.assertTrue(res.details["identity_match"])
        self.assertFalse(res.details["session_data_loss"])
        self.assertEqual(res.details["session_continuity_status"], "PASS")
        self.assertEqual(res.status, PlaneStatus.HEALTHY)

    def test_02_physical_runtime_identity_equality(self) -> None:
        sids = [f"session-root-{i}" for i in range(3)]
        for sid in sids:
            self._create_session_file(sid, [{"type": "session", "id": sid, "delegationDepth": 0}])
        self._attach_sessions_to_workspace(sids)

        res = evaluate_session_continuity_health(self.home)
        self.assertEqual(res.details["physical_count"], 3)
        self.assertEqual(res.details["runtime_enumerable_count"], 3)
        self.assertTrue(res.details["identity_match"])
        self.assertEqual(res.details["physical_minus_runtime"], [])
        self.assertEqual(res.details["runtime_minus_physical"], [])
        self.assertEqual(res.details["session_continuity_status"], "PASS")

    def test_03_true_physical_runtime_mismatch(self) -> None:
        # Disk has session-1, but workspace attaches session-ghost which does not exist on disk
        self._create_session_file("session-1", [{"type": "session", "id": "session-1", "delegationDepth": 0}])
        self._attach_sessions_to_workspace(["session-1", "session-ghost-lost"])

        res = evaluate_session_continuity_health(self.home)
        self.assertEqual(res.status, PlaneStatus.HEALTH_FAILED)
        self.assertFalse(res.details["identity_match"])
        self.assertEqual(res.details["session_continuity_status"], "FAIL")
        self.assertIn("PHYSICAL_RUNTIME_IDENTITY_MISMATCH", res.blockers)
        self.assertTrue(res.details["session_data_loss"])
        self.assertIn("session-ghost-lost", res.details["runtime_minus_physical"])

    def test_04_child_lineage_semantics(self) -> None:
        # session-root is root (delegationDepth=0)
        self._create_session_file("session-root", [{"type": "session", "id": "session-root", "delegationDepth": 0}])
        # session-child is subagent (delegationDepth=1, parentSession=session-root, origin=subagent)
        self._create_session_file("session-child", [{
            "type": "session", "id": "session-child", "delegationDepth": 1,
            "parentSession": "session-root", "origin": "subagent"
        }])
        # Only session-root is attached to workspace; child is linked via parentSession lineage
        self._attach_sessions_to_workspace(["session-root"])

        res = evaluate_session_continuity_health(self.home)
        self.assertEqual(res.details["root_count"], 1)
        self.assertEqual(res.details["child_count"], 1)
        self.assertIn("session-child", res.details["PARENT_INDEXED_CHILD_IDS"])
        self.assertNotIn("session-child", res.details["UNEXPECTED_UNATTACHED_ROOT_IDS"])
        self.assertEqual(res.details["unexpected_unattached_count"], 0)
        self.assertEqual(res.status, PlaneStatus.HEALTHY)

    def test_05_unexpected_unattached_root(self) -> None:
        # Unattached root with real user text and tools
        events = [
            {"type": "session", "id": "session-orphan-real", "delegationDepth": 0},
            {"type": "user/message", "role": "user", "data": {"content": [{"type": "text", "text": "Deploy production database"}]}},
            {"type": "tool/call", "data": {"name": "Bash"}}
        ]
        self._create_session_file("session-orphan-real", events)
        # Empty workspace attachment
        self._attach_sessions_to_workspace([])

        res = evaluate_session_continuity_health(self.home)
        self.assertEqual(res.status, PlaneStatus.HEALTH_WARNING)
        self.assertEqual(res.details["unexpected_unattached_count"], 1)
        self.assertIn("session-orphan-real", res.details["UNEXPECTED_UNATTACHED_ROOT_IDS"])
        self.assertEqual(res.details["session_continuity_status"], "PASS")
        self.assertEqual(res.details["session_attachment_health"], "WARNING")

    def _mock_convergence_in_sync(self):
        mock_in_sync = ResourceRecord(
            resource_id="mock",
            plane=SyncPlane.MCP,
            category=ResourceCategory.CONVERGENCE_PLANE,
            status=PlaneStatus.IN_SYNC,
            symbol="✓",
            summary="mock in sync",
        )
        return (
            mock.patch("sync_v2.engine.evaluate_deployment_mirror_plane", return_value=mock_in_sync),
            mock.patch("sync_v2.engine.evaluate_dsh_config_plane", return_value=mock_in_sync),
            mock.patch("sync_v2.engine.evaluate_dsh_plugins_plane", return_value=mock_in_sync),
            mock.patch("sync_v2.engine.evaluate_skills_plane", return_value=mock_in_sync),
            mock.patch("sync_v2.engine._find_live_dsh_process", return_value=None),
        )

    def test_06_health_warning_affects_overall(self) -> None:
        # Create unattached unexpected root to trigger HEALTH_WARNING
        events = [
            {"type": "session", "id": "session-warn", "delegationDepth": 0},
            {"type": "user/message", "role": "user", "data": {"content": [{"type": "text", "text": "Deploy cluster"}]}}
        ]
        self._create_session_file("session-warn", events)
        engine = SyncEngine(home=self.home, repo_root=REPO, db_path=self.db_path, state_repo=self.state_repo)

        p1, p2, p3, p4, p5 = self._mock_convergence_in_sync()
        with p1, p2, p3, p4, p5:
            receipt, _ = engine.run(check_only=True)
        self.assertEqual(receipt.convergence_status, "IN_SYNC")
        self.assertEqual(receipt.health_status, "WARNING")
        self.assertEqual(receipt.overall, OverallStatus.PASS_WITH_HEALTH_WARNINGS)
        self.assertNotEqual(receipt.overall, OverallStatus.PASS_NO_CHANGE)

    def test_07_health_failure_affects_overall(self) -> None:
        # Create missing session in workspace to trigger HEALTH_FAILED in session continuity
        self._attach_sessions_to_workspace(["session-missing-ghost"])
        engine = SyncEngine(home=self.home, repo_root=REPO, db_path=self.db_path, state_repo=self.state_repo)

        p1, p2, p3, p4, p5 = self._mock_convergence_in_sync()
        with p1, p2, p3, p4, p5:
            receipt, _ = engine.run(check_only=True)
        self.assertEqual(receipt.convergence_status, "IN_SYNC")
        self.assertEqual(receipt.health_status, "FAILED")
        self.assertEqual(receipt.overall, OverallStatus.PASS_WITH_HEALTH_FAILURE)
        self.assertNotEqual(receipt.overall, OverallStatus.PASS_NO_CHANGE)

    def test_08_warning_prevents_wu_xu_cao_zuo_false_statement(self) -> None:
        events = [
            {"type": "session", "id": "session-warn", "delegationDepth": 0},
            {"type": "user/message", "role": "user", "data": {"content": [{"type": "text", "text": "Deploy cluster"}]}}
        ]
        self._create_session_file("session-warn", events)
        engine = SyncEngine(home=self.home, repo_root=REPO, db_path=self.db_path, state_repo=self.state_repo)

        p1, p2, p3, p4, p5 = self._mock_convergence_in_sync()
        with p1, p2, p3, p4, p5:
            receipt, human = engine.run(check_only=True)
        self.assertEqual(receipt.overall, OverallStatus.PASS_WITH_HEALTH_WARNINGS)
        self.assertNotEqual(receipt.action_required_from_user, "无需你额外操作。")
        self.assertIn("项健康状态需要关注", receipt.action_required_from_user)
        self.assertNotIn("## 7. 需要用户做什么\n无需你额外操作。", human)
        self.assertIn("项健康状态需要关注", human)

    def test_09_failure_prevents_wu_xu_cao_zuo(self) -> None:
        self._attach_sessions_to_workspace(["session-ghost"])
        engine = SyncEngine(home=self.home, repo_root=REPO, db_path=self.db_path, state_repo=self.state_repo)

        p1, p2, p3, p4, p5 = self._mock_convergence_in_sync()
        with p1, p2, p3, p4, p5:
            receipt, human = engine.run(check_only=True)
        self.assertEqual(receipt.overall, OverallStatus.PASS_WITH_HEALTH_FAILURE)
        self.assertNotIn("无需你额外操作", receipt.action_required_from_user)
        self.assertNotIn("无需操作", receipt.action_required_from_user)
        self.assertIn("检测到健康异常，需要后续处理", receipt.action_required_from_user)
        self.assertIn("检测到健康异常，需要后续处理", human)

    def test_10_jobs_no_backup_yields_warning(self) -> None:
        import shutil
        self._write_verified_backup_ledger(include_jobs=False)
        shutil.rmtree(self.backup_root / "jobs", ignore_errors=True)

        res = evaluate_backup_recovery_health(self.home, state_repo=self.state_repo)
        self.assertEqual(res.status, PlaneStatus.HEALTH_WARNING)
        self.assertFalse(res.details["BACKUP_INTEGRITY"]["jobs"])

    def test_11_jobs_backup_verified_yields_local_backup_health_pass(self) -> None:
        self._write_verified_backup_ledger(include_jobs=True)

        res = evaluate_backup_recovery_health(self.home, state_repo=self.state_repo)
        self.assertEqual(res.status, PlaneStatus.HEALTHY)
        self.assertTrue(res.details["BACKUP_INTEGRITY"]["jobs"])
        self.assertEqual(res.details["BACKUP_FRESHNESS_STATE"], "PASS")
        self.assertEqual(res.details["BACKUP_INTEGRITY_STATE"], "PASS")
        self.assertEqual(res.details["LOCAL_BACKUP_HEALTH"], "PASS")
        self.assertIn(res.details["FULL_DR_READINESS"], ("INCOMPLETE", "MISSING"))


if __name__ == "__main__":
    unittest.main()
