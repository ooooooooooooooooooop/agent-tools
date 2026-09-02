"""Personal AI Structured Output & Result Envelope Package."""
from __future__ import annotations

from .contract import (
    CURRENT_SCHEMA_VERSION,
    RESULT_ENVELOPE_JSON_SCHEMA,
    ArtifactRecord,
    ExecutionStatus,
    ResultEnvelope,
    ValidationOutcome,
    ValidationRecord,
)
from .harness_adapters import (
    ClaudeStructuredAdapter,
    CodexStructuredAdapter,
    DshStructuredAdapter,
    GeminiStructuredAdapter,
    StructuredExecutionOrchestrator,
)
from .human_renderer import render_human_summary
from .validator import (
    ParseValidationResult,
    is_downstream_mutation_safe,
    validate_result_envelope,
)

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "RESULT_ENVELOPE_JSON_SCHEMA",
    "ExecutionStatus",
    "ValidationOutcome",
    "ArtifactRecord",
    "ValidationRecord",
    "ResultEnvelope",
    "validate_result_envelope",
    "is_downstream_mutation_safe",
    "ParseValidationResult",
    "ClaudeStructuredAdapter",
    "DshStructuredAdapter",
    "CodexStructuredAdapter",
    "GeminiStructuredAdapter",
    "StructuredExecutionOrchestrator",
    "render_human_summary",
]
