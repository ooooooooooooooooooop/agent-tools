#!/usr/bin/env python3
"""Synthetic Regression Tests for DSH Runtime Compatibility & Preflight Gate.

Covers:
  CASE 1: Canonical 0.1.1 runtime + canonical packages -> PASS
  CASE 2: 0.1.1 runtime + package requiring uiSession/uiWorkspace -> PREBOOT REJECT
  CASE 3: Same version but required service missing from runtime -> PREBOOT REJECT
  CASE 4: Duplicate plugin/provider ownership -> PREBOOT REJECT
  CASE 5: Candidate upgrade validation failure -> production retains last-known-good
  CASE 6: Fully compatible new release train -> candidate can be promoted
"""
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.aic import dsh_compatibility


class DshRuntimeCompatibilityRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="dsh-compat-test-"))
        self.profile_root = self.temp_dir / "profiles" / "web"
        self.profile_root.mkdir(parents=True, exist_ok=True)
        
        # Load canonical SSOT
        self.base_ssot = dsh_compatibility.get_compatibility_ssot()
        
        # Setup base directory for 0.1.1-rc.2
        self.base_dir = self.profile_root / "base-dsh-0.1.1-rc.2"
        self.node_modules = self.base_dir / "node_modules" / "@deepseek-ai"
        self.node_modules.mkdir(parents=True, exist_ok=True)

        # Setup standard core packages
        self._write_pkg("@deepseek-ai/dsh", "0.1.1-rc.2")
        self._write_pkg("@deepseek-ai/cordis", "4.0.2")
        self._write_pkg("@deepseek-ai/dsh-web-frontend", "0.1.1-rc.2")
        self._write_pkg("@deepseek-ai/dsh-client-ui-conversation", "0.1.1-rc.2",
                        inject=["slots", "layout", "sessions", "workspaces", "locale"])
        
        # Setup artifact files
        conv_dir = self.node_modules / "dsh-client-ui-conversation" / "lib"
        conv_dir.mkdir(parents=True, exist_ok=True)
        (conv_dir / "client.js").write_text(
            'const inject = ["slots", "layout", "sessions", "workspaces", "locale"];',
            encoding="utf-8"
        )
        
        dist_dir = self.node_modules / "dsh-web-frontend" / "dist"
        dist_dir.mkdir(parents=True, exist_ok=True)
        (dist_dir / "index.html").write_text("<html></html>", encoding="utf-8")

        # Setup cordis.patch.yml
        patch_file = self.profile_root / "cordis.patch.yml"
        patch_file.write_text(
            "- insert:\n"
            "    - id: ui-conversation\n"
            "      name: '@deepseek-ai/dsh-client-ui-conversation'\n",
            encoding="utf-8"
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_pkg(self, name: str, version: str, inject: list[str] | None = None) -> Path:
        pkg_rel = name.replace("@deepseek-ai/", "")
        pkg_dir = self.node_modules / pkg_rel
        pkg_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "name": name,
            "version": version,
        }
        if inject is not None:
            data["@deepseek-ai/cordis"] = {"services": {"required": inject}}
        (pkg_dir / "package.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        return pkg_dir

    def test_case_1_canonical_runtime_passes(self):
        """CASE 1: canonical 0.1.1 runtime + canonical packages -> PASS."""
        checker = dsh_compatibility.DshCompatibilityChecker(self.profile_root, self.base_ssot)
        res = checker.run_preflight(self.base_dir)
        self.assertTrue(res.passed, f"Expected PASS but got: {res.errors}")
        self.assertEqual(len(res.errors), 0)

    def test_case_2_incompatible_ui_session_demanded_preboot_reject(self):
        """CASE 2: 0.1.1 runtime + package requiring uiSession/uiWorkspace -> PREBOOT REJECT."""
        # Simulate drift to 0.1.2-alpha.3 conversation package with uiSession requirement
        self._write_pkg("@deepseek-ai/dsh-client-ui-conversation", "0.1.2-alpha.3")
        conv_dir = self.node_modules / "dsh-client-ui-conversation" / "lib"
        (conv_dir / "client.js").write_text(
            'const inject = ["slots", "sessions", "uiSession", "uiWorkspace", "locale"];',
            encoding="utf-8"
        )

        checker = dsh_compatibility.DshCompatibilityChecker(self.profile_root, self.base_ssot)
        res = checker.run_preflight(self.base_dir)
        self.assertFalse(res.passed, "Expected preflight rejection for uiSession demand")
        
        # Verify specific errors detected
        err_str = " ".join(res.errors)
        self.assertTrue(
            "uiSession" in err_str or "MIXED_RELEASE_TRAIN" in err_str,
            f"Expected forbidden service / mixed train error but got: {err_str}"
        )

    def test_case_3_required_service_missing_preboot_reject(self):
        """CASE 3: Same version but required service missing from runtime -> PREBOOT REJECT."""
        # Add a plugin demanding non-existent 'hypotheticalUnprovidedService'
        conv_dir = self.node_modules / "dsh-client-ui-conversation" / "lib"
        (conv_dir / "client.js").write_text(
            'const inject = ["slots", "hypotheticalUnprovidedService"];',
            encoding="utf-8"
        )

        checker = dsh_compatibility.DshCompatibilityChecker(self.profile_root, self.base_ssot)
        res = checker.run_preflight(self.base_dir)
        self.assertFalse(res.passed, "Expected preflight rejection for unresolved service")
        err_str = " ".join(res.errors)
        self.assertIn("hypotheticalUnprovidedService", err_str)
        self.assertIn("UNRESOLVED_SERVICE_CONTRACT", err_str)

    def test_case_4_duplicate_ownership_preboot_reject(self):
        """CASE 4: Duplicate plugin/provider ownership -> PREBOOT REJECT."""
        # Inject duplicate rows into cordis.patch.yml
        patch_file = self.profile_root / "cordis.patch.yml"
        patch_file.write_text(
            "- insert:\n"
            "    - id: ui-conversation\n"
            "      name: '@deepseek-ai/dsh-client-ui-conversation'\n"
            "    - id: ui-conversation\n"
            "      name: '@deepseek-ai/dsh-client-ui-conversation'\n",
            encoding="utf-8"
        )

        checker = dsh_compatibility.DshCompatibilityChecker(self.profile_root, self.base_ssot)
        res = checker.run_preflight(self.base_dir)
        self.assertFalse(res.passed, "Expected preflight rejection for duplicate ownership")
        err_str = " ".join(res.errors)
        self.assertIn("DUPLICATE_OWNERSHIP", err_str)

    def test_case_5_candidate_upgrade_validation_failure_retains_lkg(self):
        """CASE 5: Candidate upgrade validation failure -> production retains last-known-good."""
        state_file = self.profile_root / "dsh-managed-state.json"
        initial_state = {
            "schemaVersion": 1,
            "current": {
                "version": "0.1.1-rc.2",
                "compositionHash": "lkg-hash-123",
                "nodeRelativePath": "runtime/node-v22.19.0-win-x64",
                "entryRelative": "profiles/web/base-dsh-0.1.1-rc.2/node_modules/@deepseek-ai/dsh/lib/bin.js",
            },
            "candidate": {
                "version": "0.1.2-alpha.3",
                "compositionHash": "bad-cand-hash",
                "verdict": "CANDIDATE_REJECTED",
                "reasons": ["unresolved service: uiSession"],
            },
            "previous": None,
        }
        state_file.write_text(json.dumps(initial_state, indent=2), encoding="utf-8")

        # When candidate validation fails, recover_last_known_good restores LKG
        ssot = copy.deepcopy(self.base_ssot)
        ssot["last_known_good"] = {
            "train": "0.1.1-rc",
            "host_version": "0.1.1-rc.2",
            "composition_hash": "lkg-hash-123",
            "validated_at": "2026-09-04T12:00:00+00:00",
        }
        checker = dsh_compatibility.DshCompatibilityChecker(self.profile_root, ssot)
        rec = checker.recover_last_known_good()
        self.assertEqual(rec["status"], "RESTORED")
        self.assertEqual(rec["restored_version"], "0.1.1-rc.2")
        self.assertTrue(rec["user_model_untouched"])

        # Check state file retained LKG
        saved = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["current"]["version"], "0.1.1-rc.2")
        self.assertEqual(saved["current"]["compositionHash"], "lkg-hash-123")
        self.assertIsNone(saved["candidate"])

    def test_case_6_compatible_new_release_train_promotable(self):
        """CASE 6: Fully compatible new release train -> candidate can be promoted."""
        # Create candidate 0.2.0 runtime
        cand_base = self.profile_root / "base-dsh-0.2.0"
        cand_nm = cand_base / "node_modules" / "@deepseek-ai"
        cand_nm.mkdir(parents=True, exist_ok=True)
        
        # Write 0.2.0 packages with fully provided services
        for pkg, ver in [
            ("@deepseek-ai/dsh", "0.2.0"),
            ("@deepseek-ai/cordis", "4.0.2"),
            ("@deepseek-ai/dsh-web-frontend", "0.2.0"),
            ("@deepseek-ai/dsh-client-ui-conversation", "0.2.0"),
        ]:
            p = cand_nm / pkg.replace("@deepseek-ai/", "")
            p.mkdir(parents=True, exist_ok=True)
            (p / "package.json").write_text(json.dumps({"name": pkg, "version": ver}), encoding="utf-8")

        conv_dir = cand_nm / "dsh-client-ui-conversation" / "lib"
        conv_dir.mkdir(parents=True, exist_ok=True)
        (conv_dir / "client.js").write_text(
            'const inject = ["slots", "sessions", "uiSession", "uiWorkspace", "locale"];',
            encoding="utf-8"
        )
        
        dist_dir = cand_nm / "dsh-web-frontend" / "dist"
        dist_dir.mkdir(parents=True, exist_ok=True)
        (dist_dir / "index.html").write_text("<html></html>", encoding="utf-8")

        # SSOT for 0.2.0 includes uiSession & uiWorkspace as provided
        ssot_0_2_0 = {
            "canonical_runtime_train": "0.2.0",
            "host_version": "0.2.0",
            "allow_mixed_train": False,
            "groups": {
                "host_core": {
                    "package_rules": {
                        "@deepseek-ai/dsh": ["0.2.0"],
                        "@deepseek-ai/cordis": ["4."],
                    }
                },
                "web_surface": {
                    "package_rules": {
                        "@deepseek-ai/dsh-client-ui-conversation": ["0.2.0"],
                        "@deepseek-ai/dsh-web-frontend": ["0.2.0"],
                    }
                }
            },
            "critical_plugins": [
                {
                    "id": "ui-conversation",
                    "package": "@deepseek-ai/dsh-client-ui-conversation",
                    "required_services": ["slots", "sessions", "uiSession", "uiWorkspace", "locale"],
                }
            ],
            "runtime_provided_services": [
                "slots", "sessions", "uiSession", "uiWorkspace", "locale"
            ],
            "last_known_good": {
                "train": "0.2.0",
                "host_version": "0.2.0",
            }
        }

        checker = dsh_compatibility.DshCompatibilityChecker(self.profile_root, ssot_0_2_0)
        res = checker.run_preflight(cand_base)
        self.assertTrue(res.passed, f"Expected PASS for compatible 0.2.0 train but got: {res.errors}")

    def test_case_7_unparseable_inject_fails_closed(self):
        """CASE 7: Unparseable/obfuscated inject in critical bundle -> PARSE_UNKNOWN -> FAIL CLOSED."""
        conv_dir = self.node_modules / "dsh-client-ui-conversation" / "lib"
        # Write obfuscated or unparseable bundle lacking standard inject pattern
        (conv_dir / "client.js").write_text(
            'var _0x1a2b = function(){ /* dynamic/obfuscated loader without parseable inject */ };',
            encoding="utf-8"
        )

        checker = dsh_compatibility.DshCompatibilityChecker(self.profile_root, self.base_ssot)
        res = checker.run_preflight(self.base_dir)
        self.assertFalse(res.passed, "Expected FAIL CLOSED for unparseable critical bundle")
        err_str = " ".join(res.errors)
        self.assertIn("PARSE_UNKNOWN_FAIL_CLOSED", err_str)
        self.assertIn("ui-conversation", err_str)


if __name__ == "__main__":
    unittest.main()
