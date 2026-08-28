#!/usr/bin/env python3
"""test_bootstrap_seed.py — BOOTSTRAP.md（Personal AI Bootstrap Seed）契约与端到端演练。

契约：根目录 BOOTSTRAP.md 是唯一入口种子，必须
- 显式固化唯一用户触发句（README 第一屏同样指向它）；
- 同时覆盖"没安装 Skill → bootstrap → full profile → handoff RESTORE"与
  "已安装 → handoff AUTO_SYNC"两条分支；
- 只引用仓库内真实存在的脚本/Skill 路径；
- 明确"仅认证/secret/dirty/diverged/privacy 冲突才报告用户"。

端到端演练：fresh-device 在独立 temp 目录物理执行 BOOTSTRAP §2 的
克隆 → 校验 → 安装 full profile → RESTORE 全链路，不触碰 live 环境。
（restore 的 state 侧用最小 canonical fixture，避免在 CI 上触发 aic discover
 与真实 private remote；agent-tools 侧用真实仓库克隆，覆盖 §2 第 2-4 步。）
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "memory"))

import personal_ai_sync as pas  # noqa: E402
from provider import FileMemoryProvider  # noqa: E402

BOOTSTRAP = ROOT / "BOOTSTRAP.md"
TRIGGER = ("读取 https://github.com/ooooooooooooooooooop/agent-tools/"
           "blob/main/BOOTSTRAP.md，自主配置这台电脑")

GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(cmd, cwd=str(cwd or ROOT), text=True, capture_output=True,
                          encoding="utf-8", errors="replace", env=env, check=False)


class TestBootstrapSeedContract(unittest.TestCase):
    """BOOTSTRAP.md 静态契约。"""

    @classmethod
    def setUpClass(cls):
        cls.text = BOOTSTRAP.read_text(encoding="utf-8")

    def test_seed_exists_and_declares_single_trigger(self):
        self.assertIn(TRIGGER, self.text)
        # 触发句必须指向本仓库 main 分支的 BOOTSTRAP.md
        self.assertIn("ooooooooooooooooooop/agent-tools/blob/main/BOOTSTRAP.md", self.text)

    def test_readme_first_screen_points_to_bootstrap(self):
        first_screen = "\n".join(
            (ROOT / "README.md").read_text(encoding="utf-8").splitlines()[:20])
        self.assertIn("BOOTSTRAP.md", first_screen)
        self.assertIn(TRIGGER, first_screen)

    def test_covers_fresh_and_installed_branches(self):
        # 未安装分支：bootstrap → full profile → handoff → RESTORE
        self.assertIn("--profile full", self.text)
        self.assertIn("publish-and-reuse", self.text)
        self.assertIn("RESTORE", self.text)
        # 已安装分支：直接 handoff → AUTO_SYNC
        self.assertIn("AUTO_SYNC", self.text)
        self.assertIn("personal_ai_sync.py sync", self.text)
        self.assertIn("personal_ai_sync.py restore", self.text)

    def test_direction_judgement_delegated_to_orchestrator(self):
        for keyword in ("PULL", "PUSH", "MERGE", "NO ACTION", "REVIEW"):
            self.assertIn(keyword, self.text)
        self.assertIn("git ancestry", self.text)

    def test_escalation_only_for_real_blockers(self):
        for keyword in ("BLOCKED_AUTH", "LOCAL_DIRTY", "DIVERGED",
                        "BLOCKED_PRIVACY", "MISSING"):
            self.assertIn(keyword, self.text)
        self.assertIn("用户不需要", self.text)

    def test_referenced_repo_paths_exist(self):
        # 相对 markdown 链接必须指向仓库内真实路径
        for m in re.finditer(r"\]\((\./[^)#\s]+)", self.text):
            target = (ROOT / m.group(1)).resolve()
            self.assertTrue(target.exists(), f"broken link: {m.group(1)}")
        # 文内引用的脚本/Skill 路径必须存在
        for rel in ("scripts/personal_ai_sync.py", "scripts/sync_skills.py",
                    "scripts/validate_repo.py", "scripts/aic/aic.py",
                    "skills/publish-and-reuse/SKILL.md",
                    "skills/publish-and-reuse/references/personal-ai-lifecycle-sync.md",
                    "skills.json"):
            self.assertIn(rel, self.text)
            self.assertTrue((ROOT / rel).is_file(), rel)


class TestFreshDeviceBootstrapDrill(unittest.TestCase):
    """fresh-device 端到端演练：temp 目录模拟全新设备，不碰 live 环境。"""

    def test_clone_validate_install_restore(self):
        with tempfile.TemporaryDirectory(prefix="bootstrap-drill-") as raw:
            td = Path(raw)
            home = td / "home"
            repo = home / "Desktop" / "skills"

            # §2.2 克隆 canonical（本地克隆等价于 fresh clone 的已提交内容）
            clone = subprocess.run(["git", "clone", str(ROOT), str(repo)],
                                   capture_output=True, text=True, env=GIT_ENV)
            self.assertEqual(clone.returncode, 0, clone.stderr)
            self.assertTrue((repo / "BOOTSTRAP.md").is_file())

            # §2.3 校验 canonical
            v = run([sys.executable, "scripts/validate_repo.py", "--strict"], cwd=repo)
            self.assertEqual(v.returncode, 0, v.stdout + v.stderr)

            # §2.4 安装 full Skill profile 到"新设备" Harness skill 目录
            skills_dir = home / ".dsh" / "skills"
            a = run([sys.executable, "scripts/sync_skills.py", "--destination",
                     str(skills_dir), "--profile", "full", "--apply"], cwd=repo)
            self.assertEqual(a.returncode, 0, a.stdout + a.stderr)
            self.assertTrue((skills_dir / "publish-and-reuse" / "SKILL.md").is_file())
            c = run([sys.executable, "scripts/sync_skills.py", "--destination",
                     str(skills_dir), "--profile", "full", "--check"], cwd=repo)
            self.assertEqual(c.returncode, 0, c.stdout + c.stderr)

            # §2.5 RESTORE：state 侧最小 canonical fixture（bare remote + 一条记忆）
            remote = td / "state.git"
            subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)],
                           capture_output=True, check=True, env=GIT_ENV)
            work = td / "state-src"
            subprocess.run(["git", "clone", str(remote), str(work)],
                           capture_output=True, check=True, env=GIT_ENV)
            FileMemoryProvider(str(work), device_id="fresh-drill").write(
                scope="global", type="note", content="新设备恢复演练记忆",
                provenance={"source": "test"})
            for args in (["add", "."], ["commit", "-m", "mem"],
                         ["push", "origin", "main"]):
                subprocess.run(["git", "-C", str(work), *args],
                               capture_output=True, check=True, env=GIT_ENV)

            dest_state = home / "personal-ai-state"
            # restore 校验的 skills_dest 必须就是 §2.4 装好的 SKILLS_DIR（与 BOOTSTRAP 流程一致）
            r = pas.run_restore(repo=repo, state_repo=dest_state,
                                skills_dest=skills_dir,
                                agent_tools_remote=str(ROOT),
                                state_remote=str(remote))
            steps = {s["step"]: s["ok"] for s in r["steps"]}
            self.assertTrue(steps["clone personal-ai-state"], r["steps"])
            self.assertTrue(steps["validate canonical"], r["steps"])
            self.assertTrue(steps["skills restore (apply)"], r["steps"])
            self.assertTrue(steps["memory loadable"], r["steps"])
            v2 = pas.memory_merge_verify(dest_state)
            self.assertTrue(v2["ok"])
            self.assertEqual(v2["records"], 1)
            self.assertEqual(r["result"], "PASS", r["steps"])

            # 幂等：完整重跑一次仍 PASS
            r2 = pas.run_restore(repo=repo, state_repo=dest_state,
                                 skills_dest=skills_dir,
                                 agent_tools_remote=str(ROOT),
                                 state_remote=str(remote))
            self.assertEqual(r2["result"], "PASS", r2["steps"])


if __name__ == "__main__":
    unittest.main()
