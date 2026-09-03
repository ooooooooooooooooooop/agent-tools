"""Regression tests for the scheduler/execution result boundary."""
from __future__ import annotations

import json
import unittest

from scripts.governance import runner_adapter


class TestRunnerResultSemantics(unittest.TestCase):
    def test_domain_finding_is_successful_execution(self):
        spec = {
            "expected_codes": {0, 1},
            "marker": r"DRIFT detected",
            "nonzero_status": "DEGRADED",
            "review_patterns": [],
        }
        result = runner_adapter.classify_standard_step(spec, 1, "DRIFT detected:\n  field=x")
        self.assertEqual(result["execution_status"], "SUCCESS")
        self.assertEqual(result["domain_status"], "DEGRADED")

    def test_child_traceback_is_execution_failure(self):
        spec = {
            "expected_codes": {0, 1},
            "marker": r"VALID|INVALID:",
            "nonzero_status": "DEGRADED",
            "review_patterns": [],
        }
        output = "Traceback (most recent call last):\nModuleNotFoundError: yaml"
        result = runner_adapter.classify_standard_step(spec, 1, output)
        self.assertEqual(result["execution_status"], "FAILED")
        self.assertEqual(result["domain_status"], "UNKNOWN")

    def test_sync_review_preserves_direct_contract(self):
        payload = {"mode": "check", "result": "REVIEW", "planes": {}}
        result = runner_adapter.classify_sync_step(1, json.dumps(payload))
        self.assertEqual(result["execution_status"], "SUCCESS")
        self.assertEqual(result["domain_status"], "REVIEW")

    def test_sync_contract_mismatch_is_failure(self):
        payload = {"mode": "check", "result": "REVIEW", "planes": {}}
        result = runner_adapter.classify_sync_step(0, json.dumps(payload))
        self.assertEqual(result["execution_status"], "FAILED")
        self.assertEqual(result["failure_kind"], "SYNC_EXIT_CONTRACT_MISMATCH")


if __name__ == "__main__":
    unittest.main()
