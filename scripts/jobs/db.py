"""db.py — SQLite storage layer for Durable Execution Registry."""
from __future__ import annotations

import contextlib
import os
import sqlite3
from pathlib import Path
from typing import Generator


SCHEMA_SQL = """
PRAGMA journal_mode = DELETE;
PRAGMA busy_timeout = 10000;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    job_state TEXT NOT NULL,
    orchestration_state TEXT NOT NULL,
    validation_state TEXT NOT NULL,
    current_attempt_id TEXT,
    authorized_root TEXT NOT NULL,
    checkpoint_ref TEXT,
    recovery_policy TEXT NOT NULL DEFAULT 'auto_resume_on_valid_checkpoint',
    created_by TEXT NOT NULL DEFAULT 'system',
    cancel_requested INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(job_state);
CREATE INDEX IF NOT EXISTS idx_jobs_updated ON jobs(updated_at);

CREATE TABLE IF NOT EXISTS attempts (
    attempt_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    writer_id TEXT NOT NULL,
    worker_type TEXT NOT NULL,
    worker_identity TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    worker_state TEXT NOT NULL,
    exit_code INTEGER,
    workspace_ref TEXT,
    result_envelope_ref TEXT,
    checkpoint_ref TEXT,
    FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_attempts_job ON attempts(job_id);

CREATE TABLE IF NOT EXISTS leases (
    job_id TEXT PRIMARY KEY,
    lease_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    writer_id TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at REAL NOT NULL,
    last_renewed_at REAL NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_leases_expiry ON leases(expires_at);

CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    attempt_id TEXT,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id, timestamp);

CREATE TABLE IF NOT EXISTS validations (
    validation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    attempt_id TEXT,
    validator_id TEXT NOT NULL,
    required_evidence_level TEXT NOT NULL,
    observed_evidence_level TEXT NOT NULL,
    result TEXT NOT NULL,
    evidence_refs TEXT NOT NULL,
    validated_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_validations_job ON validations(job_id);
"""


def init_db(db_path: str | Path) -> None:
    """Initialize database schema idempotently."""
    p = Path(db_path)
    if str(db_path) != ":memory:":
        p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        conn.executescript(SCHEMA_SQL)
    finally:
        conn.close()


@contextlib.contextmanager
def get_connection(db_path: str | Path, *, write: bool = False) -> Generator[sqlite3.Connection, None, None]:
    """Provide a connection with appropriate transaction isolation."""
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        if write:
            conn.execute("BEGIN IMMEDIATE;")
        yield conn
        if write:
            conn.commit()
    except Exception:
        if write:
            conn.rollback()
        raise
    finally:
        conn.close()
