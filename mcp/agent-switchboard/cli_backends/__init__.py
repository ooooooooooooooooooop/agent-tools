"""cli_backends — pluggable CLI adapters for Agent Switchboard.

Built-in families (codex, claude, agy, gemini) wrap the existing battle-tested
``consult_*`` functions in ``agent_broker_mcp`` so the unified ``CliBackend``
interface exposes them without regressing their behavior. The four are registered
by default by :func:`register_builtin_backends`.

User-local CLIs can be registered from ``config.json`` via :class:`GenericCliBackend`
(:func:`backends_from_config`), letting a user flexibly take any local CLI that speaks
"give me a prompt, I return text".
"""

from __future__ import annotations

from typing import Any

from cli_backend_base import CliBackend, CliRegistry

from .antigravity_backend import AntigravityCliBackend
from .claude_backend import ClaudeCliBackend
from .codex_backend import CodexCliBackend
from .gemini_backend import GeminiCliBackend
from .generic import GenericCliBackend


def register_builtin_backends(registry: CliRegistry) -> None:
    """Register the four built-in family adapters onto ``registry`` (idempotent)."""
    for backend in (
        CodexCliBackend(),
        ClaudeCliBackend(),
        AntigravityCliBackend(),
        GeminiCliBackend(),
    ):
        if registry.get(backend.name) is None:
            registry.register(backend)


def backends_from_config(config: dict[str, Any]) -> list[GenericCliBackend]:
    """Build GenericCliBackend instances from the ``cli_backends`` config block.

    Shape (each value is one CLI to register)::

        "cli_backends": {
          "ollama": {
            "command": ["ollama", "run"],
            "args_template": ["{model}", "{prompt}"],
            "model_aliases": {"llama3": "llama3:70b"},
            "default_model": "llama3:70b",
            "description": "Local Ollama",
            ...
          }
        }

    Returns the list of adapters (empty when the block is absent/not a dict).
    """
    block = config.get("cli_backends") or {}
    if not isinstance(block, dict):
        return []
    backends: list[GenericCliBackend] = []
    for name, spec in block.items():
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(spec, dict):
            continue
        try:
            backends.append(GenericCliBackend(name, spec))
        except (TypeError, ValueError):
            continue
    return backends
