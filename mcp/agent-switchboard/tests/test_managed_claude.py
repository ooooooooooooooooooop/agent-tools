"""Focused tests for windowless, event-driven Claude supervision."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import agent_broker_mcp as broker  # noqa: E402
import managed_claude  # noqa: E402


SESSION_ID = "22222222-2222-4222-8222-222222222222"
SUPERVISOR_ID = "sample-managed-supervisor"


class FakeProcess:
    def __init__(self) -> None:
        self.pid = 43210
        self.stdin = io.StringIO()
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.returncode = None

    def poll(self):
        return self.returncode


class ManagedClaudeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "broker"
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir()
        self.state_dir = self.home / "supervisors" / SUPERVISOR_ID
        (self.state_dir / "commands").mkdir(parents=True)
        (self.state_dir / "decisions").mkdir()
        managed_claude._write_json(
            self.state_dir / "config.json",
            {
                "schema_version": 1,
                "supervisor_id": SUPERVISOR_ID,
                "project_root": str(self.project),
                "objective": "Finish one bounded implementation milestone.",
                "policy": "No provider retries.",
                "permission_mode": "acceptEdits",
                "decision_mode": "record_only",
                "claude_path": "claude",
                "codex_path": "codex",
                "codex_model": None,
                "codex_effort": None,
                "max_autonomous_actions": 4,
                "session_id": SESSION_ID,
                "resume_existing": False,
                "has_started": False,
                "created_at": managed_claude.utc_now(),
                "updated_at": managed_claude.utc_now(),
            },
        )
        managed_claude._write_json(
            self.state_dir / "state.json",
            {
                "schema_version": 1,
                "supervisor_id": SUPERVISOR_ID,
                "project_root": str(self.project),
                "session_id": SESSION_ID,
                "status": "ready",
                "daemon_pid": os.getpid(),
                "claude_pid": 43210,
                "decision_mode": "record_only",
                "decision_invocations": 0,
                "uses_foreground_ui": False,
                "updated_at": managed_claude.utc_now(),
            },
        )

    def daemon(self) -> managed_claude.ManagedClaudeDaemon:
        daemon = managed_claude.ManagedClaudeDaemon(self.state_dir)
        daemon.proc = FakeProcess()
        return daemon

    def queued_command(self, prompt: str = "Implement M1") -> dict[str, object]:
        command_id = "33333333-3333-4333-8333-333333333333"
        row = {
            "schema_version": 1,
            "id": command_id,
            "type": "message",
            "status": "queued",
            "prompt": prompt,
            "prompt_sha256": "hash",
            "interrupt_current": False,
            "origin": "external",
            "created_at": managed_claude.utc_now(),
            "updated_at": managed_claude.utc_now(),
        }
        managed_claude._write_json(
            self.state_dir / "commands" / f"{command_id}.json", row
        )
        return row

    def test_dry_run_is_windowless_and_does_not_override_model(self):
        result = managed_claude.create_supervisor(
            self.home,
            str(self.project),
            "dry-run-supervisor",
            "Do the task",
            decision_mode="codex",
            claude_path="claude",
            codex_path="codex",
            dry_run=True,
        )
        self.assertEqual(result["launch_mode"], "detached_stream_json")
        self.assertFalse(result["uses_foreground_ui"])
        command = result["command"]
        self.assertNotIn("--model", command)
        self.assertFalse((self.home / "supervisors" / "dry-run-supervisor").exists())
        daemon = self.daemon()
        claude_command = daemon._claude_command(resume=False)
        self.assertNotIn("--model", claude_command)
        self.assertIn("stream-json", claude_command)

    def test_live_daemon_lock_owner_blocks_duplicate_launch(self):
        (self.state_dir / "daemon.lock").write_text(str(os.getpid()), encoding="utf-8")
        state = managed_claude._read_json(self.state_dir / "state.json")
        state["daemon_pid"] = None
        managed_claude._write_json(self.state_dir / "state.json", state)
        with self.assertRaisesRegex(RuntimeError, "managed_claude_already_running"):
            managed_claude.create_supervisor(
                self.home,
                str(self.project),
                SUPERVISOR_ID,
                "Do the task",
                claude_path="claude",
                dry_run=False,
            )
        self.assertTrue((self.state_dir / "daemon.lock").exists())

    def test_managed_source_contains_no_foreground_input_primitives(self):
        source = (REPO_ROOT / "managed_claude.py").read_text(encoding="utf-8")
        for forbidden in (
            "SetForegroundWindow",
            "SetFocus",
            "SendKeys",
            "SendInput",
            "OpenClipboard",
            "SetClipboardData",
        ):
            self.assertNotIn(forbidden, source)

    def test_queue_result_does_not_expose_prompt_body(self):
        secret_prompt = "PRIVATE_IMPLEMENTATION_TEXT"
        result = managed_claude.queue_command(
            self.home,
            SUPERVISOR_ID,
            secret_prompt,
            confirmation_timeout_seconds=0,
        )
        self.assertEqual(result["status"], "queued")
        self.assertNotIn(secret_prompt, json.dumps(result))
        status = managed_claude.get_supervisor_status(self.home, SUPERVISOR_ID)
        self.assertNotIn(secret_prompt, json.dumps(status))

    def test_stream_replay_is_required_before_confirmation(self):
        daemon = self.daemon()
        row = self.queued_command()
        daemon._submit_command(row)
        command_path = self.state_dir / "commands" / f"{row['id']}.json"
        self.assertEqual(managed_claude._read_json(command_path)["status"], "submitted")
        sent = daemon.proc.stdin.getvalue()
        self.assertEqual(json.loads(sent)["session_id"], "default")
        daemon._handle_stream_event(
            {
                "type": "user",
                "message": {"role": "user", "content": json.loads(sent)["message"]["content"]},
            }
        )
        self.assertEqual(managed_claude._read_json(command_path)["status"], "confirmed")

    def test_assistant_progress_never_queues_a_codex_decision(self):
        daemon = self.daemon()
        with mock.patch.object(daemon, "_run_codex_decision") as decide:
            daemon._handle_stream_event(
                {
                    "type": "assistant",
                    "message": {"role": "assistant", "content": [{"type": "text", "text": "Reading code"}]},
                }
            )
        self.assertEqual(list((self.state_dir / "decisions").glob("*.json")), [])
        decide.assert_not_called()

    def test_stall_timer_creates_one_event_without_periodic_codex_call(self):
        daemon = self.daemon()
        row = self.queued_command()
        daemon._submit_command(row)
        daemon.config["stall_timeout_seconds"] = 30
        daemon.last_activity_monotonic -= 31
        with mock.patch.object(daemon, "_run_codex_decision") as decide:
            daemon._check_stall()
            daemon._check_stall()
        decisions = list((self.state_dir / "decisions").glob("*.json"))
        self.assertEqual(len(decisions), 1)
        self.assertEqual(managed_claude._read_json(decisions[0])["event"]["type"], "stall_timeout")
        decide.assert_not_called()

    def test_turn_completion_queues_exactly_one_material_judgment(self):
        daemon = self.daemon()
        row = self.queued_command()
        daemon._submit_command(row)
        daemon._handle_stream_event(
            {
                "type": "user",
                "message": {"role": "user", "content": daemon.proc.stdin.getvalue()},
            }
        )
        daemon._handle_stream_event(
            {"type": "result", "subtype": "success", "result": "M1 finished"}
        )
        decisions = list((self.state_dir / "decisions").glob("*.json"))
        self.assertEqual(len(decisions), 1)
        self.assertEqual(managed_claude._read_json(decisions[0])["event"]["type"], "turn_completed")
        daemon._queue_judgment(managed_claude._read_json(decisions[0])["event"])
        self.assertEqual(len(list((self.state_dir / "decisions").glob("*.json"))), 1)

    def test_failure_threshold_and_result_share_one_command_decision(self):
        daemon = self.daemon()
        row = self.queued_command()
        daemon._submit_command(row)
        daemon._handle_stream_event(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "one", "is_error": True, "content": "failed"},
                        {"type": "tool_result", "tool_use_id": "two", "is_error": True, "content": "failed again"},
                    ],
                },
            }
        )
        daemon._handle_stream_event(
            {"type": "result", "subtype": "success", "result": "stopped after failures"}
        )
        decisions = list((self.state_dir / "decisions").glob("*.json"))
        self.assertEqual(len(decisions), 1)
        self.assertEqual(managed_claude._read_json(decisions[0])["decision_key"], f"command-{row['id']}")

    def test_record_only_material_event_requires_attention_without_codex(self):
        daemon = self.daemon()
        event = daemon._event("turn_completed", result_summary="done")
        daemon._queue_judgment(event)
        path, row = daemon._next_judgment()
        with mock.patch.object(daemon, "_run_codex_decision") as decide:
            daemon._process_judgment(path, row)
        self.assertEqual(managed_claude._read_json(path)["status"], "attention_required")
        self.assertEqual(managed_claude._read_json(self.state_dir / "state.json")["status"], "attention_required")
        decide.assert_not_called()

    def test_submitted_command_is_not_replayed_after_daemon_restart(self):
        row = self.queued_command()
        row["status"] = "submitted"
        path = self.state_dir / "commands" / f"{row['id']}.json"
        managed_claude._write_json(path, row)
        managed_claude.ManagedClaudeDaemon(self.state_dir)
        recovered = managed_claude._read_json(path)
        self.assertEqual(recovered["status"], "delivery_unconfirmed_after_restart")

    def test_confirmed_command_is_not_misattributed_after_daemon_restart(self):
        row = self.queued_command()
        row["status"] = "confirmed"
        path = self.state_dir / "commands" / f"{row['id']}.json"
        managed_claude._write_json(path, row)
        daemon = managed_claude.ManagedClaudeDaemon(self.state_dir)
        recovered = managed_claude._read_json(path)
        self.assertEqual(recovered["status"], "outcome_unconfirmed_after_restart")
        self.assertIsNone(daemon._oldest_open_command())

    def test_native_interrupt_marks_old_command_terminal_before_new_submission(self):
        daemon = self.daemon()
        old = self.queued_command("old work")
        old_path = self.state_dir / "commands" / f"{old['id']}.json"
        old["status"] = "confirmed"
        managed_claude._write_json(old_path, old)
        new_id = "44444444-4444-4444-8444-444444444444"
        new = {
            **old,
            "id": new_id,
            "prompt": "new work",
            "status": "queued",
            "interrupt_current": True,
            "interrupt_mode": "native",
            "created_at": managed_claude.utc_now(),
        }
        managed_claude._write_json(self.state_dir / "commands" / f"{new_id}.json", new)
        receipt_sent = threading.Event()
        release_result = threading.Event()

        def acknowledge() -> None:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                lines = daemon.proc.stdin.getvalue().strip().splitlines()
                if lines:
                    request = json.loads(lines[0])
                    daemon._handle_stream_event(
                        {
                            "type": "control_response",
                            "response": {
                                "subtype": "success",
                                "request_id": request["request_id"],
                                "response": {},
                            },
                        }
                    )
                    receipt_sent.set()
                    release_result.wait(2)
                    daemon._handle_stream_event(
                        {"type": "result", "subtype": "success", "result": "Interrupted"}
                    )
                    return
                time.sleep(0.01)

        worker = threading.Thread(target=acknowledge)
        worker.start()
        submitter = threading.Thread(target=daemon._submit_command, args=(new,))
        with mock.patch.object(daemon, "_terminate_claude") as hard:
            submitter.start()
            self.assertTrue(receipt_sent.wait(2))
            self.assertEqual(len(daemon.proc.stdin.getvalue().strip().splitlines()), 1)
            release_result.set()
            submitter.join(timeout=2)
        worker.join(timeout=2)
        self.assertFalse(submitter.is_alive())
        hard.assert_not_called()
        self.assertEqual(managed_claude._read_json(old_path)["status"], "interrupted")
        self.assertEqual(
            managed_claude._read_json(self.state_dir / "commands" / f"{new_id}.json")["status"],
            "submitted",
        )
        sent = [json.loads(line) for line in daemon.proc.stdin.getvalue().splitlines()]
        self.assertEqual(sent[0]["request"], {"subtype": "interrupt"})
        self.assertEqual(sent[1]["type"], "user")

    def test_native_interrupt_uses_control_protocol_and_waits_for_receipt(self):
        daemon = self.daemon()
        old = self.queued_command("old work")
        old["status"] = "confirmed"
        managed_claude._write_json(
            self.state_dir / "commands" / f"{old['id']}.json", old
        )

        def acknowledge() -> None:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                raw = daemon.proc.stdin.getvalue().strip()
                if raw:
                    request = json.loads(raw.splitlines()[-1])
                    daemon._handle_stream_event(
                        {
                            "type": "control_response",
                            "response": {
                                "subtype": "success",
                                "request_id": request["request_id"],
                                "response": {},
                            },
                        }
                    )
                    daemon._handle_stream_event(
                        {"type": "result", "subtype": "success", "result": "Interrupted"}
                    )
                    return
                time.sleep(0.01)

        worker = threading.Thread(target=acknowledge)
        worker.start()
        daemon._native_interrupt("interrupt-test")
        worker.join(timeout=2)
        request = json.loads(daemon.proc.stdin.getvalue().strip())
        self.assertEqual(request["type"], "control_request")
        self.assertEqual(request["request"], {"subtype": "interrupt"})
        self.assertEqual(
            managed_claude._read_json(
                self.state_dir / "commands" / f"{old['id']}.json"
            )["status"],
            "interrupted",
        )

    def test_native_interrupt_failure_does_not_fall_back_to_hard_stop(self):
        daemon = self.daemon()
        row = self.queued_command()
        row["interrupt_current"] = True
        row["interrupt_mode"] = "native"
        managed_claude._write_json(
            self.state_dir / "commands" / f"{row['id']}.json", row
        )
        with mock.patch.object(
            daemon,
            "_native_interrupt",
            side_effect=RuntimeError("native receipt failed"),
        ), mock.patch.object(daemon, "_terminate_claude") as hard:
            daemon._submit_command(row)
        hard.assert_not_called()
        current = managed_claude._read_json(
            self.state_dir / "commands" / f"{row['id']}.json"
        )
        self.assertEqual(current["status"], "failed")
        self.assertIn("native receipt failed", current["error"])

    def test_hard_interrupt_is_only_used_when_explicit(self):
        daemon = self.daemon()
        row = self.queued_command()
        row["interrupt_current"] = True
        row["interrupt_mode"] = "hard"
        managed_claude._write_json(
            self.state_dir / "commands" / f"{row['id']}.json", row
        )
        with mock.patch.object(daemon, "_terminate_claude") as hard, mock.patch.object(
            daemon, "_start_claude"
        ) as restart, mock.patch.object(daemon, "_native_interrupt") as native:
            daemon._submit_command(row)
        hard.assert_called_once()
        restart.assert_called_once_with(resume=True)
        native.assert_not_called()

    def test_broker_exposes_managed_tools_and_legacy_foreground_flag(self):
        schemas = {tool["name"]: tool for tool in broker.tools_for_current_client()}
        for name in (
            "start_managed_claude_supervisor",
            "send_to_managed_claude_session",
            "get_managed_claude_supervisor",
            "list_managed_claude_supervisors",
            "stop_managed_claude_supervisor",
        ):
            self.assertIn(name, schemas)
        legacy = schemas["send_to_claude_session"]["inputSchema"]["properties"]
        self.assertIn("foreground_control", legacy)
        managed_send = schemas["send_to_managed_claude_session"]["inputSchema"]["properties"]
        self.assertEqual(managed_send["interrupt_mode"]["enum"], ["native", "hard"])


if __name__ == "__main__":
    unittest.main()
