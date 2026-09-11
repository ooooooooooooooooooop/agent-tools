"""Adversarial tests for governed non-HEAD ref publication (W1.1 push-ref)."""
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

WRITER = ROOT / "scripts" / "governance" / "canonical_writer.py"
ZERO = "0" * 40


def git(repo: Path, *args: str, check: bool = True) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "pushref-test",
        "GIT_AUTHOR_EMAIL": "pushref-test@invalid",
        "GIT_COMMITTER_NAME": "pushref-test",
        "GIT_COMMITTER_EMAIL": "pushref-test@invalid",
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
    git(path, "config", "user.name", "pushref-test")
    git(path, "config", "user.email", "pushref-test@invalid")
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    git(path, "add", "seed.txt")
    git(path, "commit", "-m", "initial")
    return path


def make_remote_pair(root: Path) -> tuple[Path, Path]:
    bare = root / "remote.git"
    subprocess.run(["git", "init", "--bare", str(bare)], capture_output=True, check=True)
    work = init_repo(root / "work")
    git(work, "remote", "add", "origin", str(bare))
    git(work, "push", "origin", "main")
    return bare, work


def install_pre_push_hook(work: Path) -> None:
    hooks = work / ".test-hooks"
    hooks.mkdir(exist_ok=True)
    script = (
        "#!/usr/bin/env bash\n"
        'PY="${PYTHON:-python3}"\n'
        'command -v "$PY" >/dev/null 2>&1 || PY=python\n'
        f'"$PY" "{WRITER.as_posix()}" pre-push "$@"\n'
    )
    (hooks / "pre-push").write_text(script, encoding="utf-8")
    git(work, "config", "core.hooksPath", hooks.as_posix())


def dirty_worktree(work: Path) -> None:
    (work / "seed.txt").write_text("seed-dirty\n", encoding="utf-8")
    (work / "untracked.txt").write_text("loose\n", encoding="utf-8")
    (work / "staged.txt").write_text("staged\n", encoding="utf-8")
    git(work, "add", "staged.txt")


def make_side_ref(work: Path, lock_root: Path, name: str = "w1-side") -> str:
    git(work, "checkout", "-b", name)
    (work / "owned.txt").write_text("w1 content\n", encoding="utf-8")
    ok, message = pas.commit_owned_files(
        work,
        ["owned.txt"],
        actor="test-writer",
        trigger="push-ref-test",
        task_id="push-ref-test",
        allow_foreign_dirty=True,
        validate=False,
        lock_root=lock_root,
        canonical_root=work,
    )
    if not ok:
        raise AssertionError(message)
    sha = git(work, "rev-parse", "HEAD")
    git(work, "checkout", "main")
    return sha


def hold_git_lock(work: Path, lock_root: Path) -> pas.CanonicalMutationLock:
    lock = pas.CanonicalMutationLock(
        work,
        actor="publisher",
        trigger="push-ref-test",
        task_id="push-ref-test",
        operation="push-ref",
        scope=["git-history"],
        lock_root=lock_root,
        canonical_root=work,
    )
    lock.acquire()
    return lock


def make_auth(
    lock: pas.CanonicalMutationLock,
    *,
    remote: str = "origin",
    source_ref: str = "refs/heads/w1-side",
    commit: str,
    destination_ref: str = "refs/heads/published-side",
    expected_remote: str = pas.PUSH_REF_ABSENT,
) -> Path:
    return pas.write_push_ref_authorization(
        lock,
        remote=remote,
        source_ref=source_ref,
        commit=commit,
        destination_ref=destination_ref,
        expected_remote=expected_remote,
        base_head=expected_remote,
    )


