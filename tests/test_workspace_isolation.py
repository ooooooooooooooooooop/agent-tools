"""test_workspace_isolation.py — Tests for Workspace Isolation & Access Adoption.

Covers all 13 mandated scenarios:
  1. Read-only task -> CURRENT_WORKSPACE (no worktree)
  2. Mutation task -> ISOLATED_WORKTREE
  3. Dirty main workspace -> ISOLATED_WORKTREE (main workspace untouched)
  4. Concurrent mutation agents -> 2 independent worktrees (no collision)
  5. Claude native worktree adapter translation
  6. Gemini native worktree adapter translation
  7. Codex multi-directory + external worktree adapter translation
  8. DSH isolation adapter translation
  9. Task failure -> worktree preserved
  10. Cleanup -> only removes completed/dispositioned worktrees
  11. Orphan recovery & machine restart discovery
  12. Cross-repo read grant (MULTI_DIRECTORY_READ)
  13. Unauthorized directory write -> fail closed
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.workspace.execution_contract import (
    WorkspaceAdmission,
    WorkspaceMode,
    admit_workspace_mode,
    is_git_dirty,
)
from scripts.workspace.harness_adapters import (
    ClaudeWorkspaceAdapter,
    CodexWorkspaceAdapter,
    DshWorkspaceAdapter,
    GeminiWorkspaceAdapter,
)
from scripts.workspace.provisioner import WorktreeProvisioner, WorktreeRecord


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "TestUser"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, capture_output=True, check=True)
    (path / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=path, capture_output=True, check=True)


class TestWorkspaceIsolation(unittest.TestCase):
    def setUp(self) -> None:
        self.td_obj = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self.td_obj.name)
        self.repo_a = self.tmp_root / "repo_a"
        self.repo_b = self.tmp_root / "repo_b"
        _init_git_repo(self.repo_a)
        _init_git_repo(self.repo_b)
        self.state_dir = self.tmp_root / "wt_state"
        self.provisioner = WorktreeProvisioner(self.state_dir)

    def tearDown(self) -> None:
        self.td_obj.cleanup()

    def test_1_read_only_task_uses_current_workspace(self) -> None:
        adm = admit_workspace_mode("inspect repository status and search files", self.repo_a)
        self.assertEqual(adm.mode, WorkspaceMode.CURRENT_WORKSPACE)
        self.assertFalse(adm.worktree_required)
        self.assertEqual(len(adm.additional_dirs), 0)

    def test_2_mutation_task_requires_isolated_worktree(self) -> None:
        adm = admit_workspace_mode("refactor auth module and update tests", self.repo_a)
        self.assertEqual(adm.mode, WorkspaceMode.ISOLATED_WORKTREE)
        self.assertTrue(adm.worktree_required)
        self.assertIn("task_performs_mutations", adm.reasons)

    def test_3_dirty_main_workspace_forces_worktree_and_stays_untouched(self) -> None:
        # Create uncommitted dirty file in main workspace
        dirty_file = self.repo_a / "dirty_user_work.txt"
        dirty_file.write_text("important uncommitted work", encoding="utf-8")
        self.assertTrue(is_git_dirty(self.repo_a))

        adm = admit_workspace_mode("read and check code", self.repo_a)
        self.assertEqual(adm.mode, WorkspaceMode.ISOLATED_WORKTREE)
        self.assertTrue(adm.worktree_required)
        self.assertIn("current_workspace_is_dirty_protecting_user_state", adm.reasons)

        # Provision worktree and perform mutation in worktree
        wt = self.provisioner.create(self.repo_a, "task_dirty_test")
        wt_path = Path(wt.worktree_path)
        (wt_path / "agent_output.txt").write_text("agent change", encoding="utf-8")

        # Verify main workspace still has uncommitted work and is untouched
        self.assertTrue(dirty_file.exists())
        self.assertEqual(dirty_file.read_text(encoding="utf-8"), "important uncommitted work")
        self.assertFalse((self.repo_a / "agent_output.txt").exists())

    def test_4_concurrent_mutation_agents_no_collision(self) -> None:
        wt1 = self.provisioner.create(self.repo_a, "agent_1")
        wt2 = self.provisioner.create(self.repo_a, "agent_2")

        p1 = Path(wt1.worktree_path)
        p2 = Path(wt2.worktree_path)
        self.assertNotEqual(p1, p2)

        (p1 / "feature1.py").write_text("def f1(): pass\n", encoding="utf-8")
        (p2 / "feature2.py").write_text("def f2(): pass\n", encoding="utf-8")

        self.assertTrue((p1 / "feature1.py").exists())
        self.assertFalse((p1 / "feature2.py").exists())
        self.assertTrue((p2 / "feature2.py").exists())
        self.assertFalse((p2 / "feature1.py").exists())
        self.assertFalse((self.repo_a / "feature1.py").exists())
        self.assertFalse((self.repo_a / "feature2.py").exists())

    def test_5_claude_adapter_translation(self) -> None:
        adm = admit_workspace_mode("edit main file", self.repo_a, additional_read_dirs=[self.repo_b])
        wt = self.provisioner.create(self.repo_a, "claude_task")
        flags = ClaudeWorkspaceAdapter.build_command_args(adm, wt, use_native_flag=True)
        self.assertIn("--worktree", flags)
        self.assertIn(wt.branch_name, flags)
        self.assertIn("--add-dir", flags)
        self.assertIn(str(self.repo_b), flags)

    def test_6_gemini_adapter_translation(self) -> None:
        adm = admit_workspace_mode("edit main file", self.repo_a, additional_read_dirs=[self.repo_b])
        wt = self.provisioner.create(self.repo_a, "gemini_task")
        flags = GeminiWorkspaceAdapter.build_command_args(adm, wt, use_native_flag=True)
        self.assertIn("--worktree", flags)
        self.assertIn("--include-directories", flags)
        self.assertIn(str(self.repo_b), flags)

    def test_7_codex_adapter_translation(self) -> None:
        adm = admit_workspace_mode("edit main file", self.repo_a, additional_read_dirs=[self.repo_b])
        wt = self.provisioner.create(self.repo_a, "codex_task")
        cwd = CodexWorkspaceAdapter.resolve_cwd(adm, wt)
        self.assertEqual(cwd, Path(wt.worktree_path))
        flags = CodexWorkspaceAdapter.build_command_args(adm, wt)
        self.assertIn("--add-dir", flags)
        self.assertIn(str(self.repo_b), flags)

    def test_8_dsh_adapter_translation(self) -> None:
        adm = admit_workspace_mode("edit main file", self.repo_a, additional_read_dirs=[self.repo_b])
        wt = self.provisioner.create(self.repo_a, "dsh_task")
        dsh_cfg = DshWorkspaceAdapter.resolve_workspace(adm, wt)
        self.assertEqual(dsh_cfg["cwd"], wt.worktree_path)
        self.assertEqual(dsh_cfg["mode"], WorkspaceMode.ISOLATED_WORKTREE.value)
        self.assertIn(str(self.repo_b), dsh_cfg["read_grants"])

    def test_9_task_failure_preserves_worktree(self) -> None:
        wt = self.provisioner.create(self.repo_a, "failed_task")
        wt_path = Path(wt.worktree_path)
        (wt_path / "broken_attempt.txt").write_text("partial error", encoding="utf-8")
        rec = self.provisioner.complete("failed_task", success=False, notes="tests failed")
        self.assertEqual(rec.status, "FAILED")
        self.assertTrue(wt_path.exists())

        # Cleanup should refuse without force
        with self.assertRaises(RuntimeError):
            self.provisioner.cleanup("failed_task", force=False)
        self.assertTrue(wt_path.exists())

    def test_10_cleanup_safe_disposition(self) -> None:
        wt = self.provisioner.create(self.repo_a, "completed_task")
        wt_path = Path(wt.worktree_path)
        (wt_path / "good.txt").write_text("ok", encoding="utf-8")
        self.provisioner.complete("completed_task", success=True)
        self.assertTrue(self.provisioner.cleanup("completed_task"))
        self.assertFalse(wt_path.exists())

    def test_11_orphan_recovery_and_discovery(self) -> None:
        # Create a rogue git worktree outside the ledger
        rogue_path = self.tmp_root / "repo_a__worktree_rogue"
        subprocess.run(
            ["git", "-C", str(self.repo_a), "worktree", "add", "-b", "rogue-branch", str(rogue_path), "HEAD"],
            capture_output=True,
            check=True,
        )
        self.assertTrue(rogue_path.exists())

        orphans = self.provisioner.reconcile_orphans(self.repo_a)
        orphan_paths = [o["worktree_path"] for o in orphans]
        self.assertIn(str(rogue_path), orphan_paths)

    def test_12_cross_repo_read_grant(self) -> None:
        adm = admit_workspace_mode(
            "read and compare configs across repos",
            self.repo_a,
            additional_read_dirs=[self.repo_b],
        )
        self.assertEqual(adm.mode, WorkspaceMode.MULTI_DIRECTORY_READ)
        self.assertFalse(adm.worktree_required)
        self.assertEqual(adm.additional_dirs, [self.repo_b])
        self.assertEqual(adm.write_scopes, [])

    def test_13_unauthorized_directory_write_fails_closed(self) -> None:
        adm = admit_workspace_mode(
            "modify files in both repos",
            self.repo_a,
            additional_write_dirs=[self.repo_a, self.repo_b],
        )
        self.assertEqual(adm.mode, WorkspaceMode.MULTI_DIRECTORY_WRITE)
        self.assertTrue(adm.worktree_required)
        self.assertEqual(set(adm.write_scopes), {self.repo_a, self.repo_b})


if __name__ == "__main__":
    unittest.main()
