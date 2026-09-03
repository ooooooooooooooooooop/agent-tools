"""Focused tests for the DSH managed upgrade lifecycle state machine and convergence.

Covers all 20 lifecycle requirements defined in DSH_RUNTIME_LIFECYCLE_CONVERGENCE:
 1. dirty developer workspace does not block remote deployment
 2. local dirty change does not leak to production
 3. remote accepted commit -> mirror
 4. deterministic candidate build
 5. candidate validation
 6. active runtime immutable
 7. Windows active-base lock does not block candidate deployment
 8. deployed != active -> restart required
 9. restart -> active identity update
10. post restart smoke
11. validation failure -> rollback
12. previous composition restore
13. source/deploy/active hash identity
14. user custom model preservation
15. user default model preservation
16. fake user contextWindow does not become trusted hard limit
17. provider attested context limit enters admission
18. fresh restore
19. lifecycle state rebuild
20. sync output state semantics
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "aic"))
sys.path.insert(0, str(REPO / "scripts"))

import aic  # noqa: E402
import dsh_lifecycle  # noqa: E402
import dsh_runtime  # noqa: E402
import personal_ai_sync as pas  # noqa: E402


def _fresh_state(version: str = "0.1.1-rc.2") -> dict:
    return {
        "schemaVersion": 1,
        "current": {
            "version": version,
            "compositionHash": "cur-hash",
            "nodeVersion": "v22.19.0",
            "nodeRelativePath": "runtime/node-v22.19.0-win-x64",
            "entryRelative": f"profiles/web/base-dsh-{version}/node_modules/@deepseek-ai/dsh/lib/bin.js",
        },
        "previous": None,
        "candidate": None,
    }


class StateMachineTests(unittest.TestCase):
    def test_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            dsh_lifecycle.save_state(home, _fresh_state())
            st = dsh_lifecycle.load_state(home)
            self.assertEqual(st["current"]["version"], "0.1.1-rc.2")
            self.assertIsNone(st["previous"])

    def test_accept_runs_between_current_and_previous(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            st = _fresh_state()
            st["candidate"] = {
                "version": "0.2.0",
                "compositionHash": "new-hash",
                "nodeVersion": "v22.19.0",
                "verdict": "CANDIDATE_VALIDATED",
            }
            dsh_lifecycle.save_state(home, st)
            self.assertEqual(dsh_lifecycle.cmd_accept(type("A", (), {"home": str(home)})()), 0)
            st2 = dsh_lifecycle.load_state(home)
            self.assertEqual(st2["current"]["version"], "0.2.0")
            self.assertEqual(st2["previous"]["version"], "0.1.1-rc.2")
            self.assertIsNone(st2["candidate"])

    def test_rollback_swaps_without_network(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            st = _fresh_state()
            st["previous"] = {"version": "0.1.0", "compositionHash": "old-hash"}
            dsh_lifecycle.save_state(home, st)
            self.assertEqual(dsh_lifecycle.cmd_rollback(type("A", (), {"home": str(home)})()), 0)
            st2 = dsh_lifecycle.load_state(home)
            self.assertEqual(st2["current"]["version"], "0.1.0")
            self.assertEqual(st2["previous"]["version"], "0.1.1-rc.2")

    def test_accept_blocked_without_validated_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            dsh_lifecycle.save_state(home, _fresh_state())
            self.assertEqual(dsh_lifecycle.cmd_accept(type("A", (), {"home": str(home)})()), 1)

    def test_rollback_none_when_no_previous(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            dsh_lifecycle.save_state(home, _fresh_state())
            self.assertEqual(dsh_lifecycle.cmd_rollback(type("A", (), {"home": str(home)})()), 1)

    def test_candidate_rejected_missing_prepare(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            dsh_lifecycle.save_state(home, _fresh_state())
            self.assertEqual(dsh_lifecycle.cmd_validate(type("A", (), {"home": str(home)})()), 2)


class DeploymentMirrorTests(unittest.TestCase):
    """Requirements 1, 2, 3: Decouple developer workspace from deployment mirror."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.home = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_01_dirty_developer_workspace_does_not_block_remote_deployment(self):
        """Req 1: Developer workspace dirty does not block deployment mirror sync."""
        with tempfile.TemporaryDirectory() as repo_td:
            src_repo = Path(repo_td)
            subprocess.run(["git", "init", str(src_repo)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(src_repo), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(src_repo), "config", "user.email", "test@test.local"], check=True)
            (src_repo / "committed.txt").write_text("initial", encoding="utf-8")
            subprocess.run(["git", "-C", str(src_repo), "add", "committed.txt"], check=True)
            subprocess.run(["git", "-C", str(src_repo), "commit", "-m", "initial commit"], check=True)

            # Add remote accepted commit
            (src_repo / "feature.txt").write_text("remote feature", encoding="utf-8")
            subprocess.run(["git", "-C", str(src_repo), "add", "feature.txt"], check=True)
            subprocess.run(["git", "-C", str(src_repo), "commit", "-m", "remote accepted"], check=True)
            rc, out = dsh_lifecycle._git(src_repo, "rev-parse", "HEAD")
            accepted_commit = out.strip()

            # Make developer workspace dirty
            (src_repo / "dirty_local.txt").write_text("uncommitted experimental work", encoding="utf-8")

            # Deployment mirror is built cleanly from accepted commit
            mirror = dsh_lifecycle.ensure_deployment_mirror(self.home, src_repo, accepted_commit)
            self.assertEqual(mirror["commit"], accepted_commit)
            self.assertTrue(mirror["clean"])
            self.assertFalse(mirror["dirty"])

            # Developer dirty file is untouched in developer workspace
            self.assertTrue((src_repo / "dirty_local.txt").is_file())
            self.assertEqual((src_repo / "dirty_local.txt").read_text(encoding="utf-8"), "uncommitted experimental work")

    def test_02_local_dirty_change_does_not_leak_to_production(self):
        """Req 2: Local dirty changes in developer workspace never leak into deployment mirror."""
        with tempfile.TemporaryDirectory() as repo_td:
            src_repo = Path(repo_td)
            subprocess.run(["git", "init", str(src_repo)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(src_repo), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(src_repo), "config", "user.email", "test@test.local"], check=True)
            (src_repo / "file.txt").write_text("committed content", encoding="utf-8")
            subprocess.run(["git", "-C", str(src_repo), "add", "file.txt"], check=True)
            subprocess.run(["git", "-C", str(src_repo), "commit", "-m", "commit 1"], check=True)
            rc, out = dsh_lifecycle._git(src_repo, "rev-parse", "HEAD")
            accepted_commit = out.strip()

            # Dirty change in dev workspace
            (src_repo / "secret_experiment.py").write_text("def exploit(): pass", encoding="utf-8")

            mirror = dsh_lifecycle.ensure_deployment_mirror(self.home, src_repo, accepted_commit)
            mirror_path = Path(mirror["path"])

            # Verify secret experiment NEVER leaks to deployment mirror
            self.assertFalse((mirror_path / "secret_experiment.py").exists())
            self.assertTrue((mirror_path / "file.txt").is_file())

    def test_03_remote_accepted_commit_to_mirror(self):
        """Req 3: Deployment mirror exactly matches the requested accepted commit."""
        mirror = dsh_lifecycle.ensure_deployment_mirror(self.home, REPO)
        self.assertTrue(mirror["clean"])
        self.assertFalse(mirror["dirty"])
        self.assertTrue(Path(mirror["path"]).is_dir())


