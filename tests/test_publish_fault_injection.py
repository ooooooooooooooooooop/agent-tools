#!/usr/bin/env python3
"""Fault-Injection Test for Transactional Publish Rollback.

Verifies that mid-publish failure triggers deterministic reverse rollback
leaving the production runtime in a byte-identical state matching LKG.
"""
import copy
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.aic import dsh_runtime, dsh_compatibility


def hash_tree_map(root: Path) -> dict[str, str]:
    res = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            res[rel] = dsh_runtime.sha256_file(p)
    return res


class PublishFaultInjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp_home = Path(tempfile.mkdtemp(prefix="dsh-tx-test-"))
        self.profile = self.temp_home / "profiles" / "web"
        self.profile.mkdir(parents=True, exist_ok=True)

        # Create original production artifacts (LKG state)
        (self.profile / "dsh-launch-web.ps1").write_text("# LKG Launcher v1\n", encoding="utf-8")
        (self.profile / "base-distribution.json").write_text('{"version": "0.1.1-rc.2", "v": 1}\n', encoding="utf-8")
        (self.profile / "cordis.patch.yml").write_text("# LKG Patch v1\n", encoding="utf-8")
        (self.profile / "manifest.json").write_text('{"hash": "lkg-hash", "v": 1}\n', encoding="utf-8")

        self.lkg_hashes = hash_tree_map(self.profile)

    def tearDown(self):
        shutil.rmtree(self.temp_home, ignore_errors=True)
        if "AIC_DSH_FAIL_AFTER" in os.environ:
            del os.environ["AIC_DSH_FAIL_AFTER"]

    def test_mid_publish_fault_injection_restores_byte_identical_lkg(self):
        """Inject deterministic mid-publish failure; verify byte-identical LKG restoration."""
        # Prepare staged candidate files with different content
        staged_dir = self.temp_home / "staged"
        staged_dir.mkdir(parents=True, exist_ok=True)
        (staged_dir / "launcher.ps1").write_text("# CANDIDATE Launcher v2\n", encoding="utf-8")
        (staged_dir / "base.json").write_text('{"version": "0.1.2", "v": 2}\n', encoding="utf-8")
        (staged_dir / "patch.yml").write_text("# CANDIDATE Patch v2\n", encoding="utf-8")
        (staged_dir / "manifest.json").write_text('{"hash": "cand-hash", "v": 2}\n', encoding="utf-8")

        entries = [
            ("profiles/web/dsh-launch-web.ps1", staged_dir / "launcher.ps1"),
            ("profiles/web/base-distribution.json", staged_dir / "base.json"),
            ("profiles/web/cordis.patch.yml", staged_dir / "patch.yml"),
            ("profiles/web/manifest.json", staged_dir / "manifest.json"),
        ]

        # Inject failure after publishing 2 items (mid-transaction failure)
        os.environ["AIC_DSH_FAIL_AFTER"] = "2"

        with self.assertRaises(dsh_runtime.DshCompositionError) as ctx:
            dsh_runtime._publish(self.temp_home, entries)

        self.assertIn("injected failure after publish 2", str(ctx.exception))

        # Check production state after rollback
        current_hashes = hash_tree_map(self.profile)

        # 1. Verify every single file matches LKG hash exactly (Byte-identical)
        self.assertEqual(current_hashes, self.lkg_hashes,
                         "Rollback failed to restore byte-identical LKG state")

        # 2. Verify candidate artifacts did not leak into production
        launcher_text = (self.profile / "dsh-launch-web.ps1").read_text(encoding="utf-8")
        self.assertIn("LKG Launcher v1", launcher_text)
        self.assertNotIn("CANDIDATE", launcher_text)

        base_text = (self.profile / "base-distribution.json").read_text(encoding="utf-8")
        self.assertIn('"v": 1', base_text)
        self.assertNotIn('"v": 2', base_text)


if __name__ == "__main__":
    unittest.main()
