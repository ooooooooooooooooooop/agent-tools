"""candidate_extractor.py — Extracts candidate durable memories from trusted events.

Extracts memory candidates from ResultEnvelope, Project State, and user declarations.
Enforces that ephemeral task noise is rejected and durable decisions/preferences are preserved.
"""
from __future__ import annotations

import re
from typing import Any


class MemoryCandidateExtractor:
    @staticmethod
    def from_result_envelope(envelope: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract high-value durable memory candidates from a PASS ResultEnvelope."""
        candidates = []
        status = envelope.get("status")
        if status not in ("PASS", "NO_CHANGE"):
            return []

        task_id = envelope.get("task_id", "")
        summary = envelope.get("summary", "")
        harness = envelope.get("harness", "")
        next_action = envelope.get("next_action", "")

        # Look for architectural decisions or durable facts in summary / next_action
        if summary and ("architecture" in summary.lower() or "decision" in summary.lower() or "adopted" in summary.lower() or "convention" in summary.lower()):
            candidates.append({
                "scope": "global",
                "type": "decision",
                "subject": f"Decision from {task_id}",
                "content": summary,
                "confidence": "high",
                "retention": "keep",
                "provenance": {
                    "source": f"result_envelope:{task_id}",
                    "harness": harness,
                },
            })

        return candidates

    @staticmethod
    def from_user_statement(text: str, scope: str = "personal") -> dict[str, Any] | None:
        """Extract explicit user preference or rule statement."""
        preference_triggers = [
            r"记住[：:\s]*(.*)",
            r"以后[都也要请]*(.*)",
            r"从今以后[，,\s]*(.*)",
            r"我的习惯是[：:\s]*(.*)",
            r"长期规则[：:\s]*(.*)",
        ]
        for pat in preference_triggers:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                extracted = m.group(1).strip()
                if extracted:
                    return {
                        "scope": scope,
                        "type": "preference",
                        "subject": f"User preference: {extracted[:30]}",
                        "content": extracted,
                        "confidence": "high",
                        "retention": "keep",
                        "is_explicit": True,
                        "provenance": {"source": "user_statement", "raw": text},
                    }
        return None
