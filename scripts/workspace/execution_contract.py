"""execution_contract.py — Personal AI Workspace Execution Contract & Task Admission.

Defines the 4 canonical workspace execution modes and automatic task admission policy.
Users describe task intent; Personal AI automatically selects the safe execution boundary.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class WorkspaceMode(str, Enum):
    CURRENT_WORKSPACE = "CURRENT_WORKSPACE"
    ISOLATED_WORKTREE = "ISOLATED_WORKTREE"
    MULTI_DIRECTORY_READ = "MULTI_DIRECTORY_READ"
    MULTI_DIRECTORY_WRITE = "MULTI_DIRECTORY_WRITE"


# Action keywords indicating mutation vs read-only
MUTATING_KEYWORDS = re.compile(
    r"\b(edit|write|modify|update|change|create|delete|remove|refactor|fix|repair|"
    r"implement|build|migrate|patch|rewrite|add|deploy|install)\b",
    re.IGNORECASE,
)
HIGH_RISK_KEYWORDS = re.compile(
    r"\b(migration|mass-refactor|breaking|upgrade|architecture|rebuild|destructive)\b",
    re.IGNORECASE,
)


@dataclass
class WorkspaceAdmission:
    mode: WorkspaceMode
    primary_repo: Path
    worktree_required: bool
    additional_dirs: list[Path] = field(default_factory=list)
    write_scopes: list[Path] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "primary_repo": str(self.primary_repo),
            "worktree_required": self.worktree_required,
            "additional_dirs": [str(d) for d in self.additional_dirs],
            "write_scopes": [str(d) for d in self.write_scopes],
            "reasons": self.reasons,
        }


def is_git_dirty(repo_path: Path) -> bool:
    """Check whether the git working tree has uncommitted changes."""
    if not (repo_path / ".git").exists():
        return False
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_path), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return bool(res.stdout.strip())
    except Exception:
        return False


def admit_workspace_mode(
    task_description: str,
    primary_repo: Path,
    *,
    is_mutating: bool | None = None,
    is_parallel: bool = False,
    additional_read_dirs: list[Path] | None = None,
    additional_write_dirs: list[Path] | None = None,
    is_high_risk: bool | None = None,
    rollback_required: bool = False,
    is_dirty_override: bool | None = None,
) -> WorkspaceAdmission:
    """Automatically admit a task to its safe workspace execution mode.

    Rules:
      1. Read-only single repo -> CURRENT_WORKSPACE.
      2. Multi-repo read-only -> MULTI_DIRECTORY_READ.
      3. Multi-repo write -> MULTI_DIRECTORY_WRITE (requires worktree + minimal write grant).
      4. Any single-repo mutation -> ISOLATED_WORKTREE.
      5. Parallel mutation -> ISOLATED_WORKTREE (per-worker worktree).
      6. Dirty main workspace -> ISOLATED_WORKTREE (protects user's uncommitted work).
      7. High risk / rollback required -> ISOLATED_WORKTREE.
    """
    reasons: list[str] = []
    primary = primary_repo.resolve()
    read_dirs = [d.resolve() for d in (additional_read_dirs or []) if d.resolve() != primary]
    write_dirs = [d.resolve() for d in (additional_write_dirs or [])]

    # Inferred behavior
    mutating = is_mutating if is_mutating is not None else bool(MUTATING_KEYWORDS.search(task_description))
    high_risk = is_high_risk if is_high_risk is not None else bool(HIGH_RISK_KEYWORDS.search(task_description))
    dirty = is_dirty_override if is_dirty_override is not None else is_git_dirty(primary)

    # 1. Multi-repo write
    if write_dirs and (len(write_dirs) > 1 or (write_dirs and write_dirs[0] != primary)):
        reasons.append("multi_repo_write_requested")
        reasons.append(f"minimal_write_scope: {[str(d) for d in write_dirs]}")
        return WorkspaceAdmission(
            mode=WorkspaceMode.MULTI_DIRECTORY_WRITE,
            primary_repo=primary,
            worktree_required=True,
            additional_dirs=read_dirs,
            write_scopes=write_dirs,
            reasons=reasons,
        )

    # 2. Mutation scenarios
    if mutating or high_risk or is_parallel or rollback_required or dirty:
        worktree_reasons = []
        if mutating:
            worktree_reasons.append("task_performs_mutations")
        if dirty:
            worktree_reasons.append("current_workspace_is_dirty_protecting_user_state")
        if is_parallel:
            worktree_reasons.append("parallel_execution_requires_independent_worktree")
        if high_risk:
            worktree_reasons.append("high_risk_task_requires_isolation")
        if rollback_required:
            worktree_reasons.append("strict_rollback_contract_required")

        mode = WorkspaceMode.ISOLATED_WORKTREE
        if read_dirs:
            worktree_reasons.append(f"multi_directory_read_grant: {[str(d) for d in read_dirs]}")

        return WorkspaceAdmission(
            mode=mode,
            primary_repo=primary,
            worktree_required=True,
            additional_dirs=read_dirs,
            write_scopes=[primary],
            reasons=worktree_reasons,
        )

    # 3. Multi-repo read-only
    if read_dirs:
        reasons.append(f"cross_repo_read_grant_only: {[str(d) for d in read_dirs]}")
        return WorkspaceAdmission(
            mode=WorkspaceMode.MULTI_DIRECTORY_READ,
            primary_repo=primary,
            worktree_required=False,
            additional_dirs=read_dirs,
            write_scopes=[],
            reasons=reasons,
        )

    # 4. Clean read-only
    reasons.append("read_only_low_risk_clean_workspace")
    return WorkspaceAdmission(
        mode=WorkspaceMode.CURRENT_WORKSPACE,
        primary_repo=primary,
        worktree_required=False,
        additional_dirs=[],
        write_scopes=[],
        reasons=reasons,
    )
