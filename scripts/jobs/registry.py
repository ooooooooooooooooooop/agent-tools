"""registry.py — Durable Job Registry managing jobs, attempts, state transitions and events."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .checkpoint_ext import MachineCheckpoint
from .db import get_connection, init_db
from .final_adjudicator import FinalAdjudicator
from .lease import LeaseDeniedError, LeaseManager
from .models import (
    AttemptRecord,
    EventType,
    JobRecord,
    JobState,
    OrchestrationState,
    ValidationRecord,
    ValidationState,
    WorkerState,
)


def get_default_db_path() -> Path:
    """Return the canonical database path within the durable runtime state directory."""
    custom = os.environ.get("PERSONAL_AI_JOBS_DB")
    if custom:
        return Path(custom)
    p = Path.home() / ".personal-ai" / "jobs" / "durable_jobs.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class DurableJobRegistry:
    """The central SQLite-backed coordination authority for durable jobs."""

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        self.db_path = str(db_path or get_default_db_path())
        init_db(self.db_path)
        self.lease_mgr = LeaseManager(self.db_path)
        self.adjudicator = FinalAdjudicator()

    def create_job(
        self,
        job_id: str,
        job_type: str,
        authorized_root: str,
        created_by: str = "system",
        recovery_policy: str = "auto_resume_on_valid_checkpoint",
    ) -> JobRecord:
        """Atomically register a new durable job in PENDING state."""
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with get_connection(self.db_path, write=True) as conn:
            conn.execute(
                "INSERT INTO jobs (job_id, job_type, created_at, updated_at, job_state, orchestration_state, "
                "validation_state, authorized_root, recovery_policy, created_by, cancel_requested) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (
                    job_id,
                    job_type,
                    now_iso,
                    now_iso,
                    JobState.PENDING.value,
                    OrchestrationState.WAITING.value,
                    ValidationState.NOT_STARTED.value,
                    authorized_root,
                    recovery_policy,
                    created_by,
                ),
            )
            conn.execute(
                "INSERT INTO events (job_id, attempt_id, timestamp, event_type, payload_json) "
                "VALUES (?, NULL, ?, ?, ?)",
                (
                    job_id,
                    now_iso,
                    EventType.JOB_CREATED.value,
                    f'{{"job_type": "{job_type}", "authorized_root": "{authorized_root}"}}',
                ),
            )

        return JobRecord(
            job_id=job_id,
            job_type=job_type,
            created_at=now_iso,
            updated_at=now_iso,
            job_state=JobState.PENDING.value,
            orchestration_state=OrchestrationState.WAITING.value,
            validation_state=ValidationState.NOT_STARTED.value,
            authorized_root=authorized_root,
            recovery_policy=recovery_policy,
            created_by=created_by,
        )

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        """Fetch a job record by ID."""
        with get_connection(self.db_path, write=False) as conn:
            cur = conn.execute(
                "SELECT job_id, job_type, created_at, updated_at, job_state, orchestration_state, "
                "validation_state, current_attempt_id, authorized_root, checkpoint_ref, recovery_policy, "
                "created_by, cancel_requested FROM jobs WHERE job_id = ?",
                (job_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return JobRecord(
                job_id=row["job_id"],
                job_type=row["job_type"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                job_state=row["job_state"],
                orchestration_state=row["orchestration_state"],
                validation_state=row["validation_state"],
                current_attempt_id=row["current_attempt_id"],
                authorized_root=row["authorized_root"],
                checkpoint_ref=row["checkpoint_ref"],
                recovery_policy=row["recovery_policy"],
                created_by=row["created_by"],
                cancel_requested=row["cancel_requested"],
            )

    def list_unfinished_jobs(self) -> List[JobRecord]:
        """Return all active, in-flight, or paused jobs."""
        unfinished_states = (
            JobState.PENDING.value,
            JobState.READY.value,
            JobState.RUNNING.value,
            JobState.CHECKPOINTED.value,
            JobState.WAITING_EVENT.value,
            JobState.RECOVERING.value,
        )
        placeholders = ",".join("?" * len(unfinished_states))
        with get_connection(self.db_path, write=False) as conn:
            cur = conn.execute(
                f"SELECT job_id, job_type, created_at, updated_at, job_state, orchestration_state, "
                f"validation_state, current_attempt_id, authorized_root, checkpoint_ref, recovery_policy, "
                f"created_by, cancel_requested FROM jobs WHERE job_state IN ({placeholders}) ORDER BY created_at ASC",
                unfinished_states,
            )
            return [
                JobRecord(
                    job_id=r["job_id"],
                    job_type=r["job_type"],
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                    job_state=r["job_state"],
                    orchestration_state=r["orchestration_state"],
                    validation_state=r["validation_state"],
                    current_attempt_id=r["current_attempt_id"],
                    authorized_root=r["authorized_root"],
                    checkpoint_ref=r["checkpoint_ref"],
                    recovery_policy=r["recovery_policy"],
                    created_by=r["created_by"],
                    cancel_requested=r["cancel_requested"],
                )
                for r in cur.fetchall()
            ]

    def start_attempt(
        self,
        job_id: str,
        writer_id: str,
        worker_type: str,
        worker_identity: dict[str, Any],
        workspace_ref: Optional[str] = None,
        ttl_seconds: Optional[float] = None,
    ) -> AttemptRecord:
        """Start a new attempt for a job, atomically acquiring the single-writer lease."""
        job = self.get_job(job_id)
        if job is None:
            raise ValueError(f"job '{job_id}' not found")

        if job.job_state == JobState.COMPLETED.value:
            raise RuntimeError(f"cannot start attempt: job '{job_id}' is already COMPLETED")

        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # Generate attempt identity: attempt-N
        with get_connection(self.db_path, write=False) as conn:
            cur = conn.execute("SELECT COUNT(*) FROM attempts WHERE job_id = ?", (job_id,))
            count = cur.fetchone()[0]
        attempt_id = f"{job_id}_att_{count + 1}"

        # Atomic lease acquisition (will fail closed if another active writer exists)
        self.lease_mgr.acquire_lease(job_id, attempt_id, writer_id, ttl_seconds)

        # Record attempt & update job state
        ident_json = json.dumps(worker_identity, ensure_ascii=False)
        with get_connection(self.db_path, write=True) as conn:
            conn.execute(
                "INSERT INTO attempts (attempt_id, job_id, writer_id, worker_type, worker_identity, started_at, worker_state, workspace_ref) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    attempt_id,
                    job_id,
                    writer_id,
                    worker_type,
                    ident_json,
                    now_iso,
                    WorkerState.ALIVE.value,
                    workspace_ref,
                ),
            )
            conn.execute(
                "UPDATE jobs SET job_state = ?, orchestration_state = ?, current_attempt_id = ?, updated_at = ? WHERE job_id = ?",
                (JobState.RUNNING.value, OrchestrationState.RUNNING.value, attempt_id, now_iso, job_id),
            )
            conn.execute(
                "INSERT INTO events (job_id, attempt_id, timestamp, event_type, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    job_id,
                    attempt_id,
                    now_iso,
                    EventType.ATTEMPT_STARTED.value,
                    f'{{"attempt_id": "{attempt_id}", "writer_id": "{writer_id}"}}',
                ),
            )

        return AttemptRecord(
            attempt_id=attempt_id,
            job_id=job_id,
            writer_id=writer_id,
            worker_type=worker_type,
            worker_identity=ident_json,
            started_at=now_iso,
            worker_state=WorkerState.ALIVE.value,
            workspace_ref=workspace_ref,
        )

    def update_orchestration_state(self, job_id: str, new_state: str) -> None:
        """Update orchestration state without conflating it with the job state.

        CRITICAL INVARIANT:
        Orchestration ending (e.g. ENDED_ROUND_LIMIT) NEVER automatically changes
        job_state to FAILED. The job remains RUNNING as long as its worker is alive.
        """
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with get_connection(self.db_path, write=True) as conn:
            conn.execute(
                "UPDATE jobs SET orchestration_state = ?, updated_at = ? WHERE job_id = ?",
                (new_state, now_iso, job_id),
            )

    def update_worker_state(
        self,
        attempt_id: str,
        new_state: str,
        exit_code: Optional[int] = None,
    ) -> None:
        """Update the physical execution state of an attempt."""
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with get_connection(self.db_path, write=True) as conn:
            conn.execute(
                "UPDATE attempts SET worker_state = ?, exit_code = ?, ended_at = ? WHERE attempt_id = ?",
                (new_state, exit_code, now_iso if new_state in (WorkerState.EXITED_0.value, WorkerState.EXITED_ERROR.value, WorkerState.KILLED.value) else None, attempt_id),
            )
            cur = conn.execute("SELECT job_id FROM attempts WHERE attempt_id = ?", (attempt_id,))
            row = cur.fetchone()
            if row:
                job_id = row["job_id"]
                conn.execute(
                    "INSERT INTO events (job_id, attempt_id, timestamp, event_type, payload_json) VALUES (?, ?, ?, ?, ?)",
                    (
                        job_id,
                        attempt_id,
                        now_iso,
                        EventType.WORKER_EXITED.value,
                        f'{{"worker_state": "{new_state}", "exit_code": {exit_code}}}',
                    ),
                )

    def record_checkpoint(self, job_id: str, attempt_id: str, checkpoint_path: str) -> None:
        """Record an updated machine checkpoint reference on job and attempt."""
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with get_connection(self.db_path, write=True) as conn:
            conn.execute(
                "UPDATE jobs SET checkpoint_ref = ?, job_state = ?, updated_at = ? WHERE job_id = ?",
                (checkpoint_path, JobState.CHECKPOINTED.value, now_iso, job_id),
            )
            conn.execute(
                "UPDATE attempts SET checkpoint_ref = ? WHERE attempt_id = ?",
                (checkpoint_path, attempt_id),
            )
            conn.execute(
                "INSERT INTO events (job_id, attempt_id, timestamp, event_type, payload_json) VALUES (?, ?, ?, ?, ?)",
                (
                    job_id,
                    attempt_id,
                    now_iso,
                    EventType.CHECKPOINT_WRITTEN.value,
                    f'{{"checkpoint_ref": "{checkpoint_path}"}}',
                ),
            )

    def record_result_envelope(self, job_id: str, attempt_id: str, envelope_path: str) -> None:
        """Bind an output ResultEnvelope and trigger RESULT_RECEIVED event."""
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with get_connection(self.db_path, write=True) as conn:
            conn.execute(
                "UPDATE attempts SET result_envelope_ref = ? WHERE attempt_id = ?",
                (envelope_path, attempt_id),
            )
            conn.execute(
                "INSERT INTO events (job_id, attempt_id, timestamp, event_type, payload_json) VALUES (?, ?, ?, ?, ?)",
                (
                    job_id,
                    attempt_id,
                    now_iso,
                    EventType.RESULT_RECEIVED.value,
                    f'{{"result_envelope_ref": "{envelope_path}"}}',
                ),
            )

    def final_adjudicate(
        self,
        job_id: str,
        attempt_id: str,
        validator_id: str,
        envelope_data: Dict[str, Any],
        required_validations: Optional[List[Dict[str, Any]]] = None,
    ) -> ValidationRecord:
        """Sole authoritative gateway for admitting job completion.

        Evaluates evidence levels against requirements. If observed evidence < required,
        adjudication strictly REJECTS PASS and marks REVIEW_REQUIRED.
        Only when all criteria pass does JOB_STATE mutate to COMPLETED.
        """
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

        # 1. Evaluate evidence levels through FinalAdjudicator
        outcome, records, summary_reason = self.adjudicator.adjudicate_envelope(
            envelope_data, required_validations
        )

        highest_req = "L3_REPRODUCED" if any(r.get("required_evidence_level") == "L3_REPRODUCED" for r in (required_validations or [])) else "L1_ARTIFACT"
        highest_obs = "L3_REPRODUCED" if outcome == ValidationState.PASS.value and highest_req == "L3_REPRODUCED" else "L1_ARTIFACT"

        with get_connection(self.db_path, write=True) as conn:
            # 2. Record validation finding
            conn.execute(
                "INSERT INTO validations (job_id, attempt_id, validator_id, required_evidence_level, "
                "observed_evidence_level, result, evidence_refs, validated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    attempt_id,
                    validator_id,
                    highest_req,
                    highest_obs,
                    outcome,
                    json.dumps(records, ensure_ascii=False),
                    now_iso,
                ),
            )

            # 3. Mutate job and attempt states based on adjudication outcome
            if outcome == ValidationState.PASS.value:
                conn.execute(
                    "UPDATE jobs SET job_state = ?, validation_state = ?, updated_at = ? WHERE job_id = ?",
                    (JobState.COMPLETED.value, ValidationState.PASS.value, now_iso, job_id),
                )
                conn.execute(
                    "INSERT INTO events (job_id, attempt_id, timestamp, event_type, payload_json) VALUES (?, ?, ?, ?, ?)",
                    (
                        job_id,
                        attempt_id,
                        now_iso,
                        EventType.JOB_COMPLETED.value,
                        f'{{"outcome": "{outcome}", "summary": "{summary_reason}"}}',
                    ),
                )
                # Release lease gracefully
                conn.execute("DELETE FROM leases WHERE job_id = ?", (job_id,))
            else:
                conn.execute(
                    "UPDATE jobs SET validation_state = ?, job_state = ?, updated_at = ? WHERE job_id = ?",
                    (outcome, JobState.WAITING_EVENT.value, now_iso, job_id),
                )
                conn.execute(
                    "INSERT INTO events (job_id, attempt_id, timestamp, event_type, payload_json) VALUES (?, ?, ?, ?, ?)",
                    (
                        job_id,
                        attempt_id,
                        now_iso,
                        EventType.VALIDATION_COMPLETED.value,
                        f'{{"outcome": "{outcome}", "summary": "{summary_reason}"}}',
                    ),
                )

        return ValidationRecord(
            job_id=job_id,
            attempt_id=attempt_id,
            validator_id=validator_id,
            required_evidence_level=highest_req,
            observed_evidence_level=highest_obs,
            result=outcome,
            evidence_refs=json.dumps(records, ensure_ascii=False),
            validated_at=now_iso,
        )

    def cancel_job(self, job_id: str, reason: str = "user_cancellation") -> None:
        """Cancel a job and revoke any lease."""
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with get_connection(self.db_path, write=True) as conn:
            conn.execute(
                "UPDATE jobs SET cancel_requested = 1, job_state = ?, updated_at = ? WHERE job_id = ?",
                (JobState.CANCELLED.value, now_iso, job_id),
            )
            conn.execute(
                "INSERT INTO events (job_id, attempt_id, timestamp, event_type, payload_json) VALUES (?, NULL, ?, ?, ?)",
                (
                    job_id,
                    now_iso,
                    EventType.JOB_CANCELLED.value,
                    f'{{"reason": "{reason}"}}',
                ),
            )
            conn.execute("DELETE FROM leases WHERE job_id = ?", (job_id,))

    def get_events(self, job_id: str) -> List[Dict[str, Any]]:
        """Retrieve the append-only event trail for a job."""
        with get_connection(self.db_path, write=False) as conn:
            cur = conn.execute(
                "SELECT event_id, job_id, attempt_id, timestamp, event_type, payload_json "
                "FROM events WHERE job_id = ? ORDER BY event_id ASC",
                (job_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_attempts(self, job_id: str) -> List[AttemptRecord]:
        """Fetch all attempts recorded for a job."""
        with get_connection(self.db_path, write=False) as conn:
            cur = conn.execute(
                "SELECT attempt_id, job_id, writer_id, worker_type, worker_identity, started_at, ended_at, "
                "worker_state, exit_code, workspace_ref, result_envelope_ref, checkpoint_ref "
                "FROM attempts WHERE job_id = ? ORDER BY started_at ASC",
                (job_id,),
            )
            return [
                AttemptRecord(
                    attempt_id=r["attempt_id"],
                    job_id=r["job_id"],
                    writer_id=r["writer_id"],
                    worker_type=r["worker_type"],
                    worker_identity=r["worker_identity"],
                    started_at=r["started_at"],
                    ended_at=r["ended_at"],
                    worker_state=r["worker_state"],
                    exit_code=r["exit_code"],
                    workspace_ref=r["workspace_ref"],
                    result_envelope_ref=r["result_envelope_ref"],
                    checkpoint_ref=r["checkpoint_ref"],
                )
                for r in cur.fetchall()
            ]
