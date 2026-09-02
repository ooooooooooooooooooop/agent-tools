#!/usr/bin/env python3
"""personal_ai_sync.py — Personal AI Infrastructure 生命周期同步薄编排器。

职责只允许：inspect / classify / 调用既有确定性工具 / 排序 / 报告。
禁止：实现自己的 Memory 数据库、取代 git/aic/governance、做 LLM 语义合并、变 daemon。

模式：check（只读）/ pull / push / sync（AUTO_SYNC 默认）/ restore（local canonical missing）。

方向判定只用 git ancestry（fetch → HEAD vs remote HEAD → ahead/behind → dirty），
禁止 mtime / last-write-wins。Memory 语义合并复用 MemoryProvider.import_bundle
冻结契约（scripts/memory/provider.py）；curated state 双端修改 → CONFLICT_REVIEW。

机器本地 checkpoint：~/.dsh/.personal-ai-sync/status.json（不进任何 canonical）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STATE_REPO = Path.home() / "personal-ai-state"
CHECKPOINT = Path.home() / ".dsh" / ".personal-ai-sync" / "status.json"
AIC = REPO / "scripts" / "aic" / "aic.py"
SYNC_SKILLS = REPO / "scripts" / "sync_skills.py"
GOVERNANCE_TASKS = REPO / "scripts" / "governance" / "register_governance_tasks.ps1"
PROVIDER_DIR = REPO / "scripts" / "memory"
SETTINGS = Path.home() / ".dsh" / "settings.yaml"

# 状态枚举（§6）
IN_SYNC = "IN_SYNC"
REMOTE_AHEAD = "REMOTE_AHEAD"
LOCAL_AHEAD = "LOCAL_AHEAD"
LOCAL_DIRTY = "LOCAL_DIRTY"
DIVERGED = "DIVERGED"
CONFLICT = "CONFLICT"
BLOCKED_AUTH = "BLOCKED_AUTH"
BLOCKED_PRIVACY = "BLOCKED_PRIVACY"
OPTIONAL_NOT_INSTALLED = "OPTIONAL_NOT_INSTALLED"
UNKNOWN = "UNKNOWN"

CURATED_PREFIXES = ("state/", "registry/", "sync/", "projects/", "README")
MEMORY_PREFIX = "memory/records/"

# push 前隐私扫描（public 仓库用）
PRIVACY_PATTERNS = [
    r"api[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}",
    r"Bearer\s+[A-Za-z0-9_\-\.]{16,}",
    r"\.credentials\.yaml",
    r"C:\\Users\\(?!admin\b)",  # 其他用户目录
    r"sk-[A-Za-z0-9]{20,}",
]

KNOWN_BLOCKERS = ["BACKUP_KEY_CUSTODY=WAITING_FOR_CUSTODY_ROOT",
                  "NOVEL_REPO_DURABILITY=BLOCKED_PRIVACY"]

# ---------------------------------------------------- DSH session history plane
# DSH session/history 属于 runtime/durable user data（data plane），不属于
# preferences canonical。restore 必须独立报告并验证 DSH_SESSION_HISTORY，
# 不得用一个总体 PASS 混同（事故：配置恢复 PASS 而历史会话消失）。
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
KNOWN_SESSION_ANCHORS = [
    # 历史明确存在的锚点（来自事故恢复记录；可按需扩展）
    "session-869904c0-fcd0-4ea3-a3b7-fec230ac8017",
]


def _device_backup_root() -> Path | None:
    """解析 durability backup root（personal-ai-state/sync/this-device.yaml）。
    失败返回 None（该设备未配置备份 = NOT_APPLICABLE 语义）。"""
    rel = Path.home() / "personal-ai-state" / "sync" / "this-device.yaml"
    if not rel.is_file():
        rel = Path(os.environ.get("PERSONAL_AI_STATE", "")) / "sync" / "this-device.yaml"
    if not rel.is_file():
        return None
    try:
        import yaml  # noqa: PLC0415
        cfg = yaml.safe_load(rel.read_text(encoding="utf-8-sig")) or {}
        root = cfg.get("backup_root")
        return Path(root) if root else None
    except Exception:  # noqa: BLE001
        return None


def _read_zstd_session_header(zstd_path: Path) -> dict | None:
    """从单个 session.jsonl.zstd 文件的首帧读取 SessionHeader。"""
    try:
        data = zstd_path.read_bytes()
        if len(data) < 4 or data[:4] != ZSTD_MAGIC:
            return None
        import zlib as _zlib
        # 首帧通常较小，尝试标准或 node-zstd 流式解压；标准库尝试 zlib/decompress
        # 若无 zstd C 库绑定，解析第一行明文 JSON
        try:
            import zstandard as _zstd  # noqa: PLC0415
            dctx = _zstd.ZstdDecompressor()
            first_line = dctx.decompress(data, max_output_size=65536).split(b"\n")[0]
            return json.loads(first_line.decode("utf-8"))
        except Exception:
            pass
    except Exception:
        pass
    return None


def session_history_status(live_root: Path, backup_root: Path | None = None,
                           anchors: list[str] | None = None,
                           storage_root: Path | None = None) -> dict:
    """DSH_SESSION_HISTORY plane：备份物理计数、live 物理计数、工作区挂载、锚点与可枚举性。

    同时验证两层：
      1. 物理层：live 物理文件存在、备份完整性、zstd 魔数与探针
      2. 逻辑/挂载层：workspace.json / workspaceRegistry 中已挂载 sessionIds 覆盖率、
         anchor 挂载状态、未挂载 session 发现、initialized 状态下的逻辑一致性。

    status 语义：
      PASS            物理文件完整 且 workspace 挂载全部覆盖 且 anchor 已挂载
      REVIEW          物理文件存在但 workspace 未挂载（本次事故模式），或存在未关联 session
      PARTIAL         live 物理缺失部分备份会话
      FAIL            物理与备份严重不匹配或 schema/文件损坏
      NOT_APPLICABLE  无备份索引（fresh 机器 / 从未备份过）
    """
    anchors = anchors or KNOWN_SESSION_ANCHORS
    live_count = 0
    live_names = set()
    if live_root.is_dir():
        for p in live_root.rglob("session.jsonl.zstd"):
            live_count += 1
            live_names.add(p.parent.name)

    # 1. 检查 workspace.json 挂载层（防复发核心逻辑门）
    st_root = storage_root or (live_root.parent / "storages")
    ws_file = st_root / "workspace.json"
    attached_session_ids = set()
    workspace_count = 0
    workspace_initialized = False
    unattached_sessions = []

    if ws_file.is_file():
        try:
            ws_data = json.loads(ws_file.read_text(encoding="utf-8"))
            workspace_initialized = bool(ws_data.get("global", {}).get("initialized", False))
            workspaces = ws_data.get("tables", {}).get("workspaces", {})
            workspace_count = len(workspaces)
            for ws_info in workspaces.values():
                for s_id in ws_info.get("sessionIds", []):
                    attached_session_ids.add(s_id)
        except Exception:
            pass

    # 计算未挂载的物理 root session（子代理 session 由 parentSession 索引，不挂在 workspace 顶层）
    root_live_names = {s for s in live_names if s.startswith("session-")}
    if root_live_names:
        unattached_sessions = sorted(list(root_live_names - attached_session_ids))

    attached_count = len(attached_session_ids)

    if backup_root is None or not backup_root.is_dir():
        # 无备份环境（如纯单机 / fresh 机器 / 从未备份过）
        anchor_res: dict[str, dict] = {}
        for a in anchors:
            anchor_res[a] = {
                "in_backup": False,
                "in_live": a in live_names,
                "attached": a in attached_session_ids,
            }

        status = "NOT_APPLICABLE"
        reason = "no backup root configured"

        return {
            "status": status,
            "reason": reason,
            "live_count": live_count,
            "backup_count": 0,
            "missing": 0,
            "attached_count": attached_count,
            "workspace_count": workspace_count,
            "unattached_count": len(unattached_sessions),
            "unattached_sample": unattached_sessions[:5],
            "anchors": anchor_res,
            "probe": {"checked": 0, "bad": 0}
        }

    idx = backup_root / "state" / "sessions-index.json"
    if not idx.is_file():
        return {
            "status": "NOT_APPLICABLE",
            "reason": "backup root present but sessions-index missing",
            "live_count": live_count,
            "backup_count": 0,
            "missing": 0,
            "attached_count": attached_count,
            "workspace_count": workspace_count,
            "unattached_count": len(unattached_sessions),
            "anchors": {},
            "probe": {"checked": 0, "bad": 0}
        }

    try:
        index = json.loads(idx.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "FAIL",
            "reason": f"sessions-index unreadable: {exc}",
            "live_count": live_count,
            "backup_count": -1,
            "missing": -1,
            "attached_count": attached_count,
            "workspace_count": workspace_count,
            "unattached_count": len(unattached_sessions),
            "anchors": {},
            "probe": {"checked": 0, "bad": 0}
        }

    backup_count = len(index)
    anchor_res: dict[str, dict] = {}
    for a in anchors:
        anchor_res[a] = {
            "in_backup": any(a in k for k in index),
            "in_live": a in live_names,
            "attached": a in attached_session_ids,
        }

    probe = {"checked": 0, "bad": 0}
    if live_root.is_dir():
        for p in sorted(live_root.rglob("session.jsonl.zstd"))[:3]:
            probe["checked"] += 1
            try:
                head = p.read_bytes()[:4]
                if len(head) != 4 or head != ZSTD_MAGIC or p.stat().st_size < 8:
                    probe["bad"] += 1
            except OSError:
                probe["bad"] += 1

    backup_session_ids = {Path(k).parent.name for k in index.keys()}
    unattached_backup = sorted(list((live_names & backup_session_ids) - attached_session_ids))
    missing = max(0, backup_count - live_count)

    # 门禁决策判定
    if probe["bad"]:
        status = "FAIL"
        reason = "zstd probe failed or corrupted"
    elif missing > 0 and missing >= backup_count:
        status = "FAIL"
        reason = "all backup sessions missing from live physical store"
    elif missing > 0:
        status = "PARTIAL"
        reason = f"missing {missing} backup sessions from live store"
    elif len(unattached_backup) > 0 and workspace_initialized:
        # 【关键防复发门禁】：物理 session 齐全，但 workspace.json 未全部挂载！
        status = "REVIEW"
        reason = f"workspace_unattached: {len(unattached_backup)} backup sessions not registered in workspace.json"
    elif not all(v.get("attached", False) for v in anchor_res.values() if v.get("in_backup")):
        # 锚点未挂载
        status = "REVIEW"
        reason = "one or more anchor sessions are physically present but NOT attached to workspaces"
    else:
        status = "PASS"
        reason = ""

    return {
        "status": status,
        "reason": reason,
        "live_count": live_count,
        "backup_count": backup_count,
        "missing": missing,
        "attached_count": attached_count,
        "workspace_count": workspace_count,
        "unattached_count": len(unattached_backup),
        "unattached_sample": unattached_backup[:5],
        "anchors": anchor_res,
        "probe": probe,
        "backup_source": str(idx)
    }


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 120,
        env: dict[str, str] | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace",
                           cwd=str(cwd) if cwd else None,
                           env={**os.environ, **env} if env else None)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return -1, str(exc)


def git(repo: Path, *args: str, timeout: int = 120) -> tuple[int, str]:
    return run(["git", "-C", str(repo), *args], timeout=timeout)


def _default_branch(repo: Path) -> str:
    rc, out = git(repo, "symbolic-ref", "--short", "HEAD")
    return out.strip() if rc == 0 and out.strip() else "main"


# ---------------------------------------------------------------- git classify
def classify_repo(repo: Path, fetch: bool = True) -> dict:
    """统一仓库状态分类（§6）。只读（除 fetch）。"""
    r = {"path": str(repo), "state": UNKNOWN, "ahead": 0, "behind": 0,
         "dirty": False, "branch": "", "reason": ""}
    if not (repo / ".git").exists():
        r["state"] = UNKNOWN
        r["reason"] = "not a git repo / missing"
        return r
    r["branch"] = _default_branch(repo)
    rc, out = git(repo, "status", "--porcelain")
    r["dirty"] = bool(out.strip()) if rc == 0 else False
    if fetch:
        frc, fout = git(repo, "fetch", "--quiet", "origin", timeout=90)
        if frc != 0:
            r["state"] = BLOCKED_AUTH if "Permission" in fout or "denied" in fout else UNKNOWN
            r["reason"] = f"fetch failed: {fout.splitlines()[-1] if fout else frc}"
            return r
    rc, remote = git(repo, "rev-parse", f"origin/{r['branch']}")
    if rc != 0:
        r["state"] = UNKNOWN
        r["reason"] = f"no origin/{r['branch']}"
        return r
    rc, ahead = git(repo, "rev-list", "--count", f"origin/{r['branch']}..HEAD")
    rc2, behind = git(repo, "rev-list", "--count", f"HEAD..origin/{r['branch']}")
    r["ahead"], r["behind"] = int(ahead or 0), int(behind or 0)
    if r["ahead"] == 0 and r["behind"] == 0:
        r["state"] = LOCAL_DIRTY if r["dirty"] else IN_SYNC
    elif r["behind"] > 0 and r["ahead"] == 0:
        r["state"] = REMOTE_AHEAD
    elif r["ahead"] > 0 and r["behind"] == 0:
        r["state"] = LOCAL_AHEAD
    else:
        r["state"] = DIVERGED
    return r


def changed_paths(repo: Path, refspec: str) -> list[str]:
    rc, out = git(repo, "diff", "--name-only", refspec)
    return [l.strip() for l in out.splitlines() if l.strip()] if rc == 0 else []


def privacy_scan(repo: Path, refspec: str) -> list[str]:
    """public 仓库 push 前的轻量隐私扫描（§13）。"""
    rc, diff = git(repo, "diff", refspec)
    if rc != 0:
        return ["<diff failed>"]
    hits = []
    for pat in PRIVACY_PATTERNS:
        if re.search(pat, diff):
            hits.append(pat)
    return hits


def _identity_args(repo: Path) -> list[str]:
    """merge 需要 committer identity；repo/global 都没有时用编排器固定身份兜底
    （fresh clone 不会携带旧设备的 repo-local identity）。"""
    rc, out = git(repo, "config", "user.email")
    if rc == 0 and out.strip():
        return []
    rc, out = run(["git", "config", "--global", "user.email"])
    if rc == 0 and out.strip():
        return []
    return ["-c", "user.name=Personal AI Sync",
            "-c", "user.email=personal-ai-sync@device.local"]


# ------------------------------------------------------------- memory provider
def _provider(records_root: Path):
    sys.path.insert(0, str(PROVIDER_DIR))
    from provider import FileMemoryProvider  # noqa: PLC0415
    return FileMemoryProvider(str(records_root.parent), device_id="sync-engine")


def memory_merge_verify(state_repo: Path) -> dict:
    """合并后校验：provider 能加载全部记录；检测 concurrent-revision 冲突标记。

    不重新发明 git 算法：git merge 只负责 disjoint 文件传输；
    语义判定（record/revision identity、冲突标记）由 MemoryProvider 契约给出。
    """
    prov = _provider(state_repo / "memory")
    try:
        bundle = prov.export()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "records": 0, "conflicts": []}
    conflicts = []
    for item in bundle["records"]:
        st = item.get("state", {})
        if st.get("conflict"):
            conflicts.append({"id": item["record"]["id"], "kind": st["conflict"]})
    return {"ok": True, "records": len(bundle["records"]), "conflicts": conflicts}


MEMORY_IMMUTABLE_FIELDS = ("id", "scope", "type", "created", "provenance")


def _record_id(path: str) -> str | None:
    parts = path.split("/")
    if len(parts) >= 3 and path.startswith(MEMORY_PREFIX):
        return parts[2]
    return None


def _git_show(repo: Path, ref: str, path: str) -> dict | None:
    rc, out = git(repo, "show", f"{ref}:{path}")
    if rc != 0:
        return None
    try:
        import yaml  # noqa: PLC0415
        return yaml.safe_load(out) or {}
    except Exception:  # noqa: BLE001
        return None


def state_divergence_analysis(repo: Path, branch: str) -> dict:
    """DIVERGED 时分析双端改动（§9/§10）：

    - curated（state/ registry/ sync/ projects/ README）任一侧改动 → 不自动合并
    - 同 record 的 record.yaml 不可变字段不一致 → CONFLICT（不得自动修正）
    - 同 record 双端新增不同 revision（record.yaml 仅 fingerprint churn）→
      可确定性合并：record.yaml/state.yaml 取本地侧（与 MemoryProvider.import_bundle
      的 first-write-kept + 本地 lifecycle 语义一致），双方 revision 全保留，
      concurrent 标记 conflict=concurrent-revisions
    - 其余同路径重叠 → 不自动解决
    """
    base_rc, base = git(repo, "merge-base", "HEAD", f"origin/{branch}")
    if base_rc != 0:
        return {"mergeable": False, "reason": "no merge-base"}
    base = base.strip()
    local = set(changed_paths(repo, f"{base}..HEAD"))
    remote = set(changed_paths(repo, f"{base}..origin/{branch}"))
    overlap = local & remote
    curated_touched = [p for p in local | remote if p.startswith(CURATED_PREFIXES)]

    # 按 record 聚合双端改动
    local_recs: dict[str, set] = {}
    remote_recs: dict[str, set] = {}
    for p in local:
        rid = _record_id(p)
        if rid:
            local_recs.setdefault(rid, set()).add(p)
    for p in remote:
        rid = _record_id(p)
        if rid:
            remote_recs.setdefault(rid, set()).add(p)
    both = sorted(set(local_recs) & set(remote_recs))

    immutable_conflict, concurrent, resolvable = [], [], []
    for rid in both:
        lp, rp = local_recs[rid], remote_recs[rid]
        l_meta = _git_show(repo, "HEAD", f"{MEMORY_PREFIX}{rid}/record.yaml")
        r_meta = _git_show(repo, f"origin/{branch}", f"{MEMORY_PREFIX}{rid}/record.yaml")
        if l_meta is None or r_meta is None:
            immutable_conflict.append(rid)
            continue
        if any(l_meta.get(f) != r_meta.get(f) for f in MEMORY_IMMUTABLE_FIELDS):
            immutable_conflict.append(rid)
            continue
        l_new_rev = any("/revisions/" in p for p in lp)
        r_new_rev = any("/revisions/" in p for p in rp)
        if l_new_rev and r_new_rev:
            concurrent.append(rid)
        resolvable.append(rid)

    # 不可自动解决的同路径重叠 = overlap 中不属于 resolvable record 的部分
    resolvable_paths = set()
    for rid in resolvable:
        resolvable_paths |= {p for p in overlap if _record_id(p) == rid}
    hard_overlap = sorted(p for p in overlap if p not in resolvable_paths)

    return {
        "mergeable": not curated_touched and not immutable_conflict and not hard_overlap,
        "local_changes": sorted(local), "remote_changes": sorted(remote),
        "overlap": sorted(overlap),
        "curated_touched": curated_touched,
        "curated_overlap": [p for p in overlap if p.startswith(CURATED_PREFIXES)],
        "immutable_conflict": immutable_conflict,
        "concurrent_revision_records": concurrent,
        "resolvable_records": resolvable,
        "hard_overlap": hard_overlap,
        "reason": "",
    }


# ------------------------------------------------------------------- projects
def discover_projects(state_repo: Path) -> list[dict]:
    """从 goals.md + sync/this-device.yaml 识别 ACTIVE/PAUSED 项目仓库（§14）。"""
    goals = state_repo / "state" / "goals.md"
    dev = state_repo / "sync" / "this-device.yaml"
    repos = []
    if dev.is_file():
        in_repos = False
        for line in dev.read_text(encoding="utf-8").splitlines():
            if line.startswith("repos:"):
                in_repos = True
                continue
            if in_repos:
                m = re.match(r"\s+-\s+(.+)", line)
                if m:
                    repos.append(m.group(1).strip())
                elif line.strip() and not line.startswith(" "):
                    break
    infra = {str(REPO).lower(), str(state_repo).lower(), "skills", "agent-tools", "personal-ai-state"}
    projects = []
    active_names = set()
    paused = False
    if goals.is_file():
        section = None
        for line in goals.read_text(encoding="utf-8").splitlines():
            h = re.match(r"##\s+(\w+)", line)
            if h:
                section = h.group(1).lower()
                continue
            m = re.match(r"-\s+\*\*([-\w]+)\*\*[：:](.*)", line)
            if m and section == "active":
                name = m.group(1)
                if "paused" in m.group(2):
                    paused = True
                else:
                    active_names.add(name)
    for r in repos:
        if r.lower() in infra or Path(r).name.lower() in infra:
            continue
        name = Path(r).name
        status = "ACTIVE" if name in active_names else "PAUSED"
        if name == "novel-main":
            status = "PAUSED"  # paused_external_auth（goals.md 明示）
        projects.append({"name": name, "path": r, "status": status,
                         "privacy_blocked": name == "novel-main"})
    return projects


# -------------------------------------------------------------------- secrets
def check_secrets() -> dict:
    """只检测引用是否存在（AVAILABLE/MISSING/NOT_REQUIRED），绝不读值（§19）。"""
    refs = []
    if SETTINGS.is_file():
        refs = sorted(set(re.findall(r"apiKeyEnv:\s*([A-Z0-9_]+)",
                                     SETTINGS.read_text(encoding="utf-8"))))
    missing = [r for r in refs if not os.environ.get(r)]
    return {"references": refs, "missing": missing,
            "status": "READY" if not missing else "PARTIAL"}


# ------------------------------------------------------------------ runtime
# §24 依赖映射：canonical 变化 → 受影响 Harness target（DSH ONLY 控制面）
TARGET_DEPENDENCIES = {
    "dsh": ["registry/providers.yaml", "registry/routing-policy.yaml",
            "registry/harnesses/dsh", "registry/models.yaml",
            "registry/execution-profiles.yaml"],
}
PREFERENCES_PATH = "state/preferences.md"


def affected_targets(agent_tools_changed: list[str],
                     state_changed: list[str]) -> list[str]:
    """只返回真正受 canonical change 影响的 Harness（DSH ONLY）。"""
    targets = set()
    for t, deps in TARGET_DEPENDENCIES.items():
        if any(any(f.startswith(d) for d in deps) for f in agent_tools_changed):
            targets.add(t)
    if any(f == PREFERENCES_PATH for f in state_changed):
        targets.add("dsh")
    return sorted(targets)


def runtime_refresh(affected: list[str], mode: str) -> dict:
    """受影响 target：diff →（sync/restore 模式且 drift）→ apply → post-diff。

    aic apply 自带 ownership 分类 + snapshot + rollback；REVIEW_REQUIRED 不阻塞
    其他 target（§3/§5/§12）。
    """
    out = {"affected": affected, "applied": [], "review": [], "status": "NO DRIFT"}
    for t in affected:
        diff_args = ["diff", t]
        if t == "dsh":
            diff_args.append("--runtime-only")
        rc, _ = run([sys.executable, str(AIC), *diff_args])
        if rc == 0:
            continue
        if mode not in ("sync", "restore"):
            out["review"].append({t: "drift（check/pull/push 模式不自动 apply）"})
            out["status"] = "DRIFT"
            continue
        arc, aout = run([sys.executable, str(AIC), "apply", t],
                        timeout=1800 if t == "dsh" else 120)
        if arc == 0:
            out["applied"].append(t)
        else:
            out["review"].append({t: (aout.splitlines() or ["apply failed"])[0]})
            out["status"] = "DRIFT"
    if out["applied"]:
        out["status"] = "refreshed" if out["status"] == "NO DRIFT" else out["status"]
    return out


def runtime_status(env: dict[str, str] | None = None) -> dict:
    """aic validate + 已安装 harness diff（只读）。"""
    rc, _ = run([sys.executable, str(AIC), "validate"], env=env)
    drifts = []
    for t in ("dsh", "codex", "claude", "gemini", "switchboard"):
        diff_args = ["diff", t]
        if t == "dsh":
            diff_args.append("--runtime-only")
        r, _ = run([sys.executable, str(AIC), *diff_args], env=env)
        if r != 0:
            drifts.append(t)
    return {"validate": rc == 0, "drift": drifts,
            "status": "NO DRIFT" if rc == 0 and not drifts else "DRIFT"}


# ------------------------------------------------------------------ checkpoint
def load_checkpoint() -> dict:
    if CHECKPOINT.is_file():
        try:
            return json.loads(CHECKPOINT.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_checkpoint(data: dict) -> None:
    try:
        CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
        CHECKPOINT.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    except (PermissionError, OSError):
        # Sandbox or environment limitation: checkpoint write is best-effort machine local state
        pass


# -------------------------------------------------------------------- planes
def plan_actions(classifications: dict, state_repo: Path | None,
                 mode: str) -> list[dict]:
    """fetch → classify → action plan（§21：先全部分类再执行，防止顺序性数据丢失）。"""
    plan = []
    for name, c in classifications.items():
        st = c["state"]
        if st == IN_SYNC:
            plan.append({"plane": name, "action": "NO ACTION", "state": st})
        elif st == LOCAL_DIRTY:
            plan.append({"plane": name, "action": "UNTOUCHED", "state": st,
                         "reason": "working tree dirty，禁止自动 add/commit/stash"})
        elif st == REMOTE_AHEAD:
            if c["dirty"]:
                plan.append({"plane": name, "action": "UNTOUCHED", "state": st,
                             "reason": "REMOTE_AHEAD 但本地 dirty，pull 被阻止"})
            else:
                plan.append({"plane": name, "action": "PULL", "state": st})
        elif st == LOCAL_AHEAD:
            plan.append({"plane": name, "action": "PUSH", "state": st})
        elif st == DIVERGED:
            plan.append({"plane": name, "action": "REVIEW", "state": st,
                         "reason": "history diverged"})
        else:
            plan.append({"plane": name, "action": "REVIEW", "state": st,
                         "reason": c.get("reason", "")})
    return plan


def execute_plan(plan: list[dict], classifications: dict,
                 state_repo: Path | None, mode: str,
                 results: dict) -> None:
    """只执行确定安全动作（§22）；其余保持 REVIEW。全部幂等可重跑（§36）。"""
    for item in plan:
        name, action = item["plane"], item["action"]
        c = classifications[name]
        repo = Path(c["path"])
        if mode == "check":
            item["action"] = "NO ACTION" if action in ("PULL", "PUSH") else action
            continue
        if action == "PULL":
            rc, out = git(repo, "pull", "--ff-only", "origin", c["branch"])
            item["executed"] = rc == 0
            item["state"] = "PULLED" if rc == 0 else item["state"]
            if rc != 0:
                item["action"] = "REVIEW"
                item["reason"] = out.splitlines()[-1] if out else "pull failed"
        elif action == "PUSH":
            if mode == "pull":
                item["action"] = "REVIEW"
                item["reason"] = "pull-only 模式不 push"
                continue
            if name == "agent-tools":
                hits = privacy_scan(repo, f"origin/{c['branch']}..HEAD")
                if hits:
                    item["action"] = "REVIEW"
                    item["state"] = BLOCKED_PRIVACY
                    item["reason"] = f"privacy scan hit: {hits}"
                    continue
            if name.startswith("project:") and classifications[name].get("privacy_blocked"):
                item["action"] = "REVIEW"
                item["state"] = BLOCKED_PRIVACY
                item["reason"] = "public/privacy-blocked 项目不自动 push"
                continue
            rc, out = git(repo, "push", "origin", c["branch"])
            item["executed"] = rc == 0
            item["state"] = "PUSHED" if rc == 0 else item["state"]
            if rc != 0:
                item["action"] = "REVIEW"
                item["reason"] = out.splitlines()[-1] if out else "push failed"
        elif action == "REVIEW" and item["state"] == DIVERGED and name == "personal-ai-state":
            _handle_state_divergence(item, repo, c, mode, results)


def _handle_state_divergence(item: dict, repo: Path, c: dict,
                             mode: str, results: dict) -> None:
    """personal-ai-state DIVERGED（§9/§10/§11）。

    git 只做 transport；语义判定复用 MemoryProvider 冻结契约
    （record.yaml first-write-kept、revision 全保留、concurrent 标记、
    lifecycle 分歧按 (at, device_id) 取胜者）。curated 改动/不可变元数据冲突
    → CONFLICT_REVIEW，绝不 last-write-wins。
    """
    a = state_divergence_analysis(repo, c["branch"])
    item["analysis"] = {k: a[k] for k in ("curated_touched", "immutable_conflict",
                                          "concurrent_revision_records",
                                          "hard_overlap")}
    if a["immutable_conflict"]:
        item["state"] = CONFLICT
        item["reason"] = f"immutable record metadata 双端不一致: {a['immutable_conflict']}"
        return
    if a["curated_touched"]:
        item["state"] = CONFLICT
        item["reason"] = ("curated state 双端修改，禁止 last-write-wins："
                          f"{a['curated_touched']}")
        return
    if a["hard_overlap"]:
        item["reason"] = f"同路径重叠 {a['hard_overlap']}，不自动解决"
        return
    if not a["mergeable"]:
        item["reason"] = a.get("reason") or "不满足确定性合并条件"
        return
    if mode not in ("sync", "restore"):
        item["reason"] = "diverged（pull/push-only 模式不自动 merge）"
        return

    idargs = _identity_args(repo)
    rc, _ = git(repo, *idargs, "merge", "--no-commit", "--no-ff",
                f"origin/{c['branch']}")
    if rc != 0:
        # 解决 resolvable record 的 record.yaml/state.yaml 冲突：
        # 取本地侧（= provider import_bundle 的 first-write-kept 语义）
        _, unmerged = git(repo, "diff", "--name-only", "--diff-filter=U")
        resolvable_paths = {p for rid in a["resolvable_records"]
                            for p in a["overlap"] if _record_id(p) == rid}
        todo = [p.strip() for p in unmerged.splitlines() if p.strip()]
        if any(p not in resolvable_paths for p in todo):
            git(repo, "merge", "--abort")
            item["reason"] = f"非预期冲突路径 {todo}，已 abort 转 REVIEW"
            return
        for p in todo:
            git(repo, "checkout", "--ours", "--", p)
            git(repo, "add", "--", p)

    # concurrent records：按 provider 契约标记 conflict=concurrent-revisions；
    # lifecycle 分歧按 (updated.at, device_id) 取胜者并标记 lifecycle-divergence
    flagged = []
    sys.path.insert(0, str(PROVIDER_DIR))
    from provider import _dump  # noqa: PLC0415
    for rid in a["resolvable_records"]:
        l_state = _git_show(repo, "HEAD", f"{MEMORY_PREFIX}{rid}/state.yaml") or {}
        r_state = _git_show(repo, f"origin/{c['branch']}",
                            f"{MEMORY_PREFIX}{rid}/state.yaml") or {}
        state = dict(l_state)
        if l_state.get("lifecycle") != r_state.get("lifecycle"):
            key = lambda s: (s.get("updated", {}).get("at", ""),  # noqa: E731
                             s.get("updated", {}).get("device_id", ""))
            state = dict(max([l_state, r_state], key=key))
            state["conflict"] = "lifecycle-divergence"
        if rid in a["concurrent_revision_records"]:
            state["conflict"] = "concurrent-revisions"
        if state.get("conflict"):
            (repo / MEMORY_PREFIX / rid / "state.yaml").write_text(
                _dump(state), encoding="utf-8")
            git(repo, "add", "--", f"{MEMORY_PREFIX}{rid}/state.yaml")
            flagged.append({"id": rid, "kind": state["conflict"]})

    rc, out = git(repo, *idargs, "commit", "--no-edit")
    if rc != 0:
        git(repo, "merge", "--abort")
        item["reason"] = f"merge commit 失败，已 abort: {out[-200:]}"
        return

    v = memory_merge_verify(repo)
    # 合并结果推送回 remote（private repo，确定性合并已验证），让其他设备收敛
    prc, pout = git(repo, "push", "origin", c["branch"])
    item["executed"] = True
    item["state"] = "MERGED"
    item["memory"] = v
    item["pushed"] = prc == 0
    item["conflict_flags"] = flagged
    results["memory_merge"] = v
    if prc != 0:
        item["action"] = "REVIEW"
        item["reason"] = f"merge 成功但 push 失败: {pout.splitlines()[-1] if pout else prc}"
    elif flagged:
        item["action"] = "REVIEW"
        item["reason"] = (f"CONFLICT_CONCURRENT_REVISION: {flagged}"
                          "（双方 revision 均保留并标记，后续 supersede 消解）")
    else:
        item["action"] = "MERGED"


# --------------------------------------------------------------------- sync
def run_sync(mode: str, detail: bool = False) -> dict:
    results: dict = {"mode": mode, "planes": {}, "actions": []}
    writable = mode in ("sync", "pull", "push", "restore")

    classifications: dict = {}
    classifications["agent-tools"] = classify_repo(REPO)
    state_repo = STATE_REPO if (STATE_REPO / ".git").exists() else None
    if state_repo:
        classifications["personal-ai-state"] = classify_repo(state_repo)
    else:
        results["planes"]["personal-ai-state"] = {"state": UNKNOWN,
                                                  "reason": "local canonical missing → RESTORE"}
    projects = discover_projects(state_repo) if state_repo else []
    for p in projects:
        key = f"project:{p['name']}"
        if p["status"] == "ACTIVE":
            c = classify_repo(Path(p["path"]))
            c["privacy_blocked"] = p["privacy_blocked"]
            classifications[key] = c
        else:
            # PAUSED：只读检查 remote state（§14）
            c = classify_repo(Path(p["path"]))
            c["note"] = "PAUSED 项目，只检查不动作"
            if p["privacy_blocked"]:
                c["state"] = BLOCKED_PRIVACY if c["state"] != IN_SYNC else c["state"]
            classifications[key] = c
            results["planes"][key] = c

    for name, c in classifications.items():
        results["planes"].setdefault(name, c)

    plan = plan_actions({k: v for k, v in classifications.items()
                         if not v.get("note")},  # PAUSED/known-blocker 项目只显示不进计划
                        state_repo, mode)
    if mode == "pull":
        plan = [{**i, "action": "REVIEW", "reason": "pull-only 模式"} if i["action"] == "PUSH" else i
                for i in plan]
    if mode == "push":
        plan = [{**i, "action": "REVIEW", "reason": "push-only 模式"} if i["action"] == "PULL" else i
                for i in plan]
    execute_plan(plan, classifications, state_repo, mode, results)
    results["actions"] = plan

    # 受影响面判定（§7/§8/§23）：只对真正变化的 canonical 做增量 refresh
    changed_at: list[str] = []
    changed_state: list[str] = []
    for item in plan:
        if item.get("state") not in ("PULLED", "MERGED"):
            continue
        repo = Path(classifications[item["plane"]]["path"])
        rc, head = git(repo, "rev-parse", "HEAD")
        # pull/merge 前的 HEAD：reflog 取上一个值
        rc2, prev = git(repo, "rev-parse", "HEAD@{1}")
        if rc2 == 0:
            files = changed_paths(repo, f"{prev.strip()}..{head.strip()}")
        else:
            files = []
        if item["plane"] == "agent-tools":
            changed_at = files
        elif item["plane"] == "personal-ai-state":
            changed_state = files
    results["changed"] = {"agent-tools": changed_at, "personal-ai-state": changed_state}

    pulled = bool(changed_at or changed_state)
    memory_changed = any(f.startswith(MEMORY_PREFIX) for f in changed_state)
    skills_changed = any(f.startswith("skills/") for f in changed_at)
    plugins_changed = any(f.startswith("dsh/") for f in changed_at)

    # Skills remain a library sync. DSH runtime composition has one owner:
    # aic apply dsh; sync_skills.py is not a second plugin deployment truth.
    if mode in ("sync", "restore") and skills_changed:
        cmd = [sys.executable, str(SYNC_SKILLS), "--destination",
               str(Path.home() / ".dsh" / "skills")]
        rc, out = run([*cmd, "--apply"])
        results["skill_sync"] = "PASS" if rc == 0 else f"FAIL: {out[-200:]}"
    if mode in ("sync", "restore") and plugins_changed:
        rc, out = run([sys.executable, str(AIC), "apply", "dsh"], timeout=1800)
        results["dsh_composition"] = "PASS" if rc == 0 else f"FAIL: {out[-200:]}"
    # memory-only change → 只做 derived 校验，绝不触发 Harness apply（§8）
    if memory_changed:
        results["memory_refresh"] = memory_merge_verify(state_repo) if state_repo else {}

    affected = affected_targets(changed_at, changed_state)
    if affected:
        results["runtime"] = runtime_refresh(affected, mode)
    elif pulled or mode == "check":
        results["runtime"] = runtime_status()
    else:
        results["runtime"] = {"status": "SKIPPED"}
    results["secrets"] = check_secrets()

    # checkpoint（machine-local，可删，删除后可重新 discover）
    if writable or mode == "check":
        cp = load_checkpoint()
        cp.update({
            "device_id": os.environ.get("COMPUTERNAME", "unknown"),
            "last_sync": datetime.now(timezone.utc).isoformat(),
            "last_mode": mode,
            "per_repo_head": {n: _head(Path(c["path"])) for n, c in classifications.items()},
            "last_result": _overall(plan, results),
        })
        save_checkpoint(cp)
    results["result"] = _overall(plan, results)
    results["known_blockers"] = KNOWN_BLOCKERS
    return results


def _head(repo: Path) -> str:
    rc, out = git(repo, "rev-parse", "--short", "HEAD")
    return out.strip() if rc == 0 else "?"


def _overall(plan: list[dict], results: dict) -> str:
    states = [i.get("state", "") for i in plan]
    actions = [i.get("action", "") for i in plan]
    if any(s == BLOCKED_AUTH for s in states):
        return "BLOCKED"
    if any(s in (CONFLICT, BLOCKED_PRIVACY) for s in states):
        return "REVIEW"
    if "REVIEW" in actions or "UNTOUCHED" in actions:
        # REVIEW = 需人工裁决；UNTOUCHED = 有 dirty tree 等未发布状态，如实报告
        return "REVIEW"
    if results.get("runtime", {}).get("status") == "DRIFT" \
            or results.get("runtime", {}).get("review"):
        return "REVIEW"
    return "PASS"


# -------------------------------------------------------------------- restore
def run_restore(detail: bool = False, repo: Path = REPO,
                state_repo: Path = STATE_REPO,
                skills_dest: Path | None = None,
                apply_dsh: bool = True,
                agent_tools_remote: str = "git@github.com:ooooooooooooooooooop/agent-tools.git",
                state_remote: str = "git@github.com:ooooooooooooooooooop/personal-ai-state.git",
                sessions_root: Path | None = None,
                backup_root: Path | None = None) -> dict:
    """RESTORE = local canonical missing 时的特殊 SYNC（§28），复用 PULL+bootstrap。

    路径可注入：fresh-restore 演练在独立 temp destination 上进行，不破坏 live 环境。
    DSH_SESSION_HISTORY plane 默认解析 live ~/.dsh/sessions 与 durability backup
    root（this-device.yaml）；测试注入空 root 得到 NOT_APPLICABLE。
    """
    results = {"mode": "restore", "steps": []}

    def step(name: str, ok: bool, note: str = "") -> None:
        results["steps"].append({"step": name, "ok": ok, "note": note})

    if not (repo / ".git").exists():
        rc, out = run(["git", "clone", agent_tools_remote, str(repo)], timeout=300)
        step("clone agent-tools", rc == 0, out[-200:] if rc else "")
    else:
        step("clone agent-tools", True, "already present")
    if not (state_repo / ".git").exists():
        rc, out = run(["git", "clone", state_remote, str(state_repo)], timeout=300)
        step("clone personal-ai-state", rc == 0, out[-200:] if rc else "")
    else:
        step("clone personal-ai-state", True, "already present")

    if (repo / ".git").exists():
        vscript = repo / "scripts" / "validate_repo.py"
        if vscript.is_file():
            rc, _ = run([sys.executable, str(vscript), "--strict"])
            step("validate canonical", rc == 0)
        aic_script = repo / "scripts" / "aic" / "aic.py"
        if aic_script.is_file():
            rc, _ = run([sys.executable, str(aic_script), "discover"])
            step("aic discover", rc == 0)
        sync_script = repo / "scripts" / "sync_skills.py"
        dest = skills_dest or (Path.home() / ".dsh" / "skills")
        if sync_script.is_file():
            rc, out = run([sys.executable, str(sync_script), "--destination",
                           str(dest), "--check"])
            step("skills check", rc == 0, out.splitlines()[-1] if out else "")
            rc, out = run([sys.executable, str(sync_script), "--destination",
                           str(dest), "--apply"])
            step("skills restore (apply)", rc == 0,
                 out.splitlines()[-1] if out else "")
        if aic_script.is_file():
            runtime_home = Path(os.environ.get("DSH_HOME", str(Path.home() / ".dsh")))
            if skills_dest is not None and "DSH_HOME" not in os.environ:
                runtime_home = dest.parent if dest.name == "skills" else dest / ".dsh"
            runtime_env = {"DSH_HOME": str(runtime_home)}
            if apply_dsh:
                rc, out = run([sys.executable, str(aic_script), "apply", "dsh"],
                              env=runtime_env, timeout=1800)
                step("dsh runtime composition apply", rc == 0,
                     out.splitlines()[-1] if out else "")
                rt = runtime_status(env=runtime_env)
                step("harness diff", rt["status"] == "NO DRIFT", json.dumps(rt["drift"]))
            else:
                step("harness diff", True, "skipped by test/embedded restore caller")
        governance_tasks = repo / "scripts" / "governance" / "register_governance_tasks.ps1"
        if governance_tasks.is_file() and os.name == "nt":
            rc, out = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                           "-File", str(governance_tasks)], timeout=120)
            step("governance tasks", rc == 0, out.splitlines()[-1] if out else "")
        if (state_repo / "memory").is_dir():
            v = memory_merge_verify(state_repo)
            step("memory loadable", v["ok"], f"records={v.get('records')}")
    # DSH_SESSION_HISTORY plane（§9 事故契约）：备份计数 / live 计数 / 锚点 /
    # schema 探针；非 PASS|NOT_APPLICABLE 时总体 result 不得为 PASS。
    sh_root = sessions_root or (Path.home() / ".dsh" / "sessions")
    bk_root = backup_root if backup_root is not None else _device_backup_root()
    sh = session_history_status(sh_root, bk_root)
    anchors_summary = "".join(
        f" {a[-8:]}:backup={v['in_backup']}/live={v['in_live']}"
        for a, v in sh["anchors"].items())
    step("dsh session history", sh["status"] in ("PASS", "NOT_APPLICABLE"),
         f"status={sh['status']} backup={sh['backup_count']} live={sh['live_count']}"
         f" missing={sh['missing']}{anchors_summary}"
         + (f" ({sh['reason']})" if sh.get("reason") else ""))
    results["session_history"] = sh
    sec = check_secrets()
    step("secrets", True, f"{sec['status']} missing={sec['missing']}")
    results["secrets"] = sec
    results["result"] = "PASS" if all(s["ok"] for s in results["steps"]) else "REVIEW"
    return results


# --------------------------------------------------------------------- output
def print_human(results: dict, detail: bool = False) -> None:
    print("Personal AI Sync\n")
    for name, c in results.get("planes", {}).items():
        st = c.get("state", UNKNOWN)
        note = c.get("note") or c.get("reason") or ""
        print(f"  {name:22s} {st}" + (f" — {note}" if note and st != IN_SYNC else ""))
    rt = results.get("runtime", {})
    if rt.get("status"):
        extra = f"（applied: {','.join(rt['applied'])}）" if rt.get("applied") else ""
        print(f"  {'runtime':22s} {rt['status']}{extra}")
    sec = results.get("secrets", {})
    if sec:
        print(f"  {'secrets':22s} {sec['status']}" +
              (f" — missing: {sec['missing']}" if sec.get("missing") else ""))
    sh = results.get("session_history")
    if sh:
        print(f"  {'dsh-session-history':22s} {sh['status']}"
              f" (backup={sh['backup_count']} live={sh['live_count']}"
              f" missing={sh['missing']})")
    print(f"\n  external blockers    known external blocker, unchanged "
          f"({len(KNOWN_BLOCKERS)})")
    for s in results.get("steps", []):
        print(f"  [{'OK' if s['ok'] else '!!'}] {s['step']}" +
              (f" — {s['note']}" if s.get("note") else ""))
    print(f"\nResult: {results.get('result', UNKNOWN)}")
    if detail:
        print("\n--- detail ---")
        print(json.dumps(results.get("actions", results.get("steps", [])),
                         ensure_ascii=False, indent=2))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="Personal AI Lifecycle Sync（薄编排器）")
    ap.add_argument("mode", nargs="?", default="sync",
                    choices=["check", "pull", "push", "sync", "restore"])
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    results = run_restore(args.detail) if args.mode == "restore" else run_sync(args.mode, args.detail)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print_human(results, args.detail)
    return {"PASS": 0, "REVIEW": 1, "BLOCKED": 2}.get(results.get("result", ""), 3)


if __name__ == "__main__":
    sys.exit(main())
