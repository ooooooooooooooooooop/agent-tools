"""Regression tests for canonical mutation ownership and exact commit scope."""
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


def git(repo: Path, *args: str, check: bool = True) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "ownership-test",
        "GIT_AUTHOR_EMAIL": "ownership-test@invalid",
        "GIT_COMMITTER_NAME": "ownership-test",
        "GIT_COMMITTER_EMAIL": "ownership-test@invalid",
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


class TestCanonicalMutationOwnership(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="mutation-ownership-")
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_a_dirty_worktree_is_deferred_without_mutation(self) -> None:
        repo = init_repo(self.root / "repo")
        (repo / "owned.txt").write_text("owned\n", encoding="utf-8")
        before_head = git(repo, "rev-parse", "HEAD")
        classification = {
            "path": str(repo),
            "graph_state": pas.IN_SYNC,
            "state": pas.IN_SYNC,
            "worktree_state": pas.WORKTREE_DIRTY_SAFE,
            "dirty": True,
            "eligible_canonical_changes": ["owned.txt"],
            "branch": "main",
        }
        plan = pas.plan_actions({"agent-tools": classification}, None, "sync")
        self.assertEqual(plan[0]["action"], "REVIEW")
        pas.execute_plan(plan, {"agent-tools": classification}, None, "sync", {})
        self.assertEqual(git(repo, "rev-parse", "HEAD"), before_head)
        self.assertEqual((repo / "owned.txt").read_text(encoding="utf-8"), "owned\n")
        self.assertIn("dirty", plan[0]["reason"])

    def test_b_owned_commit_preserves_foreign_dirty_file(self) -> None:
        repo = init_repo(self.root / "repo")
        (repo / "file_a.txt").write_text("a1\n", encoding="utf-8")
        (repo / "file_b.txt").write_text("b1\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-m", "files")
        (repo / "file_a.txt").write_text("a2\n", encoding="utf-8")
        (repo / "file_b.txt").write_text("b2\n", encoding="utf-8")
        lock_root = self.root / "locks"

        ok, message = pas.commit_owned_files(
            repo,
            ["file_a.txt"],
            actor="test-writer",
            trigger="test-b",
            task_id="test-b",
            allow_foreign_dirty=True,
            validate=False,
            lock_root=lock_root,
            canonical_root=repo,
        )

        self.assertTrue(ok, message)
        self.assertEqual(git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"), "file_a.txt")
        self.assertEqual((repo / "file_b.txt").read_text(encoding="utf-8"), "b2\n")
        self.assertEqual(git(repo, "status", "--porcelain"), "M file_b.txt")
        receipts = sorted((lock_root / "receipts").glob("*.json"))
        self.assertEqual(len(receipts), 1)
        receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
        self.assertEqual(receipt["staged"], ["file_a.txt"])
        self.assertEqual(receipt["changed"], ["file_a.txt"])
        self.assertEqual(receipt["ownership"]["scope"], ["file_a.txt"])

    def test_c_second_writer_defers_and_first_writer_survives(self) -> None:
        repo = init_repo(self.root / "repo")
        lock_root = self.root / "locks"
        first = pas.CanonicalMutationLock(
            repo,
            actor="writer-a",
            trigger="test-c",
            task_id="test-c-a",
            operation="test",
            scope=["file_a.txt"],
            lock_root=lock_root,
            canonical_root=repo,
        )
        second = pas.CanonicalMutationLock(
            repo,
            actor="writer-b",
            trigger="test-c",
            task_id="test-c-b",
            operation="test",
            scope=["file_b.txt"],
            lock_root=lock_root,
            canonical_root=repo,
        )
        first.acquire()
        try:
            with self.assertRaises(pas.MutationOwnershipError) as ctx:
                second.acquire()
            self.assertEqual(ctx.exception.code, "FOREIGN_LOCK")
            os.kill(os.getpid(), 0)
            self.assertTrue(first._held)
        finally:
            first.release()

    def test_d_noncanonical_restore_cannot_obtain_canonical_lease(self) -> None:
        source = init_repo(self.root / "source")
        governance = source / "scripts" / "governance"
        governance.mkdir(parents=True)
        (governance / "register_governance_tasks.ps1").write_text("Write-Output test\n", encoding="utf-8")
        git(source, "add", ".")
        git(source, "commit", "-m", "governance fixture")
        state_source = init_repo(self.root / "state-source")
        destination = self.root / "restore"
        state_destination = self.root / "state-destination"
        lock_root = self.root / "locks"
        live_head = pas.git(pas.REPO, "rev-parse", "HEAD")[1]

        with self.assertRaises(pas.MutationOwnershipError) as ctx:
            pas.CanonicalMutationLock(
                destination,
                actor="restore-test",
                trigger="test-d",
                task_id="test-d",
                operation="restore",
                scope=["repository"],
                lock_root=lock_root,
                canonical_root=pas.CANONICAL_GOVERNANCE_ROOT,
            ).acquire()
        self.assertEqual(ctx.exception.code, "NON_CANONICAL")
        self.assertFalse(lock_root.exists())

        restored = pas.run_restore(
            repo=destination,
            state_repo=state_destination,
            skills_dest=self.root / "skills",
            apply_dsh=False,
            agent_tools_remote=str(source),
            state_remote=str(state_source),
        )
        governance_steps = [s for s in restored["steps"] if s["step"] == "governance tasks"]
        self.assertEqual(len(governance_steps), 1)
        self.assertIn("non-canonical restore source", governance_steps[0]["note"])
        self.assertEqual(pas.git(pas.REPO, "rev-parse", "HEAD")[1], live_head)

    def test_e_stale_lock_recovers_but_active_writer_is_not_killed(self) -> None:
        repo = init_repo(self.root / "repo")
        lock_root = self.root / "locks"
        active = pas.CanonicalMutationLock(
            repo,
            actor="active-writer",
            trigger="test-e",
            task_id="test-e-active",
            operation="test",
            scope=["file.txt"],
            lock_root=lock_root,
            canonical_root=repo,
        )
        active.acquire()
        try:
            blocked = pas.CanonicalMutationLock(
                repo,
                actor="second-writer",
                trigger="test-e",
                task_id="test-e-second",
                operation="test",
                scope=["file.txt"],
                lock_root=lock_root,
                canonical_root=repo,
                stale_after=0,
            )
            with self.assertRaises(pas.MutationOwnershipError) as ctx:
                blocked.acquire()
            self.assertEqual(ctx.exception.code, "FOREIGN_LOCK")
            os.kill(os.getpid(), 0)
        finally:
            active.release()

        child_code = (
            "import os, sys; "
            f"sys.path.insert(0, {json.dumps(str(ROOT / 'scripts'))}); "
            "from pathlib import Path; "
            "from personal_ai_sync import CanonicalMutationLock; "
            f"lock = CanonicalMutationLock(Path({json.dumps(str(repo))}), "
            "actor='crashed-writer', trigger='test-e', task_id='test-e-crash', "
            "operation='test', scope=['file.txt'], "
            f"lock_root=Path({json.dumps(str(lock_root))}), "
            f"canonical_root=Path({json.dumps(str(repo))}), stale_after=0); "
            "lock.acquire(); os._exit(0)"
        )
        subprocess.run([sys.executable, "-c", child_code], check=True, capture_output=True)

        recovered = pas.CanonicalMutationLock(
            repo,
            actor="recovery-writer",
            trigger="test-e",
            task_id="test-e-recovery",
            operation="test",
            scope=["file.txt"],
            lock_root=lock_root,
            canonical_root=repo,
            stale_after=0,
        )
        recovered.acquire()
        try:
            self.assertTrue(recovered._held)
        finally:
            recovered.release()


if __name__ == "__main__":
    unittest.main()
