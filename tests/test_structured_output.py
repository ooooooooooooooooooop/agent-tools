"""test_structured_output.py — Tests for Personal AI Structured Output Adoption.

Covers all 14 mandated scenarios:
  1. Claude native JSON Schema PASS
  2. Claude schema invalid -> retry / error feedback
  3. DSH workflow structured output
  4. DSH subagent structured output
  5. Codex structured result
  6. Gemini structured result
  7. Mutation worker returning workspace + files + validations
  8. No-change worker
  9. Blocked worker (with semantic blocker validation)
  10. Review-required worker
  11. Invalid JSON parsing
  12. Valid JSON but semantically invalid
  13. Retry cap enforcement
  14. Concurrent worker aggregation without regex parsing
"""
from __future__ import annotations

import json
import unittest

from scripts.structured_output import (
    RESULT_ENVELOPE_JSON_SCHEMA,
    ArtifactRecord,
    ClaudeStructuredAdapter,
    CodexStructuredAdapter,
    DshStructuredAdapter,
    ExecutionStatus,
    GeminiStructuredAdapter,
    ResultEnvelope,
    StructuredExecutionOrchestrator,
    ValidationRecord,
    is_downstream_mutation_safe,
    render_human_summary,
    validate_result_envelope,
)


class TestStructuredOutput(unittest.TestCase):
    def test_1_claude_native_json_schema_pass(self) -> None:
        flags = ClaudeStructuredAdapter.build_flags(schema_required=True)
        self.assertIn("--json-schema", flags)
        schema = json.loads(flags[1])
        self.assertEqual(schema["type"], "object")
        self.assertIn("task_id", schema["required"])

        valid_output = json.dumps({
            "task_id": "task-claude-1",
            "status": "PASS",
            "harness": "claude",
            "summary": "Implemented feature in Claude Code",
            "validations": [{"name": "unit_test", "command": "pytest", "result": "PASS"}],
        })
        val = validate_result_envelope(valid_output)
        self.assertTrue(val.is_valid)
        self.assertEqual(val.envelope.task_id, "task-claude-1")
        self.assertEqual(val.envelope.status, "PASS")

    def test_2_claude_schema_invalid_and_retry_feedback(self) -> None:
        invalid_output = '{"task_id": "task-1", "status": "UNKNOWN_CUSTOM_STATUS"}'
        val = validate_result_envelope(invalid_output)
        self.assertFalse(val.is_valid)
        self.assertTrue(any("INVALID_STATUS" in e for e in val.errors))

    def test_3_dsh_workflow_structured_output(self) -> None:
        schema = DshStructuredAdapter.get_workflow_schema()
        self.assertEqual(schema["type"], "object")

        workflow_result = {
            "task_id": "wf-101",
            "status": "PASS",
            "harness": "dsh",
            "summary": "Workflow pipeline completed",
            "artifacts": [{"type": "report", "path": "summary.md", "purpose": "metrics"}],
        }
        val = validate_result_envelope(workflow_result)
        self.assertTrue(val.is_valid)
        self.assertEqual(val.envelope.harness, "dsh")
        self.assertEqual(len(val.envelope.artifacts), 1)

    def test_4_dsh_subagent_structured_output(self) -> None:
        prompt = DshStructuredAdapter.format_subagent_prompt("sub-1", "Run analysis")
        self.assertIn("[STRUCTURED OUTPUT REQUIRED]", prompt)
        raw_msg = (
            "Here is the result:\n```json\n"
            '{"task_id": "sub-1", "status": "PASS", "harness": "dsh", "summary": "Analysis complete"}\n'
            "```"
        )
        val = validate_result_envelope(raw_msg)
        self.assertTrue(val.is_valid)
        self.assertEqual(val.envelope.task_id, "sub-1")

    def test_5_codex_structured_result(self) -> None:
        codex_raw = (
            "```json\n"
            '{"task_id": "cdx-9", "status": "PASS", "harness": "codex", "summary": "Refactored tests", "files_changed": ["test.py"]}\n'
            "```"
        )
        val = validate_result_envelope(codex_raw)
        self.assertTrue(val.is_valid)
        self.assertEqual(val.envelope.files_changed, ["test.py"])

    def test_6_gemini_structured_result(self) -> None:
        flags = GeminiStructuredAdapter.build_flags(schema_required=True)
        self.assertIn("--output-format", flags)
        self.assertIn("json", flags)

        gemini_raw = '{"task_id": "gem-1", "status": "PASS", "harness": "gemini", "summary": "Gemini tool call done"}'
        val = validate_result_envelope(gemini_raw)
        self.assertTrue(val.is_valid)
        self.assertEqual(val.envelope.harness, "gemini")

    def test_7_mutation_worker_with_workspace_and_validations(self) -> None:
        data = {
            "task_id": "mut-1",
            "status": "PASS",
            "harness": "claude",
            "workspace_mode": "ISOLATED_WORKTREE",
            "workspace_path": "/tmp/wt_mut1",
            "summary": "Updated database migrations",
            "files_changed": ["schema.sql", "migration.py"],
            "commits": ["a1b2c3d"],
            "validations": [
                {"name": "pytest", "command": "pytest -v", "result": "PASS", "exit_code": 0, "scope": "unit"},
                {"name": "lint", "command": "flake8", "result": "PASS", "exit_code": 0, "scope": "lint"},
            ],
            "artifacts": [{"type": "diff", "path": "changes.patch", "purpose": "Review patch"}],
        }
        val = validate_result_envelope(data)
        self.assertTrue(val.is_valid)
        safe, reason = is_downstream_mutation_safe(val)
        self.assertTrue(safe)
        self.assertEqual(reason, "SAFE")

    def test_8_no_change_worker(self) -> None:
        data = {
            "task_id": "audit-1",
            "status": "NO_CHANGE",
            "harness": "codex",
            "summary": "Repository is up to date, no changes needed",
        }
        val = validate_result_envelope(data)
        self.assertTrue(val.is_valid)
        self.assertEqual(val.envelope.status, "NO_CHANGE")

    def test_9_blocked_worker_with_blockers(self) -> None:
        # Blocked without blockers list -> should fail validation
        bad = {"task_id": "b-1", "status": "BLOCKED", "harness": "dsh", "summary": "Cannot connect"}
        val_bad = validate_result_envelope(bad)
        self.assertFalse(val_bad.is_valid)
        self.assertTrue(any("SEMANTIC_ERROR" in e for e in val_bad.errors))

        # Blocked with blockers list -> valid envelope, but downstream unsafe
        good = {
            "task_id": "b-2",
            "status": "BLOCKED",
            "harness": "dsh",
            "summary": "Missing network access",
            "blockers": ["E_NETWORK_OFFLINE"],
        }
        val_good = validate_result_envelope(good)
        self.assertTrue(val_good.is_valid)
        safe, reason = is_downstream_mutation_safe(val_good)
        self.assertFalse(safe)
        self.assertIn("STATUS_NOT_ACTIONABLE", reason)

    def test_10_review_required_worker(self) -> None:
        data = {
            "task_id": "rev-1",
            "status": "REVIEW_REQUIRED",
            "harness": "claude",
            "summary": "Breaking API change requires human signoff",
            "warnings": ["API signature modified"],
        }
        val = validate_result_envelope(data)
        self.assertTrue(val.is_valid)
        safe, reason = is_downstream_mutation_safe(val)
        self.assertFalse(safe)
        self.assertIn("STATUS_NOT_ACTIONABLE", reason)

    def test_11_invalid_json_parsing(self) -> None:
        val = validate_result_envelope("Random plain text without json")
        self.assertFalse(val.is_valid)
        self.assertTrue(any("JSON_PARSE_ERROR" in e for e in val.errors))

    def test_12_valid_json_but_semantically_invalid(self) -> None:
        # Missing summary and invalid validation outcome
        data = {
            "task_id": "bad-1",
            "status": "PASS",
            "harness": "codex",
            "validations": [{"name": "test1", "command": "run", "result": "LOOKS_GOOD"}],
        }
        val = validate_result_envelope(data)
        self.assertFalse(val.is_valid)
        self.assertTrue(any("MISSING_REQUIRED_FIELD" in e for e in val.errors))
        self.assertTrue(any("INVALID_VALIDATION_RESULT" in e for e in val.errors))

    def test_13_retry_cap_enforcement(self) -> None:
        orchestrator = StructuredExecutionOrchestrator(retry_cap=2)
        call_count = 0

        def always_bad_runner(_correction_prompt: str | None) -> str:
            nonlocal call_count
            call_count += 1
            return "Still invalid text"

        val, attempts = orchestrator.execute_with_retry("retry-task", always_bad_runner, schema_required=True)
        self.assertFalse(val.is_valid)
        # initial attempt (1) + 2 retries = 3 attempts total
        self.assertEqual(attempts, 3)
        self.assertEqual(call_count, 3)

    def test_14_concurrent_worker_aggregation(self) -> None:
        results_from_workers = [
            '{"task_id": "worker-A", "status": "PASS", "harness": "claude", "summary": "Module A refactored", "files_changed": ["a.py"]}',
            '{"task_id": "worker-B", "status": "PASS", "harness": "codex", "summary": "Module B tests added", "files_changed": ["test_b.py"]}',
        ]
        aggregated_envelopes = []
        for raw in results_from_workers:
            val = validate_result_envelope(raw)
            self.assertTrue(val.is_valid)
            aggregated_envelopes.append(val.envelope)

        all_files = [f for env in aggregated_envelopes for f in env.files_changed]
        self.assertEqual(all_files, ["a.py", "test_b.py"])
        self.assertEqual([env.status for env in aggregated_envelopes], ["PASS", "PASS"])

        # Test human renderer
        human_text = render_human_summary(aggregated_envelopes[0])
        self.assertIn("Module A refactored", human_text)
        self.assertIn("PASS", human_text)


if __name__ == "__main__":
    unittest.main()
