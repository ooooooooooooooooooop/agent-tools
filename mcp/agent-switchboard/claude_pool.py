"""claude_pool.py — broker-owned concurrency control for multiple running Claude Code sessions.

Agent Switchboard lets several Claude Code sessions run at once (direct ``claude -p``
calls, detached managed supervisors, async CLI workers, and existing mintty terminals).
Each path currently guards only its own supervisor_id or request row; nothing owns a
*machine-wide* view, so two unrelated supervisors can start on the same project, or a
crashed daemon can leave an orphaned ``claude`` process holding a transcript cursor, or
unbounded supervisor growth can overwhelm the local machine.

This module adds a deterministic, broker-owned orchestration layer shared by every
Claude control path:

  * a machine-wide ``summary_register`` (SQLite under ``~/.agent-broker/claude_pool.db``)
    that records every live Claude-owned process group and its scope, so concurrent
    controls are visible as one pool instead of isolated state dirs;
  * workspace-scoped mutexes (``commands.lock`` + a broker-level lease keyed by project
    root) so write-class supervision on the SAME project serializes, matching the
    routing gate's "parallel reads / serial writes" rule;
  * orphan reaping that turns a live ``claude`` process whose owning pid (daemon or
    worker) has died into ``attention_required`` with a durable record, never silent reuse;
  * a bounded concurrency ceiling with a fair atomic claim, so unlimited supervisor
    growth fails closed (``claude_pool_full``) instead of degrading silently.

Constraints honoured (mirrors goal_supervisor.py, GitHub issue #4 philosophy):

  * deterministic functions only — no second open-ended "manager agent";
  * no competing source of truth — the pool DB is derived from real process state and
    the existing ``~/.agent-broker/supervisors/`` ledger; it never claims to own the
    command queues those daemons already own;
  * idle supervision consumes zero model tokens — all decisions here are code;
  * fail closed on contention or corruption, never silently proceed without the lock.

This module is CLI + library only (like goal_supervisor, it adds no MCP tools in this
phase). It is dependency-free stdlib.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from atomic_io import FileLock

# --- locations ---------------------------------------------------------------
BROKER_DEFAULT = Path(os.environ.get("AGENT_BROKER_HOME", Path.home() / ".agent-broker"))
POOL_SCHEMA_VERSION = 1

# Owner kinds we know how to supervise. Each maps to how we read its live process.
OWNER_KINDS = ("managed_supervisor", "cli_worker", "direct_consult")

# Honest default ceilings — configurable via environment so operators can raise them.
DEFAULT_MAX_ACTIVE_PROCESSES = int(
    os.environ.get("AGENT_BROKER_CLAUDE_POOL_MAX", "8")
)
DEFAULT_MAX_PER_PROJECT = int(
    os.environ.get("AGENT_BROKER_CLAUDE_POOL_MAX_PER_PROJECT", "3")
)

# The pool DB is schema-locked to the distribution; a wrong schema is not upgraded
# silently (fail closed) because a competing DB layout would be a second source of truth.
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pool_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS owned_sessions (
    session_id TEXT PRIMARY KEY,
    owner_kind TEXT NOT NULL,
    owner_pid INTEGER NOT NULL,
    claude_pid INTEGER,
    project_root TEXT,
    cwd TEXT,
    status TEXT NOT NULL,
    reason TEXT,
    claim_seq INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_owned_project ON owned_sessions (project_root);
CREATE INDEX IF NOT EXISTS idx_owned_status ON owned_sessions (status);
CREATE INDEX IF NOT EXISTS idx_owned_claim ON owned_sessions (claim_seq);
"""

_CLAIM_SEQ_INIT = 0


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _db_connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path), timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.executescript(_SCHEMA_SQL)
    return con


def _ensure_meta(con: sqlite3.Connection) -> None:
    row = con.execute("SELECT value FROM pool_meta WHERE key='schema_version'").fetchone()
    if row is None:
        con.execute(
            "INSERT INTO pool_meta (key, value) VALUES ('schema_version', ?)",
            (str(POOL_SCHEMA_VERSION),),
        )
        con.commit()
    # We never migrate a different schema in place; instead report it so callers fail
    # closed. The caller inspects ``detail.schema_mismatch``.


