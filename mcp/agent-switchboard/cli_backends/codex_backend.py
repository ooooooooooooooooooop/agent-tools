"""codex_backend.py — Codex CLI adapter.

Wraps the existing battle-tested ``consult_codex`` in ``agent_broker_mcp`` (which
drives ``codex exec --sandbox --json -`` with model/effort enforcement and runtime
attestation) behind the unified ``CliBackend`` interface. No CLI invocation logic is
duplicated here; the delegate is the single source of truth for the Codex path.
"""

from __future__ import annotations

from typing import Any

from cli_backend_base import CliBackend, CliResult


class CodexCliBackend(CliBackend):
    @property
    def name(self) -> str:
        return "codex_cli"

    @property
    def aliases(self) -> list[str]:
        return ["codex"]

    @property
    def family(self) -> str:
        return "codex"

    @property
    def description(self) -> str:
        return "OpenAI Codex CLI via `codex exec` (headless)"

    @property
    def capabilities(self) -> list[str]:
        return ["chat", "models", "reasoning", "sandbox", "async", "json", "coding"]

    def discover(self) -> str | None:
        from agent_broker_mcp import load_config, discover_codex

        return discover_codex(load_config())

    def metadata(self) -> dict[str, Any]:
        md = super().metadata()
        md.update(
            {
                "supports_effort": True,
                "supports_sandbox": True,
                "supports_async": True,
                "default_model": "gpt-5.5",
                "default_effort": "xhigh",
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
        from agent_broker_mcp import consult_codex, SYNC_CONSULT_TIMEOUT_SECONDS

        # project_root strongly hints the project passed to consult_codex; keep the
        # normal project resolution semantics by forwarding project_root when given.
        project = kwargs.get("project") or project_root
        result = consult_codex(
            project,
            prompt,
            mode=mode,
            model_name=model,
            effort=effort,
            timeout=timeout if timeout != 300 else SYNC_CONSULT_TIMEOUT_SECONDS,
        )
        return CliResult(
            response=result.response,
            status="completed" if result.model_attested else "model_mismatch",
            requested_model=result.requested_model,
            actual_model=result.actual_model,
            requested_effort=result.requested_effort,
            actual_effort=result.actual_effort,
            model_attested=result.model_attested,
            exit_code=0 if result.model_attested else 2,
            backend=self.name,
            metadata={"attested_by": "runtime-turn-context"},
        )
