"""models.py — Data models, enums and receipt schemas for Personal AI Sync V3 (Truthful Convergence)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ResourceCategory(str, Enum):
    CONVERGENCE_PLANE = "CONVERGENCE_PLANE"
    SAFETY_GATE = "SAFETY_GATE"
    HEALTH_OBSERVABILITY = "HEALTH_OBSERVABILITY"


class EvidenceLevel(str, Enum):
    L0_CLAIM = "L0_CLAIM"
    L1_ARTIFACT = "L1_ARTIFACT"
    L2_OBSERVED = "L2_OBSERVED"
    L3_REPRODUCED = "L3_REPRODUCED"
    L4_PHYSICAL_EXTERNAL = "L4_PHYSICAL_EXTERNAL"


class SyncPlane(str, Enum):
    # Convergence Planes (A)
    CANONICAL_STATE = "Canonical State Plane"
    AGENT_TOOLS_SOURCE = "Agent Tools / Source Plane"
    DEPLOYMENT_MIRROR = "Deployment Mirror Plane"
    DSH_PRESET = "DSH Preset Plane"
    DSH_CONFIG = "DSH Config Plane"
    DSH_PLUGIN = "DSH Plugin Plane"
    MCP = "MCP Plane"
    SKILL = "Skill Plane"
    RUNTIME = "Runtime Plane"

    # Safety Gates (B)
    MODEL_DISCOVERY_SAFETY = "Model Discovery / Safety Gate"

    # Health & Durability Observability (C)
    DURABLE_JOB = "Durable Job Health"
    SESSION_CONTINUITY = "Session Continuity Health"
    BACKUP_RECOVERY = "Backup / Recovery Health"


class PlaneStatus(str, Enum):
    # Standard convergence statuses
    IN_SYNC = "IN_SYNC"
    REPAIRED = "REPAIRED"
    PASS_NO_CHANGE = "PASS_NO_CHANGE"
    PASS = "PASS"
    PARTIAL_RESTART_REQUIRED = "PARTIAL_RESTART_REQUIRED"
    PARTIAL = "PARTIAL"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FAILED = "FAILED"
    OPTIONAL_UNAVAILABLE = "OPTIONAL_UNAVAILABLE"

    # Health specific
    HEALTHY = "HEALTHY"
    HEALTH_WARNING = "HEALTH_WARNING"
    HEALTH_FAILED = "HEALTH_FAILED"

    # Safety specific
    SAFETY_ADMITTED = "SAFETY_ADMITTED"
    SAFETY_CONSERVATIVE = "SAFETY_CONSERVATIVE"
    SAFETY_BLOCKED = "SAFETY_BLOCKED"


class OverallStatus(str, Enum):
    PASS_NO_CHANGE = "PASS_NO_CHANGE"
    PASS = "PASS"
    PASS_WITH_HEALTH_WARNINGS = "PASS_WITH_HEALTH_WARNINGS"
    PASS_WITH_HEALTH_FAILURE = "PASS_WITH_HEALTH_FAILURE"
    PARTIAL_RESTART_REQUIRED = "PARTIAL_RESTART_REQUIRED"
    PARTIAL_WITH_HEALTH_WARNINGS = "PARTIAL_WITH_HEALTH_WARNINGS"
    PARTIAL = "PARTIAL"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FAILED_ROLLED_BACK = "FAILED_ROLLED_BACK"
    FAILED = "FAILED"


@dataclass
class SnapshotContext:
    sync_id: str
    snapshot_id: str
    remote_fetch_at: str
    accepted_remote_commit: str
    personal_ai_state_commit: str
    started_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResourceRecord:
    resource_id: str
    plane: SyncPlane
    category: ResourceCategory
    desired_identity: str = ""
    desired_snapshot: str = ""
    source_identity: str = ""
    materialized_identity: str = ""
    registered_identity: str = ""
    active_identity: str = ""
    required_evidence_level: EvidenceLevel = EvidenceLevel.L2_OBSERVED
    evidence_refs: List[Dict[str, Any]] = field(default_factory=list)
    drift_detected: bool = False
    repair_plan: Optional[str] = None
    repair_result: Optional[str] = None
    verification_evidence: Optional[Dict[str, Any]] = None
    status: PlaneStatus = PlaneStatus.PASS
    symbol: str = "✓"  # ✓ | ○ | △ | ✗
    summary: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "plane": self.plane.value,
            "category": self.category.value,
            "desired_identity": self.desired_identity,
            "desired_snapshot": self.desired_snapshot,
            "source_identity": self.source_identity,
            "materialized_identity": self.materialized_identity,
            "registered_identity": self.registered_identity,
            "active_identity": self.active_identity,
            "required_evidence_level": self.required_evidence_level.value,
            "evidence_refs": self.evidence_refs,
            "drift_detected": self.drift_detected,
            "repair_plan": self.repair_plan,
            "repair_result": self.repair_result,
            "verification_evidence": self.verification_evidence,
            "status": self.status.value,
            "symbol": self.symbol,
            "summary": self.summary,
            "details": self.details,
            "warnings": self.warnings,
            "blockers": self.blockers,
        }


# Backwards compatibility alias
PlaneResult = ResourceRecord


@dataclass
class SyncReceipt:
    sync_id: str
    timestamp: str
    overall: OverallStatus
    snapshot: Optional[SnapshotContext] = None
    convergence_status: str = "IN_SYNC"
    safety_status: str = "ADMITTED"
    health_status: str = "HEALTHY"
    planes: Dict[str, ResourceRecord] = field(default_factory=dict)
    changes_applied: List[str] = field(default_factory=list)
    issues_encountered: List[str] = field(default_factory=list)
    tradeoff_decisions: List[Dict[str, str]] = field(default_factory=list)
    action_required_from_user: str = "无需你额外操作。"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_machine_dict(self) -> Dict[str, Any]:
        """Generate canonical machine receipt grounded strictly in verified evidence."""
        resources_list = [r.to_dict() for r in self.planes.values()]
        return {
            "SYNC_V3": True,
            "SYNC_ID": self.sync_id,
            "SNAPSHOT": self.snapshot.to_dict() if self.snapshot else {},
            "CONVERGENCE_STATUS": self.convergence_status,
            "SAFETY_STATUS": self.safety_status,
            "HEALTH_STATUS": self.health_status,
            "OVERALL": self.overall.value,
            "CHANGES_APPLIED": self.changes_applied,
            "ISSUES_ENCOUNTERED": self.issues_encountered,
            "TRADEOFF_DECISIONS": self.tradeoff_decisions,
            "ACTION_REQUIRED": self.action_required_from_user,
            "RESOURCES": resources_list,
            "METADATA": self.metadata,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_machine_dict(), indent=2, ensure_ascii=False)