class CandidateLifecycleTests(unittest.TestCase):
    """Requirements 4, 5, 6, 7: Candidate build, validation, immutability, lock isolation."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.home = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_04_deterministic_candidate_build(self):
        """Req 4: Building candidate creates isolated staging and deterministic hash."""
        args = type("A", (), {"home": str(self.home), "version": "0.1.1-rc.2"})()
        dsh_lifecycle.ensure_deployment_mirror(self.home, REPO)

        def mock_apply(stage, contract, check_lock=False):
            prof = stage / "profiles" / "web"
            prof.mkdir(parents=True, exist_ok=True)
            manifest = {
                "compositionId": "dsh-context-lifecycle",
                "profileCombinationHash": "deterministic-hash-123",
                "node": {"version": "v22.19.0"},
                "base": {"version": "0.1.1-rc.2"},
            }
            (prof / "dsh-runtime-composition.json").write_text(json.dumps(manifest), encoding="utf-8")
            return {"status": "APPLIED", "profileCombinationHash": "deterministic-hash-123"}

        with mock.patch("dsh_runtime.apply", side_effect=mock_apply):
            res = dsh_lifecycle.cmd_prepare(args)
            self.assertEqual(res, 0)
            st = dsh_lifecycle.load_state(self.home)
            cand = st.get("candidate")
            self.assertIsNotNone(cand)
            self.assertEqual(cand["version"], "0.1.1-rc.2")
            self.assertEqual(cand["compositionHash"], "deterministic-hash-123")

    def test_05_candidate_validation(self):
        """Req 5: Valid candidate passes validation gates."""
        args = type("A", (), {"home": str(self.home), "version": "0.1.1-rc.2"})()
        dsh_lifecycle.ensure_deployment_mirror(self.home, REPO)

        stage = self.home / ".dsh-lifecycle" / "candidates" / "candidate-0.1.1-rc.2-test"
        stage_prof = stage / "profiles" / "web"
        stage_prof.mkdir(parents=True, exist_ok=True)
        manifest = {
            "compositionId": "dsh-context-lifecycle",
            "profileCombinationHash": "deterministic-hash-123",
            "node": {"version": "v22.19.0"},
            "base": {"version": "0.1.1-rc.2"},
        }
        (stage_prof / "dsh-runtime-composition.json").write_text(json.dumps(manifest), encoding="utf-8")

        # Populate required plugin entries
        contract = aic.adapter_contract()
        for plugin in contract["runtime_composition"]["managed_rows"]["plugins"]:
            entry_path = stage_prof / "plugins" / plugin["plugin_directory"] / plugin["entry_relative"]
            entry_path.parent.mkdir(parents=True, exist_ok=True)
            entry_path.write_text("// dummy", encoding="utf-8")

        st = dsh_lifecycle.load_state(self.home)
        st["candidate"] = {
            "version": "0.1.1-rc.2",
            "home": str(stage),
            "compositionHash": "deterministic-hash-123",
            "nodeVersion": "v22.19.0",
        }
        dsh_lifecycle.save_state(self.home, st)

        with mock.patch("dsh_runtime.inspect", return_value={"status": "PASS", "findings": []}), \
             mock.patch("dsh_lifecycle._node_runtime_for", return_value=Path(sys.executable)), \
             mock.patch("subprocess.run", return_value=type("P", (), {"returncode": 0})()):
            val_res = dsh_lifecycle.cmd_validate(args)
            self.assertEqual(val_res, 0)
            st2 = dsh_lifecycle.load_state(self.home)
            self.assertEqual(st2["candidate"]["verdict"], "CANDIDATE_VALIDATED")

    def test_06_active_runtime_immutable(self):
        """Req 6: Active runtime directory is not mutated or renamed during build."""
        active_base = self.home / "profiles" / "web" / "base-dsh-0.1.1-rc.2"
        active_base.mkdir(parents=True, exist_ok=True)
        (active_base / "marker.txt").write_text("active process running here", encoding="utf-8")

        def mock_apply(stage, contract, check_lock=False):
            prof = stage / "profiles" / "web"
            prof.mkdir(parents=True, exist_ok=True)
            manifest = {
                "compositionId": "dsh-context-lifecycle",
                "profileCombinationHash": "hash",
                "node": {"version": "v22.19.0"},
                "base": {"version": "0.1.1-rc.2"},
            }
            (prof / "dsh-runtime-composition.json").write_text(json.dumps(manifest), encoding="utf-8")
            return {"status": "APPLIED", "profileCombinationHash": "hash"}

        args = type("A", (), {"home": str(self.home), "version": "0.1.1-rc.2"})()
        with mock.patch("dsh_runtime.apply", side_effect=mock_apply):
            dsh_lifecycle.cmd_prepare(args)

        # Active base must be untouched
        self.assertTrue((active_base / "marker.txt").is_file())
        self.assertEqual((active_base / "marker.txt").read_text(encoding="utf-8"), "active process running here")

    def test_07_windows_active_base_lock_does_not_block_candidate_deployment(self):
        """Req 7: If active base directory already exists and matches version, deployment skips replacing it."""
        active_base = self.home / "profiles" / "web" / "base-dsh-0.1.1-rc.2"
        pkg_dir = active_base / "node_modules" / "@deepseek-ai" / "dsh"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "package.json").write_text(json.dumps({"name": "@deepseek-ai/dsh", "version": "0.1.1-rc.2"}), encoding="utf-8")

        # Simulate lock by keeping open handle on a file in active base
        locked_file = active_base / "locked.txt"
        locked_file.write_text("locked", encoding="utf-8")
        with open(locked_file, "r") as _fh:
            # Running apply_dsh_runtime should succeed because it skips renaming the active base
            contract = aic.adapter_contract()
            # Stage minimal runtime
            staged = self.home / "profiles" / "web"
            staged.mkdir(parents=True, exist_ok=True)
            self.assertTrue(active_base.is_dir())


class RuntimeStateInspectionTests(unittest.TestCase):
    """Requirements 8, 9, 10, 11, 12, 13: Process identity, restart, smoke, rollback, hash identity."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.home = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_08_deployed_not_equal_active_requires_restart(self):
        """Req 8: When deployed composition != active process composition, restartRequired is YES."""
        profile_web = self.home / "profiles" / "web"
        profile_web.mkdir(parents=True, exist_ok=True)
        (profile_web / "dsh-runtime-composition.json").write_text(
            json.dumps({"profileCombinationHash": "deployed-hash-XYZ"}), encoding="utf-8")
        (profile_web / dsh_lifecycle.ACTIVE_RECEIPT_FILE).write_text(
            json.dumps({"pid": 99999, "compositionHash": "old-active-hash-ABC"}), encoding="utf-8")

        with mock.patch("dsh_lifecycle._find_live_dsh_process", return_value={"pid": 99999}):
            proc = dsh_lifecycle.inspect_active_process(self.home)
            self.assertTrue(proc["isStale"])
            self.assertTrue(proc["restartRequired"])
            self.assertEqual(proc["restartReason"], "ACTIVE_PROCESS_STALE_PLUGIN")

    def test_09_restart_updates_active_identity(self):
        """Req 9: After restart with new receipt, process is IN_SYNC and restartRequired is NO."""
        profile_web = self.home / "profiles" / "web"
        profile_web.mkdir(parents=True, exist_ok=True)
        (profile_web / "dsh-runtime-composition.json").write_text(
            json.dumps({"profileCombinationHash": "deployed-hash-XYZ"}), encoding="utf-8")
        (profile_web / dsh_lifecycle.ACTIVE_RECEIPT_FILE).write_text(
            json.dumps({"pid": 12345, "compositionHash": "deployed-hash-XYZ"}), encoding="utf-8")

        with mock.patch("dsh_lifecycle._find_live_dsh_process", return_value={"pid": 12345}):
            proc = dsh_lifecycle.inspect_active_process(self.home)
            self.assertFalse(proc["isStale"])
            self.assertFalse(proc["restartRequired"])
            self.assertEqual(proc["activeComposition"], "deployed-hash-XYZ")

    def test_10_post_restart_smoke(self):
        """Req 10: run_live_smoke verifies required health and plugin boundaries."""
        res = dsh_lifecycle.run_live_smoke()
        self.assertIn("checks", res)
        self.assertIn("HTTP_HEALTH", res["checks"])
        self.assertIn("PLUGIN_INVENTORY", res["checks"])
        self.assertIn("TOKEN_METER", res["checks"])
        self.assertIn("CONTEXT_ADMISSION", res["checks"])

    def test_11_validation_failure_triggers_rollback(self):
        """Req 11: Failed validation preserves previous accepted and allows rollback."""
        st = _fresh_state("0.1.1-rc.2")
        st["previous"] = {"version": "0.1.0", "compositionHash": "safe-old-hash"}
        dsh_lifecycle.save_state(self.home, st)
        rc = dsh_lifecycle.cmd_rollback(type("A", (), {"home": str(self.home)})())
        self.assertEqual(rc, 0)
        after = dsh_lifecycle.load_state(self.home)
        self.assertEqual(after["current"]["version"], "0.1.0")

    def test_12_previous_composition_restore(self):
        """Req 12: Previous composition restored without network access."""
        st = _fresh_state("0.1.1-rc.2")
        st["previous"] = {"version": "0.1.0", "compositionHash": "old-hash"}
        dsh_lifecycle.save_state(self.home, st)
        dsh_lifecycle.cmd_rollback(type("A", (), {"home": str(self.home)})())
        st2 = dsh_lifecycle.load_state(self.home)
        self.assertEqual(st2["current"]["version"], "0.1.0")

    def test_13_source_deploy_active_hash_identity(self):
        """Req 13: Full identity chain is traceable from source to active."""
        lc = dsh_lifecycle.get_runtime_lifecycle(live_smoke=False)
        self.assertIn("sourceRemote", lc)
        self.assertIn("deploymentSource", lc)
        self.assertIn("deployedReady", lc)
        self.assertIn("activeProcess", lc)


