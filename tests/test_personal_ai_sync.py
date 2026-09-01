#!/usr/bin/env python3
"""test_personal_ai_sync.py — PERSONAL_AI_LIFECYCLE_SYNC red-team 测试矩阵（§37/§38/§39）。

全部使用独立 temp git 仓库（bare remote + 双 working copies），不触碰 live 环境。
关键验收：NO_SILENT_DATA_LOSS / NO_SILENT_OVERWRITE / IDEMPOTENT_RERUN。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "memory"))

import personal_ai_sync as pas  # noqa: E402
from provider import FileMemoryProvider  # noqa: E402

GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(repo: Path, *args: str, check: bool = True) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                       text=True, env=GIT_ENV, encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise AssertionError(f"git {args} failed: {p.stderr}")
    return (p.stdout + p.stderr).strip()


def make_remote_with_clone(td: Path, name: str = "r") -> tuple[Path, Path]:
    """bare remote + 一个初始 clone（main 分支，一个 commit）。"""
    remote = td / f"{name}.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)],
                   capture_output=True, check=True, env=GIT_ENV)
    work = td / name
    subprocess.run(["git", "clone", str(remote), str(work)],
                   capture_output=True, check=True, env=GIT_ENV)
    (work / "seed.txt").write_text("seed", encoding="utf-8")
    git(work, "add", ".")
    git(work, "commit", "-m", "init")
    git(work, "push", "origin", "main")
    return remote, work


def commit_file(repo: Path, rel: str, content: str, msg: str = "c") -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    git(repo, "add", rel)
    git(repo, "commit", "-m", msg)


def state_provider(state_repo: Path, device: str) -> FileMemoryProvider:
    return FileMemoryProvider(str(state_repo), device_id=device)


class TestGitClassification(unittest.TestCase):
    """§37.1-5：五种仓库状态分类。"""

    def setUp(self):
        self.td_obj = tempfile.TemporaryDirectory()
        self.td = Path(self.td_obj.name)
        self.remote, self.work = make_remote_with_clone(self.td)

    def tearDown(self):
        self.td_obj.cleanup()

    def classify(self):
        return pas.classify_repo(self.work)

    def test_in_sync(self):
        c = self.classify()
        self.assertEqual(c["state"], pas.IN_SYNC)

    def test_remote_ahead_and_ff_pull(self):
        other = self.td / "other"
        subprocess.run(["git", "clone", str(self.remote), str(other)],
                       capture_output=True, check=True, env=GIT_ENV)
        commit_file(other, "new.txt", "remote change")
        git(other, "push", "origin", "main")
        c = self.classify()
        self.assertEqual(c["state"], pas.REMOTE_AHEAD)
        # AUTO_SYNC：clean FF pull 安全执行
        plan = pas.plan_actions({"agent-tools": c}, None, "sync")
        self.assertEqual(plan[0]["action"], "PULL")
        pas.execute_plan(plan, {"agent-tools": c}, None, "sync", {})
        self.assertTrue((self.work / "new.txt").is_file())
        self.assertEqual(plan[0]["state"], "PULLED")

    def test_local_ahead_push(self):
        commit_file(self.work, "local.txt", "local change")
        c = self.classify()
        self.assertEqual(c["state"], pas.LOCAL_AHEAD)
        plan = pas.plan_actions({"x": c}, None, "sync")
        self.assertEqual(plan[0]["action"], "PUSH")
        pas.execute_plan(plan, {"x": c}, None, "sync", {})
        self.assertEqual(plan[0]["state"], "PUSHED")
        self.assertEqual(pas.classify_repo(self.work)["state"], pas.IN_SYNC)

    def test_local_dirty_untouched(self):
        (self.work / "seed.txt").write_text("dirty", encoding="utf-8")
        c = self.classify()
        self.assertEqual(c["state"], pas.LOCAL_DIRTY)
        plan = pas.plan_actions({"x": c}, None, "sync")
        self.assertEqual(plan[0]["action"], "UNTOUCHED")
        pas.execute_plan(plan, {"x": c}, None, "sync", {})
        self.assertEqual((self.work / "seed.txt").read_text(), "dirty")  # 未被覆盖

    def test_diverged_review(self):
        other = self.td / "other"
        subprocess.run(["git", "clone", str(self.remote), str(other)],
                       capture_output=True, check=True, env=GIT_ENV)
        commit_file(other, "a.txt", "A")
        git(other, "push", "origin", "main")
        commit_file(self.work, "b.txt", "B")
        c = self.classify()
        self.assertEqual(c["state"], pas.DIVERGED)
        plan = pas.plan_actions({"x": c}, None, "sync")
        self.assertEqual(plan[0]["action"], "REVIEW")

    def test_check_mode_never_writes(self):
        other = self.td / "other"
        subprocess.run(["git", "clone", str(self.remote), str(other)],
                       capture_output=True, check=True, env=GIT_ENV)
        commit_file(other, "new.txt", "remote")
        git(other, "push", "origin", "main")
        c = self.classify()
        plan = pas.plan_actions({"x": c}, None, "check")
        pas.execute_plan(plan, {"x": c}, None, "check", {})
        self.assertFalse((self.work / "new.txt").is_file())  # check 不 pull


class TestPrivacyScan(unittest.TestCase):
    """§37.17：public push 前隐私命中。"""

    def test_secret_hit(self):
        with tempfile.TemporaryDirectory() as td:
            remote, work = make_remote_with_clone(Path(td))
            commit_file(work, "cfg.txt", 'api_key: "sk-abcdefghijklmnopqrstuvwxyz"')
            hits = pas.privacy_scan(work, "origin/main..HEAD")
            self.assertTrue(hits)

    def test_clean_pass(self):
        with tempfile.TemporaryDirectory() as td:
            remote, work = make_remote_with_clone(Path(td))
            commit_file(work, "readme.md", "hello docs")
            self.assertEqual(pas.privacy_scan(work, "origin/main..HEAD"), [])


class StateRepoFixture:
    """双设备 personal-ai-state 模拟：bare remote + devA + devB（§38）。"""

    def __init__(self, td: Path):
        self.remote, self.devA = make_remote_with_clone(td, "state")
        (self.devA / "state").mkdir(exist_ok=True)
        (self.devA / "state" / "identity.md").write_text("# identity", encoding="utf-8")
        (self.devA / "state" / "preferences.md").write_text("# prefs v1", encoding="utf-8")
        git(self.devA, "add", ".")
        git(self.devA, "commit", "-m", "baseline")
        git(self.devA, "push", "origin", "main")
        self.devB = td / "stateB"
        subprocess.run(["git", "clone", str(self.remote), str(self.devB)],
                       capture_output=True, check=True, env=GIT_ENV)

    def push(self, repo: Path):
        git(repo, "add", ".")
        git(repo, "commit", "-m", "dev change", check=False)
        git(repo, "push", "origin", "main")

    def sync_on(self, repo: Path) -> dict:
        c = pas.classify_repo(repo)
        results: dict = {}
        plan = pas.plan_actions({"personal-ai-state": c}, repo, "sync")
        pas.execute_plan(plan, {"personal-ai-state": c}, repo, "sync", results)
        return {"plan": plan[0], "results": results}


class TestStateCuratedConflict(unittest.TestCase):
    """§37.6-8：curated state 规则。"""

    def test_remote_ahead_pull(self):
        with tempfile.TemporaryDirectory() as td:
            f = StateRepoFixture(Path(td))
            (f.devB / "state" / "goals.md").write_text("# goals", encoding="utf-8")
            f.push(f.devB)
            r = f.sync_on(f.devA)
            self.assertEqual(r["plan"]["state"], "PULLED")
            self.assertTrue((f.devA / "state" / "goals.md").is_file())

    def test_local_ahead_push(self):
        with tempfile.TemporaryDirectory() as td:
            f = StateRepoFixture(Path(td))
            (f.devA / "state" / "goals.md").write_text("# goals A", encoding="utf-8")
            f.push(f.devA)
            r = f.sync_on(f.devA)  # 已 push 后 IN_SYNC；先构造 ahead 场景：
            # 重新制造 local ahead
            (f.devA / "state" / "goals.md").write_text("# goals A2", encoding="utf-8")
            git(f.devA, "add", ".")
            git(f.devA, "commit", "-m", "a2")
            c = pas.classify_repo(f.devA)
            self.assertEqual(c["state"], pas.LOCAL_AHEAD)

    def test_preferences_double_modified_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            f = StateRepoFixture(Path(td))
            (f.devA / "state" / "preferences.md").write_text("# prefs A", encoding="utf-8")
            f.push(f.devA)
            # B 在未 fetch 情况下也改同一文件
            (f.devB / "state" / "preferences.md").write_text("# prefs B", encoding="utf-8")
            git(f.devB, "add", ".")
            git(f.devB, "commit", "-m", "B prefs")
            c = pas.classify_repo(f.devB)
            self.assertEqual(c["state"], pas.DIVERGED)
            r = f.sync_on(f.devB)
            self.assertEqual(r["plan"]["state"], pas.CONFLICT)
            self.assertIn("curated", r["plan"]["reason"])
            # 禁止 last-write-wins：B 的本地内容未被覆盖，merge 未发生
            self.assertEqual((f.devB / "state" / "preferences.md").read_text(), "# prefs B")
            rc, out = pas.git(f.devB, "log", "--oneline", "-1")
            self.assertIn("B prefs", out)


class TestMultiDeviceMemoryMerge(unittest.TestCase):
    """§37.9-12 + §38：多设备 Memory 合并物理模拟。"""

    def test_unique_records_auto_merge_no_loss(self):
        with tempfile.TemporaryDirectory() as td:
            f = StateRepoFixture(Path(td))
            state_provider(f.devA, "A").write(
                scope="global", type="note", content="A 的独有记忆",
                provenance={"source": "test"})
            f.push(f.devA)
            state_provider(f.devB, "B").write(
                scope="global", type="note", content="B 的独有记忆",
                provenance={"source": "test"})
            git(f.devB, "add", ".")
            git(f.devB, "commit", "-m", "B memory")
            r = f.sync_on(f.devB)
            self.assertEqual(r["plan"]["state"], "MERGED", r["plan"].get("reason"))
            # 双方 unique content 都在
            bundle = state_provider(f.devB, "v").export()
            contents = [rev["content"] for it in bundle["records"]
                        for rev in it["revisions"]]
            self.assertIn("A 的独有记忆", contents)
            self.assertIn("B 的独有记忆", contents)
            # 幂等重跑：不制造 duplicate
            n1 = len(bundle["records"])
            r2 = f.sync_on(f.devB)
            self.assertEqual(r2["plan"]["state"], pas.IN_SYNC)
            bundle2 = state_provider(f.devB, "v").export()
            self.assertEqual(len(bundle2["records"]), n1)

    def test_distinct_revisions_auto_merge(self):
        with tempfile.TemporaryDirectory() as td:
            f = StateRepoFixture(Path(td))
            base = state_provider(f.devA, "A").write(
                scope="global", type="note", content="基线内容",
                provenance={"source": "test"})
            f.push(f.devA)
            git(f.devB, "pull", "--ff-only", "origin", "main")
            # A 更新 record（rev2），B 也更新（rev3）→ disjoint revision files
            pa = state_provider(f.devA, "A")
            pa.update(base["id"], "A 的修订", by_agent="t")
            f.push(f.devA)
            pb = state_provider(f.devB, "B")
            pb.update(base["id"], "B 的修订", by_agent="t")
            git(f.devB, "add", ".")
            git(f.devB, "commit", "-m", "B rev")
            r = f.sync_on(f.devB)
            self.assertEqual(r["plan"]["state"], "MERGED")
            self.assertEqual(r["plan"]["action"], "REVIEW")  # concurrent 被标记
            self.assertIn("CONFLICT_CONCURRENT_REVISION", r["plan"]["reason"])
            # 双方 revision 都保留
            prov = state_provider(f.devB, "v")
            revs = prov._revisions(base["id"])
            contents = [x["content"] for x in revs]
            self.assertIn("A 的修订", contents)
            self.assertIn("B 的修订", contents)
            self.assertIn("基线内容", contents)

    def test_incompatible_immutable_metadata_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            f = StateRepoFixture(Path(td))
            r0 = state_provider(f.devA, "A").write(
                scope="global", type="note", content="原始", provenance={"source": "t"})
            f.push(f.devA)
            git(f.devB, "pull", "--ff-only", "origin", "main")
            # 双端改同一 record.yaml（不可变元数据不一致）
            ry = Path("memory/records") / r0["id"] / "record.yaml"
            (f.devA / ry).write_text("id: %s\nscope: global\n" % r0["id"], encoding="utf-8")
            f.push(f.devA)
            (f.devB / ry).write_text("id: %s\nscope: TAMPERED\n" % r0["id"], encoding="utf-8")
            git(f.devB, "add", ".")
            git(f.devB, "commit", "-m", "tamper")
            r = f.sync_on(f.devB)
            self.assertEqual(r["plan"]["state"], pas.CONFLICT)
            self.assertIn("immutable", r["plan"]["reason"])
            # 未被自动修正
            self.assertIn("TAMPERED", (f.devB / ry).read_text())


class TestSecretsAndCheckpoint(unittest.TestCase):
    """§37.22/23/25：secret 引用检测 + checkpoint 幂等。"""

    def test_secret_missing_and_present(self):
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td) / "settings.yaml"
            fake.write_text("apiKeyEnv: DEFINITELY_MISSING_KEY_XYZ\n"
                            "apiKeyEnv: PATH\n", encoding="utf-8")
            old = pas.SETTINGS
            pas.SETTINGS = fake
            try:
                s = pas.check_secrets()
            finally:
                pas.SETTINGS = old
            self.assertEqual(s["status"], "PARTIAL")
            self.assertIn("DEFINITELY_MISSING_KEY_XYZ", s["missing"])
            self.assertNotIn("PATH", s["missing"])  # 已存在的不算缺失

    def test_checkpoint_write_and_reload(self):
        with tempfile.TemporaryDirectory() as td:
            old = pas.CHECKPOINT
            pas.CHECKPOINT = Path(td) / ".personal-ai-sync" / "status.json"
            try:
                pas.save_checkpoint({"device_id": "d1", "last_result": "PASS"})
                cp = pas.load_checkpoint()
                self.assertEqual(cp["device_id"], "d1")
                pas.save_checkpoint({"device_id": "d1", "last_result": "PASS"})  # 重跑幂等
                self.assertEqual(pas.load_checkpoint()["last_result"], "PASS")
            finally:
                pas.CHECKPOINT = old


class TestAffectedTargets(unittest.TestCase):
    def test_preferences_refreshes_all_instruction_consumers(self):
        self.assertEqual(
            pas.affected_targets([], ["state/preferences.md"]),
            ["claude", "codex", "dsh", "gemini", "switchboard"],
        )


class TestFreshRestoreRehearsal(unittest.TestCase):
    """§39：empty local state 恢复演练（temp destination，不碰 live）。"""

    def test_restore_from_empty(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            # 造最小 canonical remote：agent-tools-like + personal-ai-state-like
            at_remote, at_work = make_remote_with_clone(td / "a", "at")
            (at_work / "scripts").mkdir(exist_ok=True)
            commit_file(at_work, "SKILLS.md", "canonical")
            git(at_work, "push", "origin", "main")
            st = StateRepoFixture(td / "s")
            state_provider(st.devA, "rehearsal").write(
                scope="global", type="note", content="恢复演练记忆",
                provenance={"source": "test"})
            st.push(st.devA)

            dest_at = td / "restore" / "agent-tools"
            dest_st = td / "restore" / "personal-ai-state"
            dest_skills = td / "restore" / "skills"
            r = pas.run_restore(repo=dest_at, state_repo=dest_st,
                                skills_dest=dest_skills, apply_dsh=False,
                                agent_tools_remote=str(at_remote),
                                state_remote=str(st.remote))
            steps = {s["step"]: s["ok"] for s in r["steps"]}
            self.assertTrue(steps["clone agent-tools"])
            self.assertTrue(steps["clone personal-ai-state"])
            self.assertTrue(steps["memory loadable"])
            self.assertTrue((dest_at / "SKILLS.md").is_file())
            # memory canonical 恢复且可读
            v = pas.memory_merge_verify(dest_st)
            self.assertTrue(v["ok"])
            self.assertEqual(v["records"], 1)
            # 幂等：再跑一次 already present
            r2 = pas.run_restore(repo=dest_at, state_repo=dest_st,
                                 skills_dest=dest_skills, apply_dsh=False,
                                 agent_tools_remote=str(at_remote),
                                 state_remote=str(st.remote))
            self.assertEqual(r2["result"], "PASS")


class TestActiveProjectDiscovery(unittest.TestCase):
    """§14：ACTIVE/PAUSED 识别。"""

    def test_discover_projects_real_state(self):
        projs = pas.discover_projects(pas.STATE_REPO)
        names = {p["name"]: p for p in projs}
        # 本机真实设备元数据决定 projects 集合；novel-main 仅在被克隆/登记时才出现。
        # 无论是否存在，infra 仓库(skills/agent-tools/personal-ai-state)必须被排除。
        self.assertNotIn("skills", names)      # infra 排除
        self.assertNotIn("personal-ai-state", names)
        self.assertNotIn("agent-tools", names)
        if names.get("novel-main"):
            self.assertEqual(names["novel-main"]["status"], "PAUSED")  # paused_external_auth
            self.assertTrue(names["novel-main"]["privacy_blocked"])


if __name__ == "__main__":
    unittest.main()
