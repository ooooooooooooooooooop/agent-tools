"""Focused standard-library tests for goal_supervisor.py Phase 2 (enforcement).

Covers the issue #4 Phase-2 acceptance criteria that Phase 1 reserved:
  1. a criterion is ONLY verified by running its configured verifier (never prose);
  2. a failing verifier increments attempts and, past the max, blocks the criterion;
  3. a verifier that times out or fails to spawn fails closed (never verified);
  4. work-unit dispatch picks the next pending/inconclusive criterion and marks it running;
  5. repeated identical evidence yields a stable fingerprint (detects low-value repeat work);
  6. budget enforcement fails closed: max_actions exhaustion blocks, token/time breach
     flags attention_required, and unbudgeted goals are never silently enforced;
  7. completion still requires verified criteria — verifier-set verified unlocks it.

Uses only unittest/tempfile/sqlite3. Goal paths are redirected to a TemporaryDirectory.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import goal_supervisor  # noqa: E402

BOUNDED_CRITERIA = [
    {
        "id": "C-01",
        "required_evidence": ["tests/repro.py"],
        "verifier": "python -c \"print('repro-criterion-verified')\"",
        "stopping_test": "exit 0",
    }
]
BOUNDED_BUDGET = {"total_tokens": 50_000, "total_seconds": 3600}


def make_goals_db(path: Path, rows: list[dict] | None = None) -> None:
    """Create a fake ~/.codex/goals_1.sqlite exposing thread_goals with usage columns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    try:
        con.execute(
            "CREATE TABLE thread_goals ("
            " thread_id TEXT, goal_id TEXT, objective TEXT, status TEXT,"
            " token_budget INTEGER, tokens_used INTEGER, time_used_seconds INTEGER,"
            " created_at_ms INTEGER, updated_at_ms INTEGER)"
        )
        for row in rows or []:
            con.execute(
                "INSERT INTO thread_goals VALUES "
                "(:thread_id,:goal_id,:objective,:status,:token_budget,:tokens_used,"
                ":time_used_seconds,:created_at_ms,:updated_at_ms)",
                row,
            )
        con.commit()
    finally:
        con.close()


