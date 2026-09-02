"""Active Work Registry and Lease State Machine for Agent Switchboard.

Prevents duplicate exploration agent spawning, manages active work leases,
enforces retry budgets, enables cached receipt reuse, and preserves legitimate
parallelism across distinct targets, domains, and lanes.

Design principles:
- Deterministic work identity (work_key) computed from
  (parent_session, task_scope, lane, target, intent, evidence_domain).
- Strict lease state machine:
  PLANNED -> SPAWNING -> ACTIVE -> COMPLETED / FAILED / TIMED_OUT / CANCELLED.
- Concurrency-safe via atomic file locks and atomic writes.
- Legitimate parallelism preserved: distinct targets, domains, or lanes yield
  orthogonal work_keys.
- Explicit ownership modeling: brain_owned vs orchestrator_owned.
- Fail-safe & loop-bounded: stale leases expire, retry budgets prevent spin loops.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import atomic_io

BROKER_HOME = Path(os.environ.get("AGENT_BROKER_HOME", Path.home() / ".agent-broker"))
REGISTRY_DIR = BROKER_HOME / "work-registry"

# Work States
STATE_PLANNED = "PLANNED"
STATE_SPAWNING = "SPAWNING"
STATE_ACTIVE = "ACTIVE"
STATE_COMPLETED = "COMPLETED"
STATE_FAILED = "FAILED"
STATE_TIMED_OUT = "TIMED_OUT"
STATE_CANCELLED = "CANCELLED"

VALID_STATES = frozenset({
    STATE_PLANNED,
    STATE_SPAWNING,
    STATE_ACTIVE,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_TIMED_OUT,
    STATE_CANCELLED,
})

# Decision Actions
ACTION_SPAWN_ALLOWED = "SPAWN_ALLOWED"
ACTION_SPAWN_SUPPRESSED_DUPLICATE = "SPAWN_SUPPRESSED_DUPLICATE"
ACTION_REUSE_COMPLETED = "REUSE_COMPLETED"
ACTION_RETRY_BUDGET_EXHAUSTED = "RETRY_BUDGET_EXHAUSTED"
ACTION_OWNERSHIP_CONFLICT = "OWNERSHIP_CONFLICT"

DEFAULT_LEASE_TTL_SECONDS = 300.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_SPAWN_TIMEOUT_SECONDS = 60.0


def normalize_intent(intent: str) -> str:
    """Normalize prompt/intent string: collapse whitespace and lower-case."""
    return " ".join(str(intent or "").strip().lower().split())


def normalize_target(target: str) -> str:
    """Normalize file path or component identifier."""
    return str(target or "").strip().replace("\\", "/").rstrip("/").lower()


def compute_work_key(
    parent_session: str,
    task_scope: str = "",
    target: str = "",
    intent: str = "",
    evidence_domain: str = "code",
    explicit_independence_role: str = "",
    lane: str = "",  # execution metadata (ignored in semantic work_key)
    **kwargs: Any,
) -> str:
    """Compute canonical 64-char sha256 semantic work_key.

    Semantic work identity is determined by what work needs to be done and its
    declared semantic role, NOT by execution mechanisms (lane, agent_type,
    provider, model, or caller).

    Legitimate parallelism requires a distinct explicit_independence_role
    (e.g., 'source-inspection' vs 'opposite-review' vs 'runtime-validation').
    """
    p_sess = str(parent_session or "").strip()
    scope = str(task_scope or "").strip().lower()
    tgt = normalize_target(target)
    itn = normalize_intent(intent)
    dom = str(evidence_domain or "").strip().lower()
    role = str(
        explicit_independence_role
        or kwargs.get("independence_role")
        or kwargs.get("role")
        or kwargs.get("semantic_role")
        or ""
    ).strip().lower()
    raw = f"{p_sess}:{scope}:{tgt}:{itn}:{dom}:{role}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class WorkLease:
    work_key: str
    lease_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_session: str = ""
    task_scope: str = ""
    target: str = ""
    intent: str = ""
    evidence_domain: str = "code"
    explicit_independence_role: str = ""
    # Execution metadata (not part of semantic deduplication identity)
    lane: str = "explore"
    agent_id: str = ""
    agent_type: str = ""
    provider: str = ""
    model: str = ""
    state: str = STATE_SPAWNING
    brain_owned: bool = False
    orchestrator_owned: bool = True
    retry_count: int = 0
    max_retries: int = DEFAULT_MAX_RETRIES
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + DEFAULT_LEASE_TTL_SECONDS)
    result: Any = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkLease:
        cleaned = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**cleaned)

    def is_expired(self, now: float | None = None) -> bool:
        current = now if now is not None else time.time()
        return current > self.expires_at


def _registry_dir() -> Path:
    if REGISTRY_DIR is not None:
        return Path(REGISTRY_DIR)
    return Path(BROKER_HOME) / "work-registry"


def _ensure_dir() -> Path:
    d = _registry_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _lease_file_path(work_key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", work_key)[:128]
    return _ensure_dir() / f"{safe}.json"


def _lease_lock_path(work_key: str) -> Path:
    return _lease_file_path(work_key).with_suffix(".lock")


def _read_lease_file(work_key: str) -> WorkLease | None:
    path = _lease_file_path(work_key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return WorkLease.from_dict(data)
    except Exception:
        return None


def _write_lease_file(lease: WorkLease) -> None:
    path = _lease_file_path(lease.work_key)
    atomic_io.atomic_write_text(path, json.dumps(lease.to_dict(), indent=2))


def _update_lease_atomically(
    work_key: str,
    update_fn: Callable[[WorkLease | None], tuple[str, WorkLease | None, str]],
    timeout: float = 2.0,
) -> tuple[str, WorkLease | None, str]:
    """Execute atomic transaction on a work lease using cross-process file lock."""
    if not work_key:
        return ACTION_SPAWN_SUPPRESSED_DUPLICATE, None, "missing_work_key"
    try:
        with atomic_io.FileLock(_lease_lock_path(work_key), timeout=timeout, stale_seconds=15.0):
            current = _read_lease_file(work_key)
            decision, updated, reason = update_fn(current)
            if updated is not None:
                updated.updated_at = time.time()
                _write_lease_file(updated)
            return decision, updated, reason
    except Exception as exc:
        return ACTION_SPAWN_ALLOWED, None, f"lock_error_fail_open: {exc}"


def request_work_lease(
    parent_session: str,
    task_scope: str = "",
    target: str = "",
    intent: str = "",
    evidence_domain: str = "code",
    explicit_independence_role: str = "",
    lane: str = "explore",
    agent_id: str = "",
    agent_type: str = "",
    provider: str = "",
    model: str = "",
    brain_owned: bool = False,
    orchestrator_owned: bool = True,
    ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    **kwargs: Any,
) -> tuple[str, WorkLease | None, str]:
    """Request a lease for exploration or delegated work.

    Returns:
        (decision, lease, reason)
        where decision is one of:
        - ACTION_SPAWN_ALLOWED: proceed to spawn subagent
        - ACTION_SPAWN_SUPPRESSED_DUPLICATE: duplicate active exploration in-flight
        - ACTION_REUSE_COMPLETED: existing completed receipt available
        - ACTION_RETRY_BUDGET_EXHAUSTED: retry attempts exceeded max_retries
        - ACTION_OWNERSHIP_CONFLICT: conflicting ownership boundaries
    """
    work_key = compute_work_key(
        parent_session=parent_session,
        task_scope=task_scope,
        target=target,
        intent=intent,
        evidence_domain=evidence_domain,
        explicit_independence_role=explicit_independence_role,
        lane=lane,
        **kwargs,
    )

    def _eval(current: WorkLease | None) -> tuple[str, WorkLease | None, str]:
        now = time.time()
        if current is None:
            new_lease = WorkLease(
                work_key=work_key,
                parent_session=parent_session,
                task_scope=task_scope,
                target=target,
                intent=intent,
                evidence_domain=evidence_domain,
                explicit_independence_role=explicit_independence_role,
                lane=lane,
                agent_id=agent_id,
                agent_type=agent_type,
                provider=provider,
                model=model,
                state=STATE_SPAWNING,
                brain_owned=brain_owned,
                orchestrator_owned=orchestrator_owned,
                retry_count=0,
                max_retries=max_retries,
                created_at=now,
                updated_at=now,
                expires_at=now + ttl_seconds,
            )
            return ACTION_SPAWN_ALLOWED, new_lease, "New lease granted"

        # Check ownership integrity
        if (
            current.state in (STATE_SPAWNING, STATE_ACTIVE)
            and current.brain_owned != brain_owned
            and current.orchestrator_owned != orchestrator_owned
        ):
            return (
                ACTION_OWNERSHIP_CONFLICT,
                current,
                f"Ownership conflict: existing lease owned by "
                f"{'brain' if current.brain_owned else 'orchestrator'}",
            )

        # Handle ACTIVE / SPAWNING states
        if current.state in (STATE_SPAWNING, STATE_ACTIVE):
            if current.is_expired(now):
                current.state = STATE_TIMED_OUT
                current.error = f"Lease timed out after {ttl_seconds}s"
                # Fall through to retry check below
            else:
                return (
                    ACTION_SPAWN_SUPPRESSED_DUPLICATE,
                    current,
                    f"Duplicate active work in-flight (state={current.state}, "
                    f"agent_id={current.agent_id or 'pending'})",
                )

        # Handle COMPLETED state
        if current.state == STATE_COMPLETED:
            return (
                ACTION_REUSE_COMPLETED,
                current,
                "Completed receipt available for reuse",
            )

        # Handle FAILED / TIMED_OUT states (retry evaluation)
        if current.state in (STATE_FAILED, STATE_TIMED_OUT):
            if current.retry_count < current.max_retries:
                current.retry_count += 1
                current.state = STATE_SPAWNING
                current.agent_id = ""
                current.error = None
                current.expires_at = now + ttl_seconds
                return (
                    ACTION_SPAWN_ALLOWED,
                    current,
                    f"Retry attempt {current.retry_count}/{current.max_retries} granted",
                )
            return (
                ACTION_RETRY_BUDGET_EXHAUSTED,
                current,
                f"Retry budget exhausted ({current.retry_count}/{current.max_retries})",
            )

        # Handle CANCELLED state
        if current.state == STATE_CANCELLED:
            current.state = STATE_SPAWNING
            current.agent_id = ""
            current.error = None
            current.expires_at = now + ttl_seconds
            return ACTION_SPAWN_ALLOWED, current, "Restarted cancelled lease"

        return ACTION_SPAWN_ALLOWED, current, "Default fallback allow"

    return _update_lease_atomically(work_key, _eval)


def activate_lease(
    work_key: str,
    agent_id: str,
    ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
) -> WorkLease | None:
    """Transition a SPAWNING lease to ACTIVE once the subagent actually starts."""
    def _update(current: WorkLease | None) -> tuple[str, WorkLease | None, str]:
        if current is None:
            return "not_found", None, "not_found"
        current.state = STATE_ACTIVE
        current.agent_id = str(agent_id or "").strip()
        current.expires_at = time.time() + ttl_seconds
        return "ok", current, "activated"

    _, updated, _ = _update_lease_atomically(work_key, _update)
    return updated


def heartbeat_lease(
    work_key: str,
    ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
) -> WorkLease | None:
    """Refresh TTL for an active work lease."""
    def _update(current: WorkLease | None) -> tuple[str, WorkLease | None, str]:
        if current is None:
            return "not_found", None, "not_found"
        if current.state in (STATE_SPAWNING, STATE_ACTIVE):
            current.expires_at = time.time() + ttl_seconds
        return "ok", current, "heartbeat"

    _, updated, _ = _update_lease_atomically(work_key, _update)
    return updated


def complete_lease(
    work_key: str,
    result: Any = None,
) -> WorkLease | None:
    """Transition lease to COMPLETED and attach result payload."""
    def _update(current: WorkLease | None) -> tuple[str, WorkLease | None, str]:
        if current is None:
            return "not_found", None, "not_found"
        current.state = STATE_COMPLETED
        current.result = result
        current.error = None
        return "ok", current, "completed"

    _, updated, _ = _update_lease_atomically(work_key, _update)
    return updated


def fail_lease(
    work_key: str,
    error: str = "",
) -> WorkLease | None:
    """Transition lease to FAILED and record error message."""
    def _update(current: WorkLease | None) -> tuple[str, WorkLease | None, str]:
        if current is None:
            return "not_found", None, "not_found"
        current.state = STATE_FAILED
        current.error = str(error or "unknown failure")
        return "ok", current, "failed"

    _, updated, _ = _update_lease_atomically(work_key, _update)
    return updated


def cancel_lease(
    work_key: str,
    reason: str = "",
) -> WorkLease | None:
    """Transition lease to CANCELLED."""
    def _update(current: WorkLease | None) -> tuple[str, WorkLease | None, str]:
        if current is None:
            return "not_found", None, "not_found"
        current.state = STATE_CANCELLED
        current.error = str(reason or "cancelled")
        return "ok", current, "cancelled"

    _, updated, _ = _update_lease_atomically(work_key, _update)
    return updated


def get_lease(work_key: str) -> WorkLease | None:
    """Read a work lease without modifying it."""
    return _read_lease_file(work_key)


def list_session_leases(parent_session: str) -> list[WorkLease]:
    """Enumerate all leases belonging to a parent session."""
    reg_dir = _registry_dir()
    if not reg_dir.exists():
        return []
    leases = []
    p_sess = str(parent_session or "").strip()
    for path in reg_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            lease = WorkLease.from_dict(data)
            if not p_sess or lease.parent_session == p_sess:
                leases.append(lease)
        except Exception:
            continue
    return leases


def reconcile_leases(
    parent_session: str,
    active_agent_ids: set[str] | list[str],
) -> list[WorkLease]:
    """Reconcile active leases against the set of confirmed active host agents.

    Used during reconnects, stream dropouts, or turn recovery.
    """
    confirmed = {str(a).strip() for a in active_agent_ids if str(a).strip()}
    reconciled = []
    leases = list_session_leases(parent_session)
    now = time.time()
    for lease in leases:
        if lease.state == STATE_ACTIVE:
            if lease.agent_id and lease.agent_id not in confirmed and lease.is_expired(now):
                fail_lease(lease.work_key, error="Agent disappeared during stream dropout")
                lease.state = STATE_FAILED
            reconciled.append(lease)
        elif lease.state == STATE_SPAWNING and lease.is_expired(now):
            fail_lease(lease.work_key, error="Spawning timed out before agent start")
            lease.state = STATE_TIMED_OUT
            reconciled.append(lease)
    return reconciled


def clear_session_leases(parent_session: str) -> int:
    """Clean up registry entries for a finished parent session."""
    p_sess = str(parent_session or "").strip()
    reg_dir = _registry_dir()
    if not p_sess or not reg_dir.exists():
        return 0
    removed = 0
    for path in reg_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("parent_session") == p_sess:
                path.unlink(missing_ok=True)
                lock_file = path.with_suffix(".lock")
                if lock_file.exists():
                    lock_file.unlink(missing_ok=True)
                removed += 1
        except Exception:
            continue
    return removed
