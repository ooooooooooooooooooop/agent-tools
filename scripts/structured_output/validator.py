"""validator.py — Shared Schema Validator & Fail-Closed Semantic Checker.

Single shared validator across DSH, Codex, Claude Code, Gemini CLI, and Switchboard.
Parses, validates syntax and semantic constraints, and guards downstream execution.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .contract import (
    ArtifactRecord,
    ExecutionStatus,
    ResultEnvelope,
    ValidationOutcome,
    ValidationRecord,
)

VALID_STATUSES = {s.value for s in ExecutionStatus}
VALID_OUTCOMES = {o.value for o in ValidationOutcome}


@dataclass
class ParseValidationResult:
    is_valid: bool
    envelope: ResultEnvelope | None = None
    errors: list[str] = None
    raw_parsed: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def extract_json_payload(raw_text: str) -> dict[str, Any]:
    """Extract JSON payload from raw text, code block, or stream."""
    text = raw_text.strip()
    if not text:
        raise ValueError("empty output received")

    # 1. Direct JSON parse
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # 2. Markdown json code block
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    # 3. First balanced JSON object scan
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    raise ValueError("no valid JSON object found in output")


def validate_result_envelope(raw_input: str | dict[str, Any]) -> ParseValidationResult:
    """Validate raw harness output into a canonical ResultEnvelope."""
    errors: list[str] = []
    data: dict[str, Any] = {}

    if isinstance(raw_input, dict):
        data = raw_input
    else:
        try:
            data = extract_json_payload(raw_input)
        except Exception as exc:
            return ParseValidationResult(is_valid=False, errors=[f"JSON_PARSE_ERROR: {exc}"])

    # Required top-level fields
    for req in ("task_id", "status", "harness", "summary"):
        if not data.get(req):
            errors.append(f"MISSING_REQUIRED_FIELD: '{req}'")

    # Status check
    status = str(data.get("status", "")).upper()
    if status not in VALID_STATUSES:
        errors.append(f"INVALID_STATUS: '{data.get('status')}' (must be one of {sorted(VALID_STATUSES)})")

    # Validations semantic check
    raw_validations = data.get("validations", [])
    validations: list[ValidationRecord] = []
    if not isinstance(raw_validations, list):
        errors.append("INVALID_FIELD_TYPE: 'validations' must be an array")
    else:
        for idx, v in enumerate(raw_validations):
            if not isinstance(v, dict):
                errors.append(f"INVALID_VALIDATION_ENTRY [{idx}]: not an object")
                continue
            if not v.get("name") or not v.get("command") or not v.get("result"):
                errors.append(f"INVALID_VALIDATION_ENTRY [{idx}]: missing name, command, or result")
                continue
            v_res = str(v.get("result")).upper()
            if v_res not in VALID_OUTCOMES:
                errors.append(f"INVALID_VALIDATION_RESULT [{idx}]: '{v.get('result')}' (must be one of {sorted(VALID_OUTCOMES)})")
            validations.append(
                ValidationRecord(
                    name=str(v["name"]),
                    command=str(v["command"]),
                    result=v_res,
                    exit_code=v.get("exit_code"),
                    evidence=str(v.get("evidence", "")),
                    scope=str(v.get("scope", "unit")),
                )
            )

    # Artifacts semantic check
    raw_artifacts = data.get("artifacts", [])
    artifacts: list[ArtifactRecord] = []
    if not isinstance(raw_artifacts, list):
        errors.append("INVALID_FIELD_TYPE: 'artifacts' must be an array")
    else:
        for idx, a in enumerate(raw_artifacts):
            if not isinstance(a, dict):
                errors.append(f"INVALID_ARTIFACT_ENTRY [{idx}]: not an object")
                continue
            if not a.get("type") or not a.get("path") or not a.get("purpose"):
                errors.append(f"INVALID_ARTIFACT_ENTRY [{idx}]: missing type, path, or purpose")
                continue
            artifacts.append(
                ArtifactRecord(
                    type=str(a["type"]),
                    path=str(a["path"]),
                    purpose=str(a["purpose"]),
                    hash=str(a.get("hash", "")),
                    is_durable=bool(a.get("is_durable", True)),
                )
            )

    # Blocker status semantic alignment
    blockers = [str(b) for b in (data.get("blockers") or [])]
    if status == "BLOCKED" and not blockers:
        errors.append("SEMANTIC_ERROR: status is BLOCKED but 'blockers' list is empty")
    if status == "PASS":
        failed_tests = [v.name for v in validations if v.result == "FAIL"]
        if failed_tests:
            errors.append(f"SEMANTIC_ERROR: status is PASS but validations failed: {failed_tests}")

    if errors:
        return ParseValidationResult(is_valid=False, errors=errors, raw_parsed=data)

    envelope = ResultEnvelope(
        task_id=str(data["task_id"]),
        status=status,
        harness=str(data["harness"]),
        summary=str(data["summary"]),
        schema_version=int(data.get("schema_version", 1)),
        workspace_mode=str(data.get("workspace_mode", "CURRENT_WORKSPACE")),
        workspace_path=str(data.get("workspace_path", "")),
        files_changed=[str(f) for f in (data.get("files_changed") or [])],
        commits=[str(c) for c in (data.get("commits") or [])],
        artifacts=artifacts,
        validations=validations,
        blockers=blockers,
        warnings=[str(w) for w in (data.get("warnings") or [])],
        provenance=data.get("provenance") if isinstance(data.get("provenance"), dict) else {},
        next_action=str(data.get("next_action", "")),
    )
    return ParseValidationResult(is_valid=True, envelope=envelope, raw_parsed=data)


def is_downstream_mutation_safe(validation_result: ParseValidationResult) -> tuple[bool, str]:
    """Fail-closed check for downstream orchestration.

    If result envelope is invalid, blocked, or failed, automated downstream mutations are blocked.
    """
    if not validation_result.is_valid or not validation_result.envelope:
        return False, f"RESULT_SCHEMA_INVALID: {'; '.join(validation_result.errors)}"

    env = validation_result.envelope
    if env.status in ("FAILED", "BLOCKED", "REVIEW_REQUIRED"):
        return False, f"STATUS_NOT_ACTIONABLE: status={env.status} blockers={env.blockers}"

    # Verify if tests failed
    for v in env.validations:
        if v.result == "FAIL":
            return False, f"VALIDATION_FAILED: test '{v.name}' exited with failure ({v.evidence})"

    return True, "SAFE"