class Phase2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.codex_home = self.home / "codex"
        self.codex_db = self.codex_home / "goals_1.sqlite"
        self.broker_home = self.home / "broker"
        self.goals_root = self.broker_home / "goals"
        self._patchers = [
            mock.patch.object(goal_supervisor, "BROKER_DIR", self.broker_home),
            mock.patch.object(goal_supervisor, "GOALS_ROOT", self.goals_root),
            mock.patch.object(goal_supervisor, "CODEX_HOME", self.codex_home),
            mock.patch.object(goal_supervisor, "CODEX_GOALS_DB", self.codex_db),
        ]
        for patcher in self._patchers:
            patcher.start()
        self.addCleanup(self._stop_patchers)

    def _stop_patchers(self):
        for patcher in self._patchers:
            patcher.stop()

    def _create(self, criteria=None, budgets=None, thread=None, unbudgeted=False):
        result = goal_supervisor.create_goal(
            "reproduce bug #123 and add a regression test",
            criteria or BOUNDED_CRITERIA,
            boundaries=["prod/"],
            budgets=budgets if budgets is not None else BOUNDED_BUDGET,
            unbudgeted=unbudgeted,
            codex_thread_id=thread,
        )
        self.assertTrue(result["created"])
        return result["goal_ref"]

    # --- 1/2/3. verifier execution ------------------------------------------

    def test_verifier_success_marks_verified(self):
        goal_ref = self._create()
        result = goal_supervisor.run_verifier(goal_ref, "C-01")
        self.assertTrue(result["verified"])
        self.assertEqual(result["status"], "verified")
        status = goal_supervisor.get_goal_status(goal_ref)
        self.assertEqual(status["criteria"]["C-01"]["status"], "verified")
        # Verified criteria unlock completion.
        completion = goal_supervisor.complete_goal(goal_ref)
        self.assertTrue(completion["completed"])

    def test_verifier_failure_increments_attempts_not_verified(self):
        goal_ref = self._create()
        with mock.patch.object(goal_supervisor, "_run_command", return_value={
            "ok": False, "exit_code": 1, "stdout": "", "stderr": "boom", "timed_out": False,
        }):
            result = goal_supervisor.run_verifier(goal_ref, "C-01")
        self.assertFalse(result["verified"])
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["attempts"], 1)

    def test_verifier_timeout_fails_closed(self):
        goal_ref = self._create()
        with mock.patch.object(goal_supervisor, "_run_command", return_value={
            "ok": False, "exit_code": None, "stdout": "", "stderr": "timeout", "timed_out": True,
        }):
            result = goal_supervisor.run_verifier(goal_ref, "C-01")
        self.assertFalse(result["verified"])
        self.assertTrue(result["timed_out"])

    def test_repeated_failures_block_criterion(self):
        goal_ref = self._create()
        with mock.patch.object(goal_supervisor, "_run_command", return_value={
            "ok": False, "exit_code": 1, "stdout": "", "stderr": "fail", "timed_out": False,
        }), mock.patch.dict(os.environ, {"GOAL_MAX_VERIFIER_ATTEMPTS": "2"}):
            first = goal_supervisor.run_verifier(goal_ref, "C-01")
            second = goal_supervisor.run_verifier(goal_ref, "C-01")
        self.assertEqual(first["attempts"], 1)
        self.assertEqual(second["attempts"], 2)
        self.assertEqual(second["status"], "blocked")
        status = goal_supervisor.get_goal_status(goal_ref)
        # Only criterion is blocked and no other advanceable criterion remains:
        # the dependency graph proves no path to advance -> goal blocked.
        self.assertEqual(status["criteria"]["C-01"]["status"], "blocked")
        self.assertEqual(status["status"], "blocked")

    # --- 4. dispatch --------------------------------------------------------

    def test_dispatch_picks_next_pending_and_marks_running(self):
        goal_ref = self._create()
        result = goal_supervisor.dispatch_criterion(goal_ref)
        self.assertTrue(result["dispatched"])
        self.assertEqual(result["criterion_id"], "C-01")
        status = goal_supervisor.get_goal_status(goal_ref)
        self.assertEqual(status["criteria"]["C-01"]["status"], "running")

    def test_dispatch_nothing_when_all_terminal(self):
        goal_ref = self._create()
        goal_supervisor.dispatch_criterion(goal_ref)
        # Force C-01 verified so nothing remains to dispatch.
        with mock.patch.object(goal_supervisor, "_run_command", return_value={
            "ok": True, "exit_code": 0, "stdout": "", "stderr": "", "timed_out": False,
        }):
            goal_supervisor.run_verifier(goal_ref, "C-01")
        result = goal_supervisor.dispatch_criterion(goal_ref)
        self.assertFalse(result["dispatched"])
        self.assertEqual(result["reason"], "nothing_to_dispatch")

    # --- 5. fingerprint -----------------------------------------------------

    def test_fingerprint_stable_across_reordered_evidence(self):
        a = goal_supervisor.fingerprint_evidence(["x", "y", "z"])
        b = goal_supervisor.fingerprint_evidence(["z", "x", "y"])
        self.assertEqual(a, b)
        c = goal_supervisor.fingerprint_evidence(["x", "y", "w"])
        self.assertNotEqual(a, c)

    # --- 6. budget enforcement ----------------------------------------------

    def test_enforce_max_actions_blocks(self):
        goal_ref = self._create(budgets={"max_actions": 2})
        with mock.patch.object(goal_supervisor, "_run_command", return_value={
            "ok": False, "exit_code": 1, "stdout": "", "stderr": "fail", "timed_out": False,
        }), mock.patch.dict(os.environ, {"GOAL_MAX_VERIFIER_ATTEMPTS": "10"}):
            goal_supervisor.run_verifier(goal_ref, "C-01")
            goal_supervisor.run_verifier(goal_ref, "C-01")
        result = goal_supervisor.enforce_budgets(goal_ref)
        self.assertTrue(result["enforced"])
        self.assertEqual(result["status"], "blocked")
        kinds = {b["kind"] for b in result["breaches"]}
        self.assertIn("max_actions", kinds)

    def test_enforce_token_budget_flags_attention(self):
        goal_ref = self._create(budgets={"total_tokens": 100}, thread="t-1")
        make_goals_db(self.codex_db, rows=[{
            "thread_id": "t-1", "goal_id": "g", "objective": "o", "status": "active",
            "token_budget": 100, "tokens_used": 150, "time_used_seconds": 5,
            "created_at_ms": 1, "updated_at_ms": 2,
        }])
        result = goal_supervisor.enforce_budgets(goal_ref)
        self.assertTrue(result["enforced"])
        self.assertEqual(result["status"], "attention_required")
        self.assertIn("total_tokens", {b["kind"] for b in result["breaches"]})

    def test_enforce_unbudgeted_never_breaches(self):
        goal_ref = self._create(unbudgeted=True)
        result = goal_supervisor.enforce_budgets(goal_ref)
        self.assertTrue(result["unbudgeted"])
        self.assertEqual(result["breaches"], [])

    def test_enforce_no_breach_no_escalation(self):
        goal_ref = self._create(budgets={"max_actions": 50})
        result = goal_supervisor.enforce_budgets(goal_ref)
        self.assertTrue(result["enforced"])
        self.assertEqual(result["breaches"], [])
        self.assertEqual(result["status"], "in_progress")

    # --- acceptance 3: repeated no-progress fingerprint routes onward ---------

    def test_repeated_fingerprint_uses_alternative_route(self):
        criteria = [{
            "id": "C-01",
            "required_evidence": ["tests/a.py"],
            "verifier": "python -c \"print('ok')\"",
            "stopping_test": "exit 0",
            "alternative_routes": ["codex_exec_resume_alt1"],
        }]
        goal_ref = self._create(criteria=criteria)
        # Simulate one failed verifier attempt -> inconclusive with a fingerprint.
        with mock.patch.object(goal_supervisor, "_run_command", return_value={
            "ok": False, "exit_code": 1, "stdout": "", "stderr": "fail", "timed_out": False,
        }), mock.patch.dict(os.environ, {"GOAL_MAX_VERIFIER_ATTEMPTS": "10"}):
            goal_supervisor.run_verifier(goal_ref, "C-01")
        # Next dispatch must NOT re-run the same route; it picks the alternative.
        result = goal_supervisor.dispatch_criterion(goal_ref)
        self.assertTrue(result["dispatched"])
        self.assertEqual(result["route"], "codex_exec_resume_alt1")
        self.assertEqual(result["via"], "alternative_route")

    def test_repeated_fingerprint_without_route_emits_attention(self):
        criteria = [{
            "id": "C-01",
            "required_evidence": ["tests/a.py"],
            "verifier": "python -c \"print('ok')\"",
            "stopping_test": "exit 0",
        }]
        goal_ref = self._create(criteria=criteria)
        with mock.patch.object(goal_supervisor, "_run_command", return_value={
            "ok": False, "exit_code": 1, "stdout": "", "stderr": "fail", "timed_out": False,
        }), mock.patch.dict(os.environ, {"GOAL_MAX_VERIFIER_ATTEMPTS": "10"}):
            goal_supervisor.run_verifier(goal_ref, "C-01")
        result = goal_supervisor.dispatch_criterion(goal_ref)
        self.assertFalse(result["dispatched"])
        self.assertEqual(result["reason"], "attention_required")
        status = goal_supervisor.get_goal_status(goal_ref)
        self.assertEqual(status["status"], "attention_required")

    # --- acceptance 4: blocker is local unless the dependency graph proves global ---

    def test_blocked_criterion_does_not_stop_unrelated_criteria(self):
        criteria = [
            {"id": "C-01", "required_evidence": ["tests/a.py"], "verifier": "python -c \"print('x')\"", "stopping_test": "exit 0"},
            {"id": "C-02", "required_evidence": ["tests/b.py"], "verifier": "python -c \"print('y')\"", "stopping_test": "exit 0"},
        ]
        goal_ref = self._create(criteria=criteria)
        # Blocking requires an attempted dispatch first.
        goal_supervisor.dispatch_criterion(goal_ref)
        blocked = goal_supervisor.record_evidence(goal_ref, "C-01", ["tests/a.py"], status_hint="blocked")
        self.assertTrue(blocked["recorded"])
        status = goal_supervisor.get_goal_status(goal_ref)
        # C-01 blocked with no dependencies -> NOT a global block; C-02 stays dispatchable.
        self.assertEqual(status["status"], "attention_required")
        self.assertIn("C-02", status["criteria"])
        dispatch = goal_supervisor.dispatch_criterion(goal_ref)
        self.assertTrue(dispatch["dispatched"])
        self.assertEqual(dispatch["criterion_id"], "C-02")

    def test_dependency_chain_fully_blocked_is_global(self):
        criteria = [
            {"id": "C-01", "required_evidence": ["tests/a.py"], "verifier": "python -c \"print('x')\"", "stopping_test": "exit 0"},
            {"id": "C-02", "required_evidence": ["tests/b.py"], "verifier": "python -c \"print('y')\"", "stopping_test": "exit 0", "dependencies": ["C-01"]},
        ]
        goal_ref = self._create(criteria=criteria)
        goal_supervisor.dispatch_criterion(goal_ref)  # C-01
        goal_supervisor.record_evidence(goal_ref, "C-01", ["tests/a.py"], status_hint="blocked")
        goal_supervisor.dispatch_criterion(goal_ref)  # C-02
        goal_supervisor.record_evidence(goal_ref, "C-02", ["tests/b.py"], status_hint="blocked")
        status = goal_supervisor.get_goal_status(goal_ref)
        # C-02 depends on C-01 and both are blocked -> proven global block.
        self.assertEqual(status["status"], "blocked")

    def test_dependency_unblocked_keeps_goal_attention(self):
        criteria = [
            {"id": "C-01", "required_evidence": ["tests/a.py"], "verifier": "python -c \"print('x')\"", "stopping_test": "exit 0"},
            {"id": "C-02", "required_evidence": ["tests/b.py"], "verifier": "python -c \"print('y')\"", "stopping_test": "exit 0", "dependencies": ["C-01"]},
        ]
        goal_ref = self._create(criteria=criteria)
        goal_supervisor.dispatch_criterion(goal_ref)  # C-01
        goal_supervisor.record_evidence(goal_ref, "C-01", ["tests/a.py"], status_hint="blocked")
        # C-02 not blocked yet; C-01's chain has no dependency so it is local -> attention, not global.
        status = goal_supervisor.get_goal_status(goal_ref)
        self.assertEqual(status["status"], "attention_required")

    # --- acceptance 7: per-criterion budget + terminal receipt + telemetry fail-closed ---

    def test_per_criterion_budget_breach_blocks(self):
        criteria = [{
            "id": "C-01",
            "required_evidence": ["tests/a.py"],
            "verifier": "python -c \"print('x')\"",
            "stopping_test": "exit 0",
            "budget": {"max_actions": 1},
        }]
        # Goal-level plan only action budget so no token telemetry is required.
        goal_ref = self._create(criteria=criteria, budgets={"max_actions": 50})
        with mock.patch.object(goal_supervisor, "_run_command", return_value={
            "ok": False, "exit_code": 1, "stdout": "", "stderr": "fail", "timed_out": False,
        }), mock.patch.dict(os.environ, {"GOAL_MAX_VERIFIER_ATTEMPTS": "10"}):
            goal_supervisor.run_verifier(goal_ref, "C-01")
        result = goal_supervisor.enforce_budgets(goal_ref)
        self.assertTrue(result["enforced"])
        self.assertEqual(result["status"], "blocked")
        kinds = {b["kind"] for b in result["breaches"]}
        self.assertIn("criterion_max_actions", kinds)
        self.assertIn("C-01", result["receipt"]["per_criterion"])

    def test_enforce_requires_telemetry_fails_closed(self):
        criteria = [{
            "id": "C-01",
            "required_evidence": ["tests/a.py"],
            "verifier": "python -c \"print('x')\"",
            "stopping_test": "exit 0",
        }]
        goal_ref = self._create(criteria=criteria, budgets={"total_tokens": 100}, thread="t-none")
        # No codex DB row + require_telemetry -> fail closed, never silent pass.
        result = goal_supervisor.enforce_budgets(goal_ref, require_telemetry=True)
        self.assertFalse(result["enforced"])
        self.assertEqual(result["reason"], "enforcement_requires_telemetry")

    def test_enforce_without_require_telemetry_allows_action_only(self):
        goal_ref = self._create(budgets={"max_actions": 50}, thread="t-none")
        result = goal_supervisor.enforce_budgets(goal_ref, require_telemetry=False)
        self.assertTrue(result["enforced"])
        self.assertEqual(result["breaches"], [])
        self.assertIn("max_actions", result["receipt"])

    # --- acceptance 4/7: bounded work-unit packaging --------------------------

    def test_build_work_unit_reference_based_packaging(self):
        criteria = [{
            "id": "C-01",
            "required_evidence": ["tests/a.py"],
            "verifier": "python -c \"print('x')\"",
            "stopping_test": "exit 0",
            "alternative_routes": ["alt"],
        }]
        goal_ref = self._create(criteria=criteria)
        unit = goal_supervisor.build_work_unit(goal_ref)
        self.assertTrue(unit["built"])
        wu = unit["work_unit"]
        self.assertEqual(wu["objective_hash"], goal_supervisor.objective_hash(
            "reproduce bug #123 and add a regression test"))
        self.assertEqual(wu["criterion"]["id"], "C-01")
        self.assertIn("tests/a.py", wu["criterion"]["required_evidence"])
        self.assertIn("python", wu["criterion"]["verifier"])
        self.assertFalse(wu["transcript_replay"])
        self.assertEqual(wu["boundaries"], ["prod/"])

    def test_build_work_unit_respects_dispatched_route(self):
        criteria = [{
            "id": "C-01",
            "required_evidence": ["tests/a.py"],
            "verifier": "python -c \"print('x')\"",
            "stopping_test": "exit 0",
        }]
        goal_ref = self._create(criteria=criteria)
        goal_supervisor.dispatch_criterion(goal_ref, route="codex_alt")
        unit = goal_supervisor.build_work_unit(goal_ref, "C-01")
        self.assertTrue(unit["built"])
        self.assertEqual(unit["work_unit"]["route"], "codex_alt")

    # --- recoverable-pause gate: blocked is the LAST rung, not the first -------

    def test_blocked_without_attempt_is_rejected(self):
        # Never record a block before real work was attempted: a fresh pending
        # criterion cannot be declared blocked by an operator.
        goal_ref = self._create()
        rejected = goal_supervisor.record_evidence(
            goal_ref, "C-01", ["tests/a.py"], status_hint="blocked"
        )
        self.assertFalse(rejected["recorded"])
        self.assertIn("blocked_without_attempt", rejected["error"])
        status = goal_supervisor.get_goal_status(goal_ref)
        self.assertEqual(status["criteria"]["C-01"]["status"], "pending")

    def test_blocked_with_untried_alternative_route_is_rejected(self):
        # A declared alternative route that was never tried must be tried before
        # the criterion may be blocked: RETRY -> REPLAN -> alternative route -> block.
        criteria = [{
            "id": "C-01",
            "required_evidence": ["tests/a.py"],
            "verifier": "python -c \"print('x')\"",
            "stopping_test": "exit 0",
            "alternative_routes": ["codex_alt1", "codex_alt2"],
        }]
        goal_ref = self._create(criteria=criteria)
        goal_supervisor.dispatch_criterion(goal_ref)  # primary route attempted
        rejected = goal_supervisor.record_evidence(
            goal_ref, "C-01", ["tests/a.py"], status_hint="blocked"
        )
        self.assertFalse(rejected["recorded"])
        self.assertIn("blocked_with_untried_route", rejected["error"])
        self.assertIn("codex_alt1", rejected["error"])

    def test_blocked_reopens_on_new_evidence(self):
        # Recoverable pause, not a tombstone: new evidence on a blocked
        # criterion reopens it as inconclusive so it can be dispatched again.
        goal_ref = self._create()
        goal_supervisor.dispatch_criterion(goal_ref)
        blocked = goal_supervisor.record_evidence(
            goal_ref, "C-01", ["tests/a.py"], status_hint="blocked"
        )
        self.assertTrue(blocked["recorded"])
        reopened = goal_supervisor.record_evidence(goal_ref, "C-01", ["tests/new.py"])
        self.assertTrue(reopened["recorded"])
        self.assertEqual(reopened["status"], "inconclusive")
        status = goal_supervisor.get_goal_status(goal_ref)
        self.assertEqual(status["criteria"]["C-01"]["status"], "inconclusive")
        dispatch = goal_supervisor.dispatch_criterion(goal_ref)
        self.assertTrue(dispatch["dispatched"])

    def test_blocked_criterion_recovers_via_untried_alternative_route(self):
        # A blocked criterion with an untried alternative route is re-dispatched
        # on that route (blocked_recovery) instead of stopping the goal.
        criteria = [{
            "id": "C-01",
            "required_evidence": ["tests/a.py"],
            "verifier": "python -c \"print('x')\"",
            "stopping_test": "exit 0",
            "alternative_routes": ["codex_alt1"],
        }]
        goal_ref = self._create(criteria=criteria)
        # Primary route exhausted via verifier failures -> auto blocked
        # (verifier-driven block carries hard evidence and bypasses the gate).
        with mock.patch.object(goal_supervisor, "_run_command", return_value={
            "ok": False, "exit_code": 1, "stdout": "", "stderr": "fail", "timed_out": False,
        }), mock.patch.dict(os.environ, {"GOAL_MAX_VERIFIER_ATTEMPTS": "2"}):
            goal_supervisor.run_verifier(goal_ref, "C-01")
            goal_supervisor.run_verifier(goal_ref, "C-01")
        status = goal_supervisor.get_goal_status(goal_ref)
        self.assertEqual(status["criteria"]["C-01"]["status"], "blocked")
        # The untried alternative route keeps the goal alive.
        result = goal_supervisor.dispatch_criterion(goal_ref)
        self.assertTrue(result["dispatched"])
        self.assertEqual(result["route"], "codex_alt1")
        self.assertEqual(result["via"], "blocked_recovery")
        status = goal_supervisor.get_goal_status(goal_ref)
        self.assertEqual(status["criteria"]["C-01"]["status"], "running")

    def test_blocked_after_all_routes_tried_stays_blocked(self):
        # Once every declared route was tried, a blocked criterion is terminal:
        # no dispatch remains, and the goal reports attention_required.
        criteria = [{
            "id": "C-01",
            "required_evidence": ["tests/a.py"],
            "verifier": "python -c \"print('x')\"",
            "stopping_test": "exit 0",
            "alternative_routes": ["codex_alt1"],
        }]
        goal_ref = self._create(criteria=criteria)
        fail = {
            "ok": False, "exit_code": 1, "stdout": "", "stderr": "fail", "timed_out": False,
        }
        with mock.patch.object(goal_supervisor, "_run_command", return_value=fail), \
                mock.patch.dict(os.environ, {"GOAL_MAX_VERIFIER_ATTEMPTS": "2"}):
            # Primary route: two verifier failures -> blocked.
            goal_supervisor.run_verifier(goal_ref, "C-01")
            goal_supervisor.run_verifier(goal_ref, "C-01")
            # Alternative route recovered and also exhausted -> blocked again.
            goal_supervisor.dispatch_criterion(goal_ref)
            goal_supervisor.run_verifier(goal_ref, "C-01")
            goal_supervisor.run_verifier(goal_ref, "C-01")
        status = goal_supervisor.get_goal_status(goal_ref)
        self.assertEqual(status["criteria"]["C-01"]["status"], "blocked")
        result = goal_supervisor.dispatch_criterion(goal_ref)
        self.assertFalse(result["dispatched"])
        self.assertEqual(result["reason"], "nothing_to_dispatch")


if __name__ == "__main__":
    unittest.main()
