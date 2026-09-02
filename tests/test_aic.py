"""Unit tests for scripts/aic/aic.py (render/diff/validate core logic).

The physical drift red-team against the live DSH config is a separate manual
procedure; these tests only pin the semantics of the pure functions.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "aic"))

import aic  # noqa: E402


class TestDeepDiff(unittest.TestCase):
    def test_equal_dicts(self):
        self.assertEqual(self._diff({"a": 1}, {"a": 1}), [])

    def test_scalar_change_reported(self):
        diffs = self._diff({"a": {"b": 10}}, {"a": {"b": 11}})
        self.assertEqual(diffs, [("a.b", 10, 11)])

    def test_absent_keys(self):
        diffs = self._diff({"a": 1}, {"a": 1, "b": 2})
        self.assertEqual(diffs, [("b", "<absent>", 2)])

    def test_list_length(self):
        diffs = self._diff({"m": [1, 2]}, {"m": [1]})
        self.assertEqual(diffs[0], ("m.length", 2, 1))

    @staticmethod
    def _diff(e, a):
        out = []
        aic.deep_diff(e, a, "", out)
        return out


class TestRowProjection(unittest.TestCase):
    ROWS = [{"id": "tool-subagent", "config": {"agentOptions": {"provider": "cpa", "model": "m1"}}}]

    def test_find_row(self):
        row = aic.find_row(self.ROWS, "tool-subagent")
        self.assertEqual(aic.get_nested(row, "config.agentOptions.model"), "m1")

    def test_missing_row(self):
        self.assertIsNone(aic.find_row(self.ROWS, "nope"))

    def test_missing_key(self):
        self.assertIs(aic.get_nested(self.ROWS[0], "config.nope"), aic._MISSING)


class TestCapabilityAdoptionProjection(unittest.TestCase):
    POLICY = """## CONTINUOUS_CAPABILITY_ADOPTION\n\n- Discovery is not adoption.\n"""

    def test_policy_projection_is_checksum_protected_and_idempotent(self):
        rendered = aic.policy_projection.render_managed_block(self.POLICY)
        updated, status = aic.policy_projection.update_managed_block_text("# User rules\n", self.POLICY)
        self.assertEqual(status, "updated")
        self.assertIn(rendered, updated)
        again, status = aic.policy_projection.update_managed_block_text(updated, self.POLICY)
        self.assertEqual(status, "unchanged")
        self.assertEqual(again, updated)

    def test_tampered_policy_projection_is_rejected(self):
        updated, _ = aic.policy_projection.update_managed_block_text("", self.POLICY)
        tampered = updated.replace("Discovery is not adoption", "Discovery is adoption")
        with self.assertRaises(ValueError):
            aic.policy_projection.update_managed_block_text(tampered, self.POLICY)

    def test_diff_and_apply_use_private_canonical_without_rewriting_user_text(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            state = root / "state"
            (state / "state").mkdir(parents=True)
            (state / "state" / "preferences.md").write_text(self.POLICY, encoding="utf-8")
            (home / ".dsh").mkdir(parents=True)
            target = home / ".dsh" / "AGENTS.md"
            target.write_text("# User rules\nkeep this\n", encoding="utf-8")
            with mock.patch.object(Path, "home", return_value=home), \
                    mock.patch.object(aic, "PRIVATE_STATE", state):
                row = aic._policy_diff_row("dsh")
                self.assertFalse(row["ok"])
                rc, _ = aic._apply_policy_projection("dsh")
                self.assertEqual(rc, 0)
                row = aic._policy_diff_row("dsh")
                self.assertTrue(row["ok"])
            text = target.read_text(encoding="utf-8")
            self.assertIn("keep this", text)
            self.assertEqual(text.count("aic:continuous-capability-adoption:begin"), 1)


class TestCanonicalIntegrity(unittest.TestCase):
    """The shipped canonical must pass its own validator at all times."""

    def test_validate_clean(self):
        self.assertEqual(aic.cmd_validate(None), 0)


if __name__ == "__main__":
    unittest.main()
