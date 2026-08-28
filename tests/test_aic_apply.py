#!/usr/bin/env python3
"""test_aic_apply.py — aic apply Runtime Closure 测试。

覆盖：generated-only 写入边界 / overlay 保留 / REVIEW_REQUIRED 不写字 /
OPTIONAL_NOT_INSTALLED / snapshot+rollback / 物理 live drift 修复 /
多设备 canonical→runtime 收敛 / 增量受影响面。
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
    """temp HOME + 假 harness 文件的单元级 apply 测试。"""

    def setUp(self):
        self.td_obj = tempfile.TemporaryDirectory()
        self.home = Path(self.td_obj.name)
        self.patcher = mock.patch.object(Path, "home", return_value=self.home)
        self.patcher.start()
        # inventory 已存在则 cmd_apply 不跑 discover（不触网）

    def tearDown(self):
        self.patcher.stop()
        self.td_obj.cleanup()

    def mk_claude(self, settings: dict):
        d = self.home / ".claude"
        d.mkdir(parents=True, exist_ok=True)
        (d / "settings.json").write_text(json.dumps(settings, indent=2),
                                         encoding="utf-8")

    def test_claude_generated_fix_overlay_kept(self):
        self.mk_claude({
            "model": "WRONG",
            "env": {"ANTHROPIC_BASE_URL": "http://WRONG", "ANTHROPIC_AUTH_TOKEN": "PROXY_MANAGED"},
            "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "x"}]}]},
        })
        rc, out = run_aic("diff", "claude", env={"HOME": str(self.home),
                                                 "USERPROFILE": str(self.home)})
        self.assertEqual(rc, 1, out)
        with mock.patch.object(Path, "home", return_value=self.home):
            rc = aic.cmd_apply(_Args("claude"))
        self.assertEqual(rc, 0)
        data = json.loads((self.home / ".claude" / "settings.json").read_text())
        self.assertNotEqual(data["model"], "WRONG")
        self.assertIn("hooks", data)                      # overlay 保留
        self.assertEqual(data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"], "x")
        rows = aic.diff_harness("claude")
        # generated 字段全修好；CLAUDE.md managed-block 属 switchboard 所有权（soft 余项）
        SOFT_FIELDS = ("<managed-block>", "<exists>", "<sync-group>")
        self.assertFalse([r for r in rows
                          if not r["ok"] and r["field"] not in SOFT_FIELDS])

    def test_unknown_field_drift_review_nothing_written(self):
        before = {"model": "WRONG", "custom_user_key": {"nested": 1}}
        self.mk_claude(before)
        # custom_user_key 不在 generated_fields → 但 diff 只检查 generated_fields…
        # 用 env 下未知 key 不行；改为让 overlay 段 drift 的情况由 dsh 全文件测试覆盖。
        # 这里验证：generated 全对时 custom key 不触发 drift（diff 只看 generated）
        canon_model = None
        with mock.patch.object(Path, "home", return_value=self.home):
            rows = aic.diff_harness("claude")
        bad = [r for r in rows if not r["ok"]]
        # model=WRONG 会 drift；修成正确值后 custom_user_key 不告警
        pgw = aic.load_private_gateways()
        contract = aic.harness_contract("claude")
        self.mk_claude({**before, "model": contract["harness_defaults"]["model_alias"]})
        with mock.patch.object(Path, "home", return_value=self.home):
            rows = aic.diff_harness("claude")
        bad = [r for r in rows if not r["ok"]]
        self.assertFalse([r for r in bad if "custom_user_key" in r["field"]])

    def test_missing_harness_optional_not_installed(self):
        # 无 .claude 目录 → OPTIONAL_NOT_INSTALLED，exit 0，不阻塞
        with mock.patch.object(Path, "home", return_value=self.home):
            rc = aic.cmd_apply(_Args("claude"))
        self.assertEqual(rc, 0)

    def test_gemini_superset_adds_missing_mcp(self):
        d = self.home / ".gemini"
        d.mkdir(parents=True)
        (d / "settings.json").write_text(json.dumps(
            {"mcpServers": {"memory": {"type": "stdio", "command": "cmd",
                                       "args": ["x"]}},
             "security": {"auth": {"selectedType": "oauth-personal"}}}), encoding="utf-8")
        with mock.patch.object(Path, "home", return_value=self.home):
            rc = aic.cmd_apply(_Args("gemini"))
        self.assertEqual(rc, 0)
        data = json.loads((d / "settings.json").read_text())
        caps = aic.load_yaml(aic.REG / "capabilities.yaml")["capabilities"]["mcp_standard"]
        for k in caps:
            self.assertIn(k, data["mcpServers"])         # 缺失 key 补齐
        self.assertEqual(data["security"]["auth"]["selectedType"], "oauth-personal")

    def test_codex_toml_surgical_overlay_preserved(self):
        d = self.home / ".codex"
        d.mkdir(parents=True)
        original = ('model = "WRONG-MODEL"\n\n[sandbox]\nmode = "danger-full-access"\n'
                    '# 用户手写注释\n\n[projects."C:\\\\x"]\ntrust_level = "trusted"\n')
        (d / "config.toml").write_text(original, encoding="utf-8")
        with mock.patch.object(Path, "home", return_value=self.home):
            rc = aic.cmd_apply(_Args("codex"))
        self.assertEqual(rc, 0)
        text = (d / "config.toml").read_text(encoding="utf-8")
        self.assertIn("# 用户手写注释", text)            # overlay 字节保留
        self.assertIn('trust_level = "trusted"', text)
        self.assertNotIn("WRONG-MODEL", text)

    def test_rollback_on_post_diff_failure(self):
        """写入后 post-diff 仍失败 → before snapshot 必须恢复。"""
        self.mk_claude({"model": "WRONG", "env": {}})
        before = (self.home / ".claude" / "settings.json").read_bytes()
        real_diff = aic.diff_harness
        calls = {"n": 0}

        def flaky(name):
            calls["n"] += 1
            rows = real_diff(name)
            if calls["n"] >= 2:      # apply 后的 post-diff 强制失败
                return [{"file": "settings.json", "field": "<injected>",
                         "expected": 1, "actual": 2, "ok": False}]
            return rows

        with mock.patch.object(Path, "home", return_value=self.home), \
                mock.patch.object(aic, "diff_harness", side_effect=flaky):
            rc = aic.cmd_apply(_Args("claude"))
        self.assertEqual(rc, 3)                          # FAIL_ROLLED_BACK
        self.assertEqual((self.home / ".claude" / "settings.json").read_bytes(), before)

    def test_secret_container_never_written(self):
        d = self.home / ".gemini"
        d.mkdir(parents=True)
        (d / "settings.json").write_text(json.dumps({"mcpServers": {k: v for k, v in
            aic.load_yaml(aic.REG / "capabilities.yaml")["capabilities"]["mcp_standard"].items()}}),
            encoding="utf-8")
        # .env 缺失 → check-mode soft（§5 不阻塞其他 generated）；settings 不动、.env 不创建
        before = (d / "settings.json").read_bytes()
        with mock.patch.object(Path, "home", return_value=self.home):
            rc = aic.cmd_apply(_Args("gemini"))
        self.assertEqual(rc, 0)
        self.assertEqual((d / "settings.json").read_bytes(), before)
        self.assertFalse((d / ".env").exists())          # secret 容器不由 aic 创建


class TestDshApply(unittest.TestCase):
    """DSH full-file / cordis 外科手术 apply（fixture DSH_HOME）。"""

    def setUp(self):
        self.td_obj = tempfile.TemporaryDirectory()
        self.home = Path(self.td_obj.name)
        self.dsh = self.home / ".dsh"
        (self.dsh / ".agent-presets" / "cc").mkdir(parents=True)
        canonical = aic.load_canonical()
        overlay = aic.adapter_overlay()
        self.expected = aic.render_settings(canonical, overlay)
        import yaml
        (self.dsh / "settings.yaml").write_text(
            yaml.safe_dump(self.expected, allow_unicode=True, sort_keys=False),
            encoding="utf-8")
        # cordis fixture：顶层 list 结构，field_checks 值与 canonical 一致（无 drift），
        # 含两个已登记 opaque !!js 行
        rules = canonical["policy"]["rules"]
        ss, sf, cp = rules["subagent_spawn"], rules["subagent_fork"], rules["compaction_summary"]
        cordis = (
            "- id: tool-bash\n"
            "  disabled: !!js/function (ctx) => ctx.x\n"
            "- id: tool-pwsh\n"
            "  disabled: !!js/function (ctx) => ctx.y\n"
            "- id: tool-subagent\n"
            "  config:\n"
            "    agentOptions:\n"
            f"      provider: {ss['provider']}\n"
            f"      model: {ss['model']}\n"
            "- id: tool-subagent-fork\n"
            "  config:\n"
            "    agentOptions:\n"
            f"      provider: {sf['provider']}\n"
            f"      model: {sf['model']}\n"
            "- id: compaction-basic\n"
            "  config:\n"
            f"    summarizationProvider: {cp['provider']}\n"
            f"    summarizationModel: {cp['model']}\n"
        )
        (self.dsh / ".agent-presets" / "cc" / "agent.cordis.yml").write_text(
            cordis, encoding="utf-8")
        self.env = {"DSH_HOME": str(self.dsh)}

    def tearDown(self):
        self.td_obj.cleanup()

    def test_generated_section_drift_applied(self):
        import yaml
        data = dict(self.expected)
        data["agent-default-model"] = {"provider": "WRONG", "model": "WRONG",
                                       "reasoningEffort": "WRONG"}
        (self.dsh / "settings.yaml").write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        with mock.patch.dict(os.environ, self.env):
            rc = aic.cmd_apply(_Args("dsh"))
        self.assertEqual(rc, 0)
        back = yaml.safe_load((self.dsh / "settings.yaml").read_text(encoding="utf-8"))
        self.assertEqual(back, self.expected)

    def test_unknown_section_drift_review(self):
        import yaml
        data = dict(self.expected)
        data["user-custom-section"] = {"x": 1}           # 未登记 path
        (self.dsh / "settings.yaml").write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        before = (self.dsh / "settings.yaml").read_bytes()
        with mock.patch.dict(os.environ, self.env):
            rc = aic.cmd_apply(_Args("dsh"))
        self.assertEqual(rc, 1)                          # REVIEW_REQUIRED
        self.assertEqual((self.dsh / "settings.yaml").read_bytes(), before)

    def test_cordis_surgical_scalar_preserves_js(self):
        cordis = (
            'name: cc\n'
            'plugins:\n'
            '  - id: tool-subagent\n'
            '    config:\n'
            '      agentOptions:\n'
            '        provider: WRONGPROVIDER\n'
            '        model: WRONGMODEL\n'
            '  - id: tool-bash\n'
            '    disabled: !!js/function (ctx) => true\n'
        )
        p = self.dsh / ".agent-presets" / "cc" / "agent.cordis.yml"
        p.write_text(cordis, encoding="utf-8")
        policy = aic.load_canonical()["policy"]
        want_p = policy["rules"]["subagent_spawn"]["provider"]
        want_m = policy["rules"]["subagent_spawn"]["model"]
        new = aic._cordis_set_scalar(
            aic._cordis_set_scalar(cordis, "tool-subagent",
                                   "config.agentOptions.provider", want_p),
            "tool-subagent", "config.agentOptions.model", want_m)
        self.assertIsNotNone(new)
        self.assertIn(f"provider: {want_p}", new)
        self.assertIn(f"model: {want_m}", new)
        self.assertIn("!!js/function (ctx) => true", new)   # opaque 原样保留
        self.assertIsNone(aic._cordis_set_scalar(cordis, "no-such-row", "config.x", "1"))


class TestIncrementalDependency(unittest.TestCase):
    """§8：受影响面映射。"""

    def test_memory_only_no_harness(self):
        self.assertEqual(pas.affected_targets([], ["memory/records/x/record.yaml"]), [])

    def test_model_registry_consumers(self):
        t = pas.affected_targets(["registry/models.yaml"], [])
        self.assertEqual(t, ["codex"])                    # codex 消费 models（harness default 校验）
        self.assertNotIn("gemini", t)

    def test_gateway_mapping_hits_claude_switchboard(self):
        t = pas.affected_targets([], ["registry/gateways.yaml"])
        self.assertEqual(sorted(t), ["claude", "switchboard"])

    def test_skill_only_no_harness_render(self):
        self.assertEqual(pas.affected_targets(["skills/x/SKILL.md"], []), [])

    def test_routing_policy_hits_dsh_and_switchboard(self):
        t = pas.affected_targets(["registry/routing-policy.yaml"], [])
        self.assertEqual(sorted(t), ["dsh", "switchboard"])


class TestPhysicalLiveDrift(unittest.TestCase):
    """§10：真实 Harness 上的物理 drift 测试（每个 installed target 一个无害 generated 字段）。

    try/finally 保证：即使断言失败也恢复原始字节。
    """

    def _live(self, rel_home: str, fname: str) -> Path:
        return Path.home() / rel_home / fname

    def test_switchboard_routing_preferences(self):
        f = self._live(".agent-broker", "config.json")
        if not f.is_file():
            self.skipTest("switchboard 未安装")
        before = f.read_bytes()
        try:
            data = json.loads(before.decode("utf-8-sig"))
            data["routing_preferences"]["fast"] = ["__DRIFT__"]
            f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            rc, out = run_aic("diff", "switchboard")
            self.assertEqual(rc, 1, "drift 未被 diff 发现")
            rc, out = run_aic("apply", "switchboard")
            self.assertEqual(rc, 0, out)
            rc, out = run_aic("diff", "switchboard")
            self.assertEqual(rc, 0, f"post-diff 非 NO DRIFT: {out}")
            after = json.loads(f.read_text(encoding="utf-8-sig"))
            self.assertEqual(after.get("cli_backends", {}), data.get("cli_backends", {}))
        finally:
            rc, _ = run_aic("diff", "switchboard")
            if rc != 0:
                f.write_bytes(before)

    def test_claude_model_alias(self):
        f = self._live(".claude", "settings.json")
        if not f.is_file():
            self.skipTest("claude 未安装")
        before = f.read_bytes()
        try:
            data = json.loads(before.decode("utf-8-sig"))
            data["model"] = "__DRIFT__"
            f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            rc, _ = run_aic("diff", "claude")
            self.assertEqual(rc, 1)
            rc, out = run_aic("apply", "claude")
            self.assertEqual(rc, 0, out)
            rc, out = run_aic("diff", "claude")
            self.assertEqual(rc, 0, out)
            after = json.loads(f.read_text(encoding="utf-8-sig"))
            self.assertEqual(after.get("hooks"), data.get("hooks"))   # overlay 保留
        finally:
            rc, _ = run_aic("diff", "claude")
            if rc != 0:
                f.write_bytes(before)

    def test_codex_model_toml(self):
        f = self._live(".codex", "config.toml")
        if not f.is_file():
            self.skipTest("codex 未安装")
        before = f.read_bytes()
        try:
            text = before.decode("utf-8-sig")
            import re as _re
            drifted = _re.sub(r'^model = .*$', 'model = "__DRIFT__"', text, count=1, flags=_re.M)
            self.assertNotEqual(drifted, text)
            f.write_text(drifted, encoding="utf-8")
            rc, _ = run_aic("diff", "codex")
            self.assertEqual(rc, 1)
            rc, out = run_aic("apply", "codex")
            self.assertEqual(rc, 0, out)
            rc, out = run_aic("diff", "codex")
            self.assertEqual(rc, 0, out)
            newtext = f.read_text(encoding="utf-8")
            for marker in ("sandbox", "projects"):        # overlay 段仍在
                self.assertIn(marker, newtext)
        finally:
            rc, _ = run_aic("diff", "codex")
            if rc != 0:
                f.write_bytes(before)

    def test_gemini_mcpservers_superset(self):
        f = self._live(".gemini", "settings.json")
        if not f.is_file():
            self.skipTest("gemini 未安装")
        before = f.read_bytes()
        try:
            data = json.loads(before.decode("utf-8-sig"))
            caps = aic.load_yaml(aic.REG / "capabilities.yaml")["capabilities"]["mcp_standard"]
            victim = sorted(caps)[0]
            removed = data.get("mcpServers", {}).pop(victim, None)
            if removed is None:
                self.skipTest("live 已无该 key")
            f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            rc, _ = run_aic("diff", "gemini")
            self.assertEqual(rc, 1)
            rc, out = run_aic("apply", "gemini")
            self.assertEqual(rc, 0, out)
            rc, out = run_aic("diff", "gemini")
            self.assertEqual(rc, 0, out)
            after = json.loads(f.read_text(encoding="utf-8-sig"))
            self.assertIn(victim, after["mcpServers"])
        finally:
            rc, _ = run_aic("diff", "gemini")
            if rc != 0:
                f.write_bytes(before)

    def test_dsh_settings_default_model(self):
        f = Path(os.environ.get("DSH_HOME", Path.home() / ".dsh")) / "settings.yaml"
        if not f.is_file():
            self.skipTest("dsh 未安装")
        before = f.read_bytes()
        try:
            import yaml
            data = yaml.safe_load(before.decode("utf-8-sig"))
            data["agent-default-model"]["reasoningEffort"] = "__DRIFT__"
            f.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                         encoding="utf-8")
            rc, _ = run_aic("diff", "dsh")
            self.assertEqual(rc, 1)
            rc, out = run_aic("apply", "dsh")
            self.assertEqual(rc, 0, out)
            rc, out = run_aic("diff", "dsh")
            self.assertEqual(rc, 0, out)
            after = yaml.safe_load(f.read_text(encoding="utf-8-sig"))
            self.assertEqual(after.get("ui-onboarding"), data.get("ui-onboarding"))
        finally:
            rc, _ = run_aic("diff", "dsh")
            if rc != 0:
                f.write_bytes(before)


class TestMultiDeviceRuntimeConvergence(unittest.TestCase):
    """§9：A 改 canonical → push；B "同步一下" → PULL → affected → apply → NO DRIFT。"""

    def test_canonical_to_runtime_convergence(self):
        import os as _os
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
            # 两个 clone 带上本 Change 的工作树 aic.py（模拟本变更已部署）；
            # devB 不复制：A 的提交会通过 pull 把新 aic.py 带过去（B 工作树保持干净）
            shutil.copy2(REPO / "scripts" / "aic" / "aic.py",
                         devA / "scripts" / "aic" / "aic.py")

            # B 的 HOME：按旧 canonical 渲染的 switchboard config（制造 runtime drift）
            homeB = td / "homeB"
            broker = homeB / ".agent-broker"
            broker.mkdir(parents=True)
            import yaml
            policy = yaml.safe_load((devB / "registry" / "routing-policy.yaml")
                                    .read_text(encoding="utf-8-sig"))
            old_prefs = {k: v for k, v in policy["rules"]["broker_preferences"].items()
                         if k != "evidence"}
            cfg = {"routing_preferences": old_prefs,
                   "cli_backends": {"cpa": {"base_url": "http://127.0.0.1:8317"}},
                   "providers": {"CPA": {"base_url": "http://127.0.0.1:8317"}},
                   "codex_path": "C:\\machine-local\\codex.exe"}     # MACHINE_LOCAL overlay
            (broker / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")

            # A：合法 canonical 变更（broker_preferences.fast 追加一项）
            pa = devA / "registry" / "routing-policy.yaml"
            doc = yaml.safe_load(pa.read_text(encoding="utf-8-sig"))
            doc["rules"]["broker_preferences"]["fast"] = ["cpa", "gemini_cli"]
            pa.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                          encoding="utf-8")
            subprocess.run(["git", "-C", str(devA), "add", "."], check=True,
                           capture_output=True, env=env_git)
            subprocess.run(["git", "-C", str(devA), "commit", "-m", "routing: fast+gemini"],
                           check=True, capture_output=True, env=env_git)
            subprocess.run(["git", "-C", str(devA), "push", "origin", "main"],
                           check=True, capture_output=True, env=env_git)

            # B：pull → diff 必须发现 drift → apply → NO DRIFT
            # PERSONAL_AI_STATE 指向真实 state 仓库（gateway SSOT，只读消费）
            envB = {**os.environ, "HOME": str(homeB), "USERPROFILE": str(homeB),
                    "PERSONAL_AI_STATE": str(Path.home() / "personal-ai-state")}
            aicB = devB / "scripts" / "aic" / "aic.py"
            subprocess.run(["git", "-C", str(devB), "pull", "--ff-only", "origin", "main"],
                           check=True, capture_output=True, env=env_git)
            # pull 后 registry 变了 → diff 必须发现 drift
            p = subprocess.run([sys.executable, str(aicB), "diff", "switchboard"],
                               capture_output=True, text=True, env=envB,
                               encoding="utf-8", errors="replace")
            self.assertEqual(p.returncode, 1, "pull 后 drift 未发现")
            p = subprocess.run([sys.executable, str(aicB), "apply", "switchboard"],
                               capture_output=True, text=True, env=envB,
                               encoding="utf-8", errors="replace")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            p = subprocess.run([sys.executable, str(aicB), "diff", "switchboard"],
                               capture_output=True, text=True, env=envB,
                               encoding="utf-8", errors="replace")
            self.assertEqual(p.returncode, 0, f"post-diff 非 NO DRIFT: {p.stdout}")
            after = json.loads((broker / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(after["routing_preferences"]["fast"], ["cpa", "gemini_cli"])
            self.assertEqual(after["codex_path"], "C:\\machine-local\\codex.exe")  # overlay 不动
        finally:
            td_obj.cleanup()


if __name__ == "__main__":
    unittest.main()
