#!/usr/bin/env python3
"""Standard-library regression tests for the skill repository."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
VALIDATOR = SCRIPTS / "validate_repo.py"
SYNC = SCRIPTS / "sync_skills.py"


def _skill_path(skill_name: str) -> Path:
    """Resolve a Skill package path from the manifest (tolerance to layout changes)."""
    manifest = json.loads((ROOT / "skills.json").read_text(encoding="utf-8-sig"))
    for entry in manifest.get("skills", []):
        if entry.get("name") == skill_name:
            return (ROOT / str(entry["path"])).resolve()
    raise FileNotFoundError(f"skill not in skills.json: {skill_name}")


TASKFLOW = _skill_path("unified-taskflow") / "scripts" / "task-lifecycle.py"
QUALITY = _skill_path("skill-quality-gate") / "scripts" / "quality_report.py"


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
        self.assertIn("skill-quality-gate", names)
        self.assertNotIn("weekly-work-summary", names)
        self.assertEqual(len(names), len(manifest["skills"]))

    def test_mcp_manifest_registers_real_server_package(self) -> None:
        manifest = json.loads((ROOT / "mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 1)
        servers = {entry["name"]: entry for entry in manifest["mcp_servers"]}
        self.assertIn("agent-switchboard", servers)
        switchboard = servers["agent-switchboard"]
        package = (ROOT / switchboard["path"]).resolve()
        self.assertTrue((package / switchboard["entrypoint"]).is_file())
        self.assertTrue((package / switchboard["installer"]).is_file())
        self.assertTrue((package / "managed_claude.py").is_file())
        self.assertTrue((package / "smoke-managed-claude.py").is_file())
        spec = (package / "agent-broker.spec").read_text(encoding="utf-8")
        self.assertIn('"managed_claude"', spec)
        self.assertFalse((package / "SKILL.md").exists())
        self.assertEqual(switchboard["license"], "PolyForm-Noncommercial-1.0.0")

    def test_switchboard_distribution_excludes_machine_state_and_identifiers(self) -> None:
        package = ROOT / "mcp" / "agent-switchboard"
        forbidden_files = {"state.sqlite", "config.json", "agent-broker.log"}
        for path in package.rglob("*"):
            if path.is_file():
                self.assertNotIn(path.name.lower(), forbidden_files, str(path))
                self.assertNotEqual(path.suffix.lower(), ".jsonl", str(path))
        source_text = "\n".join(
            path.read_text(encoding="utf-8", errors="strict")
            for path in package.rglob("*")
            if path.is_file() and path.suffix.lower() in {".py", ".md", ".json", ".ps1"}
        )
        self.assertNotRegex(source_text, re.compile(r"[A-Za-z]:\\Desktop\\", re.IGNORECASE))
        self.assertNotRegex(
            source_text,
            re.compile(r"C:\\Users\\(?!Example(?:\\|$))", re.IGNORECASE),
        )
        self.assertNotRegex(
            source_text,
            re.compile(r"/c/Users/(?!Example User/)", re.IGNORECASE),
        )

    def test_switchboard_distribution_retains_upstream_license_notice(self) -> None:
        license_text = (ROOT / "mcp" / "agent-switchboard" / "LICENSE").read_text(
            encoding="utf-8"
        )
        self.assertIn("PolyForm Noncommercial License 1.0.0", license_text)
        self.assertIn("Required Notice:", license_text)

    def test_install_profiles_cover_registered_packages(self) -> None:
        manifest = json.loads((ROOT / "skills.json").read_text(encoding="utf-8"))
        names = {entry["name"] for entry in manifest["skills"]}
        tiers = {entry["name"]: entry["tier"] for entry in manifest["skills"]}
        self.assertTrue(manifest["profiles"]["core"])
        self.assertEqual(set(manifest["profiles"]["full"]), names)
        self.assertTrue(all(tier in {"core", "conditional", "optional"} for tier in tiers.values()))
        self.assertTrue(set(manifest["profiles"]["core"]).issubset(names))

    def test_public_introductions_are_chinese(self) -> None:
        manifest = json.loads((ROOT / "skills.json").read_text(encoding="utf-8"))
        readme_lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
        chinese = re.compile(r"[\u3400-\u9fff]")
        for entry in manifest["skills"]:
            self.assertRegex(entry["description"], chinese, entry["name"])
            matching = [line for line in readme_lines if f"[{entry['name']}]" in line]
            self.assertEqual(len(matching), 1, entry["name"])
            self.assertRegex(matching[0], chinese, entry["name"])
            skill_text = (ROOT / entry["path"] / "SKILL.md").read_text(encoding="utf-8")
            frontmatter = skill_text.split("---", 2)[1]
            self.assertRegex(frontmatter, chinese, entry["name"])

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

    def test_quality_gate(self) -> None:
        result = self.run_script(QUALITY, "--root", str(ROOT), "--strict")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
