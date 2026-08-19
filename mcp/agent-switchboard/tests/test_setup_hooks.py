"""Tests for Claude Code hook wiring in the distribution installer."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_SETUP_SPEC = importlib.util.spec_from_file_location("agent_switchboard_setup", REPO_ROOT / "setup.py")
assert _SETUP_SPEC and _SETUP_SPEC.loader
setup = importlib.util.module_from_spec(_SETUP_SPEC)
_SETUP_SPEC.loader.exec_module(setup)


class ClaudeCodeHookInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.settings = root / ".claude" / "settings.json"
        self.claude_json = root / ".claude.json"
        self.broker_dir = root / ".agent-broker"
        self.claude_json.write_text("{}\n", encoding="utf-8")
        self.patches = [
            mock.patch.object(setup, "CLAUDE_SETTINGS", self.settings),
            mock.patch.object(setup, "CLAUDE_JSON", self.claude_json),
            mock.patch.object(setup, "BROKER_HOME", self.broker_dir),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def read_settings(self) -> dict:
        return json.loads(self.settings.read_text(encoding="utf-8"))

    def test_full_install_writes_four_events(self) -> None:
        result = setup.install_claude_hooks()
        self.assertEqual(result, "registered")
        data = self.read_settings()
        self.assertEqual(set(data["hooks"]), set(setup.CLAUDE_HOOK_EVENTS))
        for event in setup.CLAUDE_HOOK_EVENTS:
            commands = [
                entry["command"]
                for group in data["hooks"][event]
                for entry in group["hooks"]
            ]
            self.assertEqual(commands, [setup._claude_hook_command()])
            self.assertNotIn("/event", commands[0])
            self.assertNotIn(":43827", commands[0])
        self.assertEqual(len(list(self.settings.parent.glob("settings.json.*.bak"))), 0)

    def test_second_run_is_unchanged_and_has_no_duplicate(self) -> None:
        self.assertEqual(setup.install_claude_hooks(), "registered")
        first = self.settings.read_bytes()
        self.assertEqual(setup.install_claude_hooks(), "unchanged")
        self.assertEqual(self.settings.read_bytes(), first)
        data = self.read_settings()
        for event in setup.CLAUDE_HOOK_EVENTS:
            commands = [
                entry["command"]
                for group in data["hooks"][event]
                for entry in group["hooks"]
            ]
            self.assertEqual(commands.count(setup._claude_hook_command()), 1)

    def test_existing_user_hooks_are_preserved(self) -> None:
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(
            json.dumps(
                {
                    "model": "user-selected",
                    "hooks": {
                        "Stop": [{"hooks": [{"type": "command", "command": "user-stop"}]}],
                        "PreToolUse": [{"hooks": [{"type": "command", "command": "user-pre"}]}],
                    },
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(setup.install_claude_hooks(), "registered")
        data = self.read_settings()
        self.assertEqual(data["model"], "user-selected")
        self.assertIn(
            "user-stop",
            [entry["command"] for group in data["hooks"]["Stop"] for entry in group["hooks"]],
        )
        self.assertEqual(data["hooks"]["PreToolUse"][0]["hooks"][0]["command"], "user-pre")
        self.assertEqual(len(list(self.settings.parent.glob("settings.json.*.bak"))), 1)

    def test_uninstall_precisely_removes_installer_hooks(self) -> None:
        command = setup._claude_hook_command()
        legacy_command = (
            'curl -sS -m 2 -X POST http://127.0.0.1:43827/event '
            '-H "Content-Type: application/json" --data-binary @- >/dev/null 2>&1 || true'
        )
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(
            json.dumps(
                {
                    "model": "keep",
                    "hooks": {
                        "Stop": [
                            {
                                "hooks": [
                                    {"type": "command", "command": "user-stop"},
                                    {"type": "command", "command": command},
                                ]
                            },
                            {"hooks": [{"type": "command", "command": command}]},
                        ],
                        "SubagentStop": [{"hooks": [{"type": "command", "command": command}]}],
                        "StopFailure": [
                            {
                                "hooks": [
                                    {"type": "command", "command": "user-failure"},
                                    {"type": "command", "command": legacy_command},
                                ]
                            }
                        ],
                        "SessionEnd": [{"hooks": [{"type": "command", "command": command}]}],
                        "PreToolUse": [{"hooks": [{"type": "command", "command": "user-pre"}]}],
                    },
                }
            ),
            encoding="utf-8",
        )
        endpoint = self.broker_dir / "hook-event-server.endpoint"
        endpoint.parent.mkdir(parents=True)
        endpoint.write_text("http://127.0.0.1:45555\n", encoding="utf-8")
        self.assertEqual(setup.uninstall_claude_hooks(), "removed")
        data = self.read_settings()
        self.assertEqual(data["model"], "keep")
        self.assertNotIn("SubagentStop", data["hooks"])
        self.assertNotIn("SessionEnd", data["hooks"])
        self.assertEqual(data["hooks"]["Stop"][0]["hooks"][0]["command"], "user-stop")
        self.assertEqual(data["hooks"]["StopFailure"][0]["hooks"][0]["command"], "user-failure")
        self.assertEqual(len(data["hooks"]["StopFailure"][0]["hooks"]), 1)
        self.assertEqual(data["hooks"]["PreToolUse"][0]["hooks"][0]["command"], "user-pre")
        self.assertFalse(
            any(
                setup._claude_hook_command_owned(entry)
                for groups in data["hooks"].values()
                for group in groups
                for entry in group.get("hooks", [])
            )
        )
        self.assertEqual(len(list(self.settings.parent.glob("settings.json.*.bak"))), 1)
        self.assertFalse(endpoint.exists())

    def test_invalid_json_is_not_overwritten_and_reports_error(self) -> None:
        self.settings.parent.mkdir(parents=True)
        original = b'{"hooks": invalid\n'
        self.settings.write_bytes(original)
        result = setup.install_claude_hooks()
        self.assertTrue(result.startswith("ERROR:"), result)
        self.assertEqual(self.settings.read_bytes(), original)
        self.assertEqual(len(list(self.settings.parent.glob("settings.json.*.bak"))), 0)

    def test_static_command_is_shell_compatible_and_has_no_port(self) -> None:
        self.assertEqual(setup.install_claude_hooks(), "registered")
        data = self.read_settings()
        command = data["hooks"]["Stop"][0]["hooks"][0]["command"]
        self.assertIn("agent_broker_entry.py hook-event", command)
        self.assertNotIn("curl", command)
        self.assertNotIn("/event", command)
        self.assertNotRegex(command, r":\d+/event")
        if setup.os.name == "nt":
            self.assertTrue(command.endswith(">nul 2>&1 || ver >nul"))
        else:
            self.assertTrue(command.endswith(">/dev/null 2>&1 || true"))


if __name__ == "__main__":
    unittest.main()
