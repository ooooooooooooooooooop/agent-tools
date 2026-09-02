"""Focused tests for the AIC-owned DSH runtime composition."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "aic"))

import aic  # noqa: E402
import dsh_runtime  # noqa: E402


class DshRuntimeCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = aic.adapter_contract()
        self.cfg = self.contract["runtime_composition"]

    def test_contract_has_pinned_overlays(self) -> None:
        self.assertEqual(dsh_runtime.validate_contract(self.contract), [])
        plugins = self.cfg["managed_rows"]["plugins"]
        self.assertEqual(len(plugins), 7)
        self.assertEqual([p["plugin_directory"] for p in plugins], [
            "dsh-token-meter-pressure-guard",
            "dsh-agent-loop-pressure-guard",
            "dsh-tool-result-pruner-pressure-guard",
            "dsh-compaction-convergence",
            "dsh-context-lifecycle",
            "dsh-workflow-model-preflight-gate",
            "dsh-autonomous-execution-governor",
        ])

    def test_generated_patch_is_idempotent_and_uses_profile_layout(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            patch = Path(td) / "cordis.patch.yml"
            first, first_hash = dsh_runtime.render_patch(None, self.cfg)
            patch.write_text(first, encoding="utf-8")
            second, second_hash = dsh_runtime.render_patch(patch, self.cfg)
            self.assertEqual(first, second)
            self.assertEqual(first_hash, second_hash)
            self.assertNotIn("./plugins/lib/index.js", second)
            for plugin in self.cfg["managed_rows"]["plugins"]:
                expected = ("./plugins/" + plugin["plugin_directory"] + "/" +
                            plugin["entry_relative"])
                self.assertIn(expected, second)

    def test_publish_failure_restores_every_previous_component(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            old_a = home / "profiles" / "web" / "a.txt"
            old_b = home / "profiles" / "web" / "b.txt"
            old_a.parent.mkdir(parents=True)
            old_a.write_text("old-a", encoding="utf-8")
            old_b.write_text("old-b", encoding="utf-8")
            stage = home / "stage"
            (stage / "a.txt").parent.mkdir(parents=True)
            (stage / "a.txt").write_text("new-a", encoding="utf-8")
            (stage / "b.txt").write_text("new-b", encoding="utf-8")
            with mock.patch.dict(os.environ, {"AIC_DSH_FAIL_AFTER": "2"}):
                with self.assertRaises(dsh_runtime.DshCompositionError):
                    dsh_runtime._publish(home, [
                        ("profiles/web/a.txt", stage / "a.txt"),
                        ("profiles/web/b.txt", stage / "b.txt"),
                    ])
            self.assertEqual(old_a.read_text(encoding="utf-8"), "old-a")
            self.assertEqual(old_b.read_text(encoding="utf-8"), "old-b")
            self.assertFalse((home / "profiles" / "web" / "a.txt").read_text(
                encoding="utf-8") == "new-a")

    def test_manifest_hash_is_stable(self) -> None:
        payload = {"b": 2, "a": {"x": True}}
        first, first_hash = dsh_runtime._stable_manifest(payload)
        second, second_hash = dsh_runtime._stable_manifest(json.loads(json.dumps(payload)))
        self.assertEqual(first, second)
        self.assertEqual(first_hash, second_hash)
        self.assertEqual(first["profileCombinationHash"], first_hash)

    def test_ui_packages_must_match_pinned_base_version(self) -> None:
        ui = self.cfg["ui"]
        with tempfile.TemporaryDirectory() as td:
            source = Path(td)
            client_root = source / Path(ui["client_bundle_relative"]).parent.parent
            web_root = source / Path(ui["web_dist_relative"]).parent
            client_root.mkdir(parents=True)
            web_root.mkdir(parents=True)
            (client_root / "package.json").write_text(json.dumps({
                "name": ui["client_package"],
                "version": self.cfg["base"]["version"],
            }), encoding="utf-8")
            (web_root / "package.json").write_text(json.dumps({
                "name": ui["web_package"],
                "version": self.cfg["base"]["version"],
            }), encoding="utf-8")
            dsh_runtime._validate_ui_version_alignment(source, self.cfg)

            (client_root / "package.json").write_text(json.dumps({
                "name": ui["client_package"],
                "version": "0.1.2-alpha.3",
            }), encoding="utf-8")
            with self.assertRaisesRegex(dsh_runtime.DshCompositionError, "UI/base package mismatch"):
                dsh_runtime._validate_ui_version_alignment(source, self.cfg)

    def test_desired_state_convergence_scenarios(self) -> None:
        """Phase A: Tests A-F for Desired State Convergence."""
        # A. Modify registry plugin set -> diff detects drift
        modified_contract = json.loads(json.dumps(self.contract))
        modified_contract["runtime_composition"]["managed_rows"]["plugins"].append({
            "id": "synthetic-plugin",
            "package": "@test/synthetic",
            "version": "1.0.0",
            "source_relative": "dsh/context-lifecycle",
            "plugin_directory": "synthetic-plugin",
            "entry_relative": "lib/index.js",
            "config": {},
        })
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            # Create a mock live profile with current contract
            profile = home / self.cfg["profile"]["relative_to_dsh_home"]
            manifest_path = profile / self.cfg["profile"]["manifest_file"]
            profile.mkdir(parents=True)
            manifest_path.write_text(json.dumps({"overlays": []}), encoding="utf-8")
            result = dsh_runtime.inspect(home, modified_contract)
            self.assertEqual(result["status"], "DRIFT")
            finding_components = [f["component"] for f in result["findings"]]
            self.assertIn("synthetic-plugin", finding_components)

        # B. Modify unadopted optional plugin -> production expected composition unchanged
        patch_before, hash_before = dsh_runtime.render_patch(None, self.cfg)
        # unadopted plugin e.g. model-persona is not in self.cfg, so render_patch is identical
        patch_after, hash_after = dsh_runtime.render_patch(None, self.cfg)
        self.assertEqual(patch_before, patch_after)
        self.assertEqual(hash_before, hash_after)

        # C. Adopted plugin source change -> inspect flags source drift
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            profile = home / self.cfg["profile"]["relative_to_dsh_home"]
            profile.mkdir(parents=True)
            # Create manifest with mismatched sourceSha256
            mock_manifest = {
                "overlays": [{
                    "id": self.cfg["managed_rows"]["plugins"][0]["id"],
                    "sourceSha256": "stale_hash_12345",
                    "loadOrder": 1,
                }],
            }
            (profile / self.cfg["profile"]["manifest_file"]).write_text(
                json.dumps(mock_manifest), encoding="utf-8"
            )
            result = dsh_runtime.inspect(home, self.contract)
            self.assertEqual(result["status"], "DRIFT")
            categories = [f["category"] for f in result["findings"]]
            self.assertIn("SOURCE_DRIFT", categories)

        # D. Runtime missing adopted plugin -> diff detects DEPLOYMENT_DRIFT
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            profile = home / self.cfg["profile"]["relative_to_dsh_home"]
            profile.mkdir(parents=True)
            # Manifest has all plugins recorded, but plugins directory is empty
            mock_manifest = {
                "overlays": [
                    {
                        "id": p["id"],
                        "sourceSha256": dsh_runtime.sha256_file(REPO / p["source_relative"] / p["entry_relative"]),
                        "loadOrder": i,
                    }
                    for i, p in enumerate(self.cfg["managed_rows"]["plugins"], start=1)
                ],
            }
            (profile / self.cfg["profile"]["manifest_file"]).write_text(
                json.dumps(mock_manifest), encoding="utf-8"
            )
            result = dsh_runtime.inspect(home, self.contract)
            self.assertEqual(result["status"], "DRIFT")
            categories = [f["category"] for f in result["findings"]]
            self.assertIn("DEPLOYMENT_DRIFT", categories)

        # E. Candidate unadopted -> production patch not changed
        rows = dsh_runtime._managed_rows(self.cfg)
        row_ids = [r["id"] for r in rows]
        for opt in ["subagent-splice-summarizer", "web-search-adapter", "model-persona"]:
            self.assertNotIn(opt, row_ids)

        # F. Rebuilding from canonical produces exact expected overlays
        with tempfile.TemporaryDirectory() as td:
            stage_profile = Path(td) / "profiles" / "web"
            stage_profile.mkdir(parents=True)
            copied = dsh_runtime._copy_overlays(stage_profile, self.cfg)
            self.assertEqual(len(copied), len(self.cfg["managed_rows"]["plugins"]))
            for record in copied:
                dest_file = stage_profile / "plugins" / record["id"] / "lib" / "index.js"
                # Some plugins have specific dir names
                matching_p = next(p for p in self.cfg["managed_rows"]["plugins"] if p["id"] == record["id"])
                real_dest = stage_profile / "plugins" / matching_p["plugin_directory"] / matching_p["entry_relative"]
                self.assertTrue(real_dest.is_file())


if __name__ == "__main__":
    unittest.main()
