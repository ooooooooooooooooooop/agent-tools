"""Tests for the loopback Claude Code hook event receiver."""

from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import agent_broker_mcp as broker  # noqa: E402
import hook_event_server  # noqa: E402
import managed_claude  # noqa: E402


SESSION_ID = "11111111-1111-4111-8111-111111111111"
SUPERVISOR_ID = "hook-test-supervisor"


class HookEventServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "broker"
        self.state_dir = self.root / "supervisors" / SUPERVISOR_ID
        self.state_dir.mkdir(parents=True)
        managed_claude._write_json(
            self.state_dir / "state.json",
            {"supervisor_id": SUPERVISOR_ID, "session_id": SESSION_ID, "status": "ready"},
        )
        self.server = hook_event_server.create_server(self.root, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def post(self, payload: object, raw: bytes | None = None) -> tuple[int, dict[str, object]]:
        body = raw if raw is not None else json.dumps(payload).encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(
                "POST",
                "/event",
                body=body,
                headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
            )
            response = connection.getresponse()
            data = json.loads(response.read().decode("utf-8"))
            return response.status, data
        finally:
            connection.close()

    def payload(self, event: str = "StopFailure", **extra: object) -> dict[str, object]:
        return {
            "session_id": SESSION_ID,
            "event": event,
            "cwd": str(self.root),
            "transcript_path": str(self.root / "transcript.jsonl"),
            **extra,
        }

    def test_event_writes_and_material_wait_reads_hook_terminal_events(self) -> None:
        status, result = self.post(self.payload("StopFailure", error="provider disconnected"))
        self.assertEqual(status, 202)
        self.assertEqual(result["status"], "recorded")
        status, result = self.post(self.payload("SessionEnd", reason="session ended"))
        self.assertEqual(status, 202)
        self.assertEqual(result["seq"], 2)
        events = [json.loads(line) for line in (self.state_dir / "events.jsonl").read_text().splitlines()]
        self.assertEqual([event["type"] for event in events], ["hook_stop_failure", "hook_session_end"])
        self.assertEqual([event["seq"] for event in events], [1, 2])

    def test_wait_supervisor_event_uses_hook_material_set(self) -> None:
        with mock.patch.object(broker, "BROKER_DIR", self.root):
            self.assertEqual(self.post(self.payload("Stop"))[0], 202)
            normal = broker.wait_supervisor_event(SUPERVISOR_ID, wait_seconds=0)
            self.assertTrue(normal["timeout"])
            self.assertEqual(self.post(self.payload("SessionEnd"))[0], 202)
            terminal = broker.wait_supervisor_event(SUPERVISOR_ID, wait_seconds=0)
        self.assertFalse(terminal["timeout"])
        self.assertEqual(terminal["type"], "hook_session_end")

    def test_session_mapping_hit_and_unknown_session_orphan(self) -> None:
        status, result = self.post(self.payload("Stop"))
        self.assertEqual(status, 202)
        self.assertEqual(result["status"], "recorded")
        unknown = self.payload("SubagentStop")
        unknown["session_id"] = "99999999-9999-4999-8999-999999999999"
        status, result = self.post(unknown)
        self.assertEqual(status, 202)
        self.assertEqual(result["status"], "orphan")
        orphan_lines = (self.root / hook_event_server.ORPHAN_LOG_NAME).read_text().splitlines()
        orphan = json.loads(orphan_lines[0])
        self.assertEqual(orphan["reason"], "unknown_session")
        self.assertEqual(orphan["event_type"], "hook_subagent_stop")

    def test_hook_stdin_event_name_can_replace_event_field(self) -> None:
        payload = self.payload("Stop")
        del payload["event"]
        payload["hook_event_name"] = "Stop"
        status, result = self.post(payload)
        self.assertEqual(status, 202)
        self.assertEqual(result["event_type"], "hook_stop")

    def test_invalid_json_unknown_field_and_oversize_return_errors(self) -> None:
        status, result = self.post({}, raw=b"{not-json")
        self.assertEqual(status, 400)
        self.assertIn("error", result)
        invalid = self.payload("Stop")
        invalid["not_allowed"] = True
        status, result = self.post(invalid)
        self.assertEqual(status, 400)
        self.assertIn("unknown field", result["error"])
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(
                "POST",
                "/event",
                body=b"",
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(hook_event_server.MAX_BODY_BYTES + 1),
                },
            )
            response = connection.getresponse()
            result = json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()
        self.assertEqual(response.status, 413)
        self.assertIn("too large", result["error"])

    def test_concurrent_receiver_and_daemon_writes_have_unique_sequences(self) -> None:
        config = {
            "supervisor_id": SUPERVISOR_ID,
            "project_root": str(self.root),
            "session_id": SESSION_ID,
            "objective": "test",
            "decision_mode": "record_only",
            "claude_path": "claude",
            "codex_path": "codex",
        }
        managed_claude._write_json(self.state_dir / "config.json", config)
        daemon = managed_claude.ManagedClaudeDaemon(self.state_dir)

        def write_hook(index: int) -> None:
            self.assertEqual(self.post(self.payload("SessionEnd", reason=f"hook-{index}"))[0], 202)

        def write_daemon(index: int) -> None:
            daemon._event("daemon_test_event", index=index)

        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = [pool.submit(write_hook, index) for index in range(20)]
            futures.extend(pool.submit(write_daemon, index) for index in range(20))
            for future in futures:
                future.result()
        lines = (self.state_dir / "events.jsonl").read_text().splitlines()
        events = [json.loads(line) for line in lines]
        sequences = [event["seq"] for event in events]
        self.assertEqual(len(events), 40)
        self.assertEqual(sorted(sequences), list(range(1, 41)))
        self.assertTrue(all(event["type"] in {"hook_session_end", "daemon_test_event"} for event in events))


if __name__ == "__main__":
    unittest.main()
