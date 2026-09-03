"""dsh_tool.py — DSH-native integration tools for Durable Execution management."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .models import JobRecord
from .recovery_tick import durable_execution_recovery_tick
from .registry import DurableJobRegistry, get_default_db_path


class DshJobTools:
    """Provides standard tool bindings for DSH agents to manage durable jobs."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or str(get_default_db_path())
        self.registry = DurableJobRegistry(self.db_path)

    def create_job(
        self,
        job_id: str,
        job_type: str,
        authorized_root: str,
        created_by: str = "dsh_agent",
        recovery_policy: str = "auto_resume_on_valid_checkpoint",
    ) -> Dict[str, Any]:
        """Create a new durable job record."""
        record = self.registry.create_job(
            job_id=job_id,
            job_type=job_type,
            authorized_root=authorized_root,
            created_by=created_by,
            recovery_policy=recovery_policy,
        )
        return {"ok": True, "job": record.to_dict()}

    def get_job(self, job_id: str) -> Dict[str, Any]:
        """Fetch current status and state of a job."""
        job = self.registry.get_job(job_id)
        if job is None:
            return {"ok": False, "error": f"job '{job_id}' not found"}
        lease = self.registry.lease_mgr.get_lease(job_id)
        return {
            "ok": True,
            "job": job.to_dict(),
            "lease": lease.to_dict() if lease else None,
        }

    def list_unfinished_jobs(self) -> Dict[str, Any]:
        """List all pending, running, or checkpointed jobs."""
        jobs = self.registry.list_unfinished_jobs()
        return {"ok": True, "unfinished_jobs": [j.to_dict() for j in jobs]}

    def cancel_job(self, job_id: str, reason: str = "cancelled_by_agent") -> Dict[str, Any]:
        """Request cancellation of an active or pending job."""
        self.registry.cancel_job(job_id, reason)
        return {"ok": True, "cancelled": job_id}

    def inspect_attempts(self, job_id: str) -> Dict[str, Any]:
        """View the full attempt history and workers for a job."""
        attempts = self.registry.get_attempts(job_id)
        events = self.registry.get_events(job_id)
        return {
            "ok": True,
            "job_id": job_id,
            "attempts": [a.to_dict() for a in attempts],
            "event_count": len(events),
        }

    def request_recovery(self) -> Dict[str, Any]:
        """Trigger an on-demand recovery sweep across all jobs."""
        actions = durable_execution_recovery_tick(self.db_path)
        return {
            "ok": True,
            "recovery_actions": [
                {"job_id": a.job_id, "action": a.action_type, "reason": a.reason, "details": a.details}
                for a in actions
            ],
        }
