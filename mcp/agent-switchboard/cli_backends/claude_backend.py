"""claude_backend.py — Claude Code CLI adapter.

Wraps the existing ``consult_claude`` (``claude -p --safe-mode --stream-json ...``)
behind the unified ``CliBackend`` interface. All invocation/attestation/fallback logic
remains in the delegate.
"""

from __future__ import annotations

from typing import Any

from cli_backend_base import CliBackend, CliResult


class ClaudeCliBackend(CliBackend):
    @property
    def name(self) -> str:
        return "claude_code"

    @property
    def aliases(self) -> list[str]:
        return ["claude"]

    @property
    def family(self) -> str:
        return "claude"

    @property
    def description(self) -> str:
        return "Claude Code CLI via `claude -p` (headless)"

    @property
    def capabilities(self) -> list[str]:
        return ["chat", "models", "reasoning", "streaming", "sandbox", "async", "coding", "tool_use"]

    def discover(self) -> str | None:
        from agent_broker_mcp import load_config, find_executable

        cfg = load_config()
        return find_executable(cfg, "claude_path", ["claude", "claude.cmd", "claude.ps1"])

    def metadata(self) -> dict[str, Any]:
        md = super().metadata()
        md.update(
            {
                "supports_effort": True,
                "supports_streaming": True,
                "supports_async": True,
                "default_model": "fable",
                "default_effort": "max",
            }
        )
        return md

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
        from agent_broker_mcp import consult_claude, SYNC_CONSULT_TIMEOUT_SECONDS

        project = kwargs.get("project") or project_root
        result = consult_claude(
            project,
            prompt,
            mode=mode,
            model_name=model,
            effort=effort,
            timeout=timeout if timeout != 300 else SYNC_CONSULT_TIMEOUT_SECONDS,
        )
        status = "completed"
        if not result.model_attested:
            status = "model_mismatch"
        return CliResult(
            response=result.response,
            status=status,
            requested_model=result.requested_model,
            actual_model=result.actual_model,
            model_attested=result.model_attested,
            exit_code=0 if result.model_attested else 2,
            backend=self.name,
            metadata={
                "initial_model": result.initial_model,
                "attempted_models": list(result.attempted_models),
                "fallback_reason": result.fallback_reason,
            },
        )