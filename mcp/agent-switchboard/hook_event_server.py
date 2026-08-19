"""Local Claude Code hook event receiver for Agent Switchboard.

The receiver is a broker-wide, loopback-only HTTP service.  It is launched by
managed-Claude daemons but owns no supervisor state in memory: every request
maps its session id against the durable state files before appending an event.
"""

from __future__ import annotations

import argparse
import ctypes
import http.client
import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from atomic_io import FileLock, atomic_write_text


HOST = "127.0.0.1"
DEFAULT_PORT = 43827
MAX_BODY_BYTES = 64 * 1024
MAX_STRING_CHARS = 16 * 1024
PID_FILE_NAME = "hook-event-server.pid"
SERVER_LOCK_NAME = "hook-event-server.lock"
LAUNCH_LOCK_NAME = "hook-event-server.launch.lock"
ORPHAN_LOG_NAME = "hook-events-orphans.jsonl"
ORPHAN_LOCK_NAME = "hook-events-orphans.lock"
ENDPOINT_FILE_NAME = "hook-event-server.endpoint"

EVENT_TYPES = {
    "Stop": "hook_stop",
    "SubagentStop": "hook_subagent_stop",
    "StopFailure": "hook_stop_failure",
    "SessionEnd": "hook_session_end",
}
EVENT_TYPES_BY_NORMALIZED_NAME = {
    key.casefold(): value for key, value in EVENT_TYPES.items()
}
ALLOWED_FIELDS = frozenset(
    {
        "session_id",
        "event",
        "hook_event_name",
        "cwd",
        "transcript_path",
        "source",
        "reason",
        "error",
        "message",
        "last_assistant_message",
        "prompt",
        "permission_mode",
        "stop_hook_active",
    }
)
STRING_FIELDS = frozenset(ALLOWED_FIELDS - {"stop_hook_active"})


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def port_from_environment() -> int:
    raw = os.environ.get("AGENT_BROKER_HOOK_EVENT_PORT", str(DEFAULT_PORT)).strip()
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError("AGENT_BROKER_HOOK_EVENT_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("AGENT_BROKER_HOOK_EVENT_PORT must be between 1 and 65535")
    return port


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"hook_event_state_invalid: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"hook_event_state_invalid: {path}")
    return value


def _last_seq(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except OSError as exc:
        raise RuntimeError(f"hook_event_log_unavailable: {path}") from exc
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            try:
                return max(0, int(value.get("seq") or 0))
            except (TypeError, ValueError):
                continue
    return 0


def _append_jsonl(path: Path, record: dict[str, Any], lock_path: Path) -> None:
    with FileLock(lock_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def _append_supervisor_event(
    state_dir: Path, event_type: str, payload: dict[str, Any]
) -> dict[str, Any]:
    events_path = state_dir / "events.jsonl"
    lock_path = state_dir / "events.lock"
    with FileLock(lock_path):
        seq = _last_seq(events_path) + 1
        record: dict[str, Any] = {
            "seq": seq,
            "type": event_type,
            "created_at": utc_now(),
            "hook_event": payload["event"],
        }
        for key, value in payload.items():
            if key != "event":
                record[key] = value
        events_path.parent.mkdir(parents=True, exist_ok=True)
        with events_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return record


class HookEventReceiver:
    """Validate hook payloads, map sessions, and append durable events."""

    def __init__(self, broker_dir: Path) -> None:
        self.broker_dir = Path(broker_dir).expanduser().resolve()
        self.supervisors_dir = self.broker_dir / "supervisors"
        self.orphan_path = self.broker_dir / ORPHAN_LOG_NAME
        self.orphan_lock = self.broker_dir / ORPHAN_LOCK_NAME

    def _find_supervisor(self, session_id: str) -> Path | None:
        if not self.supervisors_dir.exists():
            return None
        for state_path in sorted(self.supervisors_dir.glob("*/state.json")):
            try:
                state = _read_json_object(state_path)
            except FileNotFoundError:
                continue
            if state.get("session_id") == session_id:
                return state_path.parent
        return None

    @staticmethod
    def validate_payload(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        extra = set(payload) - ALLOWED_FIELDS
        if extra:
            names = ", ".join(sorted(str(item) for item in extra))
            raise ValueError(f"unknown field(s): {names}")
        for required in ("session_id",):
            if required not in payload:
                raise ValueError(f"missing required field: {required}")
        if "event" not in payload and "hook_event_name" not in payload:
            raise ValueError("missing required field: event or hook_event_name")
        for field in STRING_FIELDS:
            if field in payload and not isinstance(payload[field], str):
                raise ValueError(f"{field} must be a string")
            if field in payload and len(payload[field]) > MAX_STRING_CHARS:
                raise ValueError(f"{field} exceeds {MAX_STRING_CHARS} characters")
        if "stop_hook_active" in payload and not isinstance(payload["stop_hook_active"], bool):
            raise ValueError("stop_hook_active must be a boolean")
        if not payload["session_id"].strip():
            raise ValueError("session_id must not be empty")
        if "event" not in payload:
            payload = {**payload, "event": payload["hook_event_name"]}
        event_name = payload["event"].strip()
        event_type = EVENT_TYPES_BY_NORMALIZED_NAME.get(event_name.casefold())
        if event_type is None:
            raise ValueError("event must be Stop, SubagentStop, StopFailure, or SessionEnd")
        if "hook_event_name" in payload:
            hook_name = payload["hook_event_name"].strip()
            if hook_name.casefold() != event_name.casefold():
                raise ValueError("hook_event_name must match event")
        return dict(payload)

    def record(self, raw_payload: Any) -> dict[str, Any]:
        payload = self.validate_payload(raw_payload)
        event_type = EVENT_TYPES_BY_NORMALIZED_NAME[payload["event"].casefold()]
        state_dir = self._find_supervisor(payload["session_id"])
        if state_dir is None:
            orphan = {
                "created_at": utc_now(),
                "event_type": event_type,
                "reason": "unknown_session",
                "payload": payload,
            }
            _append_jsonl(self.orphan_path, orphan, self.orphan_lock)
            return {"accepted": True, "status": "orphan", "event_type": event_type}
        record = _append_supervisor_event(state_dir, event_type, payload)
        return {
            "accepted": True,
            "status": "recorded",
            "event_type": event_type,
            "supervisor_id": state_dir.name,
            "seq": record["seq"],
        }


class _HookRequestHandler(BaseHTTPRequestHandler):
    server: "HookEventHTTPServer"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _json_response(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if urlsplit(self.path).path == "/health":
            self._json_response(200, {"status": "ok", "root": str(self.server.receiver.broker_dir)})
            return
        self._json_response(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if urlsplit(self.path).path != "/event":
            self._json_response(404, {"error": "not_found"})
            return
        transfer_encoding = self.headers.get("Transfer-Encoding", "").strip().lower()
        if transfer_encoding and transfer_encoding != "identity":
            self._json_response(400, {"error": "chunked transfer is not supported"})
            return
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else -1
        except ValueError:
            length = -1
        if length < 0:
            self._json_response(400, {"error": "Content-Length is required"})
            return
        if length > MAX_BODY_BYTES:
            self._json_response(413, {"error": "request body is too large"})
            return
        body = self.rfile.read(length)
        if len(body) != length:
            self._json_response(400, {"error": "incomplete request body"})
            return
        try:
            text = body.decode("utf-8")
            payload = json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            result = self.server.receiver.record(payload)
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            self._json_response(400, {"error": str(exc)})
            return
        except (OSError, RuntimeError) as exc:
            self._json_response(500, {"error": str(exc)})
            return
        self._json_response(202, result)


class HookEventHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], receiver: HookEventReceiver) -> None:
        self.receiver = receiver
        super().__init__(address, _HookRequestHandler)


def create_server(broker_dir: Path, port: int = 0) -> HookEventHTTPServer:
    if HOST != "127.0.0.1":
        raise RuntimeError("hook event receiver must bind to 127.0.0.1")
    return HookEventHTTPServer((HOST, port), HookEventReceiver(broker_dir))


def _pid_alive(pid: Any) -> bool:
    try:
        wanted = int(pid)
    except (TypeError, ValueError):
        return False
    if wanted <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) is NOT a liveness probe on Windows: signal 0 is
        # CTRL_C_EVENT, which can deliver a Ctrl+C into a console process
        # group, return success for dead pids, or raise SystemError
        # ("returned a result with an exception set") against detached
        # processes. A SystemError escapes the OSError-based guards and killed
        # managed-daemons at startup. Use the kernel32 exit-code check that
        # managed_claude.pid_is_alive has proven instead.
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
    except (ProcessLookupError, OSError):
        return False
    except PermissionError:
        return True
    return True


def _server_info_path(broker_dir: Path) -> Path:
    return Path(broker_dir) / PID_FILE_NAME


def _endpoint_path(broker_dir: Path) -> Path:
    return Path(broker_dir) / ENDPOINT_FILE_NAME


def _read_server_info(broker_dir: Path) -> dict[str, Any] | None:
    path = _server_info_path(broker_dir)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"hook_event_server_state_invalid: {path}") from exc
    return value if isinstance(value, dict) else None


def _health_check(port: int, timeout: float = 0.25, expected_root: str | None = None) -> bool:
    connection = http.client.HTTPConnection(HOST, port, timeout=timeout)
    try:
        connection.request("GET", "/health")
        response = connection.getresponse()
        if response.status != 200:
            return False
        # Identity guard: a healthy answer on the right port is not enough — a
        # receiver rooted at a DIFFERENT broker dir (e.g. a leftover temp-dir
        # smoke instance) must not be accepted as ours, otherwise its pid-file
        # state and our events silently diverge.
        if expected_root is not None:
            try:
                body = json.loads(response.read().decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                return False
            if not isinstance(body, dict) or body.get("root") != expected_root:
                return False
        return True
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def _clear_dead_server_lock(broker_dir: Path) -> None:
    path = broker_dir / SERVER_LOCK_NAME
    try:
        owner = int(path.read_text(encoding="utf-8").strip())
    except FileNotFoundError:
        return
    except (OSError, UnicodeError, ValueError):
        owner = 0
    if owner and _pid_alive(owner):
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def ensure_hook_event_server(
    broker_dir: Path,
    command: list[str] | None = None,
    port: int | None = None,
    wait_seconds: float = 3.0,
) -> int:
    """Start or reuse the broker-wide receiver and wait for its health check."""
    root = Path(broker_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    chosen_port = port if port is not None else port_from_environment()
    if not 1 <= chosen_port <= 65535:
        raise ValueError("hook event port must be between 1 and 65535")
    with FileLock(root / LAUNCH_LOCK_NAME, timeout=5):
        info = _read_server_info(root)
        if info and _pid_alive(info.get("pid")) and int(info.get("port") or 0) == chosen_port:
            if _health_check(chosen_port, expected_root=str(root)):
                return int(info["pid"])
        _clear_dead_server_lock(root)
        if command is None:
            command = [sys.executable, str(Path(__file__).resolve()), "--broker-dir", str(root)]
        command = [*command, "--port", str(chosen_port)]
        process = subprocess.Popen(
            command,
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=(os.name != "nt"),
            creationflags=(getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) if os.name == "nt" else 0,
        )
        deadline = time.monotonic() + max(0.2, wait_seconds)
        while time.monotonic() < deadline:
            info = _read_server_info(root)
            if info and _pid_alive(info.get("pid")) and int(info.get("port") or 0) == chosen_port and _health_check(chosen_port, expected_root=str(root)):
                return int(info["pid"])
            if process.poll() is not None:
                break
            time.sleep(0.05)
    raise RuntimeError("hook_event_server_failed_to_start")


def serve(broker_dir: Path, port: int) -> int:
    root = Path(broker_dir).expanduser().resolve()
    _clear_dead_server_lock(root)
    lock = FileLock(root / SERVER_LOCK_NAME, timeout=0.1, stale_seconds=365 * 24 * 60 * 60)
    if not lock.acquire():
        return 2
    server: HookEventHTTPServer | None = None
    pid_path = _server_info_path(root)
    endpoint_path = _endpoint_path(root)
    try:
        server = create_server(root, port)
        atomic_write_text(
            pid_path,
            json.dumps({"pid": os.getpid(), "host": HOST, "port": port}, separators=(",", ":")) + "\n",
        )
        atomic_write_text(endpoint_path, f"http://{HOST}:{server.server_port}\n")
        server.serve_forever(poll_interval=0.2)
        return 0
    except OSError:
        return 1
    finally:
        if server is not None:
            server.server_close()
        try:
            pid_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        try:
            endpoint_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        lock.release()


def server_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent Switchboard Claude hook event receiver")
    parser.add_argument("--broker-dir", required=True)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    if args.port < 1 or args.port > 65535:
        raise SystemExit("port must be between 1 and 65535")
    return serve(Path(args.broker_dir), args.port)


def forward_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Forward a Claude hook event to the broker")
    parser.add_argument("--broker-dir", default=str(Path.home() / ".agent-broker"))
    try:
        args = parser.parse_args(argv)
        broker_dir = Path(args.broker_dir).expanduser().resolve()
        stdin = getattr(sys.stdin, "buffer", sys.stdin)
        body = stdin.read()
        if isinstance(body, str):
            body = body.encode("utf-8")
        endpoint = _endpoint_path(broker_dir).read_text(encoding="utf-8").strip()
        parsed = urlsplit(endpoint)
        if parsed.scheme != "http" or not parsed.hostname or parsed.port is None:
            return 0
        path = (parsed.path.rstrip("/") or "") + "/event"
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=2.0)
        try:
            connection.request(
                "POST",
                path,
                body=body,
                headers={"Content-Type": "application/json"},
            )
            connection.getresponse().read()
        finally:
            connection.close()
    except Exception:  # noqa: BLE001
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(server_main())