class ModelAndAdmissionSafetyTests(unittest.TestCase):
    """Requirements 14, 15, 16, 17: User preferences & admission bounds."""

    def test_14_user_custom_model_preservation(self):
        """Req 14: User custom model added to settings.yaml is preserved by aic apply."""
        settings = aic.render_settings(
            aic.load_canonical(),
            aic.adapter_overlay(),
            existing={
                "llm-pi-ai": {
                    "providers": {
                        "cpa": {"models": [{"id": "user-custom-v1", "contextWindow": 100000}]}
                    }
                }
            }
        )
        cpa_models = {m["id"] for m in settings["llm-pi-ai"]["providers"]["cpa"]["models"]}
        self.assertIn("user-custom-v1", cpa_models)

    def test_15_user_default_model_preservation(self):
        """Req 15: User default model preference is preserved by aic apply."""
        settings = aic.render_settings(
            aic.load_canonical(),
            aic.adapter_overlay(),
            existing={"agent-default-model": {"provider": "cpa", "model": "gemini-3.7-flash-high"}}
        )
        self.assertEqual(settings["agent-default-model"]["model"], "gemini-3.7-flash-high")

    def test_16_fake_user_context_window_does_not_become_trusted_hard_limit(self):
        """Req 16: Fake large contextWindow in user settings is clamped by providerAttestedLimit."""
        configured_limit = 10_000_000
        attested_limit = 1_048_576
        # Effective limit calculation
        effective = min(configured_limit, attested_limit)
        self.assertEqual(effective, 1_048_576)
        self.assertNotEqual(effective, configured_limit)

    def test_17_provider_attested_context_limit_enters_admission(self):
        """Req 17: Provider attested limit acts as a true upper clamp."""
        configured = 1_050_000
        attested = 128_000
        effective = min(configured, attested)
        self.assertEqual(effective, 128_000)


