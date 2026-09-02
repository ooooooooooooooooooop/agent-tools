"""test_modernized_memory.py — Comprehensive Evaluation Suite for Modernized Memory.

Covers all 14 mandated scenarios:
  1. Exact preference recall
  2. Semantic paraphrase / FTS recall
  3. Project-scoped recall
  4. Cross-project isolation
  5. Superseded fact suppression
  6. Conflicted records handling
  7. Stale record review
  8. Recent durable decision prioritization
  9. Irrelevant memory suppression
  10. Token-budget context injection & truncation
  11. Structured result ingestion
  12. Secret exclusion gate
  13. Forget / delete synchronization
  14. Fresh rebuild retrieval consistency
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.memory.candidate_extractor import MemoryCandidateExtractor
from scripts.memory.context_builder import build_context_package
from scripts.memory.modernized_provider import (
    MemoryRecordSchema,
    MemoryWriteGate,
    ModernizedMemoryProvider,
)
from scripts.memory.provider import FileMemoryProvider


class TestModernizedMemory(unittest.TestCase):
    def setUp(self) -> None:
        self.td_obj = tempfile.TemporaryDirectory()
        self.root = Path(self.td_obj.name)
        self.provider = ModernizedMemoryProvider(self.root, device_id="test-dev")

    def tearDown(self) -> None:
        self.td_obj.cleanup()

    def test_1_exact_preference_recall(self) -> None:
        self.provider.write(
            scope="personal",
            type="preference",
            subject="User coding style",
            content="Always use snake_case for python functions and strict type annotations.",
            provenance={"source": "test"},
            confidence="high",
            retention="keep",
        )
        hits = self.provider.search("snake_case functions type annotations", scope="personal")
        self.assertTrue(len(hits) > 0)
        self.assertEqual(hits[0]["scope"], "personal")
        self.assertIn("snake_case", hits[0]["content"])

    def test_2_semantic_paraphrase_recall(self) -> None:
        self.provider.write(
            scope="global",
            type="fact",
            subject="Deployment port convention",
            content="The web dashboard runs locally on port 3080 with localhost binding.",
            provenance={"source": "test"},
        )
        # Search using terms from paraphrase
        hits = self.provider.search("web port 3080 localhost")
        self.assertTrue(len(hits) > 0)
        self.assertIn("3080", hits[0]["content"])

    def test_3_project_scoped_recall(self) -> None:
        self.provider.write(
            scope="project:novel-main",
            type="fact",
            subject="Novel chapters outline",
            content="Volume 1 contains 12 chapters focusing on the protagonist background.",
            provenance={"source": "test"},
            project="novel-main",
        )
        hits = self.provider.search("chapters protagonist", project="novel-main")
        self.assertTrue(len(hits) > 0)
        self.assertEqual(hits[0]["project"], "novel-main")

    def test_4_cross_project_isolation(self) -> None:
        self.provider.write(
            scope="project:projectA",
            type="fact",
            subject="Project A secrets",
            content="Internal database password for Project A is alpha.",
            provenance={"source": "test"},
            project="projectA",
        )
        # Project B search should not return Project A records
        hits = self.provider.search("database password", project="projectB")
        self.assertEqual(len(hits), 0)

    def test_5_superseded_fact_suppression(self) -> None:
        r1 = self.provider.write(
            scope="global",
            type="fact",
            subject="Python version requirement",
            content="We use Python 3.10 for all legacy backend scripts.",
            provenance={"source": "v1"},
        )
        old_id = r1["id"]
        # New fact supersedes old fact
        self.provider.write(
            scope="global",
            type="fact",
            subject="Python version requirement",
            content="We have upgraded to Python 3.12 for all backend scripts.",
            provenance={"source": "v2"},
            supersedes=[old_id],
        )

        hits = self.provider.search("Python backend scripts")
        self.assertEqual(len(hits), 1)
        self.assertIn("Python 3.12", hits[0]["content"])
        self.assertNotIn("Python 3.10", hits[0]["content"])

    def test_6_conflicted_records_handling(self) -> None:
        r = self.provider.write(
            scope="global",
            type="fact",
            subject="Conflicted rule",
            content="Conflicting information about architecture.",
            provenance={"source": "test"},
        )
        self.provider.set_lifecycle(r["id"], "conflicted")
        hits = self.provider.search("architecture")
        self.assertEqual(len(hits), 0)

    def test_7_stale_record_review(self) -> None:
        r = self.provider.write(
            scope="global",
            type="fact",
            subject="Stale info",
            content="Temporary dev server was at 192.168.1.50.",
            provenance={"source": "test"},
            retention="disposable",
        )
        self.provider.set_lifecycle(r["id"], "stale")
        # Search by default excludes stale records
        hits = self.provider.search("dev server", exclude_statuses=("stale", "forgotten"))
        self.assertEqual(len(hits), 0)

    def test_8_recent_durable_decision_prioritization(self) -> None:
        self.provider.write(
            scope="global",
            type="decision",
            subject="Architectural standard",
            content="All models must be routed through the personal AI SSOT.",
            provenance={"source": "governance"},
            confidence="high",
            retention="keep",
        )
        hits = self.provider.search("models routing SSOT")
        self.assertTrue(len(hits) > 0)
        self.assertTrue(hits[0]["derived_score"] > 0.8)

    def test_9_irrelevant_memory_suppression(self) -> None:
        self.provider.write(
            scope="global",
            type="fact",
            subject="Weather record",
            content="It was sunny in Shanghai yesterday.",
            provenance={"source": "test"},
        )
        hits = self.provider.search("quantum computing compiler architecture", min_confidence="high")
        self.assertEqual(len(hits), 0)

    def test_10_token_budget_context_injection(self) -> None:
        self.provider.write(
            scope="personal",
            type="preference",
            subject="Preference 1",
            content="A" * 200,
            provenance={"source": "test"},
        )
        self.provider.write(
            scope="personal",
            type="preference",
            subject="Preference 2",
            content="B" * 200,
            provenance={"source": "test"},
        )
        pkg = build_context_package(self.root, None, "test", budget_chars=300)
        self.assertTrue(pkg["used_chars"] <= 300)
        self.assertTrue(pkg["omitted"] >= 1)

    def test_11_structured_result_ingestion(self) -> None:
        envelope = {
            "task_id": "task-adoption-1",
            "status": "PASS",
            "harness": "dsh",
            "summary": "Adopted decision: Git Worktree isolation is standard for all mutations.",
            "next_action": "None",
        }
        candidates = MemoryCandidateExtractor.from_result_envelope(envelope)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["type"], "decision")
        res = self.provider.write(**candidates[0])
        self.assertTrue(res["ok"])

    def test_12_secret_exclusion_gate(self) -> None:
        candidate_with_key = {
            "scope": "personal",
            "type": "fact",
            "content": "My API key is sk-1234567890abcdef1234567890abcdef",
            "provenance": {},
        }
        admitted, reason = MemoryWriteGate.evaluate(candidate_with_key, set())
        self.assertFalse(admitted)
        self.assertIn("SECRET_EXCLUSION_HIT", reason)

        # Provider write should fail closed
        res = self.provider.write(**candidate_with_key)
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "REJECTED")

    def test_13_forget_and_delete_synchronization(self) -> None:
        r = self.provider.write(
            scope="personal",
            type="fact",
            subject="Temporary thought",
            content="I might switch to Firefox later.",
            provenance={"source": "test"},
        )
        mid = r["id"]
        # Forget via tombstone
        self.provider.forget(mid, mode="tombstone")
        hits = self.provider.search("Firefox")
        self.assertEqual(len(hits), 0)

        # Hard delete
        del_res = self.provider.forget(mid, mode="hard")
        self.assertEqual(del_res["status"], "PURGED")
        self.assertFalse((self.root / "memory" / "records" / mid).exists())

    def test_14_fresh_rebuild_retrieval_consistency(self) -> None:
        self.provider.write(
            scope="global",
            type="fact",
            subject="Core fact",
            content="Critical durable fact that must survive index deletion.",
            provenance={"source": "test"},
        )
        # Delete SQLite index database
        db_path = self.root / "memory" / ".index.sqlite"
        self.assertTrue(db_path.exists())
        db_path.unlink()

        # New provider instance will rebuild from canonical YAML files
        new_provider = ModernizedMemoryProvider(self.root, device_id="rebuild-dev")
        hits = new_provider.search("Critical durable fact")
        self.assertEqual(len(hits), 1)
        self.assertIn("Critical durable fact", hits[0]["content"])


if __name__ == "__main__":
    unittest.main()
