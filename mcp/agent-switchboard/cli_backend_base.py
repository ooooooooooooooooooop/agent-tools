"""cli_backend_base.py — dependency-free CLI adapter abstraction for Agent Switchboard.

Agent Switchboard drives several local CLIs (codex, claude, agy, gemini), each with
hand-written discovery, argument construction, output parsing, and model attestation.
This module introduces the minimal shared contract so:

  * every CLI surface is reachable through one uniform ``CliBackend`` interface and a
    global ``CliRegistry`` (registered by name + aliases);
  * new / user-local CLIs can be added either in code (subclass the ABC) or purely from
    ``config.json`` via ``GenericCliBackend`` (see cli_backends/generic.py);
  * the existing battle-tested ``consult_*`` paths remain the real implementations for the
    four built-in families — the adapters delegate to them, so nothing regresses.

This module is intentionally stdlib-only and side-effect free (it never touches the
filesystem or subprocess itself); all discovery/execution stays in the backend
implementations and callers.
"""

from __future__ import annotations

import shlex
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class CliResult:
    """Canonical outcome of a CLI consult, shared by every backend."""

    response: str
    status: str = "completed"  # completed | timeout | error | model_mismatch | cli_not_found
    requested_model: str | None = None
    actual_model: str | None = None
    requested_effort: str | None = None
    actual_effort: str | None = None
    model_attested: bool = True
    exit_code: int = 0
    raw_stdout: str = ""
    raw_stderr: str = ""
    backend: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "response": self.response,
            "status": self.status,
            "requested_model": self.requested_model,
            "actual_model": self.actual_model,
            "requested_effort": self.requested_effort,
            "actual_effort": self.actual_effort,
            "model_attested": self.model_attested,
            "exit_code": self.exit_code,
            "backend": self.backend,
            "metadata": self.metadata,
        }


class CliBackend(ABC):
    """Uniform interface every CLI adapter implements."""

    #: Stable unique name (used as registry key and route target_agent).
    @property
    @abstractmethod
    def name(self) -> str: ...

    #: Extra names the router may accept for this backend.
    @property
    def aliases(self) -> list[str]:
        return []

    #: Family bucket: "codex" | "claude" | "antigravity" | "gemini" | "custom".
    @property
    def family(self) -> str:
        return "custom"

    #: Human-readable one-line description for diagnostics / list_agent_models.
    @property
    def description(self) -> str:
        return self.name

    def discover(self) -> str | None:
        """Return the resolved CLI executable path, or None if unavailable.

        Optional; GenericCliBackend and the built-in delegates override it. The
        default returns None so a backend that cannot be found reports cleanly.
        """
        return None

    def available(self) -> bool:
        return self.discover() is not None

    def build_command(
        self,
        _executable: str,
        prompt: str,
        model: str | None = None,
        effort: str | None = None,
        mode: str = "read-only",
        project_root: str | None = None,
        **kwargs: Any,
    ) -> list[str]:
        """Return the argv to invoke this CLI for the given inputs."""
        return []

    def parse_output(self, stdout: str, stderr: str, exit_code: int) -> CliResult:
        """Turn raw CLI output into a CliResult. Override per backend."""
        return CliResult(
            response=(stdout or stderr or "").strip() or "(no output)",
            status="completed" if exit_code == 0 else "error",
            exit_code=exit_code,
            raw_stdout=stdout,
            raw_stderr=stderr,
            backend=self.name,
        )

    def attest_model(self, requested: str | None, actual: str | None) -> bool:
        """Whether ``actual`` satisfies ``requested`` for this CLI."""
        if not requested or not actual:
            return actual is None
        return str(requested).strip().lower() == str(actual).strip().lower()

    def resolve_effort(self, hint: str | None, task_kind: str | None = None) -> str | None:
        return hint

    def resolve_model(self, alias: str, context: dict | None = None) -> str:
        return alias

    # -- declared capabilities (declarative, aligned with qiaomu's capabilities[]) --
    @property
    def backend_type(self) -> str:
        """How this backend is invoked: ``cli`` (arg-vector subprocess) or
        ``http`` (e.g. OpenAI-compat local serve)."""
        return "cli"

    @property
    def capabilities(self) -> list[str]:
        """Declarative capability tags for routing/discovery. Vocabulary:
        chat, reasoning, models, streaming, sandbox, json, async, tool_use, coding.
        Subclasses override to declare their exact capability set."""
        caps = ["chat"]
        if self.backend_type == "cli":
            caps.append("models")
        return caps

    def metadata(self) -> dict[str, Any]:
        md = {
            "name": self.name,
            "aliases": list(self.aliases),
            "family": self.family,
            "description": self.description,
            "backend_type": self.backend_type,
            "capabilities": list(self.capabilities),
            "default_model": None,
            "default_effort": None,
        }
        # Keep the legacy supports_* flags for backward compatibility with any caller.
        md.update(
            {
                "supports_model": "models" in md["capabilities"],
                "supports_streaming": "streaming" in md["capabilities"],
                "supports_sandbox": "sandbox" in md["capabilities"],
                "supports_async": "async" in md["capabilities"],
            }
        )
        return md

    # -- template method: execute a consult end to end --------------------------
    def execute(
        self,
        prompt: str,
        model: str | None = None,
        effort: str | None = None,
        mode: str = "read-only",
        project_root: str | None = None,
        timeout: int = 300,
        **kwargs: Any,
    ) -> CliResult:
        """Template method: discover -> build_command -> (caller runs subprocess)
        -> parse_output -> attest. Backends may override entirely.

        The default leaves execution to the caller-provided ``runner`` callable in
        kwargs, or, when absent, is a pure build+parse scaffold that subclasses fill in
        by overriding ``parse_output`` and supplying a ``runner``. Built-in delegates
        override ``execute`` directly to preserve their battle-tested code.
        """
        requested_model = self.resolve_model(model) if model else model
        requested_effort = self.resolve_effort(effort)
        exe = self.discover()
        if not exe:
            return CliResult(
                response=f"{self.name} CLI was not found.",
                status="cli_not_found",
                requested_model=requested_model,
                requested_effort=requested_effort,
                backend=self.name,
            )
        command = self.build_command(
            exe, prompt, requested_model, requested_effort, mode, project_root, **kwargs
        )
        runner = kwargs.get("runner")
        if runner is None:
            return CliResult(
                response="No runner provided for backend execution.",
                status="error",
                requested_model=requested_model,
                requested_effort=requested_effort,
                exit_code=-1,
                backend=self.name,
            )
        exit_code, stdout, stderr = runner(command, project_root, timeout=timeout)
        result = self.parse_output(stdout, stderr, exit_code)
        result.requested_model = requested_model or result.requested_model
        result.requested_effort = requested_effort or result.requested_effort
        if result.status == "completed" and requested_model:
            result.model_attested = self.attest_model(
                requested_model, result.actual_model
            )
            if not result.model_attested:
                result.status = "model_mismatch"
                result.response = (
                    f"Model attestation failed: requested '{requested_model}' but the "
                    f"{self.name} runtime reported '{result.actual_model or 'unknown'}'.\n\n"
                    f"{result.response}"
                )
        return result


class CliRegistry:
    """Global registry of CliBackend adapters, keyed by name and aliases.

    Threading note: the MCP server is single-threaded per stdio loop, but the async
    workers run in separate processes, so the registry is immutable-after-build for
    safety. Registration mutates before serve; lookup is read-only.
    """

    def __init__(self) -> None:
        self._by_name: dict[str, CliBackend] = {}
        self._by_alias: dict[str, str] = {}
        self._order: list[str] = []

    def register(self, backend: CliBackend) -> None:
        key = backend.name.strip().lower()
        if not key:
            raise ValueError("backend name must not be empty")
        if key not in self._by_name:
            self._order.append(key)
        self._by_name[key] = backend
        for alias in backend.aliases:
            a = str(alias).strip().lower()
            if a and a != key:
                self._by_alias.setdefault(a, key)

    def get(self, name: str | None) -> CliBackend | None:
        if not name:
            return None
        key = str(name).strip().lower()
        if key in self._by_name:
            return self._by_name[key]
        resolved = self._by_alias.get(key)
        return self._by_name.get(resolved) if resolved else None

    def names(self) -> list[str]:
        return list(self._order)

    def all(self) -> list[CliBackend]:
        return [self._by_name[name] for name in self._order]

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.get(name) is not None


# ---------------------------------------------------------------------------
# Shared command-vector helpers (no filesystem/subprocess side effects).
# ---------------------------------------------------------------------------

def shell_split_field(value: Any) -> list[str]:
    """Safely split a user-supplied CLI arg string into a list via shlex."""
    if not value:
        return []
    try:
        return shlex.split(str(value))
    except ValueError:
        return [str(value)]


def substitute_template(
    tokens: list[str],
    *,
    prompt: str,
    model: str | None,
    effort: str | None,
    timeout: int,
    project_root: str | None,
) -> list[str]:
    """Expand ``{prompt}``, ``{model}``, ``{effort}``, ``{timeout}``, ``{project}``
    placeholders in an args_template. Unknown placeholders are left verbatim so a
    template author sees them instead of silently getting empty text."""
    mapping = {
        "{prompt}": prompt,
        "{model}": model or "",
        "{effort}": effort or "",
        "{timeout}": str(int(timeout)),
        "{project}": project_root or "",
    }
    out: list[str] = []
    for token in tokens:
        piece = token
        for key, value in mapping.items():
            piece = piece.replace(key, value)
        out.append(piece)
    return out
