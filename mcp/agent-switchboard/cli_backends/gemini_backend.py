"""gemini_backend.py — Gemini CLI adapter.

Wraps ``consult_gemini`` (``gemini -m <model> -p``, or the off-by-default API escape
hatch) behind the unified ``CliBackend`` interface.
"""

from __future__ import annotations

from typing import Any

from cli_backend_base import CliBackend, CliResult


class GeminiCliBackend(CliBackend):
    @property
    def name(self) -> str:
        return "gemini_cli"

    @property
    def aliases(self) -> list[str]:
        return ["gemini"]

    @property
    def family(self) -> str:
        return "gemini"

    @property
    def description(self) -> str:
        return "Standalone Gemini CLI via `gemini -p`"

    @property
    def capabilities(self) -> list[str]:
        return ["chat", "models"]

    def discover(self) -> str | None:
        from agent_broker_mcp import load_config, find_executable

        cfg = load_config()
        return find_executable(cfg, "gemini_path", ["gemini", "gemini.cmd", "gemini.ps1"])

    def metadata(self) -> dict[str, Any]:
        md = super().metadata()
        md.update(
            {
                "supports_effort": False,
                "default_model": "gemini-2.5-pro",
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
        from agent_broker_mcp import consult_gemini, SYNC_CONSULT_TIMEOUT_SECONDS

        project = kwargs.get("project") or project_root
        response = consult_gemini(
            project,
            prompt,
            mode=mode,
            model_name=model,
            timeout=timeout if timeout != 300 else SYNC_CONSULT_TIMEOUT_SECONDS,
        )
        status = "completed"
        if not response or response.startswith("Gemini CLI exited") or "is not configured" in response:
            status = "error"
        return CliResult(
            response=response,
            status=status,
            requested_model=model,
            model_attested=(status == "completed"),
            exit_code=0 if status == "completed" else 2,
            backend=self.name,
        )