"""models.py — Data models, schemas and enums for Durable Execution."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class JobState(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    CHECKPOINTED = "CHECKPOINTED"
    WAITING_EVENT = "WAITING_EVENT"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class OrchestrationState(str, Enum):
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    ROUND_LIMIT = "ROUND_LIMIT"
    ENDED_ROUND_LIMIT = "ENDED_ROUND_LIMIT"
    ENDED = "ENDED"
    BLOCKED = "BLOCKED"


class WorkerState(str, Enum):
    STARTING = "STARTING"
    ALIVE = "ALIVE"
    HEARTBEAT_LOST = "HEARTBEAT_LOST"
    EXITED_0 = "EXITED_0"
    EXITED_ERROR = "EXITED_ERROR"
    KILLED = "KILLED"
    UNKNOWN = "UNKNOWN"


class ValidationState(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    PASS = "PASS"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FAIL = "FAIL"


class EvidenceLevel(int, Enum):
    L0_CLAIM = 0
    L1_ARTIFACT = 1
    L2_OBSERVED = 2
    L3_REPRODUCED = 3
    L4_PHYSICAL_EXTERNAL = 4

    @classmethod
    def from_str(cls, val: str) -> EvidenceLevel:
        s = val.strip().upper()
        if "L4" in s or "PHYSICAL" in s:
            return cls.L4_PHYSICAL_EXTERNAL
        if "L3" in s or "REPRODUCED" in s:
            return cls.L3_REPRODUCED
        if "L2" in s or "OBSERVED" in s:
            return cls.L2_OBSERVED
        if "L1" in s or "ARTIFACT" in s:
            return cls.L1_ARTIFACT
        return cls.L0_CLAIM


class EventType(str, Enum):
    JOB_CREATED = "JOB_CREATED"
    JOB_READY = "JOB_READY"
    LEASE_ACQUIRED = "LEASE_ACQUIRED"
    ATTEMPT_STARTED = "ATTEMPT_STARTED"
    CHECKPOINT_WRITTEN = "CHECKPOINT_WRITTEN"
    WORKER_EXITED = "WORKER_EXITED"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    ATTEMPT_RECOVERED = "ATTEMPT_RECOVERED"
    RESULT_RECEIVED = "RESULT_RECEIVED"
    VALIDATION_STARTED = "VALIDATION_STARTED"
    VALIDATION_COMPLETED = "VALIDATION_COMPLETED"
    JOB_COMPLETED = "JOB_COMPLETED"
    JOB_FAILED = "JOB_FAILED"
    JOB_CANCELLED = "JOB_CANCELLED"


@dataclass
class JobRecord:
    job_id: str
    job_type: str
    created_at: str
    updated_at: str
    job_state: str = JobState.PENDING.value
    orchestration_state: str = OrchestrationState.RUNNING.value
    validation_state: str = ValidationState.NOT_STARTED.value
    current_attempt_id: Optional[str] = None
    authorized_root: str = ""
    checkpoint_ref: Optional[str] = None
    recovery_policy: str = "auto_resume_on_valid_checkpoint"
    created_by: str = "system"
    cancel_requested: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AttemptRecord:
    attempt_id: str
    job_id: str
    writer_id: str
    worker_type: str
    worker_identity: str  # JSON-encoded WorkerIdentity
    started_at: str
    ended_at: Optional[str] = None
    worker_state: str = WorkerState.STARTING.value
    exit_code: Optional[int] = None
    workspace_ref: Optional[str] = None
    result_envelope_ref: Optional[str] = None
    checkpoint_ref: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LeaseRecord:
    job_id: str
    lease_id: str
    attempt_id: str
    writer_id: str
    acquired_at: str
    expires_at: float
    last_renewed_at: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EventRecord:
    event_id: Optional[int]
    job_id: str
    attempt_id: Optional[str]
    timestamp: str
    event_type: str
    payload_json: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationRecord:
    job_id: str
    attempt_id: Optional[str]
    validator_id: str
    required_evidence_level: str
    observed_evidence_level: str
    result: str  # PASS | REVIEW_REQUIRED | FAIL
    evidence_refs: str  # JSON-encoded references
    validated_at: str
    validation_id: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MachineCheckpoint:
    checkpoint_version: int
    job_id: str
    attempt_id: str
    input_identity: str
    source_hashes: dict[str, str] = field(default_factory=dict)
    cursor: Any = None
    partition: int = 0
    manifest_position: int = 0
    output_identity: str = ""
    output_hashes: dict[str, str] = field(default_factory=dict)
    authorized_root: str = ""
    algorithm_version: str = "1.0.0"
    next_operation: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MachineCheckpoint:
        fields = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**fields)
