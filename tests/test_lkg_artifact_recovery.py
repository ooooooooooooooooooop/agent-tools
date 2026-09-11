#!/usr/bin/env python3
"""Physical Artifact Payload Recovery Test.

Proves that recover_last_known_good restores real on-disk artifact files
(client.js, dist/, launcher, manifests) and matches exact byte/content hashes,
not merely metadata declarations in a JSON state file.
"""
import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.aic import dsh_compatibility, dsh_runtime


class LkgArtifactRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp_home = Path(tempfile.mkdtemp(prefix="dsh-lkg-recovery-"))
        self.profile = self.temp_home / "profiles" / "web"
        self.profile.mkdir(parents=True, exist_ok=True)

        self.base_dir = self.profile / "base-dsh-0.1.1-rc.2"
        self.conv_lib = self.base_dir / "node_modules" / "@deepseek-ai" / "dsh-client-ui-conversation" / "lib"
        self.dist_dir = self.base_dir / "node_modules" / "@deepseek-ai" / "dsh-web-frontend" / "dist"
        self.conv_lib.mkdir(parents=True, exist_ok=True)
        self.dist_dir.mkdir(parents=True, exist_ok=True)

        # 1. Materialize valid LKG payload
        self.lkg_client_js = 'const inject = ["slots", "layout", "sessions", "workspaces", "locale"];\n'
        (self.conv_lib / "client.js").write_text(self.lkg_client_js, encoding="utf-8")
        (self.dist_dir / "index.html").write_text("<!doctype html><html>LKG FRONTEND</html>\n", encoding="utf-8")
        (self.dist_dir / "app.js").write_text("console.log('lkg app');\n", encoding="utf-8")

        self.expected_client_hash = dsh_compatibility.sha256_file(self.conv_lib / "client.js")
        self.expected_dist_hash = dsh_compatibility.sha256_tree(self.dist_dir)

        # 2. Setup backup snapshot in ~/.aic-dsh-backups/
        backups_dir = self.temp_home / ".aic-dsh-backups" / "dsh-backup-lkg"
        backup_base = backups_dir / "profiles" / "web" / "base-dsh-0.1.1-rc.2"
        backup_conv = backup_base / "node_modules" / "@deepseek-ai" / "dsh-client-ui-conversation" / "lib"
        backup_dist = backup_base / "node_modules" / "@deepseek-ai" / "dsh-web-frontend" / "dist"
        backup_conv.mkdir(parents=True, exist_ok=True)
        backup_dist.mkdir(parents=True, exist_ok=True)

        shutil.copy2(self.conv_lib / "client.js", backup_conv / "client.js")
        shutil.copy2(self.dist_dir / "index.html", backup_dist / "index.html")
        shutil.copy2(self.dist_dir / "app.js", backup_dist / "app.js")

        # 3. Create SSOT pointing to these exact hashes
        self.ssot = {
            "canonical_runtime_train": "0.1.1-rc",
            "host_version": "0.1.1-rc.2",
            "allow_mixed_train": False,
            "groups": {},
            "critical_plugins": [
                {
                    "id": "ui-conversation",
                    "package": "@deepseek-ai/dsh-client-ui-conversation",
                    "required_services": ["slots", "layout", "sessions", "workspaces", "locale"],
                }
            ],
            "runtime_provided_services": ["slots", "layout", "sessions", "workspaces", "locale"],
            "last_known_good": {
                "train": "0.1.1-rc",
                "host_version": "0.1.1-rc.2",
                "composition_hash": "lkg-comp-hash",
                "ui_bundle_sha256": self.expected_client_hash,
                "web_dist_sha256": self.expected_dist_hash,
                "validated_at": "2026-09-04T12:00:00+00:00"
            }
        }

    def tearDown(self):
        shutil.rmtree(self.temp_home, ignore_errors=True)

    def test_lkg_recovery_restores_physical_payload_not_just_metadata(self):
        """Corrupt physical client.js & dist/ payload; verify recover_last_known_good restores actual bytes."""
        client_path = self.conv_lib / "client.js"
        dist_html = self.dist_dir / "index.html"

        # Step 1: Intentionally corrupt and pollute physical files on disk
        client_path.write_text("CORRUPTED BY INCOMPATIBLE ALPHA PAYLOAD", encoding="utf-8")
        dist_html.write_text("CORRUPTED HTML", encoding="utf-8")
        corrupted_client_hash = dsh_compatibility.sha256_file(client_path)
        self.assertNotEqual(corrupted_client_hash, self.expected_client_hash)

        # Preflight should detect parse/contract failure
        checker = dsh_compatibility.DshCompatibilityChecker(self.profile, self.ssot)
        res_corrupted = checker.run_preflight(self.base_dir)
        self.assertFalse(res_corrupted.passed, "Corrupted payload must fail preflight")

        # Step 2: Execute physical recovery
        rec = checker.recover_last_known_good()
        self.assertEqual(rec["status"], "RESTORED")
        self.assertTrue(rec["content_identity_match"], "Content identity must match LKG hashes")

        # Step 3: Physically re-calculate disk hashes from scratch
        actual_restored_client_hash = dsh_compatibility.sha256_file(client_path)
        actual_restored_dist_hash = dsh_compatibility.sha256_tree(self.dist_dir)

        # Assert BYTE/CONTENT IDENTITY = MATCH
        self.assertEqual(actual_restored_client_hash, self.expected_client_hash)
        self.assertEqual(actual_restored_dist_hash, self.expected_dist_hash)

        restored_client_content = client_path.read_text(encoding="utf-8")
        self.assertEqual(restored_client_content, self.lkg_client_js)

        # Step 4: Verify preflight PASSES after physical recovery
        res_restored = checker.run_preflight(self.base_dir)
        self.assertTrue(res_restored.passed, f"Preflight must pass after LKG recovery: {res_restored.errors}")


if __name__ == "__main__":
    unittest.main()
