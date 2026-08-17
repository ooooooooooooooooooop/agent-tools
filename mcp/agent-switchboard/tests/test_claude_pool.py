"""Focused standard-library tests for claude_pool.py — broker-owned concurrency
control for multiple running Claude Code sessions.

Covers the module's acceptance criteria:
  1. a machine-wide register records every Claude-owned process group with scope;
  2. duplicate session from a DIFFERENT owner fails closed (claude_pool_duplicate_session);
  3. a bounded concurrency ceiling fails closed under the machine-wide cap;
  4. the per-project ceiling fails closed with a distinct reason;
  5. orphan reaping flags a live claude_pid whose owner pid is dead (attention_required)
     and releases the slot without reuse;
  6. owner-live sessions are NOT reaped (skip / liveness respected);
  7. the workspace write lease serializes one project (FileLock-backed) and fails closed
     on contention;
  8. pool_status reports schema health and enforced ceilings honestly.

Uses only unittest/tempfile/sqlite3. The pool DB path is redirected to a TemporaryDirectory
for each test; no real ~/.agent-broker state or process is touched.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import claude_pool  # noqa: E402

SESSION_A = "11111111-1111-4111-8111-111111111111"
SESSION_B = "22222222-2222-4222-8222-222222222222"
SESSION_C = "33333333-3333-4333-8333-333333333333"
PROJECT = r"C:\Projects\Alpha"


class FakePidTable:
    """configurable liveness for a set of owner pids."""

    def __init__(self, alive_pids: set[int]):
        self.alive_pids = set(alive_pids)

    def __call__(self, pid: Any) -> bool:
        try:
            return int(pid) in self.alive_pids
        except (TypeError, ValueError):
            return False


class ClaudePoolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "broker"
        self.db = self.home / "claude_pool.db"

    # --- 1. register / list --------------------------------------------------

    def test_register_and_list_pool(self):
        result = claude_pool.register_owned_session(
            self.db, SESSION_A, "managed_supervisor", 100,
            claude_pid=200, project_root=PROJECT,
        )
        self.assertTrue(result["registered"])
        claude_pool.register_owned_session(
            self.db, SESSION_B, "cli_worker", 300, project_root=r"C:\Other"
        )
        pool = claude_pool.list_pool(self.db)
        self.assertEqual(pool["summary"]["total"], 2)
        self.assertEqual(pool["summary"]["running"], 2)
        sessions = {s["session_id"]: s for s in pool["sessions"]}
        self.assertEqual(sessions[SESSION_A]["owner_kind"], "managed_supervisor")
        self.assertEqual(sessions[SESSION_A]["claude_pid"], 200)

    def test_register_duplicate_session_different_owner_fails_closed(self):
        claude_pool.register_owned_session(
            self.db, SESSION_A, "managed_supervisor", 100, claude_pid=200, project_root=PROJECT
        )
        result = claude_pool.register_owned_session(
            self.db, SESSION_A, "cli_worker", 999, project_root=PROJECT
        )
        self.assertFalse(result["registered"])
        self.assertEqual(result["reason"], "claude_pool_duplicate_session")
        # Original owner still owns the row.
        pool = claude_pool.list_pool(self.db)
        self.assertEqual(pool["sessions"][0]["owner_pid"], 100)

    def test_register_same_owner_updates_in_place(self):
        claude_pool.register_owned_session(
            self.db, SESSION_A, "managed_supervisor", 100, claude_pid=200, project_root=PROJECT
        )
        result = claude_pool.register_owned_session(
            self.db, SESSION_A, "managed_supervisor", 100, claude_pid=222, project_root=PROJECT
        )
        self.assertTrue(result["registered"])
        self.assertEqual(result["detail"]["claude_pid"], 222)

    def test_unregister_removes_session(self):
        claude_pool.register_owned_session(
            self.db, SESSION_A, "managed_supervisor", 100, project_root=PROJECT
        )
        result = claude_pool.unregister_owned_session(self.db, SESSION_A)
        self.assertTrue(result["unregistered"])
        self.assertEqual(claude_pool.list_pool(self.db)["summary"]["total"], 0)

    # --- 3/4. ceilings -------------------------------------------------------

    def test_claim_machine_wide_ceiling_fails_closed(self):
        # Two running sessions, max_active=2.
        claude_pool.register_owned_session(self.db, SESSION_A, "cli_worker", 100, project_root=PROJECT)
        claude_pool.register_owned_session(self.db, SESSION_B, "cli_worker", 101, project_root=PROJECT)
        result = claude_pool.claim_launch_slot(
            self.db, owner_kind="cli_worker", owner_pid=102,
            session_id=SESSION_C, project_root=PROJECT, max_active=2, max_per_project=10,
        )
        self.assertFalse(result["claimed"])
        self.assertEqual(result["reason"], "claude_pool_full")

    def test_claim_per_project_ceiling_fails_closed(self):
        claude_pool.register_owned_session(self.db, SESSION_A, "cli_worker", 100, project_root=PROJECT)
        claude_pool.register_owned_session(self.db, SESSION_B, "cli_worker", 101, project_root=PROJECT)
        result = claude_pool.claim_launch_slot(
            self.db, owner_kind="cli_worker", owner_pid=102,
            session_id=SESSION_C, project_root=PROJECT, max_active=10, max_per_project=2,
        )
        self.assertFalse(result["claimed"])
        self.assertEqual(result["reason"], "claude_pool_project_full")

    def test_claim_succeeds_within_ceilings(self):
        claude_pool.register_owned_session(self.db, SESSION_A, "cli_worker", 100, project_root=PROJECT)
        result = claude_pool.claim_launch_slot(
            self.db, owner_kind="managed_supervisor", owner_pid=101,
            session_id=SESSION_B, project_root=PROJECT, max_active=2, max_per_project=2,
        )
        self.assertTrue(result["claimed"])

    # --- 5/6. orphan reaping -------------------------------------------------

    def test_reap_marks_dead_owner_attention_required(self):
        claude_pool.register_owned_session(
            self.db, SESSION_A, "managed_supervisor", 500, claude_pid=600, project_root=PROJECT
        )
        # Owner 500 is dead, claude pid 600 reported (owner table says live) -> orphan.
        alive = FakePidTable({600})
        result = claude_pool.reap_orphans(self.db, pid_is_alive=alive)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["reaped"][0]["session_id"], SESSION_A)
        pool = claude_pool.list_pool(self.db)
        self.assertEqual(pool["sessions"][0]["status"], "attention_required")

    def test_reap_keeps_live_owner(self):
        claude_pool.register_owned_session(
            self.db, SESSION_A, "managed_supervisor", 500, claude_pid=600, project_root=PROJECT
        )
        alive = FakePidTable({500, 600})
        result = claude_pool.reap_orphans(self.db, pid_is_alive=alive)
        self.assertEqual(result["count"], 0)
        self.assertEqual(claude_pool.list_pool(self.db)["sessions"][0]["status"], "running")

    def test_reap_respects_skip(self):
        claude_pool.register_owned_session(
            self.db, SESSION_A, "managed_supervisor", 500, project_root=PROJECT
        )
        # Skip A even though its owner is dead (owner finishing its finalize).
        result = claude_pool.reap_orphans(
            self.db, pid_is_alive=FakePidTable(set()), skip={SESSION_A: True}
        )
        self.assertEqual(result["count"], 0)
        self.assertEqual(claude_pool.list_pool(self.db)["sessions"][0]["status"], "running")

    # --- 7. workspace lease --------------------------------------------------

    def test_project_write_lease_serializes_and_fails_closed(self):
        lease_a = claude_pool.ProjectWriteLease(self.home, PROJECT, timeout=0.1)
        lease_b = claude_pool.ProjectWriteLease(self.home, PROJECT, timeout=0.1)
        self.assertTrue(lease_a.acquire())
        # Non-contending different-project lease succeeds.
        lease_c = claude_pool.ProjectWriteLease(self.home, r"C:\Other", timeout=0.1)
        self.assertTrue(lease_c.acquire())
        lease_c.release()
        # Same project is held -> second acquisition fails closed (returns False).
        self.assertFalse(lease_b.acquire())
        lease_a.release()
        # After release the second contender can acquire.
        self.assertTrue(lease_b.acquire())
        lease_b.release()

    # --- 8. pool_status ---------------------------------------------------------

    def test_pool_status_reports_schema_and_ceilings(self):
        claude_pool.register_owned_session(self.db, SESSION_A, "cli_worker", 100, project_root=PROJECT)
        with mock.patch.dict(
            os.environ,
            {"AGENT_BROKER_CLAUDE_POOL_MAX": "4", "AGENT_BROKER_CLAUDE_POOL_MAX_PER_PROJECT": "1"},
        ):
            status = claude_pool.pool_status(self.db)
        self.assertTrue(status["schema_ok"])
        self.assertEqual(status["max_active_processes"], 4)
        self.assertEqual(status["max_per_project"], 1)
        self.assertEqual(status["summary"]["total"], 1)
        self.assertEqual(status["owner_kinds"], list(claude_pool.OWNER_KINDS))

    def test_set_session_status_flags_reason(self):
        claude_pool.register_owned_session(self.db, SESSION_A, "cli_worker", 100, project_root=PROJECT)
        result = claude_pool.set_session_status(
            self.db, SESSION_A, "attention_required", reason="owner_crashed"
        )
        self.assertTrue(result["updated"])
        row = claude_pool.list_pool(self.db)["sessions"][0]
        self.assertEqual(row["status"], "attention_required")
        self.assertEqual(row["reason"], "owner_crashed")


if __name__ == "__main__":
    unittest.main()
