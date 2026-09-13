"""Isolated cross-harness canonical-writer containment simulations."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "governance"))

import canonical_writer as cw  # noqa: E402
import personal_ai_sync as pas  # noqa: E402


def git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "containment-test",
        "GIT_AUTHOR_EMAIL": "containment-test@invalid",
        "GIT_COMMITTER_NAME": "containment-test",
        "GIT_COMMITTER_EMAIL": "containment-test@invalid",
    }
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stdout}\n{result.stderr}")
    return (result.stdout + result.stderr).strip()


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", str(path)], capture_output=True, check=True)
    git(path, "config", "user.name", "containment-test")
    git(path, "config", "user.email", "containment-test@invalid")
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    git(path, "add", "seed.txt")
    git(path, "commit", "-m", "initial")
    return path


def preflight_args(repo: Path, lock_root: Path, **values):
    defaults = {
        "repo": str(repo),
        "lock_root": str(lock_root),
        "harness": "claude",
        "lease_id": None,
        "run_id": None,
        "path": [],
        "scope": [],
        "scope_contract": None,
        "git_operation": None,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


class TestCrossHarnessWriterContainment(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="cross-harness-containment-")
        self.root = Path(self.temp.name).resolve()
        self.repo = init_repo(self.root / "repo")
        self.locks = self.root / "locks"

    def tearDown(self) -> None:
        for key in ("PERSONAL_AI_MUTATION_LEASE_ID", "PERSONAL_AI_MUTATION_RUN_ID"):
            os.environ.pop(key, None)
        self.temp.cleanup()

    def acquire(self, scope: list[str], harness: str = "claude", stale_after: int = pas.MUTATION_LOCK_STALE_SECONDS):
        lock = pas.CanonicalMutationLock(
            self.repo,
            actor=f"{harness}-test",
            actor_type="automated",
            harness=harness,
            trigger="containment-test",
            task_id="containment-test",
            session_id="session-test",
            operation="test",
            scope=scope,
            lock_root=self.locks,
            canonical_root=self.repo,
            stale_after=stale_after,
        )
        lock.acquire()
        return lock

    def test_no_lease_is_denied(self):
        args = preflight_args(self.repo, self.locks, path=["file_a.txt"])
        output = io.StringIO()
        with mock.patch.object(cw, "_canonical_or_error"), contextlib.redirect_stdout(output):
            rc = cw.cmd_preflight(args)
        self.assertEqual(rc, 2)
        self.assertIn("NO_ACTIVE_CANONICAL_WRITER", output.getvalue())

    def test_exact_scope_allows_owned_and_denies_foreign(self):
        lock = self.acquire(["file_a.txt"])
        try:
            allowed = preflight_args(
                self.repo,
                self.locks,
                path=["file_a.txt"],
                harness="claude",
                lease_id=lock.mutation_lease_id,
                run_id=lock.run_id,
            )
            with mock.patch.object(cw, "_canonical_or_error"):
                self.assertEqual(cw.cmd_preflight(allowed), 0)
            foreign = preflight_args(
                self.repo,
                self.locks,
                path=["file_b.txt"],
                harness="claude",
                lease_id=lock.mutation_lease_id,
                run_id=lock.run_id,
            )
            with self.assertRaises(pas.MutationOwnershipError) as ctx:
                cw._load_active_lock(self.repo, foreign, scope=["file_b.txt"])
            self.assertEqual(ctx.exception.code, "ABORT_FOREIGN_STAGED_PATH")
        finally:
            lock.release()

    def test_broad_staging_is_blocked_and_foreign_dirty_is_preserved(self):
        scripts = self.repo / "scripts"
        scripts.mkdir()
        file_a = scripts / "file_a.txt"
        file_b = scripts / "file_b.txt"
        file_a.write_text("a1\n", encoding="utf-8")
        file_b.write_text("b1\n", encoding="utf-8")
        git(self.repo, "add", "scripts")
        git(self.repo, "commit", "-m", "seed files")
        file_a.write_text("a2\n", encoding="utf-8")
        file_b.write_text("b2\n", encoding="utf-8")
        lock = self.acquire(["scripts/file_a.txt"])
        try:
            git(self.repo, "add", "scripts/")
            staged = pas._staged_paths(self.repo)
            self.assertEqual(staged, ["scripts/file_a.txt", "scripts/file_b.txt"])
            with self.assertRaises(pas.MutationOwnershipError) as ctx:
                cw._load_active_lock(
                    self.repo,
                    preflight_args(
                        self.repo,
                        self.locks,
                        lease_id=lock.mutation_lease_id,
                        run_id=lock.run_id,
                    ),
                    scope=staged,
                )
            self.assertEqual(ctx.exception.code, "ABORT_FOREIGN_STAGED_PATH")
            self.assertEqual(pas._staged_paths(self.repo), staged)
        finally:
            lock.release()

    def test_governed_commit_records_harness_session_and_receipt(self):
        (self.repo / "owned.txt").write_text("owned\n", encoding="utf-8")
        ok, message = pas.commit_owned_files(
            self.repo,
            ["owned.txt"],
            actor="claude",
            actor_type="automated",
            harness="claude-code",
            trigger="containment-test",
            task_id="task-commit",
            session_id="session-commit",
            validate=False,
            message="test: governed owned commit",
            lock_root=self.locks,
            canonical_root=self.repo,
        )
        self.assertTrue(ok, message)
        receipt_path = next((self.locks / "receipts").glob("*.json"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["harness"], "claude-code")
        self.assertEqual(receipt["session_id"], "session-commit")
        self.assertEqual(receipt["owned_paths"], ["owned.txt"])
        self.assertEqual(receipt["changed_files"], ["owned.txt"])
        self.assertTrue(pas.validate_mutation_receipt(receipt_path, self.repo, commit=receipt["commit"]))

    def test_push_requires_separate_authorization(self):
        args = preflight_args(
            self.repo,
            self.locks,
            scope=["git-history"],
            git_operation="push",
        )
        output = io.StringIO()
        with mock.patch.object(cw, "_canonical_or_error"), contextlib.redirect_stdout(output):
            rc = cw.cmd_preflight(args)
        self.assertEqual(rc, 2)
        self.assertIn("PUSH_AUTHORIZATION_REQUIRED", output.getvalue())

    def test_concurrent_writer_defers_without_killing_first(self):
        first = self.acquire(["file_a.txt"], harness="claude")
        second = pas.CanonicalMutationLock(
            self.repo,
            actor="dsh",
            actor_type="automated",
            harness="dsh",
            trigger="containment-test",
            task_id="dsh-test",
            operation="test",
            scope=["file_b.txt"],
            lock_root=self.locks,
            canonical_root=self.repo,
            stale_after=0,
        )
        try:
            with self.assertRaises(pas.MutationOwnershipError) as ctx:
                second.acquire()
            self.assertEqual(ctx.exception.code, "FOREIGN_LOCK")
            self.assertTrue(first._held)
        finally:
            first.release()

    def test_stale_recovery_writes_event(self):
        child_code = (
            "import os, sys; "
            f"sys.path.insert(0, {str(ROOT / 'scripts')!r}); "
            "from pathlib import Path; "
            "import personal_ai_sync as p; "
            f"lock=p.CanonicalMutationLock(Path({str(self.repo)!r}), actor='crashed', "
            "actor_type='automated', harness='claude', trigger='test', task_id='crashed', "
            "operation='test', scope=['file_a.txt'], "
            f"lock_root=Path({str(self.locks)!r}), canonical_root=Path({str(self.repo)!r}), stale_after=0); "
            "lock.acquire(); os._exit(0)"
        )
        subprocess.run([sys.executable, "-c", child_code], check=True, capture_output=True)
        recovered = self.acquire(["file_a.txt"], harness="dsh", stale_after=0)
        try:
            events = sorted((self.locks / "events").glob("*.json"))
            self.assertTrue(events)
            event = json.loads(events[-1].read_text(encoding="utf-8"))
            self.assertEqual(event["event_type"], "STALE_LEASE_RECOVERED")
            self.assertEqual(event["recovered_lease"]["harness"], "claude")
        finally:
            recovered.release()


if __name__ == "__main__":
    unittest.main()
