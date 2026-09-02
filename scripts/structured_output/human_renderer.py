"""human_renderer.py — Formats canonical ResultEnvelope into readable Markdown for humans.

Separates human UI rendering from machine consumption.
"""
from __future__ import annotations

from .contract import ResultEnvelope


def render_human_summary(envelope: ResultEnvelope) -> str:
    """Render a clean, human-readable status card from a structured ResultEnvelope."""
    status_icon = {
        "PASS": "✅ PASS",
        "PARTIAL": "⚠️ PARTIAL",
        "FAILED": "❌ FAILED",
        "BLOCKED": "🚫 BLOCKED",
        "REVIEW_REQUIRED": "🔍 REVIEW_REQUIRED",
        "NO_CHANGE": "ℹ️ NO_CHANGE",
    }.get(envelope.status, envelope.status)

    lines = [
        f"### Task: `{envelope.task_id}` [{status_icon}]",
        f"**Summary**: {envelope.summary}",
        f"- **Harness**: `{envelope.harness}` | **Workspace**: `{envelope.workspace_mode}`",
    ]

    if envelope.workspace_path:
        lines.append(f"- **Path**: `{envelope.workspace_path}`")

    if envelope.files_changed:
        lines.append(f"- **Files Changed** ({len(envelope.files_changed)}): " + ", ".join(f"`{f}`" for f in envelope.files_changed[:5]))

    if envelope.validations:
        passed = sum(1 for v in envelope.validations if v.result == "PASS")
        total = len(envelope.validations)
        lines.append(f"- **Validations**: {passed}/{total} passed")
        for v in envelope.validations:
            mark = "✓" if v.result == "PASS" else "✗" if v.result == "FAIL" else "○"
            lines.append(f"  - [{mark}] {v.name} (`{v.command}`) → `{v.result}`")

    if envelope.artifacts:
        lines.append(f"- **Artifacts** ({len(envelope.artifacts)}):")
        for a in envelope.artifacts:
            lines.append(f"  - `{a.path}` ({a.type}) — {a.purpose}")

    if envelope.blockers:
        lines.append(f"- **Blockers**: " + "; ".join(envelope.blockers))

    if envelope.warnings:
        lines.append(f"- **Warnings**: " + "; ".join(envelope.warnings))

    if envelope.next_action:
        lines.append(f"- **Next Action**: {envelope.next_action}")

    return "\n".join(lines)
