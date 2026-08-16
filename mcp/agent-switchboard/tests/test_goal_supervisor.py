"""Focused standard-library tests for goal_supervisor.py (Phase 1: observability).

Covers GitHub issue #4's Phase-1 acceptance criteria:
  1. unbounded superlative goals are rejected (goal_contract_unbounded);
  2. no configured budget is visibly unbudgeted (or rejected);
  4. one criterion blocked does not swallow unrelated criteria (ledger face);
  5. a worker cannot complete the Goal while any mandatory criterion lacks
     verified evidence (host-computed completion);
  6. modifying/replacing the original objective causes an objective-hash failure;
  7. restarting resumes the same broker-owned criterion state (no replay);
  8. doctor distinguishes enforcement from observation-only support;
  9. ordinary MCP requests are unaffected (CLI-only: no goal tools added).

Uses only unittest/tempfile/unittest.mock + sqlite3. No real home/config/DB is
touched: goal-supervisor paths and the Codex goals DB are redirected to a
TemporaryDirectory for each test.
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
        "verifier": "pytest tests/repro.py",
        "stopping_test": "exit 0",
    }
]
BOUNDED_BUDGET = {"total_tokens": 50_000, "total_seconds": 3600}


def make_goals_db(path: Path, with_usage: bool = True, rows: list[dict] | None = None) -> None:
    """Create a fake ~/.codex/goals_1.sqlite exposing thread_goals."""
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


class GoalSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.codex_home = self.home / "codex"
        self.codex_db = self.codex_home / "goals_1.sqlite"
        self.broker_home = self.home / "broker"
        self.goals_root = self.broker_home / "goals"
        self.broker_dir_patch = mock.patch.object(goal_supervisor, "BROKER_DIR", self.broker_home)
        self.goals_root_patch = mock.patch.object(goal_supervisor, "GOALS_ROOT", self.goals_root)
        self.codex_home_patch = mock.patch.object(goal_supervisor, "CODEX_HOME", self.codex_home)
        self.codex_db_patch = mock.patch.object(goal_supervisor, "CODEX_GOALS_DB", self.codex_db)
        self.broker_dir_patch.start()
        self.goals_root_patch.start()
        self.codex_home_patch.start()
        self.codex_db_patch.start()
        self.addCleanup(self.broker_dir_patch.stop)
        self.addCleanup(self.goals_root_patch.stop)
        self.addCleanup(self.codex_home_patch.stop)
        self.addCleanup(self.codex_db_patch.stop)

    # --- 1. capability probe -------------------------------------------------

    def test_probe_state_and_usage_readable_when_thread_goals_present(self):
        make_goals_db(self.codex_db, rows=[
            {
                "thread_id": "t-1", "goal_id": "g-1", "objective": "reproduce bug #123",
                "status": "paused", "token_budget": 100000, "tokens_used": 18765470,
                "time_used_seconds": 168405, "created_at_ms": 1, "updated_at_ms": 2,
            }
        ])
        probe = goal_supervisor.probe_goal_capabilities(codex_path=None)
        self.assertTrue(probe["goal_state_readable"])
        self.assertTrue(probe["goal_usage_readable"])
        self.assertFalse(probe["not_available"])
        self.assertEqual(probe["detail"]["active_goal_rows"], 1)

    def test_probe_not_available_when_goals_db_missing(self):
        probe = goal_supervisor.probe_goal_capabilities(codex_path=None)
        self.assertTrue(probe["not_available"])
        self.assertFalse(probe["goal_state_readable"])
        self.assertFalse(probe["goal_completion_enforceable"])
        self.assertFalse(probe["observability_only"])

    def test_probe_usage_unreadable_without_telemetry_columns(self):
        # A thread_goals table without usage columns: state readable, usage not.
        self.codex_db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.codex_db))
        try:
            con.execute(
                "CREATE TABLE thread_goals (thread_id TEXT, goal_id TEXT, objective TEXT, status TEXT)"
            )
            con.commit()
        finally:
            con.close()
        probe = goal_supervisor.probe_goal_capabilities(codex_path=None)
        self.assertTrue(probe["goal_state_readable"])
        self.assertFalse(probe["goal_usage_readable"])

    def test_probe_dispatch_available_is_enforceable(self):
        make_goals_db(self.codex_db)
        with mock.patch.object(goal_supervisor, "_smoke_version", return_value=(True, "codex-cli 0.147.0")):
            with mock.patch.object(goal_supervisor, "_probe_dispatch", return_value=True):
                probe = goal_supervisor.probe_goal_capabilities(codex_path="/fake/codex.exe")
        self.assertTrue(probe["goal_work_dispatch_available"])
        self.assertTrue(probe["goal_completion_enforceable"])
        self.assertFalse(probe["observability_only"])

    def test_probe_no_dispatch_is_observability_only(self):
        # State readable but dispatch unavailable -> honest observability_only,
        # enforcement never claimed (issue #4 acceptance 8).
        make_goals_db(self.codex_db)
        with mock.patch.object(goal_supervisor, "_smoke_version", return_value=(True, "codex-cli 0.147.0")):
            with mock.patch.object(goal_supervisor, "_probe_dispatch", return_value=False):
                probe = goal_supervisor.probe_goal_capabilities(codex_path="/fake/codex.exe")
        self.assertTrue(probe["goal_state_readable"])
        self.assertFalse(probe["goal_work_dispatch_available"])
        self.assertTrue(probe["observability_only"])
        self.assertFalse(probe["goal_completion_enforceable"])
        self.assertEqual(probe["goal_pause_resume_available"], None)

    # --- 2. contract validation ---------------------------------------------

    def test_unbounded_superlative_rejected(self):
        # Acceptance 1: "find the best X until a winner exists" is rejected before work starts.
        result = goal_supervisor.validate_goal_contract(
            "find the best X until a winner exists",
            BOUNDED_CRITERIA,
            budgets=BOUNDED_BUDGET,
        )
        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "goal_contract_unbounded")
        self.assertTrue(result["unbounded_matches"])

    def test_budget_required_or_explicit_unbudgeted(self):
        # Acceptance 2: no budget and not unbudgeted -> unbounded; explicit
        # unbudgeted is visible, not silently unbounded.
        bounded = goal_supervisor.validate_goal_contract(
            "reproduce bug #123 and add a regression test",
            BOUNDED_CRITERIA,
            budgets=BOUNDED_BUDGET,
        )
        self.assertTrue(bounded["valid"])

        no_budget = goal_supervisor.validate_goal_contract(
            "reproduce bug #123 and add a regression test", BOUNDED_CRITERIA
        )
        self.assertFalse(no_budget["valid"])
        self.assertEqual(no_budget["reason"], "goal_contract_unbounded")
        self.assertIn("budget_required_or_unbudgeted", no_budget["errors"])

        unbudgeted = goal_supervisor.validate_goal_contract(
            "reproduce bug #123 and add a regression test",
            BOUNDED_CRITERIA,
            unbudgeted=True,
        )
        self.assertTrue(unbudgeted["valid"])
        self.assertTrue(unbudgeted["unbudgeted"])
        self.assertEqual(unbudgeted["budget_plan"], {})

    def test_mandatory_criterion_requires_evidence_verifier_stopping_test(self):
        result = goal_supervisor.validate_goal_contract(
            "reproduce bug #123",
            [{"id": "C-01", "mandatory": True}],
            budgets=BOUNDED_BUDGET,
        )
        self.assertFalse(result["valid"])
        errors = " ".join(result["errors"])
        self.assertIn("criterion_missing_evidence", errors)
        self.assertIn("criterion_missing_verifier", errors)
        self.assertIn("criterion_missing_stopping_test", errors)

    def test_objective_retained_verbatim_with_hash(self):
        objective = "reproduce bug #123 and add a regression test"
        result = goal_supervisor.validate_goal_contract(objective, BOUNDED_CRITERIA, budgets=BOUNDED_BUDGET)
        self.assertEqual(result["objective"], objective)
        self.assertTrue(result["objective_hash"])
        self.assertEqual(
            result["objective_hash"],
            goal_supervisor.objective_hash(objective),
        )

    # --- 3. ledger + host-computed completion --------------------------------

    def test_create_goal_persists_config_and_ledger(self):
        result = goal_supervisor.create_goal(
            "reproduce bug #123 and add a regression test",
            BOUNDED_CRITERIA,
            boundaries=["prod/"],
            budgets=BOUNDED_BUDGET,
        )
        self.assertTrue(result["created"])
        goal_ref = result["goal_ref"]
        directory = self.goals_root / goal_ref
        config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
        ledger = json.loads((directory / "ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(config["objective"], "reproduce bug #123 and add a regression test")
        self.assertEqual(config["objective_hash"], result["objective_hash"])
        self.assertEqual(config["boundaries"], ["prod/"])
        self.assertEqual(ledger["criteria"]["C-01"]["status"], "pending")
        self.assertTrue((directory / "actions.jsonl").exists())

    def test_unverified_mandatory_criterion_blocks_completion(self):
        # Acceptance 5: a worker cannot complete the Goal while a mandatory
        # criterion lacks verified evidence.
        result = goal_supervisor.create_goal(
            "reproduce bug #123 and add a regression test", BOUNDED_CRITERIA, budgets=BOUNDED_BUDGET
        )
        goal_ref = result["goal_ref"]
        completion = goal_supervisor.complete_goal(goal_ref)
        self.assertFalse(completion["completed"])
        self.assertEqual(completion["reason"], "criteria_unverified")
        self.assertIn("C-01: not verified", completion["missing"])

    def test_objective_hash_change_fails_completion(self):
        # Acceptance 6: modifying/replacing the original objective -> hash failure.
        result = goal_supervisor.create_goal(
            "reproduce bug #123 and add a regression test", BOUNDED_CRITERIA, budgets=BOUNDED_BUDGET
        )
        goal_ref = result["goal_ref"]
        ledger_path = self.goals_root / goal_ref / "ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger["objective_hash"] = goal_supervisor.objective_hash("a different objective")
        ledger_path.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")

        status = goal_supervisor.get_goal_status(goal_ref)
        self.assertFalse(status["hash_match"])
        completion = goal_supervisor.complete_goal(goal_ref)
        self.assertFalse(completion["completed"])
        self.assertEqual(completion["reason"], "objective_hash_changed")

    def test_blocked_criterion_reports_blocker_open_and_leaves_others_alone(self):
        # Acceptance 4 (ledger face): a blocker on one criterion does not swallow
        # unrelated ready criteria.
        criteria = [
            {"id": "C-01", "required_evidence": ["tests/a.py"], "verifier": "pytest tests/a.py", "stopping_test": "exit 0"},
            {"id": "C-02", "required_evidence": ["tests/b.py"], "verifier": "pytest tests/b.py", "stopping_test": "exit 0"},
        ]
        result = goal_supervisor.create_goal(
            "reproduce bug #123", criteria, budgets=BOUNDED_BUDGET
        )
        goal_ref = result["goal_ref"]
        blocked = goal_supervisor.record_evidence(goal_ref, "C-01", ["tests/a.py"], status_hint="blocked")
        self.assertTrue(blocked["recorded"])
        self.assertEqual(blocked["status"], "blocked")

        # C-02 is untouched and still trackable independently.
        other = goal_supervisor.record_evidence(goal_ref, "C-02", ["tests/b.py"])
        self.assertTrue(other["recorded"])
        status = goal_supervisor.get_goal_status(goal_ref)
        self.assertEqual(status["criteria"]["C-01"]["status"], "blocked")
        self.assertEqual(status["criteria"]["C-02"]["status"], "pending")
        self.assertEqual(status["criteria"]["C-02"]["observed_evidence"], ["tests/b.py"])
        self.assertEqual(status["status"], "blocked")

        completion = goal_supervisor.complete_goal(goal_ref)
        self.assertFalse(completion["completed"])
        self.assertEqual(completion["reason"], "blocker_open")

    def test_evidence_cannot_set_verified_in_phase1(self):
        # Phase 1 has no verifier: "verified" is unreachable so a worker can
        # never self-complete. Attempting it is rejected.
        result = goal_supervisor.create_goal(
            "reproduce bug #123", BOUNDED_CRITERIA, budgets=BOUNDED_BUDGET
        )
        rejection = goal_supervisor.record_evidence(
            result["goal_ref"], "C-01", ["tests/repro.py"], status_hint="verified"
        )
        self.assertFalse(rejection["recorded"])
        self.assertIn("status_not_allowed", rejection["error"])

    def test_restart_resumes_same_criterion_state_without_replay(self):
        # Acceptance 7: broker-owned criterion state survives restart; status
        # reads do not replay or regenerate the transcript.
        result = goal_supervisor.create_goal(
            "reproduce bug #123", BOUNDED_CRITERIA, budgets=BOUNDED_BUDGET
        )
        goal_ref = result["goal_ref"]
        goal_supervisor.record_evidence(goal_ref, "C-01", ["tests/repro.py"], status_hint="inconclusive")

        actions_path = self.goals_root / goal_ref / "actions.jsonl"
        actions_before = len(actions_path.read_text(encoding="utf-8").splitlines())

        # "Restart": a fresh status read (and a fresh module-level read chain)
        # re-derives the same broker-owned state.
        status = goal_supervisor.get_goal_status(goal_ref)
        self.assertEqual(status["status"], "attention_required")
        self.assertEqual(status["criteria"]["C-01"]["observed_evidence"], ["tests/repro.py"])
        self.assertEqual(status["criteria"]["C-01"]["status"], "inconclusive")

        # No transcript replay: reading status never appends actions.
        actions_after = len(actions_path.read_text(encoding="utf-8").splitlines())
        self.assertEqual(actions_before, actions_after)
        self.assertEqual(status["completion"]["completed"], False)

    def test_unknown_goal_status_raises(self):
        with self.assertRaises(ValueError):
            goal_supervisor.get_goal_status("goal-does-not-exist")

    def test_live_usage_read_from_codex_goals_db(self):
        # When codex_thread_id is set and the DB is readable, status folds in
        # live usage telemetry (observability, not enforcement).
        make_goals_db(self.codex_db, rows=[
            {
                "thread_id": "t-1", "goal_id": "g-1", "objective": "x",
                "status": "paused", "token_budget": 100000, "tokens_used": 18765470,
                "time_used_seconds": 168405, "created_at_ms": 1, "updated_at_ms": 2,
            }
        ])
        result = goal_supervisor.create_goal(
            "reproduce bug #123", BOUNDED_CRITERIA, budgets=BOUNDED_BUDGET, codex_thread_id="t-1"
        )
        status = goal_supervisor.get_goal_status(result["goal_ref"])
        live = status["usage"].get("codex_live")
        self.assertIsNotNone(live)
        self.assertEqual(live["tokens_used"], 18765470)
        self.assertEqual(live["goal_status"], "paused")


class GoalCliOnlyTests(unittest.TestCase):
    """Acceptance 9: ordinary MCP requests are unaffected — CLI-only means no
    goal tools were added to the broker's MCP catalog."""

    def test_no_goal_tools_added_to_mcp_catalog(self):
        import agent_broker_mcp as broker  # noqa: WPS433

        full = broker.tools_for_current_client()
        names = {tool.get("name") for tool in full}
        self.assertFalse(any(name.startswith("goal") for name in names))
        self.assertNotIn("goal_status", names)
        self.assertNotIn("goal_dispatch", names)

    def test_goal_help_mentions_only_cli_verbs(self):
        # The goal surface is exposed through bridge CLI verbs, not MCP tools.
        self.assertIn("probe", goal_supervisor.GOAL_HELP)
        self.assertIn("contract", goal_supervisor.GOAL_HELP)
        self.assertIn("create", goal_supervisor.GOAL_HELP)


if __name__ == "__main__":
    unittest.main()
