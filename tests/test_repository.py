#!/usr/bin/env python3
"""Standard-library regression tests for the skill repository."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
VALIDATOR = SCRIPTS / "validate_repo.py"
SYNC = SCRIPTS / "sync_skills.py"
TASKFLOW = ROOT / "unified-taskflow" / "scripts" / "task-lifecycle.py"


class RepositoryContractTests(unittest.TestCase):
    def run_script(self, script: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, str(script), *args],
            cwd=str(cwd or ROOT),
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="strict",
            env=environment,
            check=False,
        )

    def test_strict_repository_validation(self) -> None:
        result = self.run_script(VALIDATOR, "--strict")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_manifest_is_json_and_has_registered_packages(self) -> None:
        manifest = json.loads((ROOT / "skills.json").read_text(encoding="utf-8"))
        names = {entry["name"] for entry in manifest["skills"]}
        self.assertIn("skill-repository-maintainer", names)
        self.assertIn("environment-bootstrap", names)
        self.assertEqual(len(names), len(manifest["skills"]))

    def test_sync_apply_then_check(self) -> None:
        with tempfile.TemporaryDirectory(prefix="skills-sync-") as raw:
            destination = Path(raw) / "installed"
            applied = self.run_script(SYNC, "--destination", str(destination), "--apply")
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            extra = destination / "user-custom-file.txt"
            extra.write_text("preserve me", encoding="utf-8")
            checked = self.run_script(SYNC, "--destination", str(destination), "--check")
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            self.assertTrue(extra.is_file())

    def test_taskflow_lifecycle_in_isolated_project(self) -> None:
        with tempfile.TemporaryDirectory(prefix="skills-taskflow-") as raw:
            project = Path(raw)
            project_args = ("--project-path", str(project))
            created = self.run_script(TASKFLOW, *project_args, "new", "regression-task")
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            anchor = project / ".taskflow" / "active" / "regression-task" / "anchor.md"
            anchor_text = anchor.read_text(encoding="utf-8")
            anchor.write_text(
                anchor_text.replace("[一句话描述用户核心意图]", "验证生命周期脚本")
                .replace("[必须完成的标准]", "生命周期命令可完成"),
                encoding="utf-8",
            )
            validated = self.run_script(TASKFLOW, *project_args, "validate")
            self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)
            completed = self.run_script(TASKFLOW, *project_args, "complete", "--message", "regression test")
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            index = json.loads((project / ".taskflow" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["tasks"][0]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
