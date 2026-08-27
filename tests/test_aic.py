"""Unit tests for scripts/aic/aic.py (render/diff/validate core logic).

The physical drift red-team against the live DSH config is a separate manual
procedure; these tests only pin the semantics of the pure functions.
"""
import sys
import unittest
from pathlib import Path

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


class TestCanonicalIntegrity(unittest.TestCase):
    """The shipped canonical must pass its own validator at all times."""

    def test_validate_clean(self):
        self.assertEqual(aic.cmd_validate(None), 0)


if __name__ == "__main__":
    unittest.main()
