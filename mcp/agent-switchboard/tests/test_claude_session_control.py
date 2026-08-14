"""Focused tests for existing-process Claude Code session control."""

from __future__ import annotations

import json
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


SESSION_ID = "11111111-1111-4111-8111-111111111111"
PROJECT_NAME = "sample-project"
PROJECT_ROOT = r"D:\work\sample-project"
WATCHER_ID = "sample-supervisor"


def entry(
    kind: str,
    uuid: str,
    parent: str | None,
    text: str = "",
    *,
    attachment: bool = False,
) -> dict[str, object]:
    row: dict[str, object] = {
        "type": kind,
        "uuid": uuid,
        "parentUuid": parent,
        "isSidechain": False,
    }
    if attachment:
        row["attachment"] = {
            "type": "queued_command",
            "prompt": text,
            "commandMode": "prompt",
        }
    else:
        row["message"] = {"role": kind, "content": text}
    return row


class ClaudeSessionTargetTests(unittest.TestCase):
    def test_resume_session_id_accepts_long_and_short_flags(self):
        self.assertEqual(
            broker._resume_session_id(f'claude.exe --resume "{SESSION_ID}"'), SESSION_ID
        )
        self.assertEqual(broker._resume_session_id(f"claude -r {SESSION_ID}"), SESSION_ID)
        self.assertIsNone(broker._resume_session_id("claude -p"))

    def test_specific_transcript_requires_matching_project_cwd(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            project = home / PROJECT_NAME
            project.mkdir()
            bucket = home / ".claude" / "projects" / broker.claude_bucket_name(str(project))
            bucket.mkdir(parents=True)
            transcript = bucket / f"{SESSION_ID}.jsonl"
            transcript.write_text(
                json.dumps({"type": "user", "cwd": str(project), "message": {"content": "x"}})
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.object(broker.Path, "home", return_value=home):
                self.assertEqual(
                    broker._claude_session_path_by_id(str(project), SESSION_ID), transcript
                )
                self.assertIsNone(
                    broker._claude_session_path_by_id(str(home / "other"), SESSION_ID)
                )

    def test_running_resume_process_resolves_exact_session(self):
        project = broker.ProjectInfo(PROJECT_NAME, PROJECT_ROOT)
        transcript = Path(r"C:\Users\Example\.claude\projects\sample-project\session.jsonl")
        rows = [
            {
                "pid": 30,
                "parent_pid": 1,
                "name": "claude.exe",
                "command_line": f"claude.exe --resume {SESSION_ID}",
                "executable_path": r"C:\Tools\claude.exe",
            },
            {
                "pid": 40,
                "parent_pid": 1,
                "name": "claude.exe",
                "command_line": "claude.exe -p",
            },
        ]
        with mock.patch.object(broker, "resolve_project", return_value=project), mock.patch.object(
            broker, "_claude_session_path_by_id", return_value=transcript
        ):
            result = broker.active_claude_resume_sessions(
                project.root_path, SESSION_ID, process_rows=rows
            )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["claude_pid"], 30)

    def test_terminal_target_follows_msys_parent_chain_to_unique_mintty_window(self):
        rows = [
            {"pid": 103, "ppid": 102, "tty": "pty2", "command": "claude --resume x"},
            {"pid": 102, "ppid": 101, "tty": "pty2", "command": "/usr/bin/bash -i"},
            {"pid": 101, "ppid": 1, "tty": "?", "command": "/usr/bin/mintty -- bash"},
        ]
        winpids = {103: 30, 101: 70}
        with mock.patch.object(
            broker, "_msys_winpid", side_effect=lambda pid: winpids.get(pid)
        ), mock.patch.object(broker, "_mintty_window_handle", return_value=800):
            result = broker._claude_terminal_target(30, msys_rows=rows)
        self.assertEqual(result["tty"], "pty2")
        self.assertEqual(result["terminal_pid"], 70)
        self.assertEqual(result["terminal_hwnd"], 800)

    def test_msys_process_parser_uses_numeric_long_format_not_spaced_user_name(self):
        completed = mock.Mock(
            returncode=0,
            stderr="",
            stdout=(
                "      PID    PPID    PGID     WINPID   TTY         UID    STIME COMMAND\n"
                "     2015       1    2015      26836  ?         197609 07:57:20 /usr/bin/mintty\n"
                "I    3299    2016    3299      22680  pty0      197609 11:13:36 /c/Users/Example User/.local/bin/claude\n"
            ),
        )
        with mock.patch.object(broker, "_git_bash_executable", return_value="bash.exe"), mock.patch.object(
            broker.subprocess, "run", return_value=completed
        ) as run:
            rows = broker._msys_ps_rows()
        self.assertEqual(run.call_args.args[0][-1], "ps -el")
        self.assertEqual(rows[1]["pid"], 3299)
        self.assertEqual(rows[1]["winpid"], 22680)
        self.assertEqual(rows[1]["tty"], "pty0")
        self.assertIn("Example User", rows[1]["command"])


class ClaudeSessionDeliveryTests(unittest.TestCase):
    def target(self, transcript: Path) -> dict[str, object]:
        return {
            "session_id": SESSION_ID,
            "claude_pid": 30,
            "transcript": str(transcript),
            "project": PROJECT_NAME,
            "root_path": PROJECT_ROOT,
            "claude_executable": r"C:\Tools\claude.exe",
        }

    def terminal(self) -> dict[str, object]:
        return {
            "tty": "pty2",
            "claude_msys_pid": 103,
            "terminal_msys_pid": 101,
            "terminal_pid": 70,
            "terminal_hwnd": 800,
        }

    def delivered(self) -> dict[str, object]:
        return {
            "status": "delivered",
            "marker_seen": True,
            "marker_uuid": "marker",
            "confirmed_on_target_branch": True,
            "response": "ACK",
        }

    def test_dry_run_validates_process_terminal_and_window_without_input(self):
        project = broker.ProjectInfo(PROJECT_NAME, PROJECT_ROOT)
        target = self.target(Path("unused.jsonl"))
        with mock.patch.object(broker, "resolve_project", return_value=project), mock.patch.object(
            broker, "active_claude_resume_sessions", return_value=[target]
        ), mock.patch.object(
            broker, "_claude_terminal_target", return_value=self.terminal()
        ), mock.patch.object(broker, "_send_to_existing_mintty") as send:
            result = broker.send_to_claude_session(
                project.root_path, "check target", SESSION_ID, dry_run=True
            )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["delivery_mode"], "existing_mintty_terminal")
        self.assertEqual(result["terminal_pid"], 70)
        send.assert_not_called()

    def test_real_legacy_send_requires_explicit_foreground_authorization(self):
        project = broker.ProjectInfo(PROJECT_NAME, PROJECT_ROOT)
        target = self.target(Path("unused.jsonl"))
        with mock.patch.object(broker, "resolve_project", return_value=project), mock.patch.object(
            broker, "active_claude_resume_sessions", return_value=[target]
        ), mock.patch.object(
            broker, "_claude_terminal_target", return_value=self.terminal()
        ), mock.patch.object(broker, "_send_to_existing_mintty") as send:
            with self.assertRaisesRegex(RuntimeError, "foreground_control_required"):
                broker.send_to_claude_session(project.root_path, "task", SESSION_ID)
        send.assert_not_called()

    def test_delivery_uses_existing_terminal_and_branch_confirmed_reply(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / f"{SESSION_ID}.jsonl"
            transcript.write_text(
                json.dumps(entry("assistant", "anchor", None, "working")) + "\n",
                encoding="utf-8",
            )
            project = broker.ProjectInfo(PROJECT_NAME, PROJECT_ROOT)
            target = self.target(transcript)
            with mock.patch.object(broker, "resolve_project", return_value=project), mock.patch.object(
                broker, "active_claude_resume_sessions", return_value=[target]
            ), mock.patch.object(
                broker, "_claude_terminal_target", return_value=self.terminal()
            ), mock.patch.object(
                broker, "_send_to_existing_mintty", return_value={"ok": True, "status": "submitted"}
            ) as send, mock.patch.object(
                broker, "_transcript_delivery_evidence", return_value=self.delivered()
            ), mock.patch.object(broker, "record_agent_event") as event:
                result = broker.send_to_claude_session(
                    project.root_path,
                    "first line\nsecond line",
                    SESSION_ID,
                    topic="m1",
                    foreground_control=True,
                )
        self.assertEqual(result["status"], "delivered")
        self.assertTrue(result["confirmed_on_target_branch"])
        sent_text = send.call_args.args[1]
        self.assertIn("[Switchboard request ", sent_text)
        self.assertIn("[Switchboard ack ", sent_text)
        self.assertIn("first line second line", sent_text)
        self.assertNotIn("\n", sent_text)
        self.assertEqual(event.call_args.args[3], "claude_session_message_delivered")

    def test_terminal_input_helper_invokes_powershell_and_never_claude(self):
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps(
                {"ok": True, "status": "submitted", "terminal_pid": 70, "terminal_hwnd": 800, "enter_sent": True}
            ),
            stderr="",
        )
        target = {**self.target(Path("unused.jsonl")), **self.terminal()}
        with mock.patch.object(broker, "powershell_executable", return_value="powershell.exe"), mock.patch.object(
            broker.subprocess, "run", return_value=completed
        ) as run:
            result = broker._send_to_existing_mintty(target, "request")
        command = run.call_args.args[0]
        self.assertEqual(command[0], "powershell.exe")
        self.assertFalse(any("claude" in str(part).lower() for part in command))
        self.assertEqual(run.call_args.kwargs["env"]["AGENT_BROKER_TERMINAL_TEXT"], "request")
        self.assertEqual(result["status"], "submitted")

    def test_mintty_paste_contract_uses_sendkeys_chord_not_raw_insert(self):
        script = broker.EXISTING_MINTTY_INPUT_PS_SCRIPT
        self.assertIn("SendKeys]::SendWait('+{INSERT}')", script)
        self.assertNotIn("VirtualKey(0x2D", script)
        fixture = (REPO_ROOT / "tests" / "fixtures" / "pty_accept_once.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("SWITCHBOARD_终端_OK", fixture)
        self.assertIn("RECEIVED_HEX_", fixture)

    def test_mintty_interrupt_contract_sends_exactly_one_escape_not_ctrl_c(self):
        script = broker.EXISTING_MINTTY_INPUT_PS_SCRIPT
        self.assertIn("InterruptExact", script)
        self.assertEqual(script.count("VirtualKey(0x1B, false)"), 1)
        self.assertEqual(script.count("VirtualKey(0x1B, true)"), 1)
        self.assertNotIn("VirtualKey(0x43", script)

    def test_latest_branch_reports_only_unresolved_tool_use(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / "session.jsonl"
            rows = [
                {
                    "type": "assistant",
                    "uuid": "tool-one",
                    "parentUuid": None,
                    "isSidechain": False,
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "tool_use", "id": "call-one", "name": "Bash", "input": {"command": "sleep 1"}}
                        ],
                    },
                },
                {
                    "type": "user",
                    "uuid": "result-one",
                    "parentUuid": "tool-one",
                    "isSidechain": False,
                    "message": {
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": "call-one", "content": "done"}],
                    },
                },
                {
                    "type": "assistant",
                    "uuid": "tool-two",
                    "parentUuid": "result-one",
                    "isSidechain": False,
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "tool_use", "id": "call-two", "name": "Bash", "input": {"command": "sleep 2"}}
                        ],
                    },
                },
            ]
            transcript.write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            result = broker._claude_outstanding_tool_calls(transcript)
        self.assertEqual([item["id"] for item in result], ["call-two"])
        self.assertEqual(result[0]["name"], "Bash")
        self.assertNotIn("input", result[0])

    def test_explicit_interrupt_is_confirmed_before_message_submission(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / f"{SESSION_ID}.jsonl"
            transcript.write_text(
                json.dumps(entry("assistant", "anchor", None, "working")) + "\n",
                encoding="utf-8",
            )
            project = broker.ProjectInfo(PROJECT_NAME, PROJECT_ROOT)
            target = self.target(transcript)
            activity = {
                "active": True,
                "branch_head_uuid": "anchor",
                "transcript_size": transcript.stat().st_size,
                "outstanding_tool_calls": [{"id": "call-one", "name": "Bash"}],
            }
            interrupted = {
                "status": "interrupted",
                "confirmed": True,
                "resolved_tool_use_ids": ["call-one"],
            }
            order: list[str] = []
            with mock.patch.object(broker, "resolve_project", return_value=project), mock.patch.object(
                broker, "active_claude_resume_sessions", return_value=[target]
            ), mock.patch.object(
                broker, "_claude_terminal_target", side_effect=[self.terminal(), self.terminal()]
            ) as terminal, mock.patch.object(
                broker, "_claude_interrupt_activity", return_value=activity
            ), mock.patch.object(
                broker, "_interrupt_existing_mintty", side_effect=lambda _target: order.append("escape") or {"ok": True}
            ) as interrupt, mock.patch.object(
                broker,
                "_wait_for_claude_interrupt",
                side_effect=lambda *_args, **_kwargs: order.append("confirmed") or interrupted,
            ), mock.patch.object(
                broker,
                "_send_to_existing_mintty",
                side_effect=lambda *_args: order.append("submit") or {"ok": True, "status": "submitted"},
            ) as send, mock.patch.object(
                broker, "_transcript_delivery_evidence", return_value=self.delivered()
            ), mock.patch.object(broker, "record_agent_event"):
                result = broker.send_to_claude_session(
                    project.root_path,
                    "stop old work and do this",
                    SESSION_ID,
                    interrupt_current=True,
                    foreground_control=True,
                )
        self.assertEqual(order, ["escape", "confirmed", "submit"])
        self.assertEqual(terminal.call_count, 2)
        interrupt.assert_called_once()
        send.assert_called_once()
        self.assertEqual(result["interrupt_result"]["status"], "interrupted")

    def test_interrupt_failure_prevents_message_submission(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / f"{SESSION_ID}.jsonl"
            transcript.write_text("{}\n", encoding="utf-8")
            project = broker.ProjectInfo(PROJECT_NAME, PROJECT_ROOT)
            target = self.target(transcript)
            activity = {
                "active": True,
                "branch_head_uuid": "anchor",
                "transcript_size": transcript.stat().st_size,
                "outstanding_tool_calls": [{"id": "call-one", "name": "Bash"}],
            }
            with mock.patch.object(broker, "resolve_project", return_value=project), mock.patch.object(
                broker, "active_claude_resume_sessions", return_value=[target]
            ), mock.patch.object(
                broker, "_claude_terminal_target", return_value=self.terminal()
            ), mock.patch.object(
                broker, "_claude_interrupt_activity", return_value=activity
            ), mock.patch.object(
                broker, "_interrupt_existing_mintty", return_value={"ok": True}
            ), mock.patch.object(
                broker, "_wait_for_claude_interrupt", side_effect=RuntimeError("interrupt_failed: old tool is still active")
            ), mock.patch.object(broker, "_send_to_existing_mintty") as send:
                with self.assertRaisesRegex(RuntimeError, "interrupt_failed"):
                    broker.send_to_claude_session(
                        project.root_path,
                        "new task",
                        SESSION_ID,
                        interrupt_current=True,
                        foreground_control=True,
                    )
        send.assert_not_called()

    def test_matching_tool_result_is_required_for_interrupt_confirmation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / "session.jsonl"
            tool_use = {
                "type": "assistant",
                "uuid": "tool-one",
                "parentUuid": None,
                "isSidechain": False,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "call-one", "name": "Bash", "input": {}}],
                },
            }
            transcript.write_text(json.dumps(tool_use) + "\n", encoding="utf-8")
            activity = {
                "active": True,
                "branch_head_uuid": "tool-one",
                "transcript_size": transcript.stat().st_size,
                "outstanding_tool_calls": [{"id": "call-one", "name": "Bash"}],
            }
            tool_result = {
                "type": "user",
                "uuid": "result-one",
                "parentUuid": "tool-one",
                "isSidechain": False,
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call-one",
                            "is_error": True,
                            "content": "Interrupted by user",
                        }
                    ],
                },
            }
            with transcript.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(tool_result) + "\n")
            result = broker._wait_for_claude_interrupt(
                {"transcript": str(transcript)}, activity, 1
            )
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["resolved_tool_use_ids"], ["call-one"])
        self.assertTrue(result["tool_results"][0]["is_error"])

    def test_terminal_route_change_after_interrupt_prevents_submission(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / f"{SESSION_ID}.jsonl"
            transcript.write_text("{}\n", encoding="utf-8")
            project = broker.ProjectInfo(PROJECT_NAME, PROJECT_ROOT)
            target = self.target(transcript)
            changed_terminal = {**self.terminal(), "terminal_hwnd": 801}
            activity = {
                "active": True,
                "branch_head_uuid": "anchor",
                "transcript_size": transcript.stat().st_size,
                "outstanding_tool_calls": [{"id": "call-one", "name": "Bash"}],
            }
            with mock.patch.object(broker, "resolve_project", return_value=project), mock.patch.object(
                broker, "active_claude_resume_sessions", return_value=[target]
            ), mock.patch.object(
                broker, "_claude_terminal_target", side_effect=[self.terminal(), changed_terminal]
            ), mock.patch.object(
                broker, "_claude_interrupt_activity", return_value=activity
            ), mock.patch.object(
                broker, "_interrupt_existing_mintty", return_value={"ok": True}
            ), mock.patch.object(
                broker,
                "_wait_for_claude_interrupt",
                return_value={"status": "interrupted", "confirmed": True},
            ), mock.patch.object(broker, "_send_to_existing_mintty") as send:
                with self.assertRaisesRegex(RuntimeError, "interrupt_failed.*changed"):
                    broker.send_to_claude_session(
                        project.root_path,
                        "new task",
                        SESSION_ID,
                        interrupt_current=True,
                        foreground_control=True,
                    )
        send.assert_not_called()

    def test_interrupt_helper_requires_escape_evidence(self):
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "status": "interrupt_sent",
                    "terminal_pid": 70,
                    "terminal_hwnd": 800,
                    "escape_sent": True,
                }
            ),
            stderr="",
        )
        target = {**self.target(Path("unused.jsonl")), **self.terminal()}
        with mock.patch.object(broker, "powershell_executable", return_value="powershell.exe"), mock.patch.object(
            broker.subprocess, "run", return_value=completed
        ) as run:
            result = broker._interrupt_existing_mintty(target)
        self.assertTrue(result["escape_sent"])
        self.assertEqual(run.call_args.kwargs["env"]["AGENT_BROKER_TERMINAL_ACTION"], "interrupt")
        self.assertEqual(run.call_args.kwargs["env"]["AGENT_BROKER_TERMINAL_TEXT"], "")

    def test_interrupt_requested_without_active_tool_does_not_press_escape(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / f"{SESSION_ID}.jsonl"
            transcript.write_text(
                json.dumps(entry("assistant", "anchor", None, "idle")) + "\n",
                encoding="utf-8",
            )
            project = broker.ProjectInfo(PROJECT_NAME, PROJECT_ROOT)
            target = self.target(transcript)
            activity = {
                "active": False,
                "branch_head_uuid": "anchor",
                "transcript_size": transcript.stat().st_size,
                "outstanding_tool_calls": [],
            }
            with mock.patch.object(broker, "resolve_project", return_value=project), mock.patch.object(
                broker, "active_claude_resume_sessions", return_value=[target]
            ), mock.patch.object(
                broker, "_claude_terminal_target", return_value=self.terminal()
            ), mock.patch.object(
                broker, "_claude_interrupt_activity", return_value=activity
            ), mock.patch.object(broker, "_interrupt_existing_mintty") as interrupt, mock.patch.object(
                broker, "_send_to_existing_mintty", return_value={"ok": True, "status": "submitted"}
            ), mock.patch.object(
                broker, "_transcript_delivery_evidence", return_value=self.delivered()
            ), mock.patch.object(broker, "record_agent_event"):
                result = broker.send_to_claude_session(
                    project.root_path,
                    "new task",
                    SESSION_ID,
                    interrupt_current=True,
                    foreground_control=True,
                )
        interrupt.assert_not_called()
        self.assertEqual(result["interrupt_result"]["status"], "not_needed")

    def test_transcript_evidence_uses_parent_chain_not_file_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / "session.jsonl"
            marker = "[Switchboard request test-marker]"
            acknowledgement = "[Switchboard ack test-marker]"
            rows = [
                entry("assistant", "anchor", None, "working"),
                entry("attachment", "marker", "anchor", marker + " task", attachment=True),
                entry("assistant", "sibling", "anchor", "WRONG BRANCH"),
                entry("assistant", "reply", "marker", acknowledgement + " ACK"),
            ]
            transcript.write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            result = broker._transcript_delivery_evidence(
                transcript, marker, acknowledgement, 0, anchor_uuid="anchor"
            )
        self.assertEqual(result["status"], "delivered")
        self.assertEqual(result["response"], acknowledgement + " ACK")
        self.assertTrue(result["confirmed_on_target_branch"])

    def test_queue_marker_without_uuid_is_delivered_waiting_reply(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / "session.jsonl"
            marker = "[Switchboard request waiting]"
            acknowledgement = "[Switchboard ack waiting]"
            transcript.write_text(
                json.dumps(
                    {
                        "type": "queue-operation",
                        "operation": "enqueue",
                        "sessionId": SESSION_ID,
                        "content": marker + " task",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result = broker._transcript_delivery_evidence(
                transcript, marker, acknowledgement, 0, anchor_uuid="anchor"
            )
        self.assertEqual(result["status"], "delivered_waiting_reply")
        self.assertTrue(result["marker_seen"])
        self.assertFalse(result["confirmed_on_target_branch"])

    def test_unrelated_assistant_descendant_without_ack_stays_waiting(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / "session.jsonl"
            marker = "[Switchboard request busy]"
            acknowledgement = "[Switchboard ack busy]"
            rows = [
                entry("assistant", "anchor", None, "working"),
                entry("attachment", "marker", "anchor", marker + " task", attachment=True),
                entry("assistant", "old-task-output", "marker", "still running the old task"),
            ]
            transcript.write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            result = broker._transcript_delivery_evidence(
                transcript, marker, acknowledgement, 0, anchor_uuid="anchor"
            )
        self.assertEqual(result["status"], "delivered_waiting_reply")
        self.assertEqual(result["response"], "")
        self.assertTrue(result["confirmed_on_target_branch"])

    def test_marker_on_sibling_branch_is_delivery_failed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / "session.jsonl"
            marker = "[Switchboard request wrong-branch]"
            acknowledgement = "[Switchboard ack wrong-branch]"
            rows = [
                entry("assistant", "anchor", None, "working"),
                entry("assistant", "other", None, "other root"),
                entry("attachment", "marker", "other", marker + " task", attachment=True),
                entry("assistant", "reply", "marker", acknowledgement + " ACK"),
            ]
            transcript.write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            result = broker._transcript_delivery_evidence(
                transcript, marker, acknowledgement, 0, anchor_uuid="anchor"
            )
        self.assertEqual(result["status"], "delivery_failed")
        self.assertFalse(result["confirmed_on_target_branch"])

    def test_timeout_with_marker_returns_waiting_instead_of_false_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / f"{SESSION_ID}.jsonl"
            transcript.write_text(
                json.dumps(entry("assistant", "anchor", None, "working")) + "\n",
                encoding="utf-8",
            )
            project = broker.ProjectInfo(PROJECT_NAME, PROJECT_ROOT)
            target = self.target(transcript)
            waiting = {
                "status": "delivered_waiting_reply",
                "marker_seen": True,
                "marker_uuid": None,
                "confirmed_on_target_branch": False,
                "response": "",
            }
            with mock.patch.object(broker, "resolve_project", return_value=project), mock.patch.object(
                broker, "active_claude_resume_sessions", return_value=[target]
            ), mock.patch.object(
                broker, "_claude_terminal_target", return_value=self.terminal()
            ), mock.patch.object(
                broker, "_send_to_existing_mintty", return_value={"ok": True, "status": "submitted"}
            ), mock.patch.object(
                broker, "_transcript_delivery_evidence", return_value=waiting
            ), mock.patch.object(broker, "record_agent_event") as event, mock.patch.object(
                broker.time, "sleep"
            ):
                result = broker.send_to_claude_session(
                    project.root_path, "task", SESSION_ID, confirm_timeout_seconds=5,
                    foreground_control=True,
                )
        self.assertEqual(result["status"], "delivered_waiting_reply")
        self.assertEqual(event.call_args.args[3], "claude_session_message_waiting_reply")

    def test_no_marker_is_delivery_failed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / f"{SESSION_ID}.jsonl"
            transcript.write_text(
                json.dumps(entry("assistant", "anchor", None, "working")) + "\n",
                encoding="utf-8",
            )
            project = broker.ProjectInfo(PROJECT_NAME, PROJECT_ROOT)
            target = self.target(transcript)
            failed = {
                "status": "delivery_failed",
                "marker_seen": False,
                "marker_uuid": None,
                "confirmed_on_target_branch": False,
                "response": "",
            }
            with mock.patch.object(broker, "resolve_project", return_value=project), mock.patch.object(
                broker, "active_claude_resume_sessions", return_value=[target]
            ), mock.patch.object(
                broker, "_claude_terminal_target", return_value=self.terminal()
            ), mock.patch.object(
                broker, "_send_to_existing_mintty", return_value={"ok": True, "status": "submitted"}
            ), mock.patch.object(
                broker, "_transcript_delivery_evidence", return_value=failed
            ), mock.patch.object(broker, "record_agent_event") as event, mock.patch.object(
                broker.time, "sleep"
            ):
                with self.assertRaisesRegex(RuntimeError, "delivery_failed"):
                    broker.send_to_claude_session(
                        project.root_path, "task", SESSION_ID, confirm_timeout_seconds=5,
                        foreground_control=True,
                    )
        self.assertEqual(event.call_args.args[3], "claude_session_delivery_failed")

    def test_no_matching_terminal_fails_without_input(self):
        project = broker.ProjectInfo(PROJECT_NAME, PROJECT_ROOT)
        with mock.patch.object(broker, "resolve_project", return_value=project), mock.patch.object(
            broker, "active_claude_resume_sessions", return_value=[]
        ), mock.patch.object(broker, "_send_to_existing_mintty") as send:
            with self.assertRaisesRegex(RuntimeError, "No running Claude Code --resume"):
                broker.send_to_claude_session(project.root_path, "task", SESSION_ID)
        send.assert_not_called()

    def test_tool_is_exposed_and_dispatched(self):
        schemas = {tool["name"]: tool for tool in broker.tools_for_current_client()}
        self.assertIn("send_to_claude_session", schemas)
        self.assertIn(
            "interrupt_current",
            schemas["send_to_claude_session"]["inputSchema"]["properties"],
        )
        with mock.patch.object(
            broker, "send_to_claude_session", return_value={"status": "ready"}
        ) as send:
            result = broker.handle_tool(
                "send_to_claude_session",
                {
                    "project": PROJECT_ROOT,
                    "session_id": SESSION_ID,
                    "prompt": "task",
                    "dry_run": True,
                    "interrupt_current": True,
                },
            )
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual(payload["status"], "ready")
        send.assert_called_once()
        self.assertTrue(send.call_args.args[6])


class ClaudeSnapshotBranchTests(unittest.TestCase):
    def test_snapshot_turns_follow_latest_leaf_and_exclude_sibling_branch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / "session.jsonl"
            rows = [
                entry("user", "root", None, "ROOT"),
                entry("assistant", "sibling", "root", "WRONG BRANCH"),
                entry("user", "target-user", "root", "TARGET REQUEST"),
                entry("assistant", "target-reply", "target-user", "TARGET REPLY"),
            ]
            transcript.write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            turns = broker._claude_session_turns(transcript, last_n=8)
        self.assertEqual(turns, [("user", "ROOT"), ("user", "TARGET REQUEST"), ("assistant", "TARGET REPLY")])

    def test_uuid_less_rows_do_not_reenter_a_modern_branch_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / "session.jsonl"
            rows = [
                entry("user", "root", None, "ROOT"),
                {"type": "assistant", "message": {"role": "assistant", "content": "LEGACY NOISE"}},
                entry("assistant", "reply", "root", "TARGET REPLY"),
            ]
            transcript.write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            turns = broker._claude_session_turns(transcript, last_n=8)
        self.assertEqual(turns, [("user", "ROOT"), ("assistant", "TARGET REPLY")])


class ClaudeIncrementalMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "state.sqlite"
        self.transcript = self.root / f"{SESSION_ID}.jsonl"
        self.project = broker.ProjectInfo(PROJECT_NAME, PROJECT_ROOT)
        self.process_state = {"active": True, "claude_pid": 30}
        self.git_state = {"hash": "git-a", "changed_count": 0, "statuses": []}

        @contextmanager
        def closed_test_db():
            conn = sqlite3.connect(self.db_path, timeout=5)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

        self.patches = [
            mock.patch.object(broker, "BROKER_DIR", self.root),
            mock.patch.object(broker, "DB_PATH", self.db_path),
            mock.patch.object(broker, "db_connect", side_effect=closed_test_db),
            mock.patch.object(broker, "resolve_project", return_value=self.project),
            mock.patch.object(
                broker, "_claude_session_path_by_id", return_value=self.transcript
            ),
            mock.patch.object(
                broker,
                "_claude_monitor_process_state",
                side_effect=lambda *_args, **_kwargs: dict(self.process_state),
            ),
            mock.patch.object(
                broker,
                "_git_monitor_state",
                side_effect=lambda *_args, **_kwargs: dict(self.git_state),
            ),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.transcript.write_text(
            json.dumps(entry("assistant", "anchor", None, "already seen")) + "\n",
            encoding="utf-8",
        )

    def claim(self, watcher_id: str = WATCHER_ID) -> dict[str, object]:
        return broker.claim_claude_change(
            self.project.root_path, SESSION_ID, watcher_id
        )

    def append(self, row: dict[str, object], *, newline: bool = True) -> None:
        with self.transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + ("\n" if newline else ""))

    def test_first_claim_establishes_baseline_without_replaying_history(self):
        result = self.claim()
        self.assertEqual(result["status"], "initialized")
        self.assertEqual(result["events"], [])
        self.assertEqual(result["session_id"], SESSION_ID)
        self.assertEqual(result["watcher_id"], WATCHER_ID)

    def test_no_change_is_compact(self):
        self.claim()
        result = self.claim()
        self.assertEqual(result, {
            "status": "no_change",
            "watcher_id": WATCHER_ID,
            "session_id": SESSION_ID,
        })

    def test_unacked_change_is_replayed_with_the_same_cursor(self):
        self.claim()
        self.append(entry("assistant", "reply", "anchor", "M1 implementation finished"))
        first = self.claim()
        second = self.claim()
        self.assertEqual(first["status"], "changed")
        self.assertEqual(second, first)
        self.assertEqual(first["events"][0]["type"], "assistant_text")
        self.assertTrue(first["requires_judgment"])
        ack = broker.ack_claude_change(WATCHER_ID, first["cursor"])
        self.assertEqual(ack["status"], "acknowledged")
        self.assertEqual(self.claim()["status"], "no_change")

    def test_wrong_cursor_does_not_advance_pending_event(self):
        self.claim()
        self.append(entry("assistant", "reply", "anchor", "needs review"))
        pending = self.claim()
        with self.assertRaisesRegex(ValueError, "cursor"):
            broker.ack_claude_change(WATCHER_ID, "wrong-cursor")
        self.assertEqual(self.claim()["cursor"], pending["cursor"])

    def test_ack_is_idempotent_for_the_last_committed_cursor(self):
        self.claim()
        self.append(entry("assistant", "reply", "anchor", "review this"))
        pending = self.claim()
        first = broker.ack_claude_change(WATCHER_ID, pending["cursor"])
        second = broker.ack_claude_change(WATCHER_ID, pending["cursor"])
        self.assertEqual(first["status"], "acknowledged")
        self.assertEqual(second["status"], "already_acknowledged")

    def test_watcher_id_cannot_be_rebound_to_another_session(self):
        self.claim()
        other_session = "11111111-1111-1111-1111-111111111111"
        with self.assertRaisesRegex(ValueError, "already bound"):
            broker.claim_claude_change(
                self.project.root_path, other_session, WATCHER_ID
            )

    def test_partial_jsonl_line_is_not_consumed_until_newline_arrives(self):
        self.claim()
        row = entry("assistant", "reply", "anchor", "complete only after newline")
        self.append(row, newline=False)
        self.assertEqual(self.claim()["status"], "no_change")
        with self.transcript.open("a", encoding="utf-8") as handle:
            handle.write("\n")
        self.assertEqual(self.claim()["status"], "changed")

    def test_tool_event_never_returns_full_tool_input(self):
        self.claim()
        secret = "SECRET_" + ("x" * 5000)
        row = {
            "type": "assistant",
            "uuid": "tool-one",
            "parentUuid": "anchor",
            "isSidechain": False,
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call-one",
                        "name": "Bash",
                        "input": {"command": secret},
                    }
                ],
            },
        }
        self.append(row)
        result = self.claim()
        serialized = json.dumps(result)
        self.assertNotIn("SECRET_", serialized)
        self.assertEqual(result["events"][0], {
            "type": "tool_started",
            "tool_use_id": "call-one",
            "tool": "Bash",
        })

    def test_complete_malformed_record_fails_without_advancing(self):
        self.claim()
        with self.transcript.open("ab") as handle:
            handle.write(b"{not-json}\n")
        with self.assertRaisesRegex(RuntimeError, "claude_watch_parse_failed"):
            self.claim()
        with self.assertRaisesRegex(RuntimeError, "claude_watch_parse_failed"):
            self.claim()

    def test_more_than_forty_events_are_delivered_in_multiple_acknowledged_batches(self):
        self.claim()
        for index in range(45):
            parent = "anchor" if index == 0 else f"reply-{index - 1}"
            self.append(
                entry("assistant", f"reply-{index}", parent, f"event {index}")
            )
        first = self.claim()
        self.assertEqual(len(first["events"]), 40)
        broker.ack_claude_change(WATCHER_ID, first["cursor"])
        second = self.claim()
        self.assertEqual(len(second["events"]), 5)
        self.assertEqual(second["events"][0]["summary"], "event 40")

    def test_process_and_git_changes_are_compact_events(self):
        self.claim()
        self.process_state = {"active": False, "claude_pid": None}
        self.git_state = {
            "hash": "git-b",
            "changed_count": 2,
            "statuses": ["M src/a.py", "?? tests/test_a.py"],
        }
        result = self.claim()
        event_types = [event["type"] for event in result["events"]]
        self.assertEqual(event_types, ["session_process_stopped", "git_changed"])
        self.assertTrue(result["requires_judgment"])

    def test_tools_are_exposed_and_dispatchable(self):
        schemas = {tool["name"]: tool for tool in broker.tools_for_current_client()}
        self.assertIn("claim_claude_change", schemas)
        self.assertIn("ack_claude_change", schemas)
        with mock.patch.object(
            broker, "claim_claude_change", return_value={"status": "no_change"}
        ) as claim:
            payload = broker.handle_tool(
                "claim_claude_change",
                {
                    "project": self.project.root_path,
                    "session_id": SESSION_ID,
                    "watcher_id": WATCHER_ID,
                },
            )
        self.assertEqual(json.loads(payload["content"][0]["text"])["status"], "no_change")
        claim.assert_called_once()


if __name__ == "__main__":
    unittest.main()
