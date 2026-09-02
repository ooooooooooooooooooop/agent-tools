"""harness_adapters.py — Harness-specific Structured Output Adapters & Retry Orchestrator.

Handles schema injection, formatting, execution wrapping, and capped retry logic.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from .contract import (
    RESULT_ENVELOPE_JSON_SCHEMA,
    ResultEnvelope,
)
from .validator import (
    ParseValidationResult,
    is_downstream_mutation_safe,
    validate_result_envelope,
)

DEFAULT_RETRY_CAP = 2


class ClaudeStructuredAdapter:
    @staticmethod
    def get_json_schema_arg() -> str:
        """Return the CLI parameter value for Claude Code --json-schema."""
        return json.dumps(RESULT_ENVELOPE_JSON_SCHEMA, ensure_ascii=False)

    @staticmethod
    def build_flags(schema_required: bool = True) -> list[str]:
        if not schema_required:
            return []
        return ["--json-schema", ClaudeStructuredAdapter.get_json_schema_arg()]


class DshStructuredAdapter:
    @staticmethod
    def get_workflow_schema() -> dict[str, Any]:
        """Return the JSON schema definition for DSH workflow agent(prompt, {schema})."""
        return RESULT_ENVELOPE_JSON_SCHEMA

    @staticmethod
    def format_subagent_prompt(task_id: str, prompt: str, schema_required: bool = True) -> str:
        if not schema_required:
            return prompt
        instruction = (
            f"\n\n[STRUCTURED OUTPUT REQUIRED]\n"
            f"You must deliver your final output as a valid JSON object matching the ResultEnvelope schema.\n"
            f"Task ID: {task_id}\n"
            f"Schema structure: {{task_id, status (PASS|PARTIAL|FAILED|BLOCKED|REVIEW_REQUIRED|NO_CHANGE), "
            f"harness: 'dsh', summary, files_changed: [], commits: [], artifacts: [], validations: [], blockers: [], warnings: []}}\n"
        )
        return prompt + instruction


class CodexStructuredAdapter:
    @staticmethod
    def format_codex_prompt(task_id: str, prompt: str, schema_required: bool = True) -> str:
        if not schema_required:
            return prompt
        return (
            f"{prompt}\n\n"
            f"Deliver the final result in canonical JSON format:\n"
            f"```json\n"
            f"{{\n"
            f'  "task_id": "{task_id}",\n'
            f'  "status": "PASS",\n'
            f'  "harness": "codex",\n'
            f'  "summary": "<short description>",\n'
            f'  "files_changed": [],\n'
            f'  "commits": [],\n'
            f'  "artifacts": [],\n'
            f'  "validations": [],\n'
            f'  "blockers": []\n'
            f"}}\n"
            f"```"
        )


class GeminiStructuredAdapter:
    @staticmethod
    def build_flags(schema_required: bool = True) -> list[str]:
        if not schema_required:
            return []
        return ["--output-format", "json"]

    @staticmethod
    def format_gemini_prompt(task_id: str, prompt: str, schema_required: bool = True) -> str:
        if not schema_required:
            return prompt
        return (
            f"{prompt}\n\n"
            f"Respond with a JSON object adhering to ResultEnvelope (task_id='{task_id}', status, harness='gemini', summary, validations, artifacts)."
        )


class StructuredExecutionOrchestrator:
    """Orchestrates worker execution, validation, and capped error correction retry."""

    def __init__(self, retry_cap: int = DEFAULT_RETRY_CAP) -> None:
        self.retry_cap = retry_cap

    def execute_with_retry(
        self,
        task_id: str,
        runner: Callable[[str | None], str | dict[str, Any]],
        *,
        schema_required: bool = True,
    ) -> tuple[ParseValidationResult, int]:
        """Run execution function with schema validation and retry loop up to retry_cap."""
        correction_prompt: str | None = None
        attempts = 0
        last_result: ParseValidationResult | None = None

        while attempts <= self.retry_cap:
            attempts += 1
            raw_output = runner(correction_prompt)
            if not schema_required:
                # Optional structured output mode: wrap into default pass envelope
                summary_text = str(raw_output)[:300]
                env = ResultEnvelope(
                    task_id=task_id,
                    status="PASS",
                    harness="generic",
                    summary=summary_text,
                )
                return ParseValidationResult(is_valid=True, envelope=env), attempts

            val = validate_result_envelope(raw_output)
            last_result = val
            if val.is_valid:
                return val, attempts

            # Prepare correction feedback for the model
            err_msg = "; ".join(val.errors)
            correction_prompt = (
                f"Your previous output failed schema validation:\n"
                f"Errors: {err_msg}\n"
                f"Please fix and return the output strictly matching the ResultEnvelope JSON format."
            )

        # Cap reached
        return last_result, attempts
