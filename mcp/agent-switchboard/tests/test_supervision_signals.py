"""Tests for the Switchboard supervision-signal MCP tools.

Covers the three supervision mechanisms added for the controller/executor split:
  A. wait_supervisor_event  - zero-poll long-poll off a supervisor's events.jsonl;
  B. wait_task_receipt      - block watching a JSON task-receipt file to a terminal status;
  C. close_supervisor       - idempotent stop + archive of a closing summary to the topic.

Uses only unittest/tempfile/unittest.mock. No real broker home is touched:
BROKER_DIR/DB_PATH are redirected to a TemporaryDirectory, and supervisor
state/events are written there directly with managed_claude's own helpers.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import agent_broker_mcp as broker  # noqa: E402
import managed_claude  # noqa: E402

SUPERVISOR_ID = "sup-signals"
SESSION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
PROJECT_NAME = "signals-project"


def closing_db_connect(db_path: Path):
    """A db_connect stand-in that closes the underlying sqlite connection on
    context exit. The real broker.db_connect opens a connection that Python's
    `with sqlite3.Connection` context manager commits but does NOT close, which
    on Windows leaves state.sqlite locked and makes TemporaryDirectory teardown
    fail with PermissionError [WinError 32]. Closing on exit releases the file
    handle before the temp dir is removed."""
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


def make_supervisor(project_root: Path, state_dir: Path) -> None:
    """Write a minimal supervisor config.json/state.json like test_managed_claude."""
    (state_dir / "commands").mkdir(parents=True, exist_ok=True)
    (state_dir / "decisions").mkdir(exist_ok=True)
    managed_claude._write_json(
        state_dir / "config.json",
        {
            "schema_version": 1,
            "supervisor_id": SUPERVISOR_ID,
            "project_root": str(project_root),
            "objective": "Finish one bounded implementation milestone.",
            "permission_mode": "acceptEdits",
            "decision_mode": "record_only",
            "claude_path": "claude",
            "codex_path": "codex",
            "session_id": SESSION_ID,
            "created_at": managed_claude.utc_now(),
            "updated_at": managed_claude.utc_now(),
        },
    )
    managed_claude._write_json(
        state_dir / "state.json",
        {
            "schema_version": 1,
            "supervisor_id": SUPERVISOR_ID,
            "project_root": str(project_root),
            "session_id": SESSION_ID,
            "status": "ready",
            "daemon_pid": None,
            "claude_pid": None,
            "decision_mode": "record_only",
            "decision_invocations": 0,
            "uses_foreground_ui": False,
            "updated_at": managed_claude.utc_now(),
        },
    )


def write_event(state_dir: Path, seq: int, etype: str, **fields) -> None:
    """Append one event line to events.jsonl (same format as ManagedClaudeDaemon._event)."""
    with (state_dir / "events.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(
            json.dumps(
                {"seq": seq, "type": etype, "created_at": managed_claude.utc_now(), **fields},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )


class SupervisionSignalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "broker"
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir()
        self.state_dir = self.home / "supervisors" / SUPERVISOR_ID
        self.db_path = self.home / "state.sqlite"
        self.patches = [
            mock.patch.object(broker, "BROKER_DIR", self.home),
            mock.patch.object(broker, "DB_PATH", self.db_path),
            mock.patch.object(broker, "CONFIG_PATH", self.home / "config.json"),
            mock.patch.object(broker, "db_connect", closing_db_connect(self.db_path)),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        make_supervisor(self.project, self.state_dir)

    # ---- Feature A: wait_supervisor_event --------------------------------

    def test_wait_supervisor_event_returns_matching_material_event(self):
        write_event(self.state_dir, 1, "claude_process_started")
        write_event(self.state_dir, 2, "turn_completed", result_summary="done")
        result = broker.wait_supervisor_event(SUPERVISOR_ID, since_seq=0, wait_seconds=0)
        self.assertFalse(result["timeout"])
        self.assertEqual(result["status"], "event")
        self.assertEqual(result["type"], "turn_completed")
        self.assertEqual(result["seq"], 2)
        self.assertEqual(result["event"]["result_summary"], "done")

    def test_wait_supervisor_event_ignores_non_material_events(self):
        write_event(self.state_dir, 1, "assistant_progress")
        write_event(self.state_dir, 2, "claude_process_started")
        result = broker.wait_supervisor_event(SUPERVISOR_ID, since_seq=0, wait_seconds=0)
        # Neither is material -> timeout, no crash.
        self.assertTrue(result["timeout"])
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["last_seq"], 2)

    def test_wait_supervisor_event_respects_since_seq(self):
        write_event(self.state_dir, 1, "turn_completed")
        write_event(self.state_dir, 2, "turn_completed", result_summary="second")
        result = broker.wait_supervisor_event(SUPERVISOR_ID, since_seq=1, wait_seconds=0)
        self.assertEqual(result["seq"], 2)
        self.assertEqual(result["event"]["result_summary"], "second")

    def test_wait_supervisor_event_filters_by_event_types(self):
        write_event(self.state_dir, 1, "api_retry_exhausted", error="boom")
        result = broker.wait_supervisor_event(
            SUPERVISOR_ID, since_seq=0, event_types=["turn_completed"], wait_seconds=0
        )
        self.assertTrue(result["timeout"])
        result2 = broker.wait_supervisor_event(
            SUPERVISOR_ID, since_seq=0, event_types=["api_retry_exhausted"], wait_seconds=0
        )
        self.assertEqual(result2["type"], "api_retry_exhausted")

    def test_wait_supervisor_event_blocks_until_event_lands(self):
        write_event(self.state_dir, 1, "assistant_progress")  # non-material, ignored

        def late():
            time.sleep(0.4)
            write_event(self.state_dir, 2, "stall_timeout", silent_seconds=900)

        thread = threading.Thread(target=late)
        thread.start()
        result = broker.wait_supervisor_event(SUPERVISOR_ID, since_seq=0, wait_seconds=5)
        thread.join()
        self.assertFalse(result["timeout"])
        self.assertEqual(result["type"], "stall_timeout")

    def test_wait_supervisor_event_tolerates_corrupt_tail_line(self):
        (self.state_dir / "events.jsonl").write_text(
            "{bad json\n" + json.dumps({"seq": 1, "type": "turn_completed"}) + "\n",
            encoding="utf-8",
        )
        result = broker.wait_supervisor_event(SUPERVISOR_ID, since_seq=0, wait_seconds=0)
        self.assertEqual(result["type"], "turn_completed")

    def test_wait_supervisor_event_unknown_supervisor_raises(self):
        with self.assertRaises(ValueError):
            broker.wait_supervisor_event("nope-nope", wait_seconds=0)

    # ---- Feature B: wait_task_receipt ------------------------------------

    def test_wait_task_receipt_returns_existing_terminal_status_immediately(self):
        path = self.project / "receipt.json"
        path.write_text(
            json.dumps(
                {"protocol_version": 1, "status": "ready_for_review",
                 "completed_items": ["A"], "current_item": None,
                 "test_summary": {"command": "pytest", "collected": 3, "passed": 3,
                                  "failed": 0, "skipped": 0},
                 "commit": "abc123", "pushed": None, "blocker": None, "updated_at": "t"},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        result = broker.wait_task_receipt(str(path), wait_seconds=0)
        self.assertFalse(result["timeout"])
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["receipt"]["status"], "ready_for_review")

    def test_wait_task_receipt_blocks_until_terminal_status_appears(self):
        path = self.project / "receipt.json"

        def write():
            time.sleep(0.4)
            path.write_text(
                json.dumps({"protocol_version": 1, "status": "pushed", "updated_at": "t"}),
                encoding="utf-8",
            )

        thread = threading.Thread(target=write)
        thread.start()
        result = broker.wait_task_receipt(str(path), wait_seconds=5)
        thread.join()
        self.assertFalse(result["timeout"])
        self.assertEqual(result["receipt"]["status"], "pushed")

    def test_wait_task_receipt_tolerates_invalid_json_then_succeeds(self):
        path = self.project / "receipt.json"
        path.write_text("{not json", encoding="utf-8")

        def fix():
            time.sleep(0.4)
            path.write_text(
                json.dumps({"status": "ready_for_review", "protocol_version": 1}),
                encoding="utf-8",
            )

        thread = threading.Thread(target=fix)
        thread.start()
        result = broker.wait_task_receipt(
            str(path), terminal_statuses=["ready_for_review"], wait_seconds=5
        )
        thread.join()
        self.assertFalse(result["timeout"])
        self.assertEqual(result["receipt"]["status"], "ready_for_review")

    def test_wait_task_receipt_tolerates_missing_status_then_blocks(self):
        # A file that exists but as a bare object with no `status` (e.g. in progress)
        # must not satisfy the wait and must not crash.
        path = self.project / "receipt.json"
        path.write_text(json.dumps({"protocol_version": 1}), encoding="utf-8")
        result = broker.wait_task_receipt(str(path), wait_seconds=0)
        self.assertTrue(result["timeout"])

    def test_wait_task_receipt_empty_terminal_statuses_accepts_any_valid_object(self):
        path = self.project / "receipt.json"

        def write():
            time.sleep(0.3)
            path.write_text(json.dumps({"status": "in_progress"}), encoding="utf-8")

        thread = threading.Thread(target=write)
        thread.start()
        result = broker.wait_task_receipt(str(path), terminal_statuses=[], wait_seconds=5)
        thread.join()
        self.assertFalse(result["timeout"])
        self.assertEqual(result["receipt"]["status"], "in_progress")

    def test_wait_task_receipt_times_out_on_never_terminal(self):
        path = self.project / "receipt.json"
        path.write_text(
            json.dumps({"protocol_version": 1, "status": "in_progress"}), encoding="utf-8"
        )
        result = broker.wait_task_receipt(str(path), wait_seconds=0)
        self.assertTrue(result["timeout"])
        self.assertEqual(result["status"], "timeout")

    def test_wait_task_receipt_requires_path(self):
        with self.assertRaises(ValueError):
            broker.wait_task_receipt("", wait_seconds=0)

    # ---- Feature C: close_supervisor -------------------------------------

    def test_close_supervisor_archives_summary_and_returns_archive_id(self):
        result = broker.close_supervisor(
            SUPERVISOR_ID, "Milestone done; awaiting review.", receipt_path=None
        )
        self.assertTrue(result["archive_id"])
        self.assertTrue(result["timeline_id"])
        self.assertTrue(result["archived"])
        self.assertEqual(result["stop_status"], "already_stopped")  # daemon not alive
        timeline = broker.get_topic_timeline(str(self.project), SUPERVISOR_ID)
        by_type = {entry["event_type"]: entry for entry in timeline["items"]}
        self.assertEqual(by_type["work_memory"]["topic"], SUPERVISOR_ID)
        self.assertIn("Milestone done", by_type["work_memory"]["summary"])
        self.assertEqual(by_type["supervisor_closed"]["topic"], SUPERVISOR_ID)

    def test_close_supervisor_archives_receipt_summary_when_provided(self):
        receipt = self.project / "receipt.json"
        receipt.write_text(
            json.dumps({"protocol_version": 1, "status": "ready_for_review",
                        "hashes": ["x"], "updated_at": "t"}),
            encoding="utf-8",
        )
        result = broker.close_supervisor(
            SUPERVISOR_ID, "Closed with receipt.", receipt_path=str(receipt)
        )
        self.assertTrue(result["receipt_recorded"])
        timeline = broker.get_topic_timeline(str(self.project), SUPERVISOR_ID)
        closed = next(e for e in timeline["items"] if e["event_type"] == "supervisor_closed")
        details = json.loads(closed["details"])
        self.assertEqual(details["receipt"]["status"], "ready_for_review")

    def test_close_supervisor_idempotent_when_daemon_alive_returns_stop(self):
        # Treat the daemon as alive so stop is invoked; it will time out but the
        # archive must still be recorded instead of raising.
        managed_claude._write_json(
            self.state_dir / "state.json",
            managed_claude._read_json(self.state_dir / "state.json", {}) | {"daemon_pid": os.getpid()},
        )
        with mock.patch.object(
            managed_claude, "stop_supervisor", side_effect=RuntimeError("stop timed out")
        ) as stop:
            result = broker.close_supervisor(SUPERVISOR_ID, "Closing live supervisor.")
            stop.assert_called_once()
        self.assertEqual(result["stop_status"], "stop_error")
        self.assertTrue(result["archived"])

    def test_close_supervisor_unknown_supervisor_raises(self):
        with self.assertRaises(ValueError):
            broker.close_supervisor("nope-nope", "archive")


if __name__ == "__main__":
    unittest.main()
