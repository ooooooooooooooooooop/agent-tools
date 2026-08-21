"""antigravity_backend.py — Antigravity CLI (agy) adapter.

Wraps ``consult_antigravity_cli`` (``agy --print --output-format json --json-schema``
with work-package validation) behind the unified ``CliBackend`` interface.
"""

from __future__ import annotations

from typing import Any

from cli_backend_base import CliBackend, CliResult


class AntigravityCliBackend(CliBackend):
    @property
    def name(self) -> str:
        return "antigravity_cli"

    @property
    def aliases(self) -> list[str]:
        return ["agy", "antigravity"]

    @property
    def family(self) -> str:
        return "antigravity"

    @property
    def description(self) -> str:
        return "Antigravity Gemini Flash via `agy` (structured, sandboxed)"

    @property
    def capabilities(self) -> list[str]:
        return ["chat", "models", "reasoning", "sandbox", "json", "async"]

    def discover(self) -> str | None:
        from agent_broker_mcp import load_config, discover_antigravity_cli

        return discover_antigravity_cli(load_config())

    def metadata(self) -> dict[str, Any]:
        md = super().metadata()
        md.update(
            {
                "supports_effort": True,
                "supports_sandbox": True,
                "default_model": "gemini-3.6-flash-high",
                "default_effort": "high",
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
        from agent_broker_mcp import (
            consult_antigravity_cli,
            prepare_flash_work_package,
            SYNC_CONSULT_TIMEOUT_SECONDS,
        )

        project = kwargs.get("project") or project_root
        work_package = kwargs.get("work_package")
        response = consult_antigravity_cli(
            project,
            prompt,
            mode=mode,
            model_name=model,
            effort=effort,
            timeout=timeout if timeout != 300 else SYNC_CONSULT_TIMEOUT_SECONDS,
            work_package=work_package,
        )
        status = "completed"
        if response.startswith("Antigravity CLI structured-output validation failed"):
            status = "error"
        return CliResult(
            response=response,
            status=status,
            requested_model=model,
            requested_effort=effort,
            model_attested=(status == "completed"),
            exit_code=0 if status == "completed" else 2,
            backend=self.name,
            metadata={"validated": status == "completed"},
        )