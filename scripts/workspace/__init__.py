"""Personal AI Workspace Isolation & Access Package."""
from __future__ import annotations

from .execution_contract import (
    WorkspaceAdmission,
    WorkspaceMode,
    admit_workspace_mode,
    is_git_dirty,
)
from .harness_adapters import (
    ClaudeWorkspaceAdapter,
    CodexWorkspaceAdapter,
    DshWorkspaceAdapter,
    GeminiWorkspaceAdapter,
)
from .provisioner import WorktreeProvisioner, WorktreeRecord

__all__ = [
    "WorkspaceMode",
    "WorkspaceAdmission",
    "admit_workspace_mode",
    "is_git_dirty",
    "WorktreeProvisioner",
    "WorktreeRecord",
    "ClaudeWorkspaceAdapter",
    "CodexWorkspaceAdapter",
    "DshWorkspaceAdapter",
    "GeminiWorkspaceAdapter",
]