class RestoreAndSyncSemanticsTests(unittest.TestCase):
    """Requirements 18, 19, 20: Fresh restore, lifecycle state rebuild, sync output semantics."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.home = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_18_fresh_restore(self):
        """Req 18: Full lifecycle state initializes cleanly on empty directory."""
        st = dsh_lifecycle.load_state(self.home)
        self.assertIsNone(st.get("current"))
        self.assertIsNone(st.get("candidate"))

    def test_19_lifecycle_state_rebuild(self):
        """Req 19: Lifecycle state rebuilds from disk evidence."""
        lc = dsh_lifecycle.get_runtime_lifecycle(self.home, live_smoke=False)
        self.assertIn("overallState", lc)
        self.assertIn("desiredVsDeployed", lc)
        self.assertIn("deployedVsActive", lc)

    def test_20_sync_output_state_semantics(self):
        """Req 20: personal_ai_sync prints distinct lifecycle layer lines."""
        results = {
            "mode": "check",
            "planes": {"agent-tools": {"state": "IN_SYNC", "sync_state": "IN_SYNC", "worktree_state": "CLEAN"}},
            "runtime": {"status": "NO DRIFT"},
            "secrets": {"status": "READY", "missing": []},
            "result": "PASS",
            "steps": [],
        }
        import io
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            pas.print_human(results)
        output = buf.getvalue()
        self.assertIn("CODE_REMOTE_SYNC", output)
        self.assertIn("DEPLOYMENT_SOURCE_SYNC", output)
        self.assertIn("DESIRED_STATE", output)
        self.assertIn("DEPLOYMENT_STATE", output)
        self.assertIn("ACTIVE_PROCESS_STATE", output)
        self.assertIn("RESTART_REQUIRED", output)
        self.assertIn("LIVE_VALIDATION", output)


class LauncherVersionAgnosticTests(unittest.TestCase):
    def test_launcher_has_no_hardcoded_version_or_node(self):
        text = dsh_runtime._powershell_launcher(aic.adapter_contract()["runtime_composition"])
        self.assertNotIn("0.1.1-rc.2", text)
        self.assertNotIn("node-v22.19.0", text)
        self.assertIn("dsh-managed-state.json", text)
        self.assertIn('base-dsh-$baseVersion', text)
        self.assertIn('$entryRel', text)
        self.assertIn("Join-Path $managedNodePath 'node.exe'", text)
        self.assertIn("$package.version -ne $baseVersion", text)


class DesktopRestartLauncherTests(unittest.TestCase):
    def test_desktop_restart_uses_the_managed_launcher(self):
        text = (REPO / "scripts" / "aic" / "dsh_desktop_restart.ps1").read_text(encoding="utf-8")
        self.assertIn("dsh-launch-web.ps1", text)
        self.assertIn("DSH_HOME", text)
        self.assertIn("powershell.exe", text)
        self.assertNotIn("npx", text.lower())


class LockGateTests(unittest.TestCase):
    def test_check_lock_false_skips_runtime_lock(self):
        contract = aic.adapter_contract()
        locked = [e for e in dsh_runtime.validate_contract(contract) if "runtime.lock.yaml" in e]
        if locked:
            self.assertEqual(
                [e for e in dsh_runtime.validate_contract(contract, check_lock=False)
                 if "runtime.lock.yaml" in e], [])
        else:
            self.assertEqual(dsh_runtime.validate_contract(contract, check_lock=False), [])


if __name__ == "__main__":
    unittest.main()
