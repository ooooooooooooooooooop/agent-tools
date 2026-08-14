#!/usr/bin/env python3
"""Start and stop a managed Claude stream without sending a model prompt."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

import managed_claude


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=str(Path.cwd()))
    parser.add_argument("--claude-path", default=shutil.which("claude") or "")
    args = parser.parse_args()
    if not args.claude_path:
        raise SystemExit("Claude Code CLI was not found")
    project = str(Path(args.project).expanduser().resolve())
    with tempfile.TemporaryDirectory(prefix="agent-switchboard-managed-smoke-") as raw:
        home = Path(raw) / "broker"
        started = managed_claude.create_supervisor(
            home,
            project,
            "smoke",
            "Startup-only smoke; no model prompt is sent.",
            claude_path=args.claude_path,
            decision_mode="record_only",
        )
        print(
            json.dumps(
                {
                    "start_status": started["status"],
                    "daemon_alive": started["daemon_alive"],
                    "claude_alive": started["claude_alive"],
                    "uses_foreground_ui": started["uses_foreground_ui"],
                    "decision_invocations": started["decision_invocations"],
                },
                ensure_ascii=False,
            )
        )
        stopped = managed_claude.stop_supervisor(home, "smoke", timeout_seconds=30)
        print(
            json.dumps(
                {
                    "stop_status": stopped["status"],
                    "daemon_alive": stopped["daemon_alive"],
                    "claude_alive": stopped["claude_alive"],
                    "decision_invocations": stopped["decision_invocations"],
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