class TestGovernedPushRef(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="push-ref-")
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _pair(self) -> tuple[Path, Path, Path]:
        bare, work = make_remote_pair(self.root)
        lock_root = pas._mutation_lock_root_for_repo(work)
        return bare, work, lock_root

    def test_a_dirty_worktree_side_ref_publishes_exact_sha(self) -> None:
        bare, work, lock_root = self._pair()
        install_pre_push_hook(work)
        side_sha = make_side_ref(work, lock_root)
        dirty_worktree(work)
        head_before = git(work, "rev-parse", "HEAD")
        status_before = git(work, "status", "--porcelain")
        staged_before = git(work, "diff", "--cached", "--name-only")

        lock = hold_git_lock(work, lock_root)
        try:
            ok, message = pas.push_governed_ref(
                lock,
                remote="origin",
                source_ref="w1-side",
                destination_ref="published-side",
            )
        finally:
            lock.release()

        self.assertTrue(ok, message)
        remote_after = git(bare, "rev-parse", "refs/heads/published-side")
        self.assertEqual(remote_after, side_sha)
        self.assertEqual(git(work, "rev-parse", "HEAD"), head_before)
        self.assertEqual(git(work, "status", "--porcelain"), status_before)
        self.assertEqual(git(work, "diff", "--cached", "--name-only"), staged_before)
        self.assertNotEqual(side_sha, head_before)

    def test_b_source_ref_moved_denied(self) -> None:
        bare, work, lock_root = self._pair()
        install_pre_push_hook(work)
        side_sha = make_side_ref(work, lock_root)
        lock = hold_git_lock(work, lock_root)
        try:
            auth = make_auth(lock, commit=side_sha)
            git(work, "update-ref", "refs/heads/w1-side", "main")
            ok, detail = pas.validate_push_ref_authorization(
                auth, work, remote="origin",
                local_ref="refs/heads/w1-side",
                local_sha=git(work, "rev-parse", "main"),
                remote_ref="refs/heads/published-side",
                remote_sha=ZERO,
            )
            self.assertFalse(ok)
            self.assertIn(detail, ("PUSH_REF_SOURCE_MOVED", "PUSH_AUTHORIZATION_COMMIT_MISMATCH"))
        finally:
            lock.release()

    def test_c_commit_mismatch_denied(self) -> None:
        bare, work, lock_root = self._pair()
        side_sha = make_side_ref(work, lock_root)
        lock = hold_git_lock(work, lock_root)
        try:
            auth = make_auth(lock, commit=side_sha)
            other = git(work, "rev-parse", "main")
            ok, detail = pas.validate_push_ref_authorization(
                auth, work, remote="origin",
                local_ref="refs/heads/w1-side",
                local_sha=other,
                remote_ref="refs/heads/published-side",
                remote_sha=ZERO,
            )
            self.assertFalse(ok)
            self.assertEqual(detail, "PUSH_AUTHORIZATION_COMMIT_MISMATCH")
        finally:
            lock.release()

    def test_d_remote_drift_after_authorization_denied(self) -> None:
        bare, work, lock_root = self._pair()
        side_sha = make_side_ref(work, lock_root)
        other = init_repo(self.root / "other")
        git(other, "remote", "add", "origin", str(bare))
        git(other, "fetch", "origin")
        git(other, "checkout", "-b", "pub", "origin/main")
        (other / "d1.txt").write_text("d1\n", encoding="utf-8")
        git(other, "add", "d1.txt")
        git(other, "commit", "-m", "remote work 1")
        git(other, "push", "origin", "HEAD:refs/heads/published-side")
        authorized_remote = git(bare, "rev-parse", "refs/heads/published-side")
        lock = hold_git_lock(work, lock_root)
        try:
            auth = make_auth(lock, commit=side_sha, expected_remote=authorized_remote)
            (other / "d2.txt").write_text("d2\n", encoding="utf-8")
            git(other, "add", "d2.txt")
            git(other, "commit", "-m", "remote work 2")
            git(other, "push", "origin", "HEAD:refs/heads/published-side")
            drifted_remote = git(bare, "rev-parse", "refs/heads/published-side")
            self.assertNotEqual(drifted_remote, authorized_remote)
            ok, detail = pas.validate_push_ref_authorization(
                auth, work, remote="origin",
                local_ref="refs/heads/w1-side",
                local_sha=side_sha,
                remote_ref="refs/heads/published-side",
                remote_sha=drifted_remote,
            )
            self.assertFalse(ok)
            self.assertEqual(detail, "PUSH_REMOTE_CHANGED")
        finally:
            lock.release()

    def test_e_delete_denied(self) -> None:
        bare, work, lock_root = self._pair()
        side_sha = make_side_ref(work, lock_root)
        lock = hold_git_lock(work, lock_root)
        try:
            auth = make_auth(lock, commit=side_sha)
            ok, detail = pas.validate_push_ref_authorization(
                auth, work, remote="origin",
                local_ref="(delete)",
                local_sha=ZERO,
                remote_ref="refs/heads/published-side",
                remote_sha=ZERO,
            )
            self.assertFalse(ok)
            self.assertEqual(detail, "PRE_PUSH_DELETE_DENIED")
        finally:
            lock.release()

    def test_f_non_fast_forward_denied(self) -> None:
        bare, work, lock_root = self._pair()
        side_sha = make_side_ref(work, lock_root)
        other = init_repo(self.root / "other")
        git(other, "remote", "add", "origin", str(bare))
        git(other, "fetch", "origin")
        (other / "foreign.txt").write_text("foreign\n", encoding="utf-8")
        git(other, "add", "foreign.txt")
        git(other, "commit", "-m", "remote-side work")
        git(other, "push", "origin", "HEAD:refs/heads/published-side")
        remote_tip = git(bare, "rev-parse", "refs/heads/published-side")
        lock = hold_git_lock(work, lock_root)
        try:
            with self.assertRaises(pas.MutationOwnershipError) as ctx:
                pas.push_governed_ref(
                    lock,
                    remote="origin",
                    source_ref="w1-side",
                    destination_ref="published-side",
                )
            self.assertEqual(ctx.exception.code, "PUSH_REF_NON_FAST_FORWARD")
            ok, detail = pas.validate_push_ref_authorization(
                make_auth(lock, commit=side_sha, expected_remote=remote_tip),
                work, remote="origin",
                local_ref="refs/heads/w1-side", local_sha=side_sha,
                remote_ref="refs/heads/published-side", remote_sha=remote_tip,
            )
            self.assertFalse(ok)
            self.assertEqual(detail, "PUSH_REF_NON_FAST_FORWARD")
        finally:
            lock.release()

    def test_g_commit_without_receipt_denied(self) -> None:
        bare, work, lock_root = self._pair()
        git(work, "checkout", "-b", "no-receipt")
        (work / "x.txt").write_text("x\n", encoding="utf-8")
        git(work, "add", "x.txt")
        git(work, "commit", "-m", "no receipt")
        sha = git(work, "rev-parse", "HEAD")
        git(work, "checkout", "main")
        lock = hold_git_lock(work, lock_root)
        try:
            with self.assertRaises(pas.MutationOwnershipError) as ctx:
                pas.push_governed_ref(
                    lock, remote="origin",
                    source_ref="no-receipt", destination_ref="published-side",
                )
            self.assertEqual(ctx.exception.code, "PUSH_REF_MISSING_COMMIT_RECEIPT")
        finally:
            lock.release()

    def test_h_repo_mismatch_denied(self) -> None:
        bare, work, lock_root = self._pair()
        other = init_repo(self.root / "other")
        side_sha = make_side_ref(work, lock_root)
        lock = hold_git_lock(work, lock_root)
        try:
            auth = make_auth(lock, commit=side_sha)
            ok, detail = pas.validate_push_ref_authorization(
                auth, other, remote="origin",
                local_ref="refs/heads/w1-side", local_sha=side_sha,
                remote_ref="refs/heads/published-side", remote_sha=ZERO,
            )
            self.assertFalse(ok)
            self.assertEqual(detail, "PUSH_AUTHORIZATION_REPO_MISMATCH")
        finally:
            lock.release()

    def test_i_authorization_single_use(self) -> None:
        bare, work, lock_root = self._pair()
        install_pre_push_hook(work)
        side_sha = make_side_ref(work, lock_root)
        lock = hold_git_lock(work, lock_root)
        try:
            ok, message = pas.push_governed_ref(
                lock, remote="origin",
                source_ref="w1-side", destination_ref="published-side",
            )
            self.assertTrue(ok, message)
            auth_dir = lock_root / "push-authorizations"
            auth = sorted(auth_dir.glob("*push-ref*.json"))[-1]
            self.assertEqual(json.loads(auth.read_text(encoding="utf-8"))["status"], "CONSUMED")
            ok2, detail = pas.validate_push_ref_authorization(
                auth, work, remote="origin",
                local_ref="refs/heads/w1-side", local_sha=side_sha,
                remote_ref="refs/heads/published-side", remote_sha=side_sha,
            )
            self.assertFalse(ok2)
            self.assertEqual(detail, "PUSH_AUTHORIZATION_ALREADY_CONSUMED")
        finally:
            lock.release()

    def test_j_legacy_push_contract_unchanged(self) -> None:
        bare, work, lock_root = self._pair()
        dirty_worktree(work)
        head = git(work, "rev-parse", "HEAD")
        lock = hold_git_lock(work, lock_root)
        try:
            auth = pas.write_push_authorization(
                lock, remote="origin", branch="main",
                expected_remote=git(bare, "rev-parse", "refs/heads/main"),
                commit=head, base_head=head,
            )
            ok, detail = pas.validate_push_authorization(
                auth, work, remote="origin", branch="main", commit=head,
            )
            self.assertFalse(ok)
            self.assertEqual(detail, "PUSH_AUTHORIZATION_DIRTY_WORKTREE")
            moved_head_auth = pas.write_push_authorization(
                lock, remote="origin", branch="main",
                expected_remote=git(bare, "rev-parse", "refs/heads/main"),
                commit=head,
                base_head=head,
            )
            (work / "extra.txt").write_text("y\n", encoding="utf-8")
            git(work, "add", "extra.txt")
            git(work, "commit", "-m", "moved head")
            ok2, detail2 = pas.validate_push_authorization(
                moved_head_auth, work, remote="origin", branch="main", commit=head,
            )
            self.assertFalse(ok2)
            self.assertEqual(detail2, "PUSH_AUTHORIZATION_HEAD_MISMATCH")
        finally:
            lock.release()


if __name__ == "__main__":
    unittest.main()
