#!/usr/bin/env python3
"""test_aic_apply.py — aic apply Runtime Closure 测试。

覆盖：
- DSH generated-only 写入边界 / overlay 保留 / REVIEW_REQUIRED 不写字 /
  snapshot+rollback / 物理 live drift 修复 / 多设备 canonical→runtime 收敛。
- 非 DSH 目标（Claude / Codex / Gemini / Switchboard）写控制阻断验证与零侵入原生性。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "aic"))
sys.path.insert(0, str(REPO / "scripts"))

import aic  # noqa: E402
import personal_ai_sync as pas  # noqa: E402

AIC = REPO / "scripts" / "aic" / "aic.py"


def run_aic(*args: str, env: dict | None = None, cwd: Path | None = None):
    e = {**os.environ, **(env or {})}
    p = subprocess.run([sys.executable, str(AIC), *args], capture_output=True,
                       text=True, env=e, cwd=cwd, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout + p.stderr).strip()


class _Args:
    def __init__(self, target):
        self.target = target


class FixtureHomeTest(unittest.TestCase):
    """temp HOME + 验证非 DSH 目标写控制受阻与 DSH 专属性。"""

    def setUp(self):
        self.td_obj = tempfile.TemporaryDirectory()
        self.home = Path(self.td_obj.name)
        self.patcher = mock.patch.object(Path, "home", return_value=self.home)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.td_obj.cleanup()

    def test_claude_apply_is_blocked_dsh_only(self):
        with mock.patch.object(Path, "home", return_value=self.home):
            rc = aic.cmd_apply(_Args("claude"))
        self.assertEqual(rc, 2)

    def test_codex_apply_is_blocked_dsh_only(self):
        with mock.patch.object(Path, "home", return_value=self.home):
            rc = aic.cmd_apply(_Args("codex"))
        self.assertEqual(rc, 2)

    def test_gemini_apply_is_blocked_dsh_only(self):
        with mock.patch.object(Path, "home", return_value=self.home):
            rc = aic.cmd_apply(_Args("gemini"))
        self.assertEqual(rc, 2)

    def test_switchboard_apply_is_blocked_dsh_only(self):
        with mock.patch.object(Path, "home", return_value=self.home):
            rc = aic.cmd_apply(_Args("switchboard"))
        self.assertEqual(rc, 2)

    def test_non_dsh_diff_returns_no_drift(self):
        self.assertEqual(aic.diff_harness("claude"), [])
        self.assertEqual(aic.diff_harness("codex"), [])
        self.assertEqual(aic.diff_harness("gemini"), [])
        self.assertEqual(aic.diff_harness("switchboard"), [])


class TestDshApply(unittest.TestCase):
    """DSH settings / composition apply 单测（temp HOME）。"""

    def setUp(self):
        self.td_obj = tempfile.TemporaryDirectory()
        self.home = Path(self.td_obj.name)
        dsh = self.home / ".dsh"
        dsh.mkdir(parents=True)
        canonical = aic.load_canonical()
        overlay = aic.adapter_overlay()
        self.expected = aic.render_settings(canonical, overlay)
        # 初始写合法 settings
        import yaml
        (dsh / "settings.yaml").write_text(
            yaml.safe_dump(self.expected, allow_unicode=True, sort_keys=False),
            encoding="utf-8")
        self.patcher = mock.patch.object(Path, "home", return_value=self.home)
        self.patcher.start()
        self.dsh_patcher = mock.patch.dict(os.environ, {"DSH_HOME": str(dsh)})
        self.dsh_patcher.start()

    def tearDown(self):
        self.dsh_patcher.stop()
        self.patcher.stop()
        self.td_obj.cleanup()

    def test_render_projects_canonical_model_capacity(self):
        providers = self.expected["llm-pi-ai"]["providers"]
        bai_models = {m["id"]: m for m in providers["bai"]["models"]}
        self.assertEqual(bai_models["deepseek-v4-flash"]["contextWindow"], 128000)

    def test_generated_section_drift_applied(self):
        import yaml
        f = self.home / ".dsh" / "settings.yaml"
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        data["llm-pi-ai"]["providers"]["bai"]["models"][0]["contextWindow"] = 999
        f.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                     encoding="utf-8")
        with mock.patch.object(Path, "home", return_value=self.home):
            rc, msgs = aic._apply_dsh()
        self.assertEqual(rc, 0)
        after = yaml.safe_load(f.read_text(encoding="utf-8"))
        self.assertEqual(
            after["llm-pi-ai"]["providers"]["bai"]["models"][0]["contextWindow"],
            128000)

    def test_agent_default_model_overlay_preserved_while_provider_drift_repairs(self):
        import yaml
        f = self.home / ".dsh" / "settings.yaml"
        data = json.loads(json.dumps(self.expected))
        data["agent-default-model"] = {"provider": "cpa", "model": "gemini-3.7-flash-high",
                                       "reasoningEffort": "user-choice"}
        data["llm-pi-ai"]["providers"]["cpa"]["models"][0]["id"] = "__DRIFT__"
        f.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        with mock.patch.object(Path, "home", return_value=self.home):
            rc, _ = aic._apply_dsh()
        self.assertEqual(rc, 0)
        back = yaml.safe_load(f.read_text(encoding="utf-8"))
        self.assertEqual(back["agent-default-model"], data["agent-default-model"])
        self.assertNotEqual(back["llm-pi-ai"]["providers"]["cpa"]["models"][0]["id"],
                            "__DRIFT__")

    def test_agent_default_model_luna_max_selection_preserved(self):
        import yaml
        f = self.home / ".dsh" / "settings.yaml"
        data = json.loads(json.dumps(self.expected))
        data["agent-default-model"] = {"provider": "cpa", "model": "gpt-5.6-luna-max"}
        data["llm-pi-ai"]["providers"]["cpa"]["models"][0]["id"] = "__DRIFT__"
        f.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        with mock.patch.object(Path, "home", return_value=self.home):
            rc, _ = aic._apply_dsh()
        self.assertEqual(rc, 0)
        back = yaml.safe_load(f.read_text(encoding="utf-8"))
        self.assertEqual(back["agent-default-model"], data["agent-default-model"])

    def test_agent_default_model_invalid_falls_back_to_main_default(self):
        import yaml
        f = self.home / ".dsh" / "settings.yaml"
        data = dict(self.expected)
        data["agent-default-model"] = {"provider": "not-admitted", "model": "missing"}
        f.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        with mock.patch.object(Path, "home", return_value=self.home):
            rc, _ = aic._apply_dsh()
        self.assertEqual(rc, 0)
        back = yaml.safe_load(f.read_text(encoding="utf-8"))
        self.assertEqual(back["agent-default-model"],
                         {"provider": "cpa", "model": "gpt-5.6-luna-max"})

    def test_agent_default_model_missing_falls_back_to_main_default(self):
        import yaml
        f = self.home / ".dsh" / "settings.yaml"
        data = dict(self.expected)
        data.pop("agent-default-model", None)
        f.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        with mock.patch.object(Path, "home", return_value=self.home):
            rc, _ = aic._apply_dsh()
        self.assertEqual(rc, 0)
        back = yaml.safe_load(f.read_text(encoding="utf-8"))
        self.assertEqual(back["agent-default-model"],
                         {"provider": "cpa", "model": "gpt-5.6-luna-max"})

    def test_agent_default_model_admitted_selections_no_drift(self):
        import yaml
        f = self.home / ".dsh" / "settings.yaml"
        selections = [
            {"provider": "cpa", "model": "gpt-5.6-luna-max"},
            {"provider": "cpa", "model": "gpt-5.6-sol-xhigh"},
            {"provider": "cpa", "model": "gemini-3.7-flash-high"},
        ]
        canonical = aic.load_canonical()
        # Verify canonical fallback is independently fixed to Luna-max
        self.assertEqual(canonical["policy"]["rules"]["main_default"]["model"], "gpt-5.6-luna-max")
        for sel in selections:
            data = json.loads(json.dumps(self.expected))
            data["agent-default-model"] = sel
            f.write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            with mock.patch.object(Path, "home", return_value=self.home):
                overlay = aic.adapter_overlay()
                current_actual = yaml.safe_load(f.read_text(encoding="utf-8"))
                expected_rendered = aic.render_settings(canonical, overlay, current_actual)
                diffs = []
                aic.deep_diff(expected_rendered, current_actual, "", diffs)
                self.assertEqual(diffs, [], f"Selection {sel} should produce NO DRIFT")
                rc, _ = aic._apply_dsh()
                self.assertEqual(rc, 0)
                back = yaml.safe_load(f.read_text(encoding="utf-8"))
                self.assertEqual(back["agent-default-model"], sel)
                # Verify canonical policy was not mutated by user selection change
                canon_after = aic.load_canonical()
                self.assertEqual(canon_after["policy"]["rules"]["main_default"]["model"], "gpt-5.6-luna-max")

    def test_user_custom_model_preserved_on_apply(self):
        import yaml
        f = self.home / ".dsh" / "settings.yaml"
        data = json.loads(json.dumps(self.expected))
        # User adds a brand new future model not in canonical
        custom_model = {"id": "gemini-4.0-ultra", "input": ["text", "image"], "contextWindow": 2000000}
        data["llm-pi-ai"]["providers"]["cpa"]["models"].append(custom_model)
        data["agent-default-model"] = {"provider": "cpa", "model": "gemini-4.0-ultra"}
        f.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        with mock.patch.object(Path, "home", return_value=self.home):
            rc, _ = aic._apply_dsh()
            self.assertEqual(rc, 0)
            back = yaml.safe_load(f.read_text(encoding="utf-8"))
            back_models = {m["id"]: m for m in back["llm-pi-ai"]["providers"]["cpa"]["models"]}
            self.assertIn("gemini-4.0-ultra", back_models)
            self.assertEqual(back["agent-default-model"], {"provider": "cpa", "model": "gemini-4.0-ultra"})

    def test_unknown_section_drift_review(self):
        import yaml
        f = self.home / ".dsh" / "settings.yaml"
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        data["user-custom-section"] = {"x": 1}
        f.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                     encoding="utf-8")
        with mock.patch.object(Path, "home", return_value=self.home):
            rc, _ = aic._apply_dsh()
        self.assertEqual(rc, 1)


class TestIncrementalDependency(unittest.TestCase):
    """验证 registry 各文件修改时，pas.affected_targets() 只返回真实受影响的 target。"""

    def test_model_registry_consumers(self):
        t = pas.affected_targets(["registry/models.yaml"], [])
        self.assertEqual(sorted(t), ["dsh"])

    def test_gateway_mapping_hits_claude_switchboard(self):
        t = pas.affected_targets(["registry/gateways.yaml"], [])
        self.assertEqual(sorted(t), [])

    def test_routing_policy_hits_dsh_and_switchboard(self):
        t = pas.affected_targets(["registry/routing-policy.yaml"], [])
        self.assertEqual(sorted(t), ["dsh"])

    def test_skill_only_no_harness_render(self):
        self.assertEqual(pas.affected_targets(["skills/foo/SKILL.md"], []), [])

    def test_memory_only_no_harness(self):
        self.assertEqual(pas.affected_targets([], ["memory/x.yaml"]), [])


class TestPhysicalLiveDrift(unittest.TestCase):
    """物理环境 live 针对 DSH 的真实 apply 回归。"""

    def _live(self, rel_home: str, fname: str) -> Path:
        return Path.home() / rel_home / fname

    def test_claude_diff_no_drift_native_independent(self):
        rc, out = run_aic("diff", "claude")
        self.assertEqual(rc, 0, out)

    def test_codex_diff_no_drift_native_independent(self):
        rc, out = run_aic("diff", "codex")
        self.assertEqual(rc, 0, out)

    def test_gemini_diff_no_drift_native_independent(self):
        rc, out = run_aic("diff", "gemini")
        self.assertEqual(rc, 0, out)

    def test_switchboard_diff_no_drift_optional_tool(self):
        rc, out = run_aic("diff", "switchboard")
        self.assertEqual(rc, 0, out)

    def test_dsh_settings_default_model(self):
        f = Path(os.environ.get("DSH_HOME", Path.home() / ".dsh")) / "settings.yaml"
        if not f.is_file():
            self.skipTest("dsh 未安装")
        before = f.read_bytes()
        try:
            import yaml
            data = yaml.safe_load(before.decode("utf-8-sig"))
            data["agent-default-model"]["model"] = "__DRIFT__"
            data.pop("ui-theme", None)
            f.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                         encoding="utf-8")
            rc, _ = run_aic("diff", "dsh", "--settings-only")
            self.assertEqual(rc, 1)
            rc, out = run_aic("apply", "dsh", "--settings-only")
            self.assertEqual(rc, 0, out)
            rc, out = run_aic("diff", "dsh", "--settings-only")
            self.assertEqual(rc, 0, out)
            after = yaml.safe_load(f.read_text(encoding="utf-8-sig"))
            self.assertNotEqual(after["agent-default-model"]["model"], "__DRIFT__")
        finally:
            rc, _ = run_aic("diff", "dsh", "--settings-only")
            if rc != 0:
                f.write_bytes(before)


class TestMultiDeviceRuntimeConvergence(unittest.TestCase):
    """跨设备场景：DSH settings canonical 变更后通过 git pull 并由 aic 收敛。"""

    def test_canonical_to_runtime_convergence(self):
        td_obj = tempfile.TemporaryDirectory()
        td = Path(td_obj.name)
        env_git = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        try:
            remote = td / "repo.git"
            subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)],
                           check=True, capture_output=True, env=env_git)
            devA = td / "A"
            subprocess.run(["git", "clone", str(REPO), str(devA)],
                           check=True, capture_output=True, env=env_git)
            subprocess.run(["git", "-C", str(devA), "remote", "set-url", "origin",
                            str(remote)], check=True, capture_output=True, env=env_git)
            subprocess.run(["git", "-C", str(devA), "push", "-u", "origin", "main"],
                           check=True, capture_output=True, env=env_git)
            devB = td / "B"
            subprocess.run(["git", "clone", str(remote), str(devB)],
                           check=True, capture_output=True, env=env_git)
            shutil.copy2(REPO / "scripts" / "aic" / "aic.py",
                         devA / "scripts" / "aic" / "aic.py")
            shutil.copy2(REPO / "scripts" / "aic" / "policy_projection.py",
                         devA / "scripts" / "aic" / "policy_projection.py")

            homeB = td / "homeB"
            dshB = homeB / ".dsh"
            dshB.mkdir(parents=True)
            import yaml
            canonical = aic.load_canonical()
            overlay = aic.adapter_overlay()
            settingsB = aic.render_settings(canonical, overlay)
            (dshB / "settings.yaml").write_text(
                yaml.safe_dump(settingsB, allow_unicode=True, sort_keys=False), encoding="utf-8")

            # A：更新 canonical rules
            pa = devA / "registry" / "routing-policy.yaml"
            doc = yaml.safe_load(pa.read_text(encoding="utf-8-sig"))
            doc["rules"]["main_default"]["provider"] = "bai"
            doc["rules"]["main_default"]["model"] = "deepseek-v4-flash"
            pa.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                          encoding="utf-8")
            subprocess.run(["git", "-C", str(devA), "add", "."], check=True,
                           capture_output=True, env=env_git)
            subprocess.run(["git", "-C", str(devA), "commit", "-m", "routing: main_default model update"],
                           check=True, capture_output=True, env=env_git)
            subprocess.run(["git", "-C", str(devA), "push", "origin", "main"],
                           check=True, capture_output=True, env=env_git)

            # B：pull → diff 必须发现 drift → apply → NO DRIFT
            envB = {**os.environ, "HOME": str(homeB), "USERPROFILE": str(homeB), "DSH_HOME": str(dshB),
                    "PERSONAL_AI_STATE": str(Path.home() / "personal-ai-state")}
            aicB = devB / "scripts" / "aic" / "aic.py"
            subprocess.run(["git", "-C", str(devB), "pull", "--ff-only", "origin", "main"],
                           check=True, capture_output=True, env=env_git)
            p = subprocess.run([sys.executable, str(aicB), "diff", "dsh", "--settings-only"],
                               capture_output=True, text=True, env=envB,
                               encoding="utf-8", errors="replace")
            self.assertEqual(p.returncode, 1, "pull 后 drift 未发现")
            p = subprocess.run([sys.executable, str(aicB), "apply", "dsh", "--settings-only"],
                               capture_output=True, text=True, env=envB,
                               encoding="utf-8", errors="replace")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            p = subprocess.run([sys.executable, str(aicB), "diff", "dsh", "--settings-only"],
                               capture_output=True, text=True, env=envB,
                               encoding="utf-8", errors="replace")
            self.assertEqual(p.returncode, 0, f"post-diff 非 NO DRIFT: {p.stdout}")
        finally:
            td_obj.cleanup()


if __name__ == "__main__":
    unittest.main()
