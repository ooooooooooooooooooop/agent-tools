"""contract.py — Personal AI Result Envelope & Status Contract.

Defines the canonical machine-readable result schema across DSH, Codex, Claude Code,
and Gemini CLI. Separates machine consumption from human-readable rendering.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

CURRENT_SCHEMA_VERSION = 1


class ExecutionStatus(str, Enum):
    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    NO_CHANGE = "NO_CHANGE"


class ValidationOutcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_RUN = "NOT_RUN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass
class ArtifactRecord:
    type: str  # e.g., "code_diff", "report", "test_log", "binary", "data"
    path: str
    purpose: str
    hash: str = ""
    is_durable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationRecord:
    name: str
    command: str
    result: str  # PASS | FAIL | NOT_RUN | NOT_APPLICABLE
    exit_code: int | None = None
    evidence: str = ""
    scope: str = "unit"  # unit | integration | lint | static_analysis | manual

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResultEnvelope:
    task_id: str
    status: str  # Must be one of ExecutionStatus
    harness: str  # "dsh" | "codex" | "claude" | "gemini"
    summary: str
    schema_version: int = CURRENT_SCHEMA_VERSION
    workspace_mode: str = "CURRENT_WORKSPACE"
    workspace_path: str = ""
    files_changed: list[str] = field(default_factory=list)
    commits: list[str] = field(default_factory=list)
    artifacts: list[ArtifactRecord] = field(default_factory=list)
    validations: list[ValidationRecord] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    next_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["artifacts"] = [a.to_dict() if isinstance(a, ArtifactRecord) else a for a in self.artifacts]
        d["validations"] = [v.to_dict() if isinstance(v, ValidationRecord) else v for v in self.validations]
        return d

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


# JSON Schema for ResultEnvelope (used for Claude --json-schema and DSH workflow schema)
RESULT_ENVELOPE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "integer"},
        "task_id": {"type": "string"},
        "status": {
            "type": "string",
            "enum": ["PASS", "PARTIAL", "FAILED", "BLOCKED", "REVIEW_REQUIRED", "NO_CHANGE"],
        },
        "harness": {"type": "string"},
        "workspace_mode": {"type": "string"},
        "workspace_path": {"type": "string"},
        "summary": {"type": "string"},
        "files_changed": {
            "type": "array",
            "items": {"type": "string"},
        },
        "commits": {
            "type": "array",
            "items": {"type": "string"},
        },
        "artifacts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "path": {"type": "string"},
                    "purpose": {"type": "string"},
                    "hash": {"type": "string"},
                    "is_durable": {"type": "boolean"},
                },
                "required": ["type", "path", "purpose"],
            },
        },
        "validations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "command": {"type": "string"},
                    "result": {"type": "string", "enum": ["PASS", "FAIL", "NOT_RUN", "NOT_APPLICABLE"]},
                    "exit_code": {"type": ["integer", "null"]},
                    "evidence": {"type": "string"},
                    "scope": {"type": "string"},
                },
                "required": ["name", "command", "result"],
            },
        },
        "blockers": {
            "type": "array",
            "items": {"type": "string"},
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
        },
        "provenance": {"type": "object"},
        "next_action": {"type": "string"},
    },
    "required": ["task_id", "status", "harness", "summary"],
}
