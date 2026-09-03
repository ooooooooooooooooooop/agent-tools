"""Regression tests for canonical writer provenance and commit audit."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import personal_ai_sync as pas  # noqa: E402


def git(repo: Path, *args: str, check: bool = True, author: str = "ownership-test") -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": author,
        "GIT_AUTHOR_EMAIL": f"{author}@invalid",
        "GIT_COMMITTER_NAME": author,
        "GIT_COMMITTER_EMAIL": f"{author}@invalid",
    }
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stdout}\n{result.stderr}")
    return (result.stdout + result.stderr).strip()


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", str(path)], capture_output=True, check=True)
    git(path, "config", "user.name", "ownership-test")
    git(path, "config", "user.email", "ownership-test@invalid")
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    git(path, "add", "seed.txt")
    git(path, "commit", "-m", "initial")
    return path


class TestCanonicalWriterProvenance(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="writer-provenance-")
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_a_governed_commit_contains_complete_provenance_and_audits_clean(self) -> None:
        repo = init_repo(self.root / "repo")
        locks = self.root / "locks"
        audit_root = self.root / "audit"
        (repo / "owned.txt").write_text("owned\n", encoding="utf-8")

        ok, message = pas.commit_owned_files(
            repo,
            ["owned.txt"],
            actor="test-writer",
            actor_type="automated",
            trigger="test-a",
            task_id="task-a",
            thread_id="thread-a",
            entrypoint="test-entrypoint",
            process_start_time="2026-09-03T00:00:00+00:00",
            run_id="run-a",
            validate=False,
            lock_root=locks,
            canonical_root=repo,
        )
        self.assertTrue(ok, message)
        receipt_path = next((locks / "receipts").glob("*.json"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertTrue(all(field in receipt for field in pas.PROVENANCE_REQUIRED_FIELDS))
        self.assertEqual(receipt["actor"], "test-writer")
        self.assertEqual(receipt["actor_type"], "automated")
        self.assertEqual(receipt["task_id"], "task-a")
        self.assertEqual(receipt["run_id"], "run-a")
        self.assertEqual(receipt["thread_id"], "thread-a")
        self.assertEqual(receipt["entrypoint"], "test-entrypoint")
        self.assertEqual(receipt["owned_scope"], ["owned.txt"])
        self.assertEqual(receipt["changed_files"], ["owned.txt"])
        self.assertTrue(pas.validate_mutation_receipt(receipt_path, repo, commit=receipt["commit"]))

        audit = pas.audit_canonical_commits(
            repo,
            audit_root=audit_root,
            receipt_root=locks / "receipts",
            previous_head=receipt["base_head"],
            persist=False,
        )
        self.assertEqual(audit["status"], "CLEAN")
        self.assertEqual(audit["unauthorized"], [])

    def test_b_raw_commit_is_detected_without_head_repair(self) -> None:
        repo = init_repo(self.root / "repo")
        audit_root = self.root / "audit"
        initial = git(repo, "rev-parse", "HEAD")
        baseline = pas.audit_canonical_commits(repo, audit_root=audit_root)
        self.assertEqual(baseline["status"], "BASELINE_INITIALIZED")

        (repo / "raw.txt").write_text("raw\n", encoding="utf-8")
        git(repo, "add", "raw.txt")
        git(repo, "commit", "-m", "raw writer")
        after = git(repo, "rev-parse", "HEAD")
        audit = pas.audit_canonical_commits(repo, audit_root=audit_root)

        self.assertEqual(audit["status"], pas.UNAUTHORIZED_OR_UNATTRIBUTED_CANONICAL_MUTATION)
        self.assertEqual(audit["result"], "REVIEW")
        self.assertEqual(audit["previous_audited_head"], initial)
        self.assertEqual(audit["current_head"], after)
        self.assertEqual(audit["unauthorized"][0]["commit"], after)
        self.assertEqual(audit["unauthorized"][0]["affected_files"], ["raw.txt"])
        self.assertEqual(git(repo, "rev-parse", "HEAD"), after)

    def test_c_governed_push_writes_remote_integrity_fields(self) -> None:
        repo = init_repo(self.root / "repo")
        remote = self.root / "remote.git"
        subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)
        git(repo, "remote", "add", "origin", str(remote))
        git(repo, "push", "-u", "origin", "main")

        mutation_root = repo.parent / f".{repo.name}.personal-ai-mutation"
        (repo / "owned.txt").write_text("owned\n", encoding="utf-8")
        ok, message = pas.commit_owned_files(
            repo,
            ["owned.txt"],
            actor="test-writer",
            actor_type="automated",
            trigger="test-c",
            task_id="task-c",
            validate=False,
            lock_root=mutation_root,
            canonical_root=repo,
        )
        self.assertTrue(ok, message)

        plan = [{"plane": "agent-tools", "action": "PUSH", "state": pas.LOCAL_AHEAD}]
        classification = {
            "path": str(repo),
            "branch": "main",
            "graph_state": pas.LOCAL_AHEAD,
            "state": pas.LOCAL_AHEAD,
            "worktree_state": pas.WORKTREE_CLEAN,
            "dirty": False,
        }
        pas.execute_plan(plan, {"agent-tools": classification}, None, "push", {
            "actor": "test-writer",
            "actor_type": "automated",
            "trigger": "test-c",
            "task_id": "task-c",
            "run_id": "run-c",
            "thread_id": "thread-c",
        })

        self.assertTrue(plan[0].get("executed"), plan[0])
        push_receipts = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (mutation_root / "receipts").glob("*.json")
            if json.loads(path.read_text(encoding="utf-8")).get("result") == "PUSHED"
        ]
        self.assertEqual(len(push_receipts), 1)
        receipt = push_receipts[0]
        self.assertNotEqual(receipt["remote_before"], pas.PROVENANCE_UNKNOWN)
        self.assertNotEqual(receipt["remote_after"], pas.PROVENANCE_UNKNOWN)
        self.assertEqual(receipt["push_target"], "origin/main")
        self.assertTrue(pas.validate_mutation_receipt(
            mutation_root / "receipts" / next(
                path.name for path in (mutation_root / "receipts").glob("*.json")
                if json.loads(path.read_text(encoding="utf-8")).get("result") == "PUSHED"
            ),
            repo,
            commit=receipt["commit"],
            operation="explicit-push",
        ))

    def test_d_foreign_commit_is_reviewed_and_not_reverted(self) -> None:
        repo = init_repo(self.root / "repo")
        audit_root = self.root / "audit"
        pas.audit_canonical_commits(repo, audit_root=audit_root)
        (repo / "foreign.txt").write_text("foreign\n", encoding="utf-8")
        git(repo, "add", "foreign.txt", author="foreign-writer")
        git(repo, "commit", "-m", "foreign commit", author="foreign-writer")
        before_audit = git(repo, "rev-parse", "HEAD")

        audit = pas.audit_canonical_commits(repo, audit_root=audit_root)

        self.assertEqual(audit["result"], "REVIEW")
        self.assertEqual(audit["unauthorized"][0]["author"]["name"], "foreign-writer")
        self.assertEqual(git(repo, "rev-parse", "HEAD"), before_audit)

    def test_e_no_change_does_not_raise_false_alert(self) -> None:
        repo = init_repo(self.root / "repo")
        audit_root = self.root / "audit"
        pas.audit_canonical_commits(repo, audit_root=audit_root)

        audit = pas.audit_canonical_commits(repo, audit_root=audit_root)

        self.assertEqual(audit["status"], "NO_CHANGE")
        self.assertEqual(audit["result"], "PASS")
        self.assertEqual(audit["unauthorized"], [])


if __name__ == "__main__":
    unittest.main()
