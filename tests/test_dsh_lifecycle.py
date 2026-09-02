"""Focused tests for the DSH managed upgrade lifecycle state machine."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "aic"))

import aic  # noqa: E402
import dsh_lifecycle  # noqa: E402
import dsh_runtime  # noqa: E402


def _fresh_state(version: str = "0.1.1-rc.2") -> dict:
    return {
        "schemaVersion": 1,
        "current": {"version": version, "compositionHash": "cur-hash",
                    "nodeVersion": "v22.19.0", "nodeRelativePath": "runtime/node-v22.19.0-win-x64",
                    "entryRelative": f"profiles/web/base-dsh-{version}/node_modules/@deepseek-ai/dsh/lib/bin.js"},
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
            st["candidate"] = {"version": "0.2.0", "compositionHash": "new-hash",
                               "nodeVersion": "v22.19.0", "verdict": "CANDIDATE_VALIDATED"}
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


class LockGateTests(unittest.TestCase):
    def test_check_lock_false_skips_runtime_lock(self):
        contract = aic.adapter_contract()
        locked = [e for e in dsh_runtime.validate_contract(contract) if "runtime.lock.yaml" in e]
        if locked:  # only meaningful when lock disagreements exist
            self.assertEqual(
                [e for e in dsh_runtime.validate_contract(contract, check_lock=False)
                 if "runtime.lock.yaml" in e], [])
        else:
            self.assertEqual(dsh_runtime.validate_contract(contract, check_lock=False), [])


if __name__ == "__main__":
    unittest.main()
