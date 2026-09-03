"""Personal AI Minimal Durable Execution Coordination Layer.

Provides atomic SQLite-backed Job Registry, single-writer atomic lease,
decoupled 4-domain state machine, machine-extended checkpoints,
L0-L4 evidence levels, and deterministic recovery ticks without resident daemons.
"""
from __future__ import annotations

from .models import (
    JobState,
    OrchestrationState,
    WorkerState,
    ValidationState,
    EvidenceLevel,
    EventType,
    JobRecord,
    AttemptRecord,
    LeaseRecord,
    EventRecord,
    ValidationRecord,
    MachineCheckpoint,
)
from .registry import (
    DurableJobRegistry,
    get_default_db_path,
)
from .lease import (
    LeaseManager,
    LeaseDeniedError,
)
from .worker_identity import (
    WorkerIdentity,
    create_worker_identity,
    is_worker_alive,
)
from .checkpoint_ext import (
    MachineCheckpointManager,
)
from .final_adjudicator import (
    FinalAdjudicator,
)
from .dsh_tool import (
    DshJobTools,
)
from .recovery_tick import (
    durable_execution_recovery_tick,
)

__all__ = [
    "JobState",
    "OrchestrationState",
    "WorkerState",
    "ValidationState",
    "EvidenceLevel",
    "EventType",
    "JobRecord",
    "AttemptRecord",
    "LeaseRecord",
    "EventRecord",
    "ValidationRecord",
    "MachineCheckpoint",
    "DurableJobRegistry",
    "get_default_db_path",
    "LeaseManager",
    "LeaseDeniedError",
    "WorkerIdentity",
    "create_worker_identity",
    "is_worker_alive",
    "MachineCheckpointManager",
    "FinalAdjudicator",
    "DshJobTools",
    "durable_execution_recovery_tick",
]
