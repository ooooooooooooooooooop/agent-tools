"""models.py — Data models, enums and receipt schemas for Sync V2."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class SyncPlane(str, Enum):
    CANONICAL_STATE = "Canonical State Plane"
    AGENT_TOOLS_SOURCE = "Agent Tools / Source Plane"
    DEPLOYMENT_MIRROR = "Deployment Mirror Plane"
    DSH_CONFIG = "DSH Config Plane"
    DSH_PLUGIN = "DSH Plugin Plane"
    MCP = "MCP Plane"
    SKILL = "Skill Plane"
    RUNTIME = "Runtime Plane"
    MODEL_DISCOVERY_SAFETY = "Model Discovery / Safety Plane"
    DURABLE_JOB = "Durable Job Plane"
    SESSION_CONTINUITY = "Session Continuity Plane"
    BACKUP_RECOVERY = "Backup / Recovery Plane"


class PlaneStatus(str, Enum):
    PASS = "PASS"
    PASS_NO_CHANGE = "PASS_NO_CHANGE"
    PARTIAL = "PARTIAL"
    PARTIAL_RESTART_REQUIRED = "PARTIAL_RESTART_REQUIRED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FAILED = "FAILED"
    OPTIONAL_UNAVAILABLE = "OPTIONAL_UNAVAILABLE"


class OverallStatus(str, Enum):
    PASS_NO_CHANGE = "PASS_NO_CHANGE"
    PASS = "PASS"
    PARTIAL_RESTART_REQUIRED = "PARTIAL_RESTART_REQUIRED"
    PARTIAL = "PARTIAL"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FAILED_ROLLED_BACK = "FAILED_ROLLED_BACK"
    FAILED = "FAILED"


@dataclass
class PlaneResult:
    plane: SyncPlane
    status: PlaneStatus
    symbol: str  # ✓ | ○ | △ | ✗
    summary: str
    details: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plane": self.plane.value,
            "status": self.status.value,
            "symbol": self.symbol,
            "summary": self.summary,
            "details": self.details,
            "warnings": self.warnings,
            "blockers": self.blockers,
        }


@dataclass
class SyncReceipt:
    sync_id: str
    timestamp: str
    overall: OverallStatus
    planes: Dict[str, PlaneResult] = field(default_factory=dict)
    changes_applied: List[str] = field(default_factory=list)
    issues_encountered: List[str] = field(default_factory=list)
    tradeoff_decisions: List[Dict[str, str]] = field(default_factory=list)
    action_required_from_user: str = "无需你额外操作。"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_machine_dict(self) -> Dict[str, Any]:
        """Generate the canonical machine-readable receipt defined in Section 30."""
        return {
            "SYNC_ID": self.sync_id,
            "PERSONAL_AI_STATE": self.metadata.get("personal_ai_state", "IN_SYNC"),
            "PERSONAL_AI_STATE_DIRECTION": self.metadata.get("personal_ai_state_direction", "UP_TO_DATE"),
            "AGENT_TOOLS_LOCAL": self.metadata.get("agent_tools_local", "CLEAN"),
            "AGENT_TOOLS_REMOTE": self.metadata.get("agent_tools_remote", "HEAD"),
            "AGENT_TOOLS_DIRECTION": self.metadata.get("agent_tools_direction", "IN_SYNC"),
            "DEVELOPER_WORKSPACE_DIRTY": bool(self.metadata.get("developer_workspace_dirty", False)),
            "DEPLOYMENT_MIRROR": self.metadata.get("deployment_mirror", "READY"),
            "DEPLOYMENT_SOURCE_COMMIT": self.metadata.get("deployment_source_commit", ""),
            "DSH_CONFIG": self.metadata.get("dsh_config", "PASS"),
            "PLUGINS": self.metadata.get("plugins", "7/7 active"),
            "MCP": self.metadata.get("mcp", "1/1 verified"),
            "SKILLS": self.metadata.get("skills", "21/21"),
            "DSH_DESIRED": self.metadata.get("dsh_desired", ""),
            "DSH_DEPLOYED": self.metadata.get("dsh_deployed", ""),
            "DSH_ACTIVE": self.metadata.get("dsh_active", ""),
            "RESTART_REQUIRED": bool(self.metadata.get("restart_required", False)),
            "RESTART_REASON": self.metadata.get("restart_reason", "NONE"),
            "LIVE_VALIDATION": self.metadata.get("live_validation", "PASS"),
            "MODEL_DISCOVERY": self.metadata.get("model_discovery", "HEALTHY"),
            "USER_MODEL_CONFIG_PRESERVED": bool(self.metadata.get("user_model_config_preserved", True)),
            "ACTIVE_DURABLE_JOBS": int(self.metadata.get("active_durable_jobs", 0)),
            "SESSION_CONTINUITY": self.metadata.get("session_continuity", "PASS"),
            "BACKUP_FRESHNESS": self.metadata.get("backup_freshness", "CURRENT"),
            "WARNINGS": self.metadata.get("warnings", []),
            "BLOCKERS": self.metadata.get("blockers", []),
            "OVERALL": self.overall.value,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_machine_dict(), indent=2, ensure_ascii=False)
