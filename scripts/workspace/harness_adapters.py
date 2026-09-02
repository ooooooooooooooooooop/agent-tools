"""harness_adapters.py — Translates Workspace Execution Contract into Harness-native options.

Handles native and external worktree dispatch for Claude Code, Gemini CLI, Codex, and DSH.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .execution_contract import WorkspaceAdmission, WorkspaceMode
from .provisioner import WorktreeRecord


class ClaudeWorkspaceAdapter:
    @staticmethod
    def build_command_args(
        admission: WorkspaceAdmission,
        worktree: WorktreeRecord | None = None,
        *,
        use_native_flag: bool = True,
    ) -> list[str]:
        args: list[str] = []
        if admission.worktree_required and worktree and use_native_flag:
            args.extend(["--worktree", worktree.branch_name])
        for d in admission.additional_dirs:
            args.extend(["--add-dir", str(d)])
        return args

    @staticmethod
    def resolve_cwd(
        admission: WorkspaceAdmission,
        worktree: WorktreeRecord | None = None,
        *,
        use_native_flag: bool = True,
    ) -> Path:
        if admission.worktree_required and worktree and not use_native_flag:
            return Path(worktree.worktree_path)
        return admission.primary_repo


class GeminiWorkspaceAdapter:
    @staticmethod
    def build_command_args(
        admission: WorkspaceAdmission,
        worktree: WorktreeRecord | None = None,
        *,
        use_native_flag: bool = True,
    ) -> list[str]:
        args: list[str] = []
        if admission.worktree_required and worktree and use_native_flag:
            args.extend(["--worktree"])
        for d in admission.additional_dirs:
            args.extend(["--include-directories", str(d)])
        return args

    @staticmethod
    def resolve_cwd(
        admission: WorkspaceAdmission,
        worktree: WorktreeRecord | None = None,
        *,
        use_native_flag: bool = True,
    ) -> Path:
        if admission.worktree_required and worktree and not use_native_flag:
            return Path(worktree.worktree_path)
        return admission.primary_repo


class CodexWorkspaceAdapter:
    @staticmethod
    def build_command_args(
        admission: WorkspaceAdmission,
        worktree: WorktreeRecord | None = None,
    ) -> list[str]:
        args: list[str] = []
        for d in admission.additional_dirs:
            args.extend(["--add-dir", str(d)])
        return args

    @staticmethod
    def resolve_cwd(
        admission: WorkspaceAdmission,
        worktree: WorktreeRecord | None = None,
    ) -> Path:
        if admission.worktree_required and worktree:
            return Path(worktree.worktree_path)
        return admission.primary_repo


class DshWorkspaceAdapter:
    @staticmethod
    def resolve_workspace(
        admission: WorkspaceAdmission,
        worktree: WorktreeRecord | None = None,
    ) -> dict[str, Any]:
        cwd = Path(worktree.worktree_path) if (admission.worktree_required and worktree) else admission.primary_repo
        return {
            "cwd": str(cwd),
            "mode": admission.mode.value,
            "read_grants": [str(d) for d in admission.additional_dirs],
            "write_scopes": [str(d) for d in admission.write_scopes],
        }
