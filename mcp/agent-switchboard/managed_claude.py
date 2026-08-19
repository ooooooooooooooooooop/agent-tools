#!/usr/bin/env python3
"""Detached, windowless Claude Code supervision for Agent Switchboard.

The MCP server is not the owner of this process.  It writes durable commands
under ``~/.agent-broker/supervisors`` and a detached daemon owns Claude's
stream-json stdin/stdout.  This keeps desktop focus and the clipboard out of
the control plane and lets the MCP host restart without losing supervisor
state.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from atomic_io import FileLock, atomic_write_text
from hook_event_server import ensure_hook_event_server


WINDOWS_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
WINDOWS_NEW_PROCESS_GROUP = (
    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
)
WINDOWS_DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0) if os.name == "nt" else 0

SUPERVISOR_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")
REQUEST_MARKER_RE = re.compile(r"\[Switchboard managed request ([0-9a-fA-F-]{36})\]")
ALLOWED_PERMISSION_MODES = {
    "plan",
    "manual",
    "acceptEdits",
    "auto",
    "dontAsk",
    "bypassPermissions",
}
ALLOWED_DECISION_MODES = {"record_only", "codex"}
ALLOWED_DECISIONS = {"ACK", "SEND", "INTERRUPT", "ASK_USER", "ACCEPT"}
ALLOWED_INTERRUPT_MODES = {"native", "hard"}
MAX_PROMPT_CHARS = 200_000
POLL_SECONDS = 0.20
STREAM_STARTUP_TIMEOUT_SECONDS = 60.0
CONTROL_REQUEST_TIMEOUT_SECONDS = 10.0
INTERRUPT_RESULT_TIMEOUT_SECONDS = 60.0


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def compact_text(value: Any, limit: int = 600) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 16)].rstrip() + " ... [truncated]"


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"managed_claude_state_invalid: {path}") from exc


def _write_json(path: Path, payload: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def validate_supervisor_id(value: Any) -> str:
    supervisor_id = str(value or "").strip()
    if not SUPERVISOR_ID_RE.fullmatch(supervisor_id):
        raise ValueError(
            "supervisor_id must contain 1-100 letters, digits, dot, underscore, colon, or hyphen"
        )
    return supervisor_id


def validate_permission_mode(value: Any) -> str:
    raw = str(value or "acceptEdits").strip()
    aliases = {
        "default": "manual",
        "acceptedits": "acceptEdits",
        "accept_edits": "acceptEdits",
        "accept-edits": "acceptEdits",
        "bypasspermissions": "bypassPermissions",
        "bypass_permissions": "bypassPermissions",
        "bypass-permissions": "bypassPermissions",
    }
    normalized = aliases.get(raw.lower(), raw)
    if normalized not in ALLOWED_PERMISSION_MODES:
        raise ValueError(
            "permission_mode must be plan, manual, acceptEdits, auto, dontAsk, or bypassPermissions"
        )
    return normalized


def validate_decision_mode(value: Any) -> str:
    mode = str(value or "record_only").strip().lower()
    if mode not in ALLOWED_DECISION_MODES:
        raise ValueError("decision_mode must be record_only or codex")
    return mode


def validate_interrupt_mode(value: Any) -> str:
    mode = str(value or "native").strip().lower()
    if mode not in ALLOWED_INTERRUPT_MODES:
        raise ValueError("interrupt_mode must be native or hard")
    return mode


def supervisor_dir(broker_home: Path, supervisor_id: str) -> Path:
    return Path(broker_home) / "supervisors" / validate_supervisor_id(supervisor_id)


def pid_is_alive(pid: Any) -> bool:
    try:
        wanted = int(pid)
    except (TypeError, ValueError):
        return False
    if wanted <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        open_process.restype = ctypes.c_void_p
        get_exit_code = kernel32.GetExitCodeProcess
        get_exit_code.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        get_exit_code.restype = ctypes.c_int
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        handle = open_process(process_query_limited_information, 0, wanted)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_uint32()
            if not get_exit_code(handle, ctypes.byref(exit_code)):
                return False
            return int(exit_code.value) == still_active
        finally:
            close_handle(handle)
    try:
        os.kill(wanted, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _daemon_command(state_dir: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "managed-claude-daemon", "--state-dir", str(state_dir)]
    return [sys.executable, str(Path(__file__).resolve()), "daemon", "--state-dir", str(state_dir)]


def _hook_event_server_command(broker_dir: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "hook-event-server", "--broker-dir", str(broker_dir)]
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "hook-event-server",
        "--broker-dir",
        str(broker_dir),
    ]


def _detached_popen(command: list[str], cwd: str) -> subprocess.Popen[Any]:
    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "env": os.environ.copy(),
    }
    if os.name == "nt":
        kwargs["creationflags"] = WINDOWS_DETACHED_PROCESS | WINDOWS_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


def _create_supervisor_unlocked(
    broker_home: Path,
    project_root: str,
    supervisor_id: str,
    objective: str,
    permission_mode: str = "acceptEdits",
    decision_mode: str = "record_only",
    policy: str | None = None,
    claude_path: str | None = None,
    codex_path: str | None = None,
    codex_model: str | None = None,
    codex_effort: str | None = None,
    max_autonomous_actions: int = 4,
    stall_timeout_seconds: int = 900,
    startup_timeout_seconds: float = 8.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create or restart one detached supervisor without sending a model prompt."""
    sid = validate_supervisor_id(supervisor_id)
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project_root is not a directory: {root}")
    clean_objective = str(objective or "").strip()
    if not clean_objective:
        raise ValueError("objective is required")
    if len(clean_objective) > 4_000:
        raise ValueError("objective exceeds 4000 characters")
    clean_policy = str(policy or "").strip()
    if len(clean_policy) > 8_000:
        raise ValueError("policy exceeds 8000 characters")
    mode = validate_permission_mode(permission_mode)
    decision = validate_decision_mode(decision_mode)
    max_actions = int(max_autonomous_actions)
    if max_actions < 0 or max_actions > 50:
        raise ValueError("max_autonomous_actions must be between 0 and 50")
    stall_seconds = int(stall_timeout_seconds)
    if stall_seconds < 30 or stall_seconds > 86_400:
        raise ValueError("stall_timeout_seconds must be between 30 and 86400")
    state_dir = supervisor_dir(Path(broker_home), sid)
    state_path = state_dir / "state.json"
    old_state = _read_json(state_path, {}) or {}
    if pid_is_alive(old_state.get("daemon_pid")):
        raise RuntimeError(f"managed_claude_already_running: {sid}")
    stale_lock = state_dir / "daemon.lock"
    if stale_lock.exists():
        try:
            lock_owner = int(stale_lock.read_text(encoding="utf-8").strip())
        except (OSError, UnicodeError, ValueError):
            lock_owner = 0
        if pid_is_alive(lock_owner):
            raise RuntimeError(
                f"managed_claude_already_running: {sid} (daemon lock owner {lock_owner})"
            )
        try:
            stale_lock.unlink()
        except OSError as exc:
            raise RuntimeError(
                f"managed_claude_stale_lock_failed: cannot remove {stale_lock}"
            ) from exc
    old_config = _read_json(state_dir / "config.json", {}) or {}
    if old_config:
        old_root = os.path.normcase(str(old_config.get("project_root") or ""))
        if old_root and old_root != os.path.normcase(str(root)):
            raise RuntimeError(
                "managed_claude_scope_mismatch: an existing supervisor id is bound to another project"
            )
    resolved_claude = str(claude_path or old_config.get("claude_path") or shutil.which("claude") or "")
    if not resolved_claude:
        raise RuntimeError("managed_claude_unavailable: Claude Code CLI was not found")
    resolved_codex = str(codex_path or old_config.get("codex_path") or shutil.which("codex") or "")
    if decision == "codex" and not resolved_codex:
        raise RuntimeError("managed_claude_unavailable: Codex CLI was not found")
    session_id = str(old_config.get("session_id") or uuid.uuid4())
    config = {
        "schema_version": 1,
        "supervisor_id": sid,
        "project_root": str(root),
        "objective": clean_objective,
        "policy": clean_policy,
        "permission_mode": mode,
        "decision_mode": decision,
        "claude_path": resolved_claude,
        "codex_path": resolved_codex,
        "codex_model": str(codex_model or "").strip() or None,
        "codex_effort": str(codex_effort or "").strip() or None,
        "max_autonomous_actions": max_actions,
        "stall_timeout_seconds": stall_seconds,
        "session_id": session_id,
        "resume_existing": bool(old_config.get("has_started")),
        "has_started": bool(old_config.get("has_started")),
        "created_at": old_config.get("created_at") or utc_now(),
        "updated_at": utc_now(),
    }
    command = _daemon_command(state_dir)
    if dry_run:
        return {
            "status": "ready",
            "supervisor_id": sid,
            "project_root": str(root),
            "session_id": session_id,
            "decision_mode": decision,
            "permission_mode": mode,
            "launch_mode": "detached_stream_json",
            "command": command,
            "changes_global_settings": False,
            "uses_foreground_ui": False,
        }
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "commands").mkdir(exist_ok=True)
    (state_dir / "decisions").mkdir(exist_ok=True)
    _write_json(state_dir / "config.json", config)
    _write_json(
        state_path,
        {
            "schema_version": 1,
            "supervisor_id": sid,
            "project_root": str(root),
            "session_id": session_id,
            "status": "launching",
            "daemon_pid": None,
            "claude_pid": None,
            "decision_mode": decision,
            "pending_commands": 0,
            "decision_invocations": int(old_state.get("decision_invocations") or 0),
            "uses_foreground_ui": False,
            "updated_at": utc_now(),
        },
    )
    proc = _detached_popen(command, str(root))
    deadline = time.monotonic() + max(0.5, float(startup_timeout_seconds))
    current: dict[str, Any] = {}
    while time.monotonic() < deadline:
        current = _read_json(state_path, {}) or {}
        if current.get("status") in {"ready", "idle", "attention_required", "failed"}:
            break
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    current = _read_json(state_path, {}) or {}
    if current.get("status") == "failed":
        raise RuntimeError(
            f"managed_claude_launch_failed: {current.get('last_error') or 'daemon reported failure'}"
        )
    if not pid_is_alive(current.get("daemon_pid")) or not pid_is_alive(current.get("claude_pid")):
        raise RuntimeError("managed_claude_launch_failed: daemon or Claude process did not become ready")
    return public_status(state_dir, recent_events=3)