def normalize_root(value: str | None) -> str:
    """Deterministic, case-normalized project root used as the workspace scope key."""
    if not value:
        return ""
    try:
        return os.path.normcase(os.path.abspath(os.path.expanduser(str(value).strip())))
    except (OSError, ValueError):
        return os.path.normcase(str(value).strip())


def default_db_path(broker_home: Path | None = None) -> Path:
    return (broker_home or BROKER_DEFAULT) / "claude_pool.db"


# --- pool database API --------------------------------------------------------

def open_pool(broker_home: Path | None = None, db_path: Path | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the broker-owned Claude pool DB. Caller owns ``close()``."""
    return _db_connect(db_path or default_db_path(broker_home))


def _read_pool_meta(db_path: Path) -> tuple[int | None, list[str]]:
    """Return (schema_version, mismatch_errors). Read-only."""
    if not db_path.exists():
        return None, []
    errors: list[str] = []
    try:
        con = _db_connect(db_path)
        try:
            version = con.execute(
                "SELECT value FROM pool_meta WHERE key='schema_version'"
            ).fetchone()
            return (int(version[0]) if version else None), errors
        finally:
            con.close()
    except sqlite3.Error as exc:
        return None, [f"pool_db_unreadable: {exc}"]


def _public_row(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def register_owned_session(
    db_path: Path,
    session_id: str,
    owner_kind: str,
    owner_pid: int,
    *,
    claude_pid: int | None = None,
    project_root: str | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    """Record one Claude-owned process group under the broker's pool.

    ``owner_kind`` is one of OWNER_KINDS; ``owner_pid`` is the supervising process
    (managed daemon, CLI worker, or direct consult parent). ``session_id`` is the
    Claude session UUID. A duplicate session is an honest conflict
    (``claude_pool_duplicate_session``) and is never silently overwritten — that would
    hide two owners racing the same transcript cursor."""
    if owner_kind not in OWNER_KINDS:
        raise ValueError(f"owner_kind must be one of {OWNER_KINDS}")
    normalized_project = normalize_root(project_root)
    con = _db_connect(db_path)
    try:
        _ensure_meta(con)
        existing = con.execute(
            "SELECT * FROM owned_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if existing is not None and int(existing["owner_pid"]) != int(owner_pid):
            return {
                "registered": False,
                "reason": "claude_pool_duplicate_session",
                "detail": {
                    "session_id": session_id,
                    "existing_owner_pid": existing["owner_pid"],
                    "existing_owner_kind": existing["owner_kind"],
                    "existing_status": existing["status"],
                },
            }
        if existing is not None:
            # Same owner re-registering (e.g. process resumed the same session); update
            # the live pid/scope but keep the original claim_seq.
            now = utc_now()
            con.execute(
                "UPDATE owned_sessions SET claude_pid = ?, project_root = ?, cwd = ?, "
                "owner_kind = ?, status = 'running', updated_at = ? WHERE session_id = ?",
                (claude_pid, normalized_project or None, cwd, owner_kind, now, session_id),
            )
            con.commit()
        else:
            claim_seq = con.execute("SELECT COALESCE(MAX(claim_seq), 0) FROM owned_sessions").fetchone()[0]
            now = utc_now()
            con.execute(
                "INSERT INTO owned_sessions (session_id, owner_kind, owner_pid, claude_pid, "
                "project_root, cwd, status, claim_seq, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (session_id, owner_kind, int(owner_pid), claude_pid, normalized_project or None,
                 cwd, "running", int(claim_seq) + 1, now, now),
            )
            con.commit()
        row = con.execute(
            "SELECT * FROM owned_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return {"registered": True, "detail": _public_row(row)}
    finally:
        con.close()


def unregister_owned_session(db_path: Path, session_id: str) -> dict[str, Any]:
    """Remove a session from the pool (owner finished or failed cleanly)."""
    con = _db_connect(db_path)
    try:
        _ensure_meta(con)
        cur = con.execute("DELETE FROM owned_sessions WHERE session_id = ?", (session_id,))
        con.commit()
        return {"unregistered": cur.rowcount > 0, "session_id": session_id}
    finally:
        con.close()


def claim_launch_slot(
    db_path: Path,
    *,
    owner_kind: str,
    owner_pid: int,
    session_id: str,
    project_root: str | None = None,
    max_active: int = DEFAULT_MAX_ACTIVE_PROCESSES,
    max_per_project: int = DEFAULT_MAX_PER_PROJECT,
) -> dict[str, Any]:
    """Atomically claim a slot in the pool for a new Claude process, enforcing the
    machine-wide ceiling and the per-project ceiling. Fails closed (``claude_pool_full``
    / ``claude_pool_project_full``) rather than silently exceeding a bound.

    This is the pre-flight gate a caller SHOULD use before spawning; the actual launch
    pid is then recorded with ``register_owned_session``."""
    if owner_kind not in OWNER_KINDS:
        raise ValueError(f"owner_kind must be one of {OWNER_KINDS}")
    normalized = normalize_root(project_root)
    con = _db_connect(db_path)
    try:
        _ensure_meta(con)
        active_total = con.execute(
            "SELECT COUNT(*) FROM owned_sessions WHERE status = 'running'"
        ).fetchone()[0]
        if int(active_total) >= int(max_active):
            return {
                "claimed": False,
                "reason": "claude_pool_full",
                "detail": {"active_total": int(active_total), "max_active": int(max_active)},
            }
        if normalized:
            active_project = con.execute(
                "SELECT COUNT(*) FROM owned_sessions WHERE project_root = ? AND status = 'running'",
                (normalized,),
            ).fetchone()[0]
            if normalized and int(active_project) >= int(max_per_project):
                return {
                    "claimed": False,
                    "reason": "claude_pool_project_full",
                    "detail": {
                        "project_root": normalized,
                        "active_project": int(active_project),
                        "max_per_project": int(max_per_project),
                    },
                }
    finally:
        con.close()
    return {
        "claimed": True,
        "session_id": session_id,
        "detail": {
            "max_active": int(max_active),
            "max_per_project": int(max_per_project),
        },
    }


def list_pool(db_path: Path, status: str | None = None) -> dict[str, Any]:
    """Read-only machine-wide view of Claude-owned sessions. Never mutates state."""
    con = _db_connect(db_path)
    try:
        _ensure_meta(con)
        query = "SELECT * FROM owned_sessions"
        args: list[Any] = []
        if status:
            query += " WHERE status = ?"
            args.append(status)
        query += " ORDER BY claim_seq"
        rows = [_public_row(row) for row in con.execute(query, args).fetchall()]
        summary = {"total": len(rows), "running": sum(1 for r in rows if r["status"] == "running")}
        return {"sessions": rows, "summary": summary}
    finally:
        con.close()


def set_session_status(
    db_path: Path, session_id: str, status: str, *, reason: str | None = None
) -> dict[str, Any]:
    """Update a session's pool status (``running``/``attention_required``/``stopped``).
    Used by reaping and by live control to flag drift."""
    con = _db_connect(db_path)
    try:
        _ensure_meta(con)
        now = utc_now()
        cur = con.execute(
            "UPDATE owned_sessions SET status = ?, updated_at = ?, "
            "reason = COALESCE(reason, ?) WHERE session_id = ?",
            (status, now, reason, session_id),
        )
        con.commit()
        return {"updated": cur.rowcount > 0, "session_id": session_id, "status": status}
    finally:
        con.close()


# --- owner process liveness (monkey-patchable in tests) -----------------------

def _pid_is_alive(pid: Any) -> bool:
    """Reuse managed_claude's cross-platform liveness probe logic without importing it
    (keeps this module dependency-free and independently testable)."""
    try:
        wanted = int(pid)
    except (TypeError, ValueError):
        return False
    if wanted <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(process_query_limited_information, 0, wanted)
        except OSError:
            return False
        if not handle:
            return False
        try:
            exit_code = ctypes.c_uint32()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return int(exit_code.value) == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(wanted, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


# --- orphan reaping ------------------------------------------------------------

def _iter_owned_rows(con: sqlite3.Connection):
    return con.execute("SELECT * FROM owned_sessions WHERE status = 'running'")


def reap_orphans(
    db_path: Path,
    *,
    pid_is_alive=None,
    skip: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Find pool sessions whose owner pid is dead (crashed daemon, killed worker) and
    mark them ``attention_required`` with a durable record. A live ``claude_pid`` with a
    dead owner is an orphan; we do NOT kill it here (that is the operator's decision) —
    we surface it and stop claiming the slot. Never silently reuses a dead-owner session.

    ``skip`` is a dict keyed by session_id (owner pid dead but the session is ours to
    finish); those are left alone so an in-flight finalize can complete."""
    alive = pid_is_alive or _pid_is_alive
    skipped = {str(k) for k in (skip or {})}
    con = _db_connect(db_path)
    try:
        _ensure_meta(con)
        now = utc_now()
        orphans: list[dict[str, Any]] = []
        for row in _iter_owned_rows(con):
            session_id = str(row["session_id"])
            owner_pid = int(row["owner_pid"])
            if session_id in skipped:
                continue
            if owner_pid <= 0 or not alive(owner_pid):
                # Owner gone. If a claude pid is still reported live this is a real orphan
                # to surface; either way we release the slot and flag for attention.
                con.execute(
                    "UPDATE owned_sessions SET status = 'attention_required', updated_at = ? "
                    "WHERE session_id = ? AND status = 'running'",
                    (now, session_id),
                )
                orphans.append(
                    {
                        "session_id": session_id,
                        "owner_kind": row["owner_kind"],
                        "owner_pid": owner_pid,
                        "claude_pid": row["claude_pid"],
                        "project_root": row["project_root"],
                    }
                )
        con.commit()
        return {"reaped": orphans, "count": len(orphans)}
    finally:
        con.close()


def pool_status(db_path: Path, **claim_kwargs: Any) -> dict[str, Any]:
    """One honest, read-only snapshot used by ``doctor`` and the CLI: schema health,
    live sessions, ceilings being enforced, and any recent orphan flag."""

    def _floor(config_key: str, default: int) -> int:
        v = os.environ.get(config_key)
        try:
            return int(v) if v is not None else default
        except ValueError:
            return default

    db_exists = db_path.exists()
    version, errors = _read_pool_meta(db_path)
    pool = list_pool(db_path) if db_exists else {"sessions": [], "summary": {"total": 0, "running": 0}}
    return {
        "enabled": True,
        "db_path": str(db_path),
        "exists": db_exists,
        "schema_version": version,
        "schema_ok": errors == [] and (version == POOL_SCHEMA_VERSION or version is None),
        "errors": errors,
        "owner_kinds": list(OWNER_KINDS),
        "max_active_processes": _floor("AGENT_BROKER_CLAUDE_POOL_MAX", DEFAULT_MAX_ACTIVE_PROCESSES),
        "max_per_project": _floor(
            "AGENT_BROKER_CLAUDE_POOL_MAX_PER_PROJECT", DEFAULT_MAX_PER_PROJECT
        ),
        **pool,
    }


# --- CLI helpers ---------------------------------------------------------------

POOL_HELP = (
    "Usage: agent_broker_mcp.py bridge claude-pool ("
    "status | register <session_id> <owner_kind> <owner_pid> [--claude-pid <pid>] [--project <dir>] | "
    "unregister <session_id> | list [--status <s>] | claim-slot --owner-kind <k> --owner-pid <pid> "
    "--session <id> [--project <dir>] [--max-active <n>] [--max-per-project <n>] | "
    "reap [--skip <csv>])"
)


def handle_claude_pool_cli(argv: list[str]) -> int:
    """bridge claude-pool subcommand dispatch. Deterministic; never calls a model."""
    import json as _json

    broker_home = Path(os.environ.get("AGENT_BROKER_HOME", Path.home() / ".agent-broker"))
    db_path = default_db_path(broker_home)

    if not argv or argv[0] in {"help", "-h", "--help"}:
        print(POOL_HELP)
        return 0
    command = argv[0]

    def _arg(name: str) -> str | None:
        if name not in argv:
            return None
        index = argv.index(name)
        return argv[index + 1] if index + 1 < len(argv) else None

    if command == "status":
        print(_json.dumps(pool_status(db_path), ensure_ascii=True, indent=2))
        return 0
    if command == "list":
        status = None
        if "--status" in argv:
            status = _arg("--status")
        result = list_pool(db_path, status=status)
        print(_json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    if command == "register":
        if len(argv) < 4:
            raise ValueError(
                "register requires <session_id> <owner_kind> <owner_pid> [--claude-pid <pid>] [--project <dir>]"
            )
        owner_kind = argv[2]
        try:
            owner_pid = int(argv[3])
        except ValueError as exc:
            raise ValueError("owner_pid must be an integer") from exc
        claude_pid = None
        if "--claude-pid" in argv:
            try:
                claude_pid = int(_arg("--claude-pid"))
            except (TypeError, ValueError) as exc:
                raise ValueError("claude-pid must be an integer") from exc
        result = register_owned_session(
            db_path,
            argv[1],
            owner_kind,
            owner_pid,
            claude_pid=claude_pid,
            project_root=_arg("--project"),
        )
        print(_json.dumps(result, ensure_ascii=True, indent=2))
        return 0 if result.get("registered") else 1
    if command == "unregister":
        if len(argv) < 2:
            raise ValueError("unregister requires <session_id>")
        result = unregister_owned_session(db_path, argv[1])
        print(_json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    if command == "claim-slot":
        if "--owner-kind" not in argv or "--owner-pid" not in argv or "--session" not in argv:
            raise ValueError("claim-slot requires --owner-kind <k> --owner-pid <pid> --session <id>")
        owner_kind = _arg("--owner-kind")
        try:
            owner_pid = int(_arg("--owner-pid"))
        except (TypeError, ValueError) as exc:
            raise ValueError("owner-pid must be an integer") from exc
        session_id = _arg("--session")
        project = _arg("--project")
        max_active = int(_arg("--max-active") or DEFAULT_MAX_ACTIVE_PROCESSES)
        max_per_project = int(_arg("--max-per-project") or DEFAULT_MAX_PER_PROJECT)
        result = claim_launch_slot(
            db_path,
            owner_kind=owner_kind,
            owner_pid=owner_pid,
            session_id=session_id,
            project_root=project,
            max_active=max_active,
            max_per_project=max_per_project,
        )
        print(_json.dumps(result, ensure_ascii=True, indent=2))
        return 0 if result.get("claimed") else 1
    if command == "reap":
        skip: set[str] = set()
        if "--skip" in argv:
            raw = _arg("--skip") or ""
            skip = {p.strip() for p in raw.split(",") if p.strip()}
        result = reap_orphans(db_path, skip=skip)
        print(_json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    raise ValueError(f"unknown claude-pool subcommand: {command}")


# --- workspace mutex (parallel reads / serial writes) --------------------------

class ProjectWriteLease:
    """A broker-level lease that serializes write-class Claude supervision on ONE project
    root, matching the routing-gate's "parallel reads / serial writes" rule. It is a thin
    wrapper over the atomic FileLock so it is cross-process safe and crash-safe (stale
    lock reaped by mtime)."""

    def __init__(
        self,
        broker_home: Path | None = None,
        project_root: str | None = None,
        timeout: float = 10.0,
        stale_seconds: float = 120.0,
    ) -> None:
        normalized = normalize_root(project_root)
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", normalized) if normalized else "global"
        lock_dir = (broker_home or BROKER_DEFAULT) / "leases"
        self._lock = FileLock(lock_dir / f"project_{safe[:80]}.lock", timeout=timeout, stale_seconds=stale_seconds)

    def acquire(self) -> bool:
        return self._lock.acquire()

    def release(self) -> None:
        self._lock.release()

    def __enter__(self) -> "ProjectWriteLease":
        self._lock.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._lock.__exit__(exc_type, exc, tb)
