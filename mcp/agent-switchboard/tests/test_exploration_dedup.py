"""Comprehensive Regression Test Matrix for Exploration Deduplication and Semantic Work Registry."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import routing_gate  # noqa: E402
import work_registry  # noqa: E402


class ExplorationDedupMatrixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.broker_home = Path(self.tmp.name) / ".agent-broker"
        self.registry_dir = self.broker_home / "work-registry"
        self.state_dir = self.broker_home / "routing-gate"

        self.patches = [
            mock.patch.object(work_registry, "BROKER_HOME", self.broker_home),
            mock.patch.object(work_registry, "REGISTRY_DIR", self.registry_dir),
            mock.patch.object(routing_gate, "BROKER_HOME", self.broker_home),
            mock.patch.object(routing_gate, "STATE_DIR", self.state_dir),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    # 1. Duplicate active work suppression
    def test_scenario_1_duplicate_spawn_suppressed_when_active(self):
        session_id = "sess-1"
        dec1, lease1, _ = work_registry.request_work_lease(
            parent_session=session_id,
            task_scope="repo",
            target="src/core.py",
            intent="find authentication handler",
            evidence_domain="code",
            lane="explore",
        )
        self.assertEqual(dec1, work_registry.ACTION_SPAWN_ALLOWED)
        self.assertEqual(lease1.state, work_registry.STATE_SPAWNING)

        # Agent starts
        work_registry.activate_lease(lease1.work_key, agent_id="agent-worker-1")

        # Duplicate request with identical semantic attributes
        dec2, lease2, reason = work_registry.request_work_lease(
            parent_session=session_id,
            task_scope="repo",
            target="src/core.py",
            intent="find authentication handler",
            evidence_domain="code",
            lane="explore",
        )
        self.assertEqual(dec2, work_registry.ACTION_SPAWN_SUPPRESSED_DUPLICATE)
        self.assertEqual(lease2.state, work_registry.STATE_ACTIVE)
        self.assertEqual(lease2.agent_id, "agent-worker-1")
        self.assertIn("Duplicate active work in-flight", reason)

    # 2. Reuse completed receipt
    def test_scenario_2_reuse_completed_receipt(self):
        session_id = "sess-2"
        dec1, lease1, _ = work_registry.request_work_lease(
            parent_session=session_id,
            task_scope="repo",
            target="src/models.py",
            intent="inspect user schema",
            evidence_domain="ast",
            lane="explore",
        )
        self.assertEqual(dec1, work_registry.ACTION_SPAWN_ALLOWED)
        work_registry.activate_lease(lease1.work_key, agent_id="agent-2")
        work_registry.complete_lease(
            lease1.work_key, result={"classes": ["User", "Profile"], "fields": 12}
        )

        # Subsequent request with identical work_key
        dec2, lease2, reason = work_registry.request_work_lease(
            parent_session=session_id,
            task_scope="repo",
            target="src/models.py",
            intent="inspect user schema",
            evidence_domain="ast",
            lane="explore",
        )
        self.assertEqual(dec2, work_registry.ACTION_REUSE_COMPLETED)
        self.assertEqual(lease2.state, work_registry.STATE_COMPLETED)
        self.assertEqual(lease2.result, {"classes": ["User", "Profile"], "fields": 12})
        self.assertIn("Completed receipt available", reason)

    # 3. Legitimate parallelism: distinct targets
    def test_scenario_3_legitimate_parallelism_different_targets(self):
        session_id = "sess-3"
        dec_a, lease_a, _ = work_registry.request_work_lease(
            parent_session=session_id,
            task_scope="repo",
            target="src/auth.py",
            intent="find token validator",
            evidence_domain="code",
            lane="explore",
        )
        dec_b, lease_b, _ = work_registry.request_work_lease(
            parent_session=session_id,
            task_scope="repo",
            target="src/billing.py",
            intent="find token validator",
            evidence_domain="code",
            lane="explore",
        )
        self.assertEqual(dec_a, work_registry.ACTION_SPAWN_ALLOWED)
        self.assertEqual(dec_b, work_registry.ACTION_SPAWN_ALLOWED)
        self.assertNotEqual(lease_a.work_key, lease_b.work_key)

    # 4. Legitimate parallelism: distinct evidence domains
    def test_scenario_4_legitimate_parallelism_different_domains(self):
        session_id = "sess-4"
        dec_ast, lease_ast, _ = work_registry.request_work_lease(
            parent_session=session_id,
            task_scope="repo",
            target="src/auth.py",
            intent="analyze call graph",
            evidence_domain="ast",
            lane="explore",
        )
        dec_git, lease_git, _ = work_registry.request_work_lease(
            parent_session=session_id,
            task_scope="repo",
            target="src/auth.py",
            intent="analyze call graph",
            evidence_domain="git",
            lane="explore",
        )
        self.assertEqual(dec_ast, work_registry.ACTION_SPAWN_ALLOWED)
        self.assertEqual(dec_git, work_registry.ACTION_SPAWN_ALLOWED)
        self.assertNotEqual(lease_ast.work_key, lease_git.work_key)

    # 5. Cross-lane regression 1: same semantic work with different lanes suppressed
    def test_scenario_5_cross_lane_same_semantic_work_suppressed(self):
        session_id = "sess-5"
        dec_explore, lease_exp, _ = work_registry.request_work_lease(
            parent_session=session_id,
            task_scope="repo",
            target="src/auth.py",
            intent="verify token signature",
            evidence_domain="code",
            lane="explore",
        )
        self.assertEqual(dec_explore, work_registry.ACTION_SPAWN_ALLOWED)
        work_registry.activate_lease(lease_exp.work_key, agent_id="agent-explore-1")

        # Second request with lane='audit' but SAME semantic target and intent must be SUPPRESSED
        dec_audit, lease_aud, reason = work_registry.request_work_lease(
            parent_session=session_id,
            task_scope="repo",
            target="src/auth.py",
            intent="verify token signature",
            evidence_domain="code",
            lane="audit",
        )
        self.assertEqual(dec_audit, work_registry.ACTION_SPAWN_SUPPRESSED_DUPLICATE)
        self.assertEqual(lease_exp.work_key, lease_aud.work_key)
        self.assertIn("Duplicate active work in-flight", reason)

    # 6. Cross-lane regression 2: same task with distinct explicit independence roles allowed
    def test_scenario_6_distinct_roles_allowed_parallelism(self):
        session_id = "sess-6"
        dec_src, lease_src, _ = work_registry.request_work_lease(
            parent_session=session_id,
            task_scope="wp-audit",
            target="src/auth.py",
            intent="security audit",
            evidence_domain="code",
            explicit_independence_role="source-inspection",
        )
        dec_opp, lease_opp, _ = work_registry.request_work_lease(
            parent_session=session_id,
            task_scope="wp-audit",
            target="src/auth.py",
            intent="security audit",
            evidence_domain="code",
            explicit_independence_role="opposite-review",
        )
        self.assertEqual(dec_src, work_registry.ACTION_SPAWN_ALLOWED)
        self.assertEqual(dec_opp, work_registry.ACTION_SPAWN_ALLOWED)
        self.assertNotEqual(lease_src.work_key, lease_opp.work_key)

    # 7. Cross-lane regression 3: different provider and model with same semantic work deduped
    def test_scenario_7_different_provider_model_same_semantic_work_deduped(self):
        session_id = "sess-7"
        dec_claude, lease_claude, _ = work_registry.request_work_lease(
            parent_session=session_id,
            task_scope="wp-review",
            target="src/crypto.py",
            intent="verify cipher implementation",
            evidence_domain="code",
            provider="claude",
            model="claude-haiku-4-5",
            agent_type="Explore",
            lane="explore",
        )
        self.assertEqual(dec_claude, work_registry.ACTION_SPAWN_ALLOWED)
        work_registry.activate_lease(lease_claude.work_key, agent_id="agent-claude-haiku")

        # Second request from Codex Luna with same semantic identity must be SUPPRESSED
        dec_codex, lease_codex, reason = work_registry.request_work_lease(
            parent_session=session_id,
            task_scope="wp-review",
            target="src/crypto.py",
            intent="verify cipher implementation",
            evidence_domain="code",
            provider="codex",
            model="gpt-5.6-luna",
            agent_type="explorer",
            lane="worker",
        )
        self.assertEqual(dec_codex, work_registry.ACTION_SPAWN_SUPPRESSED_DUPLICATE)
        self.assertEqual(lease_claude.work_key, lease_codex.work_key)
        self.assertEqual(lease_codex.agent_id, "agent-claude-haiku")
        self.assertIn("Duplicate active work in-flight", reason)

    # 8. Retry within budget after failure
    def test_scenario_8_retry_within_budget_after_failure(self):
        session_id = "sess-8"
        _, lease1, _ = work_registry.request_work_lease(
            parent_session=session_id,
            task_scope="repo",
            target="src/db.py",
            intent="check pool connections",
            max_retries=3,
        )
        work_registry.fail_lease(lease1.work_key, error="connection timeout")

        # Retry attempt 1
        dec2, lease2, reason = work_registry.request_work_lease(
            parent_session=session_id,
            task_scope="repo",
            target="src/db.py",
            intent="check pool connections",
            max_retries=3,
        )
        self.assertEqual(dec2, work_registry.ACTION_SPAWN_ALLOWED)
        self.assertEqual(lease2.retry_count, 1)
        self.assertEqual(lease2.state, work_registry.STATE_SPAWNING)
        self.assertIn("Retry attempt 1/3 granted", reason)

    # 9. Retry budget exhausted
    def test_scenario_9_retry_budget_exhausted(self):
        session_id = "sess-9"
        # Initial attempt (attempt 0) + 3 retries (attempts 1, 2, 3) = 4 failures
        for i in range(1, 5):
            dec, lease, _ = work_registry.request_work_lease(
                parent_session=session_id,
                task_scope="repo",
                target="src/err.py",
                intent="reproduce crash",
                max_retries=3,
            )
            self.assertEqual(dec, work_registry.ACTION_SPAWN_ALLOWED)
            work_registry.fail_lease(lease.work_key, error=f"error-{i}")

        # 5th attempt: budget is exhausted (exceeded max_retries=3)
        dec_exhausted, lease_exhausted, reason = work_registry.request_work_lease(
            parent_session=session_id,
            task_scope="repo",
            target="src/err.py",
            intent="reproduce crash",
            max_retries=3,
        )
        self.assertEqual(dec_exhausted, work_registry.ACTION_RETRY_BUDGET_EXHAUSTED)
        self.assertEqual(lease_exhausted.retry_count, 3)
        self.assertEqual(lease_exhausted.state, work_registry.STATE_FAILED)
        self.assertIn("Retry budget exhausted", reason)

    # 10. Lease timeout recovery
    def test_scenario_10_lease_timeout_recovery(self):
        session_id = "sess-10"
        dec1, lease1, _ = work_registry.request_work_lease(
            parent_session=session_id,
            task_scope="repo",
            target="src/slow.py",
            intent="indexing",
            ttl_seconds=0.1,
            max_retries=2,
        )
        self.assertEqual(dec1, work_registry.ACTION_SPAWN_ALLOWED)
        work_registry.activate_lease(lease1.work_key, agent_id="agent-slow", ttl_seconds=0.1)

        # Wait for TTL expiration
        time.sleep(0.15)

        # Subsequent request detects timeout and permits recovery retry
        dec2, lease2, reason = work_registry.request_work_lease(
            parent_session=session_id,
            task_scope="repo",
            target="src/slow.py",
            intent="indexing",
            ttl_seconds=60.0,
            max_retries=2,
        )
        self.assertEqual(dec2, work_registry.ACTION_SPAWN_ALLOWED)
        self.assertEqual(lease2.retry_count, 1)
        self.assertIn("Retry attempt 1/2 granted", reason)

    # 11. Stream dropout reconciliation (Cross-lane regression 5)
    def test_scenario_11_stream_dropout_reconciliation(self):
        session_id = "sess-11"
        # Create two active leases
        _, lease_alive, _ = work_registry.request_work_lease(
            parent_session=session_id, task_scope="s", target="alive.py", intent="i1"
        )
        work_registry.activate_lease(lease_alive.work_key, agent_id="agent-alive", ttl_seconds=300)

        _, lease_dead, _ = work_registry.request_work_lease(
            parent_session=session_id, task_scope="s", target="dead.py", intent="i2",
            ttl_seconds=0.1
        )
        work_registry.activate_lease(lease_dead.work_key, agent_id="agent-dead", ttl_seconds=0.1)
        time.sleep(0.15)

        # Host reconnects: only "agent-alive" is alive in active workers
        reconciled = work_registry.reconcile_leases(
            parent_session=session_id, active_agent_ids={"agent-alive"}
        )
        states = {l.target: l.state for l in reconciled}
        self.assertEqual(states["alive.py"], work_registry.STATE_ACTIVE)
        self.assertEqual(states["dead.py"], work_registry.STATE_FAILED)

    # 12. Turn restart and replay deduplication
    def test_scenario_12_restart_replay_dedup(self):
        session_id = "sess-12"
        # Turn 1: request and start agent
        payload1 = {
            "session_id": session_id,
            "tool_name": "Agent",
            "tool_input": {
                "subagent_type": "Explore",
                "prompt": "Find all API route handlers in server/",
                "target": "server/",
                "evidence_domain": "code",
            },
        }
        res1 = routing_gate.pre_tool_use(payload1)
        self.assertEqual(res1, {})  # Allowed

        routing_gate.subagent_start({
            "session_id": session_id,
            "agent_id": "explore-agent-1",
            "agent_type": "Explore",
            "turn_id": "turn-1",
        })

        # Turn 1 replay / re-entry: identical prompt in same session
        res2 = routing_gate.pre_tool_use(payload1)
        self.assertIsNotNone(res2)
        self.assertEqual(res2.get("hookSpecificOutput", {}).get("permissionDecision"), "deny")
        self.assertIn("Duplicate exploration work suppressed",
                      res2.get("hookSpecificOutput", {}).get("permissionDecisionReason", ""))

        # Turn 1 completes
        routing_gate.subagent_stop({
            "session_id": session_id,
            "agent_id": "explore-agent-1",
            "agent_type": "Explore",
            "turn_id": "turn-1",
            "last_assistant_message": "Found 5 route handlers",
        })

        # Turn 2: replaying the same completed task reuses receipt
        res3 = routing_gate.pre_tool_use(payload1)
        self.assertIsNotNone(res3)
        self.assertEqual(res3.get("hookSpecificOutput", {}).get("permissionDecision"), "deny")
        self.assertIn("identical exploration has already COMPLETED",
                      res3.get("hookSpecificOutput", {}).get("permissionDecisionReason", ""))

    # 13. Brain vs Orchestrator ownership boundaries (Cross-lane regression 3)
    def test_scenario_13_brain_vs_orchestrator_ownership(self):
        session_id = "sess-13"
        # Brain initiates brain-owned lease
        dec1, lease1, _ = work_registry.request_work_lease(
            parent_session=session_id,
            task_scope="wp1",
            target="spec.md",
            intent="review spec",
            brain_owned=True,
            orchestrator_owned=False,
        )
        self.assertEqual(dec1, work_registry.ACTION_SPAWN_ALLOWED)
        self.assertTrue(lease1.brain_owned)
        self.assertFalse(lease1.orchestrator_owned)

        # Conflicting orchestrator ownership request attempting takeover
        dec2, _, reason = work_registry.request_work_lease(
            parent_session=session_id,
            task_scope="wp1",
            target="spec.md",
            intent="review spec",
            brain_owned=False,
            orchestrator_owned=True,
        )
        self.assertEqual(dec2, work_registry.ACTION_OWNERSHIP_CONFLICT)
        self.assertIn("Ownership conflict: existing lease owned by brain", reason)

    # 14. Concurrent lease acquisition race condition
    def test_scenario_14_concurrent_lease_acquisition_race(self):
        session_id = "sess-14"
        results = []

        def _worker(thread_id: int):
            dec, lease, _ = work_registry.request_work_lease(
                parent_session=session_id,
                task_scope="repo",
                target="race.py",
                intent="parallel search",
            )
            return dec, thread_id

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(_worker, i) for i in range(8)]
            for f in futures:
                results.append(f.result())

        decisions = [r[0] for r in results]
        # Exactly one thread must win ACTION_SPAWN_ALLOWED, all other 7 must be SPAWN_SUPPRESSED_DUPLICATE
        self.assertEqual(decisions.count(work_registry.ACTION_SPAWN_ALLOWED), 1)
        self.assertEqual(
            decisions.count(work_registry.ACTION_SPAWN_SUPPRESSED_DUPLICATE), 7
        )


if __name__ == "__main__":
    unittest.main()
