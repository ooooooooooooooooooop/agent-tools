"""recovery_tick.py — Low-cost idempotent recovery supervisor tick without resident daemons."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .checkpoint_ext import MachineCheckpointManager
from .models import (
    EventType,
    JobRecord,
    JobState,
    OrchestrationState,
    WorkerState,
)
from .registry import DurableJobRegistry
from .worker_identity import WorkerIdentity, is_worker_alive


@dataclass
class RecoveryAction:
    job_id: str
    action_type: str  # NO_ACTION | RESUME_ATTEMPT | WAITING_EVENT | REVIEW_REQUIRED
    reason: str
    details: Dict[str, Any]


def durable_execution_recovery_tick(
    db_path: Optional[str | Path] = None,
    *,
    now_epoch: Optional[float] = None,
    auto_spawn_handler: Optional[Any] = None,
) -> List[RecoveryAction]:
    """Execute one periodic recovery sweep across all unfinished jobs.

    Pure Python / SQLite / OS: executes with ZERO LLM calls.
    Idempotent: repeatedly executing when a worker is alive never spawns a duplicate worker.
    """
    registry = DurableJobRegistry(db_path)
    ckpt_mgr = MachineCheckpointManager()
    now = now_epoch if now_epoch is not None else time.time()
    actions: List[RecoveryAction] = []

    unfinished = registry.list_unfinished_jobs()
    for job in unfinished:
        # Rule 1: Never touch completed or cancelled jobs
        if job.job_state in (JobState.COMPLETED.value, JobState.CANCELLED.value):
            continue

        lease = registry.lease_mgr.get_lease(job.job_id)
        current_attempt = None
        if job.current_attempt_id:
            attempts = registry.get_attempts(job.job_id)
            current_attempt = next((a for a in attempts if a.attempt_id == job.current_attempt_id), None)

        worker_alive = False
        if current_attempt and current_attempt.worker_identity:
            try:
                ident = WorkerIdentity.from_json(current_attempt.worker_identity)
                worker_alive = is_worker_alive(ident)
            except Exception:
                worker_alive = False

        # Rule 2: Worker is alive (regardless of orchestration state!)
        if worker_alive:
            # Even if orchestration round limit was reached: NO SECOND WORKER!
            if job.orchestration_state == OrchestrationState.ENDED_ROUND_LIMIT.value:
                actions.append(RecoveryAction(
                    job_id=job.job_id,
                    action_type="NO_ACTION",
                    reason="orchestration ended round limit but worker is still alive; no duplicate worker",
                    details={"worker_alive": True, "attempt_id": job.current_attempt_id},
                ))
            else:
                actions.append(RecoveryAction(
                    job_id=job.job_id,
                    action_type="NO_ACTION",
                    reason="worker is healthy and running",
                    details={"worker_alive": True, "attempt_id": job.current_attempt_id},
                ))
            continue

        # Rule 3: Worker is dead / host restarted / PID missing
        # If job is already waiting on event or review, don't spin
        if job.job_state in (JobState.WAITING_EVENT.value,):
            actions.append(RecoveryAction(
                job_id=job.job_id,
                action_type="NO_ACTION",
                reason="job is waiting for external event or human review",
                details={"job_state": job.job_state},
            ))
            continue

        # Worker is dead while job is RUNNING / PENDING / CHECKPOINTED
        # Check machine checkpoint validity
        checkpoint_obj = None
        checkpoint_valid = False
        invalid_reason = "no checkpoint recorded"

        if job.checkpoint_ref and Path(job.checkpoint_ref).is_file():
            try:
                raw_ckpt = json.loads(Path(job.checkpoint_ref).read_text(encoding="utf-8"))
                from .models import MachineCheckpoint
                checkpoint_obj = MachineCheckpoint.from_dict(raw_ckpt)
                checkpoint_valid, invalid_reason = ckpt_mgr.validate_checkpoint_for_resume(checkpoint_obj)
            except Exception as exc:
                checkpoint_valid = False
                invalid_reason = f"checkpoint corrupted: {exc}"

        if checkpoint_valid and checkpoint_obj is not None:
            # Revoke stale lease
            registry.lease_mgr.revoke_or_expire_lease(job.job_id, reason="dead_worker_recovered")

            # Start attempt N+1
            new_writer_id = f"recovered_worker_{os.getpid()}"
            new_attempt = registry.start_attempt(
                job_id=job.job_id,
                writer_id=new_writer_id,
                worker_type=current_attempt.worker_type if current_attempt else "recovered_process",
                worker_identity={"pid": os.getpid(), "host": "local", "resumed": True},
                workspace_ref=current_attempt.workspace_ref if current_attempt else None,
            )

            # Link checkpoint and emit ATTEMPT_RECOVERED
            registry.record_checkpoint(job.job_id, new_attempt.attempt_id, job.checkpoint_ref)

            action = RecoveryAction(
                job_id=job.job_id,
                action_type="RESUME_ATTEMPT",
                reason="dead worker recovered from valid machine checkpoint; attempt advanced",
                details={
                    "previous_attempt": job.current_attempt_id,
                    "resumed_attempt": new_attempt.attempt_id,
                    "checkpoint_cursor": checkpoint_obj.cursor,
                },
            )
            actions.append(action)

            # If an execution callback is supplied, trigger it
            if auto_spawn_handler:
                auto_spawn_handler(job, new_attempt, checkpoint_obj)

        else:
            # Checkpoint is invalid or missing -> do NOT blindly restart from scratch!
            registry.lease_mgr.revoke_or_expire_lease(job.job_id, reason="dead_worker_invalid_checkpoint")
            from .db import get_connection
            with get_connection(registry.db_path, write=True) as conn:
                conn.execute(
                    "UPDATE jobs SET job_state = ?, validation_state = ? WHERE job_id = ?",
                    (JobState.WAITING_EVENT.value, "REVIEW_REQUIRED", job.job_id),
                )

            actions.append(RecoveryAction(
                job_id=job.job_id,
                action_type="REVIEW_REQUIRED",
                reason=f"worker dead but checkpoint cannot resume deterministically: {invalid_reason}",
                details={"checkpoint_ref": job.checkpoint_ref, "validation_error": invalid_reason},
            ))

    return actions
