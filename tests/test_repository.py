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
        self.assertIn("weekly-work-summary", names)
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

    def test_subagent_usage_observer_behaviors(self) -> None:
        plugin = ROOT / "dsh" / "subagent-usage-observer" / "subagent-usage-observer-v1.mjs"
        script = r'''
import { pathToFileURL } from 'node:url';
const mod = await import(pathToFileURL(process.argv[1]).href);
const definitions = [];
const calls = [
  { type:'assistant/message', data:{ usage:{ inputTokens:999, outputTokens:999 }, message:{ content:[] } } },
  { type:'turn/start', data:{} },
  { type:'turn/start', data:{} },
  { type:'turn/start', data:{} },
  { type:'assistant/message', data:{ usage:{ inputTokens:10, outputTokens:2, cacheReadTokens:3, cacheWriteTokens:4, reasoningTokens:5 }, message:{ content:[] } } },
  { type:'tool/call', data:{ callId:'ok', name:'edit' } },
  { type:'tool/result', data:{ message:{ content:[{ type:'tool-result', toolCallId:'ok', content:[], isError:false }] } } },
  { type:'tool/call', data:{ callId:'bad', name:'write' } },
  { type:'tool/result', data:{ error:{ name:'FsError', code:'DENIED' }, message:{ content:[{ type:'tool-result', toolCallId:'bad', content:[], isError:true }] } } },
  { type:'tool/call', data:{ callId:'probe', name:'read' } },
];
const ctx = {
  sessionQuery: { readSession: async () => ({ session:{ agentPreset:'cc', seedLength:1 }, events:calls }) },
  tools: { register: (definition) => definitions.push(definition) },
};
mod.apply(ctx);
const usage = await definitions[0].execute({ sessionId:'s' }, { signal:new AbortController().signal });
const mutated = await definitions[1].execute({ sessionId:'s', mutationBudget:1, toolBudget:1, turnBudget:1 }, { signal:new AbortController().signal });
const probeEvents = [
  { type:'turn/start', data:{} }, { type:'turn/start', data:{} }, { type:'turn/start', data:{} },
  { type:'tool/call', data:{ callId:'cmd', name:'pwsh', arguments:'{"command":"Get-ChildItem"}' } },
  { type:'tool/call', data:{ callId:'r1', name:'read' } }, { type:'tool/call', data:{ callId:'r2', name:'read' } },
  { type:'tool/call', data:{ callId:'r3', name:'read' } }, { type:'tool/call', data:{ callId:'r4', name:'read' } },
  { type:'tool/call', data:{ callId:'r5', name:'read' } },
];
const probeDefs=[]; mod.apply({ sessionQuery:{ readSession:async()=>({ session:{ agentPreset:'cc' }, events:probeEvents }) }, tools:{ register:(d)=>probeDefs.push(d) } });
const stalled = await probeDefs[1].execute({ sessionId:'s', toolBudget:6, turnBudget:3 }, {});
const commandEvents = [...probeEvents, { type:'tool/call', data:{ callId:'writecmd', name:'pwsh', arguments:'{"command":"Set-Content -Path x -Value y"}' } }];
const commandDefs=[]; mod.apply({ sessionQuery:{ readSession:async()=>({ session:{ agentPreset:'cc' }, events:commandEvents }) }, tools:{ register:(d)=>commandDefs.push(d) } });
const indeterminate = await commandDefs[1].execute({ sessionId:'s', toolBudget:6, turnBudget:3 }, {});
const blockedDefs=[]; mod.apply({ sessionQuery:{ readSession:async()=>({ session:{ agentPreset:'cc' }, events:[
  { type:'assistant/message', data:{ message:{ content:[{ type:'text', text:'missing_fact: x\nwhy_required: y\nalready_checked: z\nrequested_context: q' }] } } },
] }) }, tools:{ register:(d)=>blockedDefs.push(d) } });
const blocked = await blockedDefs[1].execute({ sessionId:'s' }, {});
const errorDefs=[]; mod.apply({ sessionQuery:{ readSession:async()=>{ throw new Error('boom') } }, tools:{ register:(d)=>errorDefs.push(d) } });
const readFailure = await errorDefs[0].execute({ sessionId:'s' }, {});
console.log(JSON.stringify({ usage, mutated, stalled, indeterminate, blocked, readFailure }));
'''
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script, str(plugin)],
            cwd=str(ROOT), text=True, capture_output=True, encoding="utf-8", check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["usage"]["preset"], "cc")
        self.assertEqual(data["usage"]["seedLength"], 1)
        self.assertEqual(data["usage"]["tokens"]["uncachedInput"], 10)
        self.assertEqual(data["usage"]["tokens"]["cacheWrite"], 4)
        self.assertEqual(data["usage"]["tokens"]["reasoning"], 5)
        self.assertEqual(data["usage"]["mutations"], 1)
        self.assertEqual(data["mutated"]["state"], "MUTATED")
        self.assertEqual(data["stalled"]["state"], "STALLED")
        self.assertEqual(data["indeterminate"]["state"], "IMPLEMENTING")
        self.assertIn("explicit mutation intent", data["indeterminate"]["evidence"])
        self.assertEqual(data["blocked"]["state"], "BLOCKED")
        self.assertEqual(data["readFailure"]["error"], "session read failed")

    def test_subagent_splice_summarizer_preserves_non_text_blocks(self) -> None:
        plugin = ROOT / "dsh" / "subagent-splice-summarizer" / "subagent-splice-summarizer-v1.mjs"
        script = r'''
import { pathToFileURL } from 'node:url';
const mod = await import(pathToFileURL(process.argv[1]).href);
let listener; const ctx={ on:(_name, fn)=>{ listener=fn } }; mod.apply(ctx,{ thresholdChars:20 });
const image={ type:'image', attachment:{ id:'a' } };
const raw=['## Status','DONE','','## Changed','a.js','','## Validation','PASS','','details '.repeat(100)].join('\n');
const report={ id:'r', role:'user', source:{ kind:'subagent-report' }, content:[{ type:'text', text:raw }, image] };
const human={ id:'h', role:'user', source:{ kind:'human' }, content:[{ type:'text', text:'keep' }] };
const short={ id:'s', role:'user', source:{ kind:'subagent-report' }, content:[{ type:'text', text:'short' }] };
let nextCalls=0; const out=await listener({}, async()=>{ nextCalls++; return { kind:'enter', messages:[human, short, report] } });
console.log(JSON.stringify({ nextCalls, humanSame:out.messages[0]===human, shortSame:out.messages[1]===short, imageSame:out.messages[2].content[1]===image, types:out.messages[2].content.map(x=>x.type), summary:out.messages[2].content[0].text }));
'''
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script, str(plugin)],
            cwd=str(ROOT), text=True, capture_output=True, encoding="utf-8", check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["nextCalls"], 1)
        self.assertTrue(data["humanSame"])
        self.assertTrue(data["shortSame"])
        self.assertTrue(data["imageSame"])
        self.assertEqual(data["types"], ["text", "image"])
        self.assertIn("## Status", data["summary"])

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
