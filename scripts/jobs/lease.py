"""lease.py — Atomic SQLite lease management for Single Writer mutual exclusion."""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .db import get_connection
from .models import EventType, LeaseRecord


class LeaseDeniedError(RuntimeError):
    """Raised when an active lease is held by another writer."""


class LeaseManager:
    """Manages mutual exclusion leases on jobs with atomic compare-and-swap semantics."""

    def __init__(self, db_path: str | Path, default_ttl: float = 300.0) -> None:
        self.db_path = str(db_path)
        self.default_ttl = default_ttl

    def acquire_lease(
        self,
        job_id: str,
        attempt_id: str,
        writer_id: str,
        ttl_seconds: Optional[float] = None,
        *,
        now_epoch: Optional[float] = None,
    ) -> LeaseRecord:
        """Atomically acquire a single-writer lease for a job.

        Fails with LeaseDeniedError if a valid unexpired lease is already held by a different writer.
        """
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
        now = now_epoch if now_epoch is not None else time.time()
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        new_lease_id = f"lease-{uuid.uuid4().hex[:12]}"
        new_expires_at = now + ttl

        with get_connection(self.db_path, write=True) as conn:
            # Query existing lease atomically under BEGIN IMMEDIATE
            cur = conn.execute(
                "SELECT lease_id, attempt_id, writer_id, acquired_at, expires_at, last_renewed_at "
                "FROM leases WHERE job_id = ?",
                (job_id,),
            )
            row = cur.fetchone()
            if row is not None:
                existing_expires = float(row["expires_at"])
                existing_writer = str(row["writer_id"])
                # Active lease check
                if existing_expires > now and existing_writer != writer_id:
                    raise LeaseDeniedError(
                        f"LEASE_DENIED: active lease held by writer '{existing_writer}' "
                        f"until {existing_expires} (now: {now})"
                    )

            # Insert or replace atomically
            conn.execute(
                "INSERT OR REPLACE INTO leases (job_id, lease_id, attempt_id, writer_id, acquired_at, expires_at, last_renewed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job_id, new_lease_id, attempt_id, writer_id, now_iso, new_expires_at, now),
            )

            # Append event
            conn.execute(
                "INSERT INTO events (job_id, attempt_id, timestamp, event_type, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    job_id,
                    attempt_id,
                    now_iso,
                    EventType.LEASE_ACQUIRED.value,
                    f'{{"lease_id": "{new_lease_id}", "writer_id": "{writer_id}", "expires_at": {new_expires_at}}}',
                ),
            )

            return LeaseRecord(
                job_id=job_id,
                lease_id=new_lease_id,
                attempt_id=attempt_id,
                writer_id=writer_id,
                acquired_at=now_iso,
                expires_at=new_expires_at,
                last_renewed_at=now,
            )

    def renew_lease(
        self,
        job_id: str,
        lease_id: str,
        ttl_seconds: Optional[float] = None,
        *,
        now_epoch: Optional[float] = None,
    ) -> LeaseRecord:
        """Renew an existing lease held by the caller."""
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
        now = now_epoch if now_epoch is not None else time.time()
        new_expires_at = now + ttl

        with get_connection(self.db_path, write=True) as conn:
            cur = conn.execute(
                "SELECT lease_id, attempt_id, writer_id, acquired_at, expires_at, last_renewed_at "
                "FROM leases WHERE job_id = ?",
                (job_id,),
            )
            row = cur.fetchone()
            if row is None or row["lease_id"] != lease_id:
                raise LeaseDeniedError(f"cannot renew: lease '{lease_id}' not found for job '{job_id}'")

            conn.execute(
                "UPDATE leases SET expires_at = ?, last_renewed_at = ? WHERE job_id = ? AND lease_id = ?",
                (new_expires_at, now, job_id, lease_id),
            )

            return LeaseRecord(
                job_id=job_id,
                lease_id=lease_id,
                attempt_id=row["attempt_id"],
                writer_id=row["writer_id"],
                acquired_at=row["acquired_at"],
                expires_at=new_expires_at,
                last_renewed_at=now,
            )

    def release_lease(self, job_id: str, lease_id: str) -> bool:
        """Release a lease gracefully upon worker completion or shutdown."""
        with get_connection(self.db_path, write=True) as conn:
            cur = conn.execute("DELETE FROM leases WHERE job_id = ? AND lease_id = ?", (job_id, lease_id))
            return cur.rowcount > 0

    def revoke_or_expire_lease(self, job_id: str, reason: str = "recovery_revocation") -> bool:
        """Revoke a stale or dead worker's lease during recovery."""
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with get_connection(self.db_path, write=True) as conn:
            cur = conn.execute("SELECT attempt_id, lease_id, writer_id FROM leases WHERE job_id = ?", (job_id,))
            row = cur.fetchone()
            if row is None:
                return False

            attempt_id = row["attempt_id"]
            lease_id = row["lease_id"]

            conn.execute("DELETE FROM leases WHERE job_id = ?", (job_id,))
            conn.execute(
                "INSERT INTO events (job_id, attempt_id, timestamp, event_type, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    job_id,
                    attempt_id,
                    now_iso,
                    EventType.LEASE_EXPIRED.value,
                    f'{{"lease_id": "{lease_id}", "reason": "{reason}"}}',
                ),
            )
            return True

    def get_lease(self, job_id: str) -> Optional[LeaseRecord]:
        """Query the current lease record for a job."""
        with get_connection(self.db_path, write=False) as conn:
            cur = conn.execute(
                "SELECT job_id, lease_id, attempt_id, writer_id, acquired_at, expires_at, last_renewed_at "
                "FROM leases WHERE job_id = ?",
                (job_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return LeaseRecord(
                job_id=row["job_id"],
                lease_id=row["lease_id"],
                attempt_id=row["attempt_id"],
                writer_id=row["writer_id"],
                acquired_at=row["acquired_at"],
                expires_at=float(row["expires_at"]),
                last_renewed_at=float(row["last_renewed_at"]),
            )
