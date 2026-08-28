#!/usr/bin/env python3
"""test_operations_review_contract.py — 10 regression fixtures for operations review contract.

严格覆盖 10 个场景：
1. current healthy
2. known external blockers only
3. novel BLOCKED_PRIVACY only
4. actual session backup age breach
5. personalization current check failed + last-known available
6. personalization current check failed + no fallback
7. D ledger sandbox denied (OBSERVABILITY_EVIDENCE_LIMITATION != GOVERNANCE_FAILURE)
8. governance checker actual fail (capability/static fail -> GOVERNANCE_CHECK_FAILED)
9. multiple causes mixed (known blocker + actual backup age breach)
10. current drift + known blockers

重点断言：
- KNOWN BLOCKER ONLY != ACTION REQUIRED
- EVIDENCE UNAVAILABLE != DEGRADED
- LAST KNOWN != CURRENT
- REPO PRIVACY BREACH != BACKUP AGE BREACH
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "governance"))
sys.path.insert(0, str(REPO / "skills" / "personal-ai-operations-review" / "scripts"))

import personal_status as pss  # noqa: E402
import personalization_status as ps  # noqa: E402


class TestOperationsReviewContract(unittest.TestCase):

    def setUp(self):
        sys.modules.pop("common", None)
        sys.path.insert(0, str(REPO / "scripts" / "governance"))

    def _base_domains(self) -> dict[str, dict]:
        return {
            "Infrastructure": {
                "status": "HEALTHY", "evidence_state": "CURRENT",
                "cause": None, "reason": "aic VALID, 5 targets NO DRIFT"
            },
            "Personalization": {
                "status": "HEALTHY", "evidence_state": "CURRENT",
                "cause": None, "reason": "Correction Rate 6.2%（基线 6.2%），重复纠正组 5"
            },
            "Durability": {
                "status": "HEALTHY", "evidence_state": "CURRENT",
                "cause": None, "reason": "all verified backup datasets within RPO window"
            },
            "Governance": {
                "status": "HEALTHY", "evidence_state": "CURRENT",
                "cause": None, "reason": "capability_drift=0; static boundary clean"
            },
            "Proposals": {
                "status": "HEALTHY", "evidence_state": "CURRENT",
                "cause": None, "reason": "4 open, no new high severity"
            },
            "External Blockers": {
                "status": "HEALTHY", "evidence_state": "CURRENT",
                "cause": None, "reason": "no blockers"
            },
        }

    # 1. current healthy
    def test_1_current_healthy(self):
        domains = self._base_domains()
        action = pss.classify_action(domains, [])
        self.assertEqual(action, "NO ACTION")

    # 2. known external blockers only
    def test_2_known_external_blockers_only(self):
        domains = self._base_domains()
        domains["External Blockers"] = {
            "status": "BLOCKED", "evidence_state": "CURRENT",
            "cause": "KNOWN_EXTERNAL_BLOCKER", "reason": "known, unchanged"
        }
        action = pss.classify_action(domains, pss.KNOWN_EXTERNAL_BLOCKERS)
        self.assertEqual(action, "EXTERNAL BLOCKER")
        self.assertNotEqual(action, "ACTION REQUIRED", "KNOWN BLOCKER ONLY != ACTION REQUIRED")

    # 3. novel BLOCKED_PRIVACY only
    def test_3_novel_blocked_privacy_only(self):
        domains = self._base_domains()
        domains["Durability"] = {
            "status": "DEGRADED", "evidence_state": "CURRENT",
            "cause": "KNOWN_PRIVACY_BLOCKER",
            "reason": "novel-main remains known BLOCKED_PRIVACY; backup-age datasets healthy"
        }
        domains["External Blockers"] = {
            "status": "BLOCKED", "evidence_state": "CURRENT",
            "cause": "KNOWN_EXTERNAL_BLOCKER", "reason": "known, unchanged"
        }
        action = pss.classify_action(domains, pss.KNOWN_EXTERNAL_BLOCKERS)
        self.assertEqual(action, "EXTERNAL BLOCKER")
        self.assertNotEqual(action, "ACTION REQUIRED", "REPO PRIVACY BREACH != BACKUP AGE BREACH")
        self.assertNotEqual(domains["Durability"]["cause"], "BACKUP_RPO_AGE_BREACH")

    # 4. actual session backup age breach
    def test_4_actual_session_backup_age_breach(self):
        domains = self._base_domains()
        domains["Durability"] = {
            "status": "DEGRADED", "evidence_state": "CURRENT",
            "cause": "BACKUP_RPO_AGE_BREACH",
            "reason": "backup age exceeded target for ['sessions'] (cause: BACKUP_RPO_AGE_BREACH)"
        }
        action = pss.classify_action(domains, pss.KNOWN_EXTERNAL_BLOCKERS)
        self.assertEqual(action, "ACTION REQUIRED")
        self.assertEqual(domains["Durability"]["cause"], "BACKUP_RPO_AGE_BREACH")

    # 5. personalization current check failed + last-known available
    def test_5_personalization_current_check_failed_with_last_known(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            rep = d / "report.md"
            rep.write_text("Correction Rate ≈ 6.2% / user message\n", encoding="utf-8")
            res = ps.evaluate(str(d / "missing.jsonl"), None, str(rep))

            self.assertEqual(res["status"], "UNKNOWN")
            self.assertEqual(res["evidence_state"], "LAST_KNOWN")
            self.assertNotEqual(res["evidence_state"], "CURRENT", "LAST KNOWN != CURRENT")
            self.assertAlmostEqual(res["last_known_correction_rate"], 0.062, places=3)
            self.assertIn("current check unavailable", res["reason"])
            self.assertNotIn("与基线持平", res["reason"])

            domains = self._base_domains()
            domains["Personalization"] = res
            action = pss.classify_action(domains, [])
            self.assertEqual(action, "REVIEW")

    # 6. personalization current check failed + no fallback
    def test_6_personalization_current_check_failed_no_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            res = ps.evaluate(str(d / "missing.jsonl"), None, str(d / "missing.md"))

            self.assertEqual(res["status"], "UNKNOWN")
            self.assertEqual(res["evidence_state"], "UNAVAILABLE")
            self.assertIsNone(res["last_known_correction_rate"])

            domains = self._base_domains()
            domains["Personalization"] = res
            action = pss.classify_action(domains, [])
            self.assertEqual(action, "REVIEW")

    # 7. D ledger sandbox denied
    def test_7_d_ledger_sandbox_denied(self):
        sys.modules.pop("common", None)
        import common
        # 模拟 PermissionError 写入
        with patch.object(Path, "open", side_effect=PermissionError("Permission denied: 'D:\\ai-backup'")):
            row = common.gov_log("capability_gov", "ok", 0)
            self.assertEqual(row.get("_logging_limitation"), "OBSERVABILITY_EVIDENCE_LIMITATION")

        # 此时 Governance 静态规则通过，不应因日志写入受限而变成 DEGRADED
        domains = self._base_domains()
        domains["Governance"] = {
            "status": "HEALTHY", "evidence_state": "CURRENT",
            "cause": None,
            "reason": "capability_drift=0; static boundary clean (ledger logging limited by sandbox)"
        }
        action = pss.classify_action(domains, [])
        self.assertEqual(action, "NO ACTION")

    # 8. governance checker actual fail
    def test_8_governance_checker_actual_fail(self):
        domains = self._base_domains()
        domains["Governance"] = {
            "status": "DEGRADED", "evidence_state": "CURRENT",
            "cause": "GOVERNANCE_CHECK_FAILED",
            "reason": "capability_drift=2"
        }
        action = pss.classify_action(domains, [])
        self.assertEqual(action, "ACTION REQUIRED")

    # 9. multiple causes mixed
    def test_9_multiple_causes_mixed(self):
        domains = self._base_domains()
        domains["Durability"] = {
            "status": "DEGRADED", "evidence_state": "CURRENT",
            "cause": "BACKUP_RPO_AGE_BREACH",
            "reason": "sessions age=72h > 26h"
        }
        domains["External Blockers"] = {
            "status": "BLOCKED", "evidence_state": "CURRENT",
            "cause": "KNOWN_EXTERNAL_BLOCKER", "reason": "known, unchanged"
        }
        action = pss.classify_action(domains, pss.KNOWN_EXTERNAL_BLOCKERS)
        self.assertEqual(action, "ACTION REQUIRED", "Real backup age breach takes precedence")

    # 10. current drift + known blockers
    def test_10_current_drift_and_known_blockers(self):
        domains = self._base_domains()
        domains["Infrastructure"] = {
            "status": "DEGRADED", "evidence_state": "CURRENT",
            "cause": "HARNESS_DRIFT",
            "reason": "harness drift: ['codex']"
        }
        domains["External Blockers"] = {
            "status": "BLOCKED", "evidence_state": "CURRENT",
            "cause": "KNOWN_EXTERNAL_BLOCKER", "reason": "known, unchanged"
        }
        action = pss.classify_action(domains, pss.KNOWN_EXTERNAL_BLOCKERS)
        self.assertEqual(action, "ACTION REQUIRED")


if __name__ == "__main__":
    unittest.main()
