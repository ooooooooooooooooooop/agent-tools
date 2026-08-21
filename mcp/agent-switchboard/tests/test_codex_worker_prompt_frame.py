"""Regression tests for the queued-Codex-worker prompt frame.

Forensics on 2026-08-19 rollouts showed the failure mode this guards against:
queue_codex_request workers ran through consult_codex(), which always applied
the advisory sanitize_prompt() wrapper ("Answer with concise technical
advice"). A workspace-write implementation task under an advice-only frame can
finish in seconds with ZERO tool calls and a proposal-shaped answer. The fix
selects an execution frame whenever the worker was granted a writable sandbox.
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import agent_broker_mcp as broker  # noqa: E402


def closing_db_connect(db_path: Path):
    @contextmanager
    def _cm():
        conn = sqlite3.connect(str(db_path), timeout=5)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    return _cm


class WorkerPromptFrameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="broker-frame-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "state.sqlite"
        self.patches = [
            mock.patch.object(broker, "BROKER_DIR", self.root),
            mock.patch.object(broker, "DB_PATH", self.db_path),
            mock.patch.object(broker, "db_connect", closing_db_connect(self.db_path)),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        broker.init_db()

    def test_execution_wrapper_frames_action_and_keeps_secret_guard(self) -> None:
        wrapped = broker.sanitize_codex_worker_prompt("TASK BODY")
        self.assertIn("executing one bounded task", wrapped)
        self.assertIn("actually perform the work", wrapped)
        self.assertIn(".credentials.json", wrapped)
        self.assertTrue(wrapped.endswith("TASK BODY"))

    def test_consult_codex_applies_custom_prompt_wrapper(self) -> None:
        seen: list[str] = []

        def fake_run_process(command, cwd, prompt, timeout=None, env_override=None):
            seen.append(prompt)
            return 0, "", ""

        parsed = mock.Mock(response="ok", thread_id=None)
        with (
            mock.patch.object(broker, "load_config", return_value={}),
            mock.patch.object(broker, "discover_codex", return_value="codex"),
            mock.patch.object(
                broker, "resolve_project", return_value=mock.Mock(root_path=str(self.root))
            ),
            mock.patch.object(broker, "run_process", side_effect=fake_run_process),
            mock.patch.object(broker, "parse_codex_stream_output", return_value=parsed),
        ):
            broker.consult_codex("p", "TASK BODY", mode="workspace-write",
                                 prompt_wrapper=broker.sanitize_codex_worker_prompt)
        self.assertEqual(len(seen), 1)
        self.assertIn("executing one bounded task", seen[0])
        self.assertIn("TASK BODY", seen[0])

    def _queue_request(self, rid: str, mode: str) -> None:
        with broker.db_connect() as conn:
            conn.execute(
                "INSERT INTO codex_requests (id, project, root_path, prompt, status, created_at, mode, task_kind, target_model, effort)"
                " VALUES (?, ?, ?, ?, 'queued', '2026-08-19T00:00:00Z', ?, 'implementation', 'gpt-5.6-luna', 'high')",
                (rid, "p", str(self.root), "do work", mode),
            )

    def _run_and_capture_wrapper(self, rid: str):
        captured: dict[str, object] = {}

        def fake_consult(project, prompt, mode, model_name, effort, timeout, prompt_wrapper=None):
            captured["mode"] = mode
            captured["prompt_wrapper"] = prompt_wrapper
            return broker.CodexConsultResult(
                response="ok",
                requested_model=model_name,
                actual_model=model_name or "gpt-5.6-luna",
                requested_effort=effort,
                actual_effort=effort,
                model_attested=True,
            )

        with mock.patch.object(broker, "consult_codex", side_effect=fake_consult):
            broker.run_codex_request_worker(rid)
        return captured

    def test_worker_uses_execution_frame_for_writable_sandbox(self) -> None:
        self._queue_request("rid-write", "workspace-write")
        captured = self._run_and_capture_wrapper("rid-write")
        self.assertIs(captured["prompt_wrapper"], broker.sanitize_codex_worker_prompt)

    def test_worker_keeps_advisory_frame_for_read_only(self) -> None:
        self._queue_request("rid-read", "read-only")
        captured = self._run_and_capture_wrapper("rid-read")
        self.assertIs(captured["prompt_wrapper"], broker.sanitize_prompt)


if __name__ == "__main__":
    unittest.main()
