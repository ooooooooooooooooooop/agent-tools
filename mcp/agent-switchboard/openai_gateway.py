"""openai_gateway.py — optional OpenAI-compatible HTTP exit for Agent Switchboard.

Publishes every backend in the global ``CliRegistry`` behind a single
``POST /v1/chat/completions`` endpoint (OpenAI-compatible), turning Switchboard into a
router/gateway that any OpenAI-format client can point at (Aider, Cline, LangChain,
LiteLLM, ``curl`` ...). Also exposes ``GET /health`` for lifecycle checks.

Design:
- Zero extra deps: stdlib ``http.server.ThreadingHTTPServer`` + ``urllib`` (mirrors
  ``hook_event_server.py``'s loopback HTTP service conventions).
- Loopback-only by default (``127.0.0.1``); bind ``0.0.0.0`` only with an explicit flag.
- Model routing: ``<backend>/<model>`` exact, or bare ``<model>`` resolved by priority
  (exact name/alias -> default_model/models -> routing_preferences -> 400).
- Uses the same ``cli_registry()`` and ``routing_preferences`` as the MCP tools.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import request as _urlreq

HOST = "127.0.0.1"
DEFAULT_PORT = 9090
MAX_BODY_BYTES = 64 * 1024
PID_FILE_NAME = "openai-gateway.pid"
ENDPOINT_FILE_NAME = "openai-gateway.endpoint"
LOCK_FILE_NAME = "openai-gateway.lock"
LAUNCH_LOCK_NAME = "openai-gateway.launch.lock"


def _bool_arg(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


# Imported lazily to avoid import cycles; the gateway reads the SAME registry/config the
# MCP path uses.
def _registry():
    import agent_broker_mcp as m

    return m.cli_registry()


def _run_process(command, cwd, timeout=300):
    import agent_broker_mcp as m

    return m.run_process(command, cwd or str(Path.cwd()), timeout=timeout)


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _default_effort(backend) -> str | None:
    """Return a safe reasoning effort for a backend, reading ``default_effort`` from metadata.

    Some backends (notably codex) have a ``default_effort`` that is supported by the
    model, whereas an unset effort (`None`) can resolve to a model-unsupported value
    like ``max``.  This helper returns the backend's declared default_effort (or None
    when absent, so the backend falls back to its own logic).
    """
    try:
        md = backend.metadata() if hasattr(backend, "metadata") else {}
        return md.get("default_effort") if isinstance(md, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _messages_to_prompt(messages: Any) -> str:
    """Flatten chat messages to a single prompt (last user content, or joined)."""
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty array")
    parts: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            parts.append(content.strip())
    if not parts:
        raise ValueError("messages must contain at least one non-empty content")
    return "\n".join(parts)


def _parse_model_name(model: Any) -> tuple[str | None, str | None]:
    """Split 'backend/model' -> (backend, model); bare string -> (None, model)."""
    raw = str(model or "").strip()
    if not raw:
        return None, None
    if "/" in raw:
        backend, _, rest = raw.partition("/")
        return (backend.strip() or None, rest.strip() or None)
    return (None, raw if raw else None)


def _resolve_backend(registry, backend_hint: str | None, model: str | None):
    """Resolve a backend + effective model from the 'model' request field.

    Returns (backend, model_to_use) or raises ValueError("model_not_found: ...").
    Priority: explicit backend> -> exact name/alias -> default_model/models ->
    routing_preferences (bare model only).
    """
    if backend_hint:
        b = registry.get(backend_hint)
        if b is None:
            raise ValueError(f"model_not_found: {backend_hint}/{model or ''}")
        return b, model or b.metadata().get("default_model")

    # Bare model resolution.
    if not model:
        raise ValueError("model_required")

    # 1) exact backend name/alias
    b = registry.get(model)
    if b is not None:
        return b, b.metadata().get("default_model")

    # 2) a backend whose default_model / models list matches
    for b in registry.all():
        md = b.metadata()
        defaults = [str(x).lower() for x in [md.get("default_model")] if x]
        models_list = [str(x).lower() for x in (md.get("models") or [])]
        if model.lower() in defaults or model.lower() in models_list:
            return b, model

    # 3) routing_preferences by task type
    import agent_broker_mcp as m

    prefs = m.route_from_task_preferences(model, m.load_config(), registry)
    if prefs is not None:
        pref_backend = registry.get(prefs[0])
        if pref_backend is not None:
            return pref_backend, model
    raise ValueError(f"model_not_found: {model}")


def _list_models(registry=None) -> dict[str, Any]:
    """Build the OpenAI-standard /v1/models list from the CLI registry.

    Every registered backend contributes:
      1. its own name as a model id (`<backend_name>`)
      2. `<backend>/<default_model>` if it declares a default_model
      3. `<backend>/<model>` for every entry in its `models` list
    Availability is not enforced here (a listed model may be offline), consistent with
    how /v1/models is advisory.
    """
    registry = registry or _registry()
    data: list[dict[str, Any]] = []
    now = int(time.time())
    seen: set[str] = set()
    for b in registry.all():
        md = b.metadata() if hasattr(b, "metadata") else {}
        ids: list[str] = [b.name]
        default_model = md.get("default_model") if isinstance(md, dict) else None
        models_list = md.get("models") if isinstance(md, dict) else None
        if default_model:
            ids.append(f"{b.name}/{default_model}")
        if isinstance(models_list, (list, tuple)):
            for m in models_list:
                if str(m).strip():
                    ids.append(f"{b.name}/{m}")
        for mid in ids:
            if mid in seen:
                continue
            seen.add(mid)
            data.append(
                {
                    "id": mid,
                    "object": "model",
                    "created": now,
                    "owned_by": b.name,
                    "_backend": b.name,
                }
            )
    return {"object": "list", "data": data}


def _chat_completion(messages: Any, model_field: Any, timeout: int) -> dict[str, Any]:
    registry = _registry()
    backend_hint, model_name = _parse_model_name(model_field)
    backend, effective_model = _resolve_backend(registry, backend_hint, model_name)
    prompt = _messages_to_prompt(messages)

    result = backend.execute(
        prompt,
        model=effective_model,
        effort=_default_effort(backend),
        timeout=timeout,
        runner=lambda cmd, cwd, t: _run_process(cmd, cwd, timeout=t),
    )
    if result.status == "cli_not_found":
        raise RuntimeError(f"cli_not_found: {backend.name} CLI not available")
    if result.status not in ("completed", "model_mismatch"):
        raise RuntimeError(f"upstream_error: {result.response[:500]}")

    usage = result.metadata.get("usage") if isinstance(result.metadata, dict) else None
    usage_payload = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if isinstance(usage, dict):
        usage_payload.update(
            {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }
        )
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_field or f"{backend.name}/{effective_model or ''}".rstrip("/"),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": result.response or ""},
                "finish_reason": "stop",
            }
        ],
        "usage": usage_payload,
    }


class OpenAICompatibleHandler(BaseHTTPRequestHandler):
    server_version = "AgentSwitchboardGateway/1.0"

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, code: str, message: str) -> None:
        self._send_json(status, {"error": {"message": message, "code": code}})

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0].rstrip("/")
        if path == "/health":
            names = [b.name for b in (_registry().all() if _registry() is not None else [])]
            self._send_json(
                200,
                {
                    "status": "ok",
                    "time": utc_now(),
                    "backends": len(names),
                    "route": names,
                },
            )
            return
        if path == "/v1/models":
            try:
                models = _list_models()
            except Exception as exc:  # noqa: BLE001
                self._error(500, "internal_error", f"models: {type(exc).__name__}: {exc}")
                return
            self._send_json(200, models)
            return
        self._error(404, "not_found", "unknown path")

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            raise ValueError("request_too_large")
        if length <= 0:
            return b"{}"
        return self.rfile.read(length)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            self._error(404, "not_found", "only POST /v1/chat/completions is supported")
            return
        try:
            raw = self._read_body()
        except ValueError as exc:
            self._error(413, "request_too_large", str(exc))
            return
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._error(400, "invalid_request", "request body must be valid JSON")
            return
        if not isinstance(payload, dict):
            self._error(400, "invalid_request", "request body must be a JSON object")
            return
        if _bool_arg(payload.get("stream")):
            self._error(400, "not_implemented", "streaming is not yet supported; set stream=false")
            return
        timeout = 300
        try:
            timeout = max(1, min(int(payload.get("max_tokens") or 300), 600))
        except (TypeError, ValueError):
            timeout = 300
        try:
            result_payload = _chat_completion(payload.get("messages"), payload.get("model"), timeout)
        except ValueError as exc:
            self._error(400, "bad_model", str(exc))
            return
        except RuntimeError as exc:
            self._error(502, "upstream_error", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self._error(500, "internal_error", f"{type(exc).__name__}: {exc}")
            return
        self._send_json(200, result_payload)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Keep gateway logs under the broker log; avoid noisy per-request stderr.
        try:
            import agent_broker_mcp as m

            m.log(f"gateway {self.command} {self.path}: {format % args}")
        except Exception:  # noqa: BLE001
            pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _read_gateway_info(broker_dir: Path) -> dict[str, Any]:
    path = Path(broker_dir) / ENDPOINT_FILE_NAME
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        return info if isinstance(info, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _health_check(port: int) -> bool:
    try:
        with _urlreq.urlopen(f"http://{HOST}:{port}/health", timeout=2) as resp:  # noqa: S310
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def serve(broker_dir: str | Path, port: int, host: str = HOST) -> int:
    """Start the gateway (blocking). Writes pid/endpoint files. Returns 0 on clean exit."""
    root = Path(broker_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Initialise the registry so /health reflects backends; errors here shouldn't crash serve.
    try:
        registry = _registry()
        if registry is None:
            raise SystemExit("gateway_registry_unavailable")
    except Exception as exc:  # noqa: BLE001
        print(f"Error: failed to initialize CLI registry: {exc}", file=sys.stderr)
        return 2
    try:
        httpd = ThreadingHTTPServer((host, port), OpenAICompatibleHandler)
    except OSError as exc:
        if _health_check(port):
            print(f"Gateway already running on {host}:{port}.", file=sys.stderr)
            return 0
        print(f"Error: cannot bind {host}:{port}: {exc}", file=sys.stderr)
        return 1
    actual_port = httpd.server_address[1]
    (root / PID_FILE_NAME).write_text(str(os.getpid()), encoding="utf-8")
    (root / ENDPOINT_FILE_NAME).write_text(
        json.dumps({"pid": os.getpid(), "port": actual_port, "host": host, "time": utc_now()}),
        encoding="utf-8",
    )
    print(f"Agent Switchboard OpenAI gateway listening on http://{host}:{actual_port}"
          f" (backends: {[b.name for b in registry.all()]})", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for fname in (PID_FILE_NAME, ENDPOINT_FILE_NAME):
            try:
                (root / fname).unlink()
            except OSError:
                pass
    return 0


def ensure_gateway_server(
    broker_dir: str | Path,
    port: int | None = None,
    host: str = HOST,
    command: list[str] | None = None,
    wait_seconds: float = 3.0,
) -> int:
    """Start or reuse the detached gateway and wait for its health check."""
    root = Path(broker_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    chosen_port = port if port is not None else DEFAULT_PORT
    try:
        from atomic_io import FileLock
    except ImportError:
        FileLock = None
    info = _read_gateway_info(root)
    if info.get("pid") and _pid_alive(int(info.get("pid") or 0)) and int(info.get("port") or 0) == chosen_port:
        if _health_check(chosen_port):
            return int(info["pid"])
    if FileLock is not None:
        lock = FileLock(root / LAUNCH_LOCK_NAME, timeout=5)
        lock.__enter__()
    try:
        info = _read_gateway_info(root)
        if info.get("pid") and _pid_alive(int(info.get("pid") or 0)) and int(info.get("port") or 0) == chosen_port:
            if _health_check(chosen_port):
                return int(info["pid"])
        if command is None:
            command = [sys.executable, str(Path(__file__).resolve()), "--broker-dir", str(root)]
        command = [*command, "--port", str(chosen_port), "--host", host]
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
            info = _read_gateway_info(root)
            if info.get("pid") and _pid_alive(int(info.get("pid") or 0)) and int(info.get("port") or 0) == chosen_port and _health_check(chosen_port):
                return int(info["pid"])
            if process.poll() is not None:
                break
            time.sleep(0.05)
        raise RuntimeError("openai_gateway_failed_to_start")
    finally:
        if FileLock is not None:
            lock.__exit__(None, None, None)


def handle_openai_gateway_cli(argv: list[str]) -> int:
    """bridge openai-serve: start or reuse the detached gateway."""
    parser = argparse.ArgumentParser(prog="bridge openai-serve")
    parser.add_argument("--broker-dir", default=None)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--foreground", action="store_true")
    args, _ = parser.parse_known_args(argv)

    broker_dir = args.broker_dir or os.environ.get("AGENT_BROKER_HOME") or str(Path.home() / ".agent-broker")
    if args.foreground:
        return serve(broker_dir, args.port, args.host)
    pid = ensure_gateway_server(broker_dir, args.port, args.host)
    port_info = _read_gateway_info(Path(broker_dir))
    print(f"Agent Switchboard OpenAI gateway running on http://{args.host}:{port_info.get('port', args.port)} (pid {pid})")
    return 0


if __name__ == "__main__":
    raise SystemExit(handle_openai_gateway_cli(sys.argv[1:]))
