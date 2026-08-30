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

    def test_contract_has_five_pinned_overlays(self) -> None:
        self.assertEqual(dsh_runtime.validate_contract(self.contract), [])
        plugins = self.cfg["managed_rows"]["plugins"]
        self.assertEqual(len(plugins), 5)
        self.assertEqual([p["plugin_directory"] for p in plugins], [
            "dsh-token-meter-pressure-guard",
            "dsh-agent-loop-pressure-guard",
            "dsh-tool-result-pruner-pressure-guard",
            "dsh-compaction-convergence",
            "dsh-context-lifecycle",
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


if __name__ == "__main__":
    unittest.main()
