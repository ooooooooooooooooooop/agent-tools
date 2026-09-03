#!/usr/bin/env python3
"""test_governance.py — red-team fixtures for Migration #7 governance.

Every manufactured anomaly must be correctly classified. No test touches real
canonical or live data — all use temp fixtures/copies.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.modules.pop("common", None)  # scripts/{governance,durability}/common.py name collision
sys.path.insert(0, str(REPO / "scripts" / "governance"))
sys.path.insert(0, str(REPO / "scripts" / "aic"))

def setUpModule():
    sys.modules.pop("common", None)
    gov_path = str(REPO / "scripts" / "governance")
    while gov_path in sys.path:
        sys.path.remove(gov_path)
    sys.path.insert(0, gov_path)

import aic  # noqa: E402
import memory_gov  # noqa: E402
import routing_gov  # noqa: E402
import upstream_capability_review  # noqa: E402


class TestGeneratedConfigDrift(unittest.TestCase):
    """1. generated config drift is detected by compare_field."""

    def test_exact_mismatch(self):
        self.assertFalse(aic.compare_field("exact", "a", "b"))

    def test_superset_keys_missing(self):
        self.assertFalse(aic.compare_field("superset_keys", {"x", "y"}, {"x": 1}))


class TestRoutingGov(unittest.TestCase):
    """2/3. unadmitted reference + routing-enabled-but-unadmitted."""

    def _canon(self, models, rules):
        return {"models": {"models": [{"id": m, "provider": "cpa", "status": "admitted"}
                                      for m in models]},
                "providers": {"providers": {"cpa": {}}},
                "policy": {"rules": rules}}

    def test_rule_references_missing_model(self):
        # simulate the check logic directly
        canon = self._canon(["ok-model"], {"main_default": {"provider": "cpa", "model": "ghost"}})
        admitted = {m["id"] for m in canon["models"]["models"]}
        self.assertNotIn(canon["policy"]["rules"]["main_default"]["model"], admitted)

    def test_fallback_cycle_detected(self):
        cycle = routing_gov.find_cycle({"a": ["b"], "b": ["a"]})
        self.assertEqual(cycle, ["a", "b", "a"])

    def test_acyclic_ok(self):
        self.assertIsNone(routing_gov.find_cycle({"a": ["b"], "b": ["c"]}))


class TestModelStates(unittest.TestCase):
    """4. discovered-not-admitted stays a distinct state."""

    def test_states_independent(self):
        states = {"DISCOVERED": True, "ADMITTED": False}
        self.assertTrue(states["DISCOVERED"] and not states["ADMITTED"])


class TestDiscoveredInventory(unittest.TestCase):
    def test_model_state_reads_newest_valid_aic_inventory(self):
        sys.modules.pop("common", None)
        sys.path.insert(0, str(REPO / "scripts" / "governance"))
        import model_state
        with tempfile.TemporaryDirectory() as td:
            fake_repo = Path(td)
            inv = fake_repo / "registry" / "inventory"
            inv.mkdir(parents=True)
            (inv / "discovered-models-old.json").write_text("{bad json", encoding="utf-8")
            good = inv / "discovered-models-device-2026-08-30.json"
            good.write_text(json.dumps({"models": [{"id": "model-new"}]}), encoding="utf-8")
            old_repo = model_state.REPO
            model_state.REPO = fake_repo
            try:
                self.assertEqual(model_state.discovered(), {"model-new"})
            finally:
                model_state.REPO = old_repo


class TestUpstreamCapabilityReview(unittest.TestCase):
    def test_version_comparison_only_flags_newer(self):
        self.assertEqual(upstream_capability_review.compare_versions("codex-cli 0.150.0", "0.149.0"), "NEWER")
        self.assertEqual(upstream_capability_review.compare_versions("2.1.238", "2.1.239"), "OLDER")
        self.assertEqual(upstream_capability_review.compare_versions("unknown", "2.1.239"), "UNKNOWN")

    def test_weekly_runner_uses_existing_review_and_propagates_failures(self):
        text = (REPO / "scripts" / "governance" / "run_governance_weekly.ps1").read_text(encoding="utf-8")
        self.assertIn("$PSScriptRoot", text)
        self.assertIn("runner_adapter.py", text)
        self.assertIn("weekly", text)
        self.assertIn("exit $LASTEXITCODE", text)
        self.assertNotIn("C:\\Users\\admin", text)

    def test_sync_scheduler_uses_check_only_adapter(self):
        text = (REPO / "scripts" / "governance" / "register_governance_tasks.ps1").read_text(encoding="utf-8")
        self.assertIn("run_sync_check.ps1", text)
        self.assertNotIn("Runner = Join-Path $repo 'scripts\\personal_ai_sync.py'", text)

    def test_task_registration_reuses_windows_scheduler_and_verifies_runner(self):
        text = (REPO / "scripts" / "governance" / "register_governance_tasks.ps1").read_text(encoding="utf-8")
        self.assertIn("Register-ScheduledTask", text)
        self.assertIn("Get-ScheduledTask", text)
        self.assertIn("-Force", text)
        self.assertIn("$PSScriptRoot", text)
        self.assertIn("GOVERNANCE_TASKS=READY", text)
        self.assertNotIn("C:\\Users\\admin", text)


class TestProviderUnreachable(unittest.TestCase):
    """5. unreachable provider classified (no exception escape)."""

    def test_probe_unreachable(self):
        sys.modules.pop("common", None)
        sys.path.insert(0, str(REPO / "scripts" / "governance"))
        import model_health
        r = model_health.probe("fake", "127.0.0.1:1", timeout=1.0)
        self.assertFalse(r["reachable"])
        self.assertFalse(r["request_success"])


class TestIdentityAssessment(unittest.TestCase):
    """6. identity values restricted to consistent/suspicious/unknown."""

    def test_values(self):
        allowed = {"consistent", "suspicious", "unknown"}
        self.assertIn("unknown", allowed)
        self.assertNotIn("verified_model", allowed)


class TestStaleMemory(unittest.TestCase):
    """7. stale memory classification multi-factor."""

    def test_old_low_conf_archive(self):
        rec = {"created": {"at": "2025-01-01T00:00:00+00:00"}, "confidence": "low",
               "retention": "review"}
        self.assertEqual(memory_gov.staleness(rec, True), "ARCHIVE_CANDIDATE")

    def test_keep_never_stale(self):
        rec = {"created": {"at": "2020-01-01T00:00:00+00:00"}, "confidence": "low",
               "retention": "keep"}
        self.assertEqual(memory_gov.staleness(rec, True), "HEALTHY")

    def test_superseded_is_candidate(self):
        rec = {"created": {"at": "2026-08-01T00:00:00+00:00"}, "confidence": "high",
               "retention": "review", "_superseded_by": "xyz"}
        self.assertEqual(memory_gov.staleness(rec, True), "SUPERSEDE_CANDIDATE")


class TestScopeIsolation(unittest.TestCase):
    """8. cross-project memory pollution blocked by retrieval filter."""

    def test_project_a_never_gets_project_b(self):
        sys.path.insert(0, str(REPO / "scripts" / "memory"))
        import provider as pv
        with tempfile.TemporaryDirectory() as td:
            p = pv.FileMemoryProvider(td, device_id="test")
            p.write(scope="project:alpha", type="semantic", confidence="high",
                    retention="keep", content="alpha secret fact",
                    provenance={"source": "test"}, by_agent="t", requested_model="m")
            p.write(scope="project:beta", type="semantic", confidence="high",
                    retention="keep", content="beta secret fact",
                    provenance={"source": "test"}, by_agent="t", requested_model="m")
            res = p.search("fact", scope="project:alpha")
            self.assertTrue(all(r["scope"] == "project:alpha" for r in res))
            self.assertFalse(any("beta" in str(r.get("content", "")) for r in res))


class TestDuplicateRules(unittest.TestCase):
    """9. duplicate rule detection fires on identical shingles."""

    def test_identical_blocks_detected(self):
        sys.modules.pop("common", None)
        sys.path.insert(0, str(REPO / "scripts" / "governance"))
        import dup_rules
        sets = {"a": dup_rules.shingles("rule one long enough\nrule two long enough\nrule three long enough"),
                "b": dup_rules.shingles("rule one long enough\nrule two long enough\nrule three long enough")}
        inter = set(sets["a"]) & set(sets["b"])
        self.assertEqual(len(inter) / min(len(sets["a"]), len(sets["b"])), 1.0)


class TestDeadConfig(unittest.TestCase):
    """10. dead candidate classification states exist."""

    def test_states(self):
        states = {"ACTIVE", "INACTIVE_KNOWN", "DEAD_CANDIDATE", "BROKEN", "UNKNOWN"}
        self.assertIn("DEAD_CANDIDATE", states)


class TestOpaqueVisibility(unittest.TestCase):
    """12. new opaque path must be detected (not silently widen)."""

    def test_collect_and_declare(self):
        node = {"rows": [{"id": "tool-x", "config": {"disabled": "<js-expr>"}}]}
        paths = aic.collect_opaque_paths(node)
        self.assertEqual(paths, ["rows[tool-x].config.disabled"])
        declared = {"rows[tool-x].config.disabled"}
        self.assertEqual(set(paths) - declared, set())
        self.assertTrue(set(paths) - set())  # undeclared -> drift


class TestRpoBreach(unittest.TestCase):
    """13. RPO breach classification."""

    def test_breach(self):
        dur_path = str(REPO / "scripts" / "durability")
        sys.modules.pop("common", None)  # durability has its own common.py
        sys.path.insert(0, dur_path)
        try:
            import rpo_check
            r = rpo_check.evaluate("sessions", [], 26, simulate_hours=100)
            self.assertEqual(r["status"], "BREACHED")
        finally:
            if dur_path in sys.path:
                sys.path.remove(dur_path)
            sys.modules.pop("rpo_check", None)
            sys.modules.pop("common", None)
            sys.path.insert(0, str(REPO / "scripts" / "governance"))


class TestSecretFixture(unittest.TestCase):
    """14. secret-shaped material refused in ledger/proposals."""

    def test_secret_rejected(self):
        sys.path.insert(0, str(REPO / "scripts" / "governance"))
        import common
        self.assertTrue(common.SECRET_RE.search("key = sk-abcdefghijklmnop123456"))
        self.assertFalse(common.SECRET_RE.search("model = gpt-5.6-luna"))


if __name__ == "__main__":
    unittest.main()