def create_supervisor(
    broker_home: Path,
    project_root: str,
    supervisor_id: str,
    objective: str,
    permission_mode: str = "acceptEdits",
    decision_mode: str = "record_only",
    policy: str | None = None,
    claude_path: str | None = None,
    codex_path: str | None = None,
    codex_model: str | None = None,
    codex_effort: str | None = None,
    max_autonomous_actions: int = 4,
    stall_timeout_seconds: int = 900,
    startup_timeout_seconds: float = 8.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Serialize launches so one supervisor id can never acquire two daemons."""
    sid = validate_supervisor_id(supervisor_id)
    arguments = {
        "broker_home": broker_home,
        "project_root": project_root,
        "supervisor_id": sid,
        "objective": objective,
        "permission_mode": permission_mode,
        "decision_mode": decision_mode,
        "policy": policy,
        "claude_path": claude_path,
        "codex_path": codex_path,
        "codex_model": codex_model,
        "codex_effort": codex_effort,
        "max_autonomous_actions": max_autonomous_actions,
        "stall_timeout_seconds": stall_timeout_seconds,
        "startup_timeout_seconds": startup_timeout_seconds,
        "dry_run": dry_run,
    }
    if dry_run:
        return _create_supervisor_unlocked(**arguments)
    state_dir = supervisor_dir(Path(broker_home), sid)
    state_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(state_dir / "launch.lock", timeout=10, stale_seconds=30):
        return _create_supervisor_unlocked(**arguments)


def _command_path(state_dir: Path, command_id: str) -> Path:
    return state_dir / "commands" / f"{command_id}.json"


def queue_command(
    broker_home: Path,
    supervisor_id: str,
    prompt: str,
    interrupt_current: bool = False,
    interrupt_mode: str = "native",
    confirmation_timeout_seconds: float = 0,
    origin: str = "external",
) -> dict[str, Any]:
    sid = validate_supervisor_id(supervisor_id)
    state_dir = supervisor_dir(Path(broker_home), sid)
    state = _read_json(state_dir / "state.json", {}) or {}
    if not pid_is_alive(state.get("daemon_pid")):
        raise RuntimeError(f"managed_claude_not_running: {sid}")
    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        raise ValueError("prompt is required")
    if len(clean_prompt) > MAX_PROMPT_CHARS:
        raise ValueError(f"prompt exceeds {MAX_PROMPT_CHARS} characters")
    mode = validate_interrupt_mode(interrupt_mode)
    command_id = str(uuid.uuid4())
    payload = {
        "schema_version": 1,
        "id": command_id,
        "type": "message",
        "status": "queued",
        "prompt": clean_prompt,
        "prompt_sha256": hashlib.sha256(clean_prompt.encode("utf-8")).hexdigest(),
        "interrupt_current": bool(interrupt_current),
        "interrupt_mode": mode,
        "origin": str(origin or "external"),
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    commands_dir = state_dir / "commands"
    commands_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(state_dir / "commands.lock", timeout=5):
        _write_json(_command_path(state_dir, command_id), payload)
    deadline = time.monotonic() + max(0.0, float(confirmation_timeout_seconds))
    while time.monotonic() < deadline:
        current = _read_json(_command_path(state_dir, command_id), payload) or payload
        if current.get("status") in {
            "confirmed",
            "completed",
            "failed",
            "delivery_unconfirmed_after_restart",
            "outcome_unconfirmed_after_restart",
            "interrupted",
        }:
            payload = current
            break
        time.sleep(0.05)
    current = _read_json(_command_path(state_dir, command_id), payload) or payload
    status = str(current.get("status") or "queued")
    return {
        "id": command_id,
        "status": status,
        "supervisor_id": sid,
        "session_id": state.get("session_id"),
        "confirmed_in_stream": status in {"confirmed", "completed"},
        "interrupt_current": bool(interrupt_current),
        "interrupt_mode": mode if interrupt_current else None,
        "delivery_mode": "managed_stream_json",
        "uses_foreground_ui": False,
        "prompt_sha256": payload["prompt_sha256"],
        "error": current.get("error"),
    }


def stop_supervisor(
    broker_home: Path,
    supervisor_id: str,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    sid = validate_supervisor_id(supervisor_id)
    state_dir = supervisor_dir(Path(broker_home), sid)
    state = _read_json(state_dir / "state.json", {}) or {}
    if not state:
        raise ValueError(f"managed_claude_unknown_supervisor: {sid}")
    if not pid_is_alive(state.get("daemon_pid")):
        return {**public_status(state_dir, recent_events=3), "status": "already_stopped"}
    command_id = str(uuid.uuid4())
    payload = {
        "schema_version": 1,
        "id": command_id,
        "type": "stop",
        "status": "queued",
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    with FileLock(state_dir / "commands.lock", timeout=5):
        _write_json(_command_path(state_dir, command_id), payload)
    deadline = time.monotonic() + max(0.5, float(timeout_seconds))
    while time.monotonic() < deadline:
        current = _read_json(state_dir / "state.json", {}) or {}
        if current.get("status") == "stopped":
            return public_status(state_dir, recent_events=5)
        time.sleep(0.05)
    raise RuntimeError("managed_claude_stop_timeout: daemon did not confirm stop")


def _recent_jsonl(path: Path, limit: int) -> list[dict[str, Any]]:
    if limit <= 0 or not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except OSError as exc:
        raise RuntimeError(f"managed_claude_state_unavailable: {path}") from exc
    result: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def public_status(state_dir: Path, recent_events: int = 10) -> dict[str, Any]:
    state = _read_json(Path(state_dir) / "state.json", {}) or {}
    if not state:
        raise ValueError(f"managed_claude_unknown_supervisor: {Path(state_dir).name}")
    commands = []
    for path in sorted((Path(state_dir) / "commands").glob("*.json")):
        row = _read_json(path, {}) or {}
        if row.get("type") == "message" and row.get("status") not in {"completed"}:
            commands.append(
                {
                    "id": row.get("id"),
                    "status": row.get("status"),
                    "interrupt_current": bool(row.get("interrupt_current")),
                    "interrupt_mode": row.get("interrupt_mode") if row.get("interrupt_current") else None,
                    "origin": row.get("origin"),
                    "prompt_sha256": row.get("prompt_sha256"),
                    "error": row.get("error"),
                }
            )
    allowed = {
        "schema_version",
        "supervisor_id",
        "project_root",
        "session_id",
        "status",
        "daemon_pid",
        "claude_pid",
        "claude_model",
        "capabilities",
        "decision_mode",
        "decision_invocations",
        "attention",
        "last_error",
        "last_result_summary",
        "last_activity_at",
        "uses_foreground_ui",
        "updated_at",
    }
    result = {key: value for key, value in state.items() if key in allowed}
    result["daemon_alive"] = pid_is_alive(state.get("daemon_pid"))
    result["claude_alive"] = pid_is_alive(state.get("claude_pid"))
    result["pending_commands"] = commands
    result["recent_events"] = _recent_jsonl(
        Path(state_dir) / "events.jsonl", max(0, min(int(recent_events), 50))
    )
    return result


def get_supervisor_status(
    broker_home: Path, supervisor_id: str, recent_events: int = 10
) -> dict[str, Any]:
    return public_status(
        supervisor_dir(Path(broker_home), validate_supervisor_id(supervisor_id)),
        recent_events=recent_events,
    )


def list_supervisors(broker_home: Path) -> dict[str, Any]:
    root = Path(broker_home) / "supervisors"
    supervisors: list[dict[str, Any]] = []
    if root.exists():
        for state_path in sorted(root.glob("*/state.json")):
            try:
                state = public_status(state_path.parent, recent_events=0)
            except (ValueError, RuntimeError):
                continue
            supervisors.append(state)
    return {"supervisors": supervisors}


class ManagedClaudeDaemon:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir).resolve()
        self.config_path = self.state_dir / "config.json"
        self.state_path = self.state_dir / "state.json"
        self.events_path = self.state_dir / "events.jsonl"
        self.log_path = self.state_dir / "daemon.log"
        self.config = _read_json(self.config_path, {}) or {}
        if not self.config:
            raise RuntimeError(f"managed_claude_config_missing: {self.config_path}")
        self.project_root = str(self.config["project_root"])
        self.session_id = str(self.config["session_id"])
        self.proc: subprocess.Popen[str] | None = None
        self.stop_requested = threading.Event()
        self.io_lock = threading.Lock()
        self.control_lock = threading.Lock()
        self.interrupt_lock = threading.Lock()
        self.event_lock = threading.Lock()
        self.state_lock = threading.Lock()
        recent = _recent_jsonl(self.events_path, 1)
        self.event_seq = int(recent[-1].get("seq") or 0) if recent else 0
        self.intentional_process_stop = False
        self.stdout_thread: threading.Thread | None = None
        self.stderr_thread: threading.Thread | None = None
        self.stream_initialized = threading.Event()
        self.pending_control: dict[str, dict[str, Any]] = {}
        self.native_interrupt: dict[str, Any] | None = None
        self.tool_failures = 0
        self.last_activity_monotonic = time.monotonic()
        self.stall_notified: set[str] = set()
        self._recover_ambiguous_commands()
        old_state = _read_json(self.state_path, {}) or {}
        self.state: dict[str, Any] = {
            "schema_version": 1,
            "supervisor_id": self.config["supervisor_id"],
            "project_root": self.project_root,
            "session_id": self.session_id,
            "status": "starting",
            "daemon_pid": os.getpid(),
            "claude_pid": None,
            "claude_model": None,
            "capabilities": [],
            "decision_mode": self.config["decision_mode"],
            "decision_invocations": int(old_state.get("decision_invocations") or 0),
            "attention": old_state.get("attention"),
            "last_error": None,
            "last_result_summary": old_state.get("last_result_summary"),
            "last_activity_at": old_state.get("last_activity_at"),
            "uses_foreground_ui": False,
            "updated_at": utc_now(),
        }
        self.ledger = _read_json(
            self.state_dir / "ledger.json",
            {
                "objective": self.config["objective"],
                "policy": self.config.get("policy") or "",
                "current_milestone": None,
                "last_decision": None,
                "autonomous_actions_since_external": 0,
                "updated_at": utc_now(),
            },
        )

    def _recover_ambiguous_commands(self) -> None:
        commands_dir = self.state_dir / "commands"
        commands_dir.mkdir(parents=True, exist_ok=True)
        for path in commands_dir.glob("*.json"):
            row = _read_json(path, {}) or {}
            if row.get("status") in {"submitting", "submitted", "confirmed"}:
                was_confirmed = row.get("status") == "confirmed"
                row["status"] = (
                    "outcome_unconfirmed_after_restart"
                    if was_confirmed
                    else "delivery_unconfirmed_after_restart"
                )
                row["error"] = (
                    "daemon restarted after delivery confirmation but before a terminal result; "
                    "the command was not replayed"
                    if was_confirmed
                    else "daemon restarted after the stream write boundary; command was not replayed"
                )
                row["updated_at"] = utc_now()
                _write_json(path, row)

    def _save_state(self, **changes: Any) -> None:
        with self.state_lock:
            self.state.update(changes)
            self.state["updated_at"] = utc_now()
            _write_json(self.state_path, self.state)

    def _save_ledger(self, **changes: Any) -> None:
        self.ledger.update(changes)
        self.ledger["updated_at"] = utc_now()
        _write_json(self.state_dir / "ledger.json", self.ledger)

    def _event(self, event_type: str, **fields: Any) -> dict[str, Any]:
        with self.event_lock:
            with FileLock(self.state_dir / "events.lock"):
                recent = _recent_jsonl(self.events_path, 1)
                latest_seq = int(recent[-1].get("seq") or 0) if recent else 0
                self.event_seq = max(self.event_seq, latest_seq) + 1
                payload = {
                    "seq": self.event_seq,
                    "type": event_type,
                    "created_at": utc_now(),
                    **fields,
                }
                self.events_path.parent.mkdir(parents=True, exist_ok=True)
                with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                return payload

    def _log(self, message: str) -> None:
        with self.log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{utc_now()} {message.rstrip()}\n")

    def _claude_command(self, resume: bool) -> list[str]:
        command = [
            str(self.config["claude_path"]),
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--replay-user-messages",
            "--permission-mode",
            str(self.config["permission_mode"]),
            "--no-chrome",
        ]
        if resume:
            command.extend(["--resume", self.session_id])
        else:
            command.extend(["--session-id", self.session_id])
        return command

    def _start_claude(self, resume: bool) -> None:
        self.stream_initialized.clear()
        command = self._claude_command(resume)
        environment = os.environ.copy()
        environment["AGENT_BROKER_CHILD"] = "1"
        kwargs: dict[str, Any] = {
            "cwd": self.project_root,
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "strict",
            "bufsize": 1,
            "env": environment,
        }
        if os.name == "nt":
            kwargs["creationflags"] = WINDOWS_NEW_PROCESS_GROUP | WINDOWS_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        self.proc = subprocess.Popen(command, **kwargs)
        self.config["has_started"] = True
        self.config["resume_existing"] = True
        self.config["updated_at"] = utc_now()
        _write_json(self.config_path, self.config)
        self._save_state(status="starting", claude_pid=self.proc.pid, last_error=None)
        self._event(
            "claude_process_started",
            claude_pid=self.proc.pid,
            session_id=self.session_id,
            resumed=bool(resume),
            uses_foreground_ui=False,
        )
        self.stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self.stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self.stdout_thread.start()
        self.stderr_thread.start()
        try:
            initialization = self._send_control_request(
                {"subtype": "initialize", "hooks": None},
                timeout_seconds=STREAM_STARTUP_TIMEOUT_SECONDS,
            )
        except RuntimeError as exc:
            self._save_state(
                status="failed",
                last_error=str(exc),
            )
            self._event(
                "claude_stream_init_failed",
                claude_pid=self.proc.pid if self.proc else None,
                error=str(exc),
            )
            self._terminate_claude("stream_init_failed")
            raise
        capabilities = initialization.get("capabilities")
        self._save_state(
            status="idle",
            capabilities=list(capabilities) if isinstance(capabilities, list) else [],
        )
        self._event(
            "claude_control_initialized",
            capabilities=list(capabilities) if isinstance(capabilities, list) else [],
        )
        self.stream_initialized.set()

    def _terminate_claude(self, reason: str) -> None:
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        self.intentional_process_stop = True
        self._event("claude_hard_interrupt_started", reason=reason, claude_pid=proc.pid)
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
                creationflags=WINDOWS_NO_WINDOW,
            )
            if result.returncode != 0 and proc.poll() is None:
                self.intentional_process_stop = False
                raise RuntimeError("managed_claude_interrupt_failed: taskkill did not stop the process tree")
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired as exc:
            self.intentional_process_stop = False
            raise RuntimeError("managed_claude_interrupt_failed: process did not exit") from exc
        self._event("claude_hard_interrupt_completed", reason=reason, exit_code=proc.returncode)
        self.proc = None

    def _mark_open_commands_interrupted(self, by_command_id: str) -> None:
        for path in (self.state_dir / "commands").glob("*.json"):
            row = _read_json(path, {}) or {}
            if row.get("type") != "message" or row.get("status") not in {
                "submitting",
                "submitted",
                "confirmed",
            }:
                continue
            command_id = str(row.get("id") or "")
            self._update_command(
                command_id,
                status="interrupted",
                interrupted_at=utc_now(),
                interrupted_by=by_command_id,
            )
            self._event(
                "command_interrupted",
                command_id=command_id,
                interrupted_by=by_command_id,
            )

    def _handle_control_response(self, payload: dict[str, Any]) -> None:
        response = payload.get("response")
        if not isinstance(response, dict):
            return
        request_id = str(response.get("request_id") or "")
        if not request_id:
            return
        with self.control_lock:
            pending = self.pending_control.get(request_id)
            if pending is None:
                return
            pending["response"] = response
            event = pending["event"]
        event.set()

    def _send_control_request(
        self,
        request: dict[str, Any],
        timeout_seconds: float = CONTROL_REQUEST_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        proc = self.proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            raise RuntimeError("managed_claude_control_failed: Claude stream is not writable")
        request_id = f"req_switchboard_{uuid.uuid4().hex}"
        receipt = threading.Event()
        with self.control_lock:
            self.pending_control[request_id] = {"event": receipt, "response": None}
        payload = {
            "type": "control_request",
            "request_id": request_id,
            "request": request,
        }
        try:
            with self.io_lock:
                proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                proc.stdin.flush()
        except (BrokenPipeError, OSError, UnicodeError) as exc:
            with self.control_lock:
                self.pending_control.pop(request_id, None)
            raise RuntimeError(f"managed_claude_control_write_failed: {exc}") from exc
        if not receipt.wait(max(0.1, float(timeout_seconds))):
            with self.control_lock:
                self.pending_control.pop(request_id, None)
            raise RuntimeError(
                f"managed_claude_control_timeout: {request.get('subtype')}"
            )
        with self.control_lock:
            pending = self.pending_control.pop(request_id, None)
        response = pending.get("response") if pending else None
        if not isinstance(response, dict):
            raise RuntimeError("managed_claude_control_failed: response is missing")
        if response.get("subtype") == "error":
            raise RuntimeError(
                "managed_claude_control_failed: "
                + str(response.get("error") or "unknown control error")
            )
        if response.get("subtype") != "success":
            raise RuntimeError("managed_claude_control_failed: unexpected response subtype")
        response_data = response.get("response")
        return response_data if isinstance(response_data, dict) else {}

    def _native_interrupt(self, command_id: str) -> None:
        interrupted_ids = [
            str(row.get("id") or "")
            for row in self._open_commands()
            if str(row.get("id") or "")
        ]
        if not interrupted_ids:
            self._event("claude_native_interrupt_not_needed", command_id=command_id)
            return
        result_received = threading.Event()
        context: dict[str, Any] = {
            "by_command_id": command_id,
            "interrupted_ids": interrupted_ids,
            "receipt_confirmed": False,
            "result_payload": None,
            "result_received": result_received,
        }
        with self.interrupt_lock:
            if self.native_interrupt is not None:
                raise RuntimeError("managed_claude_native_interrupt_already_pending")
            self.native_interrupt = context
        self._event("claude_native_interrupt_started", command_id=command_id)
        try:
            self._send_control_request({"subtype": "interrupt"})
        except RuntimeError:
            with self.interrupt_lock:
                saved_result = context.get("result_payload")
                if self.native_interrupt is context:
                    self.native_interrupt = None
            if isinstance(saved_result, dict):
                self._handle_regular_result(saved_result)
            raise
        with self.interrupt_lock:
            context["receipt_confirmed"] = True
            saved_result = context.get("result_payload")
        if isinstance(saved_result, dict):
            self._finalize_native_interrupt(context, saved_result)
        if not result_received.wait(INTERRUPT_RESULT_TIMEOUT_SECONDS):
            with self.interrupt_lock:
                if self.native_interrupt is context:
                    self.native_interrupt = None
            raise RuntimeError(
                "managed_claude_native_interrupt_timeout: no terminal result followed the receipt"
            )
        self._event("claude_native_interrupt_completed", command_id=command_id)

    def _read_stdout(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        proc = self.proc
        try:
            for line in proc.stdout:
                raw = line.rstrip("\r\n")
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError as exc:
                    self._save_state(
                        status="failed",
                        last_error="managed_claude_stream_parse_failed: non-JSON stdout",
                    )
                    self._event("stream_parse_failed", summary=compact_text(raw, 300))
                    self.stop_requested.set()
                    self._log(f"stream parse error: {exc}: {raw[:500]}")
                    return
                if isinstance(payload, dict):
                    self._handle_stream_event(payload)
        except UnicodeError as exc:
            self._save_state(status="failed", last_error=f"managed_claude_unicode_failed: {exc}")
            self._event("stream_unicode_failed", summary=compact_text(exc, 300))
            self.stop_requested.set()

    def _read_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        proc = self.proc
        for line in proc.stderr:
            if line.strip():
                self._log("claude stderr: " + line.rstrip())

    def _message_text(self, payload: dict[str, Any]) -> str:
        message = payload.get("message")
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""
        return "\n".join(
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()

    def _update_command(self, command_id: str, **changes: Any) -> dict[str, Any]:
        path = _command_path(self.state_dir, command_id)
        row = _read_json(path, {}) or {}
        if not row:
            raise RuntimeError(f"managed_claude_command_missing: {command_id}")
        row.update(changes)
        row["updated_at"] = utc_now()
        _write_json(path, row)
        return row

    def _handle_user_replay(self, payload: dict[str, Any]) -> None:
        text = self._message_text(payload)
        marker = REQUEST_MARKER_RE.search(text)
        if not marker:
            return
        command_id = marker.group(1)
        path = _command_path(self.state_dir, command_id)
        row = _read_json(path, {}) or {}
        if not row:
            self._event("unmatched_user_replay", command_id=command_id)
            return
        if row.get("status") in {"submitting", "submitted"}:
            self._update_command(command_id, status="confirmed", confirmed_at=utc_now())
        self._event("command_confirmed", command_id=command_id, prompt_sha256=row.get("prompt_sha256"))

    def _tool_failures_from_payload(self, payload: dict[str, Any]) -> int:
        message = payload.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            return 0
        failures = 0
        for block in message["content"]:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result" and block.get("is_error"):
                failures += 1
                self._event(
                    "tool_failed",
                    tool_use_id=str(block.get("tool_use_id") or ""),
                    summary=compact_text(block.get("content"), 300),
                )
        return failures

    def _open_commands(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in (self.state_dir / "commands").glob("*.json"):
            row = _read_json(path, {}) or {}
            if row.get("type") == "message" and row.get("status") in {
                "submitted",
                "confirmed",
            }:
                rows.append(row)
        rows.sort(key=lambda row: str(row.get("created_at") or ""))
        return rows

    def _oldest_open_command(self) -> dict[str, Any] | None:
        rows = self._open_commands()
        return rows[0] if rows else None

    def _git_summary(self) -> dict[str, Any]:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=self.project_root,
            text=True,
            encoding="utf-8",
            errors="strict",
            capture_output=True,
            timeout=12,
            check=False,
            creationflags=WINDOWS_NO_WINDOW,
        )
        if result.returncode != 0:
            return {"status": "failed", "summary": compact_text(result.stderr or result.stdout, 300)}
        statuses = [compact_text(line, 220) for line in result.stdout.splitlines() if line.strip()]
        return {"status": "ok", "changed_count": len(statuses), "statuses": statuses[:20]}

    def _queue_judgment(self, source_event: dict[str, Any]) -> None:
        command_id = str(source_event.get("command_id") or "").strip()
        if not command_id:
            open_command = self._oldest_open_command()
            command_id = str(open_command.get("id") or "") if open_command else ""
        decision_key = (
            f"command-{command_id}"
            if command_id
            else f"event-{int(source_event['seq']):08d}"
        )
        decision_path = self.state_dir / "decisions" / f"{decision_key}.json"
        if decision_path.exists():
            return
        _write_json(
            decision_path,
            {
                "schema_version": 1,
                "event_seq": source_event["seq"],
                "decision_key": decision_key,
                "status": "queued",
                "event": source_event,
                "created_at": utc_now(),
                "updated_at": utc_now(),
            },
        )

    def _handle_regular_result(self, payload: dict[str, Any]) -> None:
        result_text = str(payload.get("result") or "")
        command = self._oldest_open_command()
        command_id = str(command.get("id")) if command else None
        if command_id:
            self._update_command(
                command_id,
                status="completed",
                completed_at=utc_now(),
                response_summary=compact_text(result_text, 1200),
            )
        self.tool_failures = 0
        git = self._git_summary()
        event = self._event(
            "turn_completed",
            command_id=command_id,
            result_summary=compact_text(result_text, 2000),
            git=git,
            is_error=str(payload.get("subtype") or "").startswith("error"),
        )
        self._save_state(status="idle", last_result_summary=compact_text(result_text, 800))
        self._queue_judgment(event)

    def _finalize_native_interrupt(
        self, context: dict[str, Any], payload: dict[str, Any]
    ) -> None:
        with self.interrupt_lock:
            if context.get("finalized"):
                return
            context["finalized"] = True
            if self.native_interrupt is context:
                self.native_interrupt = None
        interrupted_ids: list[str] = []
        for command_id in context.get("interrupted_ids", []):
            path = _command_path(self.state_dir, str(command_id))
            row = _read_json(path, {}) or {}
            if row.get("status") not in {"submitting", "submitted", "confirmed"}:
                continue
            self._update_command(
                str(command_id),
                status="interrupted",
                interrupted_at=utc_now(),
                interrupted_by=context.get("by_command_id"),
            )
            interrupted_ids.append(str(command_id))
        result_text = str(payload.get("result") or "")
        self.tool_failures = 0
        self._event(
            "turn_interrupted",
            command_ids=interrupted_ids,
            interrupted_by=context.get("by_command_id"),
            result_summary=compact_text(result_text, 800),
        )
        self._save_state(status="idle", last_result_summary=compact_text(result_text, 800))
        context["result_received"].set()

    def _handle_result(self, payload: dict[str, Any]) -> None:
        with self.interrupt_lock:
            context = self.native_interrupt
            if context is not None:
                context["result_payload"] = payload
                receipt_confirmed = bool(context.get("receipt_confirmed"))
            else:
                receipt_confirmed = False
        if context is None:
            self._handle_regular_result(payload)
        elif receipt_confirmed:
            self._finalize_native_interrupt(context, payload)

    def _handle_stream_event(self, payload: dict[str, Any]) -> None:
        self.last_activity_monotonic = time.monotonic()
        self._save_state(last_activity_at=utc_now())
        event_type = str(payload.get("type") or "")
        subtype = str(payload.get("subtype") or "")
        if event_type == "control_response":
            self._handle_control_response(payload)
            return
        if event_type == "system" and subtype == "init":
            capabilities = payload.get("capabilities")
            self._save_state(
                status="busy" if self._oldest_open_command() else "idle",
                session_id=str(payload.get("session_id") or self.session_id),
                claude_model=payload.get("model"),
                capabilities=list(capabilities) if isinstance(capabilities, list) else [],
            )
            self._event(
                "claude_stream_initialized",
                session_id=str(payload.get("session_id") or self.session_id),
                model=payload.get("model"),
                capabilities=list(capabilities) if isinstance(capabilities, list) else [],
            )
            self.stream_initialized.set()
            return
        if event_type == "system" and subtype == "api_retry":
            attempt = int(payload.get("attempt") or 0)
            maximum = int(payload.get("max_retries") or 0)
            event = self._event(
                "api_retry",
                attempt=attempt,
                max_retries=maximum,
                error=str(payload.get("error") or "unknown"),
            )
            if maximum and attempt >= maximum:
                open_command = self._oldest_open_command()
                exhausted = self._event(
                    "api_retry_exhausted",
                    attempt=attempt,
                    max_retries=maximum,
                    error=str(payload.get("error") or "unknown"),
                    source_seq=event["seq"],
                    command_id=open_command.get("id") if open_command else None,
                )
                self._queue_judgment(exhausted)
            return
        if event_type == "user":
            self._handle_user_replay(payload)
            failures = self._tool_failures_from_payload(payload)
            previous_failures = self.tool_failures
            self.tool_failures += failures
            if previous_failures < 2 <= self.tool_failures:
                open_command = self._oldest_open_command()
                threshold = self._event(
                    "tool_failure_threshold",
                    count=self.tool_failures,
                    command_id=open_command.get("id") if open_command else None,
                )
                self._queue_judgment(threshold)
            return
        if event_type == "assistant":
            text = self._message_text(payload)
            if text:
                self._event("assistant_progress", summary=compact_text(text, 500))
            failures = self._tool_failures_from_payload(payload)
            previous_failures = self.tool_failures
            self.tool_failures += failures
            if previous_failures < 2 <= self.tool_failures:
                open_command = self._oldest_open_command()
                threshold = self._event(
                    "tool_failure_threshold",
                    count=self.tool_failures,
                    command_id=open_command.get("id") if open_command else None,
                )
                self._queue_judgment(threshold)
            self._save_state(status="busy")
            return
        if event_type == "result":
            self._handle_result(payload)

    def _next_queued_command(self) -> dict[str, Any] | None:
        rows: list[dict[str, Any]] = []
        for path in (self.state_dir / "commands").glob("*.json"):
            row = _read_json(path, {}) or {}
            if row.get("status") == "queued":
                rows.append(row)
        rows.sort(key=lambda row: str(row.get("created_at") or ""))
        return rows[0] if rows else None

    def _submit_command(self, row: dict[str, Any]) -> None:
        command_id = str(row["id"])
        if row.get("type") == "stop":
            self._update_command(command_id, status="completed", completed_at=utc_now())
            self._save_state(status="stopping")
            self.stop_requested.set()
            return
        if row.get("type") != "message":
            self._update_command(command_id, status="failed", error="unknown command type")
            return
        if str(row.get("origin") or "external") == "external":
            self._save_ledger(autonomous_actions_since_external=0)
        try:
            if bool(row.get("interrupt_current")):
                interrupt_mode = validate_interrupt_mode(row.get("interrupt_mode"))
                if self.proc is None or self.proc.poll() is not None:
                    self._mark_open_commands_interrupted(command_id)
                    self._start_claude(resume=True)
                elif interrupt_mode == "native":
                    self._native_interrupt(command_id)
                else:
                    self._mark_open_commands_interrupted(command_id)
                    self._terminate_claude(f"command:{command_id}")
                    self.intentional_process_stop = False
                    self._start_claude(resume=True)
            elif self.proc is None or self.proc.poll() is not None:
                self._start_claude(resume=True)
        except (RuntimeError, ValueError) as exc:
            self._update_command(command_id, status="failed", error=str(exc))
            self._save_state(status="attention_required", last_error=str(exc))
            self._event(
                "command_interrupt_failed",
                command_id=command_id,
                interrupt_mode=row.get("interrupt_mode") or "native",
                error=str(exc),
            )
            return
        if self.proc is None or self.proc.poll() is not None or self.proc.stdin is None:
            self._update_command(command_id, status="failed", error="Claude stream is not writable")
            return
        marker = f"[Switchboard managed request {command_id}]"
        text = (
            f"{marker}\n{row['prompt']}\n\n"
            f"Acknowledge this request in your response with [Switchboard managed ack {command_id}]."
        )
        stream_message = {
            "type": "user",
            "message": {"role": "user", "content": text},
            "parent_tool_use_id": None,
            "session_id": "default",
        }
        self._update_command(command_id, status="submitting")
        try:
            with self.io_lock:
                self.proc.stdin.write(json.dumps(stream_message, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
        except (BrokenPipeError, OSError, UnicodeError) as exc:
            self._update_command(command_id, status="failed", error=f"stream write failed: {exc}")
            self._save_state(status="failed", last_error=f"managed_claude_stream_write_failed: {exc}")
            return
        self._update_command(command_id, status="submitted", submitted_at=utc_now())
        self.last_activity_monotonic = time.monotonic()
        self.stall_notified.discard(command_id)
        self._save_state(status="busy", attention=None, last_activity_at=utc_now())
        self._event(
            "command_submitted",
            command_id=command_id,
            prompt_sha256=row.get("prompt_sha256"),
            interrupt_current=bool(row.get("interrupt_current")),
            interrupt_mode=(row.get("interrupt_mode") if row.get("interrupt_current") else None),
            origin=row.get("origin"),
        )

    def _codex_schema_path(self) -> Path:
        path = self.state_dir / "codex-decision-schema.json"
        if not path.exists():
            _write_json(
                path,
                {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": sorted(ALLOWED_DECISIONS)},
                        "message": {"type": "string"},
                        "reason": {"type": "string"},
                        "milestone": {"type": ["string", "null"]},
                    },
                    "required": ["action", "message", "reason", "milestone"],
                    "additionalProperties": False,
                },
            )
        return path

    def _parse_codex_message(self, stdout: str) -> str:
        messages: list[str] = []
        for line in stdout.splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict) or payload.get("type") != "item.completed":
                continue
            item = payload.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = str(item.get("text") or "").strip()
                if text:
                    messages.append(text)
        return messages[-1] if messages else ""

    def _run_codex_decision(self, event: dict[str, Any]) -> dict[str, Any]:
        prompt = (
            "You are the decision-only supervisor for a background Claude Code worker. "
            "Do not edit files and do not perform the implementation. Decide only the next control action.\n\n"
            "Allowed actions:\n"
            "ACK: record progress and send nothing.\n"
            "SEND: send the message to Claude without interrupting the current turn.\n"
            "INTERRUPT: use Claude's native stream interrupt, wait for its receipt and terminal result, then send the message.\n"
            "ASK_USER: stop autonomous actions and expose the message for the human.\n"
            "ACCEPT: the objective or milestone is adequately complete; send nothing.\n\n"
            "Choose SEND or INTERRUPT only when the message contains a concrete necessary instruction. "
            "Do not create periodic check-ins, do not ask Claude for status without evidence, and do not retry a failed "
            "provider call merely to see whether it works.\n\n"
            f"Objective: {self.config['objective']}\n"
            f"Policy: {self.config.get('policy') or '(none)'}\n"
            f"Durable ledger: {json.dumps(self.ledger, ensure_ascii=False, separators=(',', ':'))}\n"
            f"Material event: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n"
        )
        command = [
            str(self.config["codex_path"]),
            "exec",
            "--ephemeral",
            "--cd",
            self.project_root,
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--output-schema",
            str(self._codex_schema_path()),
            "--json",
            "-",
        ]
        if self.config.get("codex_effort"):
            command[2:2] = ["-c", f"model_reasoning_effort={self.config['codex_effort']}"]
        if self.config.get("codex_model"):
            command[2:2] = ["--model", str(self.config["codex_model"])]
        result = subprocess.run(
            command,
            cwd=self.project_root,
            input=prompt,
            text=True,
            encoding="utf-8",
            errors="strict",
            capture_output=True,
            timeout=600,
            check=False,
            creationflags=WINDOWS_NEW_PROCESS_GROUP | WINDOWS_NO_WINDOW,
        )
        self._save_state(decision_invocations=int(self.state.get("decision_invocations") or 0) + 1)
        if result.returncode != 0:
            raise RuntimeError(
                "managed_claude_codex_failed: " + compact_text(result.stderr or result.stdout, 700)
            )
        message = self._parse_codex_message(result.stdout)
        try:
            decision = json.loads(message)
        except json.JSONDecodeError as exc:
            raise RuntimeError("managed_claude_codex_invalid: final message was not JSON") from exc
        if not isinstance(decision, dict) or decision.get("action") not in ALLOWED_DECISIONS:
            raise RuntimeError("managed_claude_codex_invalid: unsupported action")
        for key in ("message", "reason"):
            if not isinstance(decision.get(key), str):
                raise RuntimeError(f"managed_claude_codex_invalid: {key} must be a string")
        return decision

    def _apply_decision(self, decision: dict[str, Any], event: dict[str, Any]) -> None:
        action = str(decision["action"])
        message = str(decision.get("message") or "").strip()
        self._save_ledger(
            last_decision={
                "action": action,
                "reason": compact_text(decision.get("reason"), 700),
                "event_seq": event.get("seq"),
                "created_at": utc_now(),
            },
            current_milestone=decision.get("milestone"),
        )
        self._event(
            "codex_decision",
            source_seq=event.get("seq"),
            action=action,
            reason=compact_text(decision.get("reason"), 700),
        )
        if action in {"SEND", "INTERRUPT"}:
            if not message:
                raise RuntimeError("managed_claude_codex_invalid: control action has no message")
            count = int(self.ledger.get("autonomous_actions_since_external") or 0) + 1
            self._save_ledger(autonomous_actions_since_external=count)
            queue_command(
                self.state_dir.parents[1],
                str(self.config["supervisor_id"]),
                message,
                interrupt_current=action == "INTERRUPT",
                interrupt_mode="native",
                origin="codex_decision",
            )
            return
        if action == "ASK_USER":
            self._save_state(
                status="attention_required",
                attention={
                    "message": message,
                    "reason": decision.get("reason"),
                    "event_seq": event.get("seq"),
                },
            )
            return
        if action == "ACCEPT":
            self._save_state(status="accepted", attention=None)
            return
        self._save_state(status="idle", attention=None)

    def _next_judgment(self) -> tuple[Path, dict[str, Any]] | None:
        for path in sorted((self.state_dir / "decisions").glob("*.json")):
            row = _read_json(path, {}) or {}
            if row.get("status") == "queued":
                return path, row
        return None

    def _check_stall(self) -> None:
        if self.state.get("status") != "busy":
            return
        command = self._oldest_open_command()
        if not command:
            return
        command_id = str(command.get("id") or "")
        if not command_id or command_id in self.stall_notified:
            return
        stall_seconds = int(self.config.get("stall_timeout_seconds") or 900)
        if time.monotonic() - self.last_activity_monotonic < stall_seconds:
            return
        self.stall_notified.add(command_id)
        event = self._event(
            "stall_timeout",
            command_id=command_id,
            silent_seconds=stall_seconds,
        )
        self._queue_judgment(event)

    def _process_judgment(self, path: Path, row: dict[str, Any]) -> None:
        event = row.get("event")
        if not isinstance(event, dict):
            row.update(status="failed", error="missing event", updated_at=utc_now())
            _write_json(path, row)
            return
        if self.config["decision_mode"] == "record_only":
            row.update(status="attention_required", updated_at=utc_now())
            _write_json(path, row)
            self._save_state(
                status="attention_required",
                attention={
                    "message": "A material Claude event requires judgment.",
                    "event_seq": event.get("seq"),
                    "event_type": event.get("type"),
                },
            )
            return
        max_actions = int(self.config.get("max_autonomous_actions") or 0)
        autonomous = int(self.ledger.get("autonomous_actions_since_external") or 0)
        if max_actions >= 0 and autonomous >= max_actions:
            row.update(status="attention_required", updated_at=utc_now())
            _write_json(path, row)
            self._save_state(
                status="attention_required",
                attention={
                    "message": "Autonomous action limit reached; human judgment is required.",
                    "event_seq": event.get("seq"),
                },
            )
            self._event(
                "autonomous_action_limit_reached",
                source_seq=event.get("seq"),
                limit=max_actions,
            )
            return
        row.update(status="deciding", updated_at=utc_now())
        _write_json(path, row)
        try:
            decision = self._run_codex_decision(event)
            self._apply_decision(decision, event)
        except Exception as exc:  # noqa: BLE001
            row.update(status="failed", error=str(exc), updated_at=utc_now())
            _write_json(path, row)
            self._save_state(status="attention_required", last_error=str(exc))
            self._event("codex_decision_failed", source_seq=event.get("seq"), error=compact_text(exc, 700))
            return
        row.update(status="completed", decision=decision, updated_at=utc_now())
        _write_json(path, row)

    def run(self) -> int:
        lock = FileLock(
            self.state_dir / "daemon.lock",
            timeout=0.1,
            stale_seconds=365 * 24 * 60 * 60,
        )
        if not lock.acquire():
            return 2
        try:
            broker_dir = self.state_dir.parents[1]
            ensure_hook_event_server(
                broker_dir,
                command=_hook_event_server_command(broker_dir),
            )
            self._save_state(status="starting", daemon_pid=os.getpid(), uses_foreground_ui=False)
            self._event("daemon_started", daemon_pid=os.getpid(), uses_foreground_ui=False)
            self._start_claude(resume=bool(self.config.get("resume_existing")))
            while not self.stop_requested.is_set():
                command = self._next_queued_command()
                if command:
                    try:
                        self._submit_command(command)
                    except Exception as exc:  # noqa: BLE001
                        self._update_command(str(command["id"]), status="failed", error=str(exc))
                        self._save_state(status="attention_required", last_error=str(exc))
                        self._event("command_failed", command_id=command.get("id"), error=compact_text(exc, 700))
                    continue
                judgment = self._next_judgment()
                if judgment:
                    self._process_judgment(*judgment)
                    continue
                if self.proc is not None and self.proc.poll() is not None:
                    exit_code = self.proc.returncode
                    self.proc = None
                    if self.intentional_process_stop:
                        self.intentional_process_stop = False
                    else:
                        event = self._event("claude_process_exited", exit_code=exit_code)
                        self._save_state(
                            status="attention_required",
                            claude_pid=None,
                            last_error=f"Claude process exited with code {exit_code}",
                        )
                        self._queue_judgment(event)
                self._check_stall()
                time.sleep(POLL_SECONDS)
            if self.proc is not None and self.proc.poll() is None:
                self._terminate_claude("supervisor_stop")
            self._save_state(status="stopped", claude_pid=None, attention=None)
            self._event("daemon_stopped", daemon_pid=os.getpid())
            return 0
        except Exception as exc:  # noqa: BLE001
            self._save_state(status="failed", last_error=str(exc), claude_pid=None)
            self._event("daemon_failed", error=compact_text(exc, 800))
            self._log(f"daemon failed: {type(exc).__name__}: {exc}")
            return 1
        finally:
            lock.release()


def daemon_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent Switchboard managed Claude daemon")
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args(argv)
    return ManagedClaudeDaemon(Path(args.state_dir)).run()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "daemon":
        args = args[1:]
        return daemon_main(args)
    if args and args[0] == "hook-event-server":
        from hook_event_server import server_main

        return server_main(args[1:])
    return daemon_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
