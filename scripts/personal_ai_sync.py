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
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STATE_REPO = Path.home() / "personal-ai-state"
CHECKPOINT = Path.home() / ".dsh" / ".personal-ai-sync" / "status.json"
AIC = REPO / "scripts" / "aic" / "aic.py"
SYNC_SKILLS = REPO / "scripts" / "sync_skills.py"
GOVERNANCE_TASKS = REPO / "scripts" / "governance" / "register_governance_tasks.ps1"
CANONICAL_GOVERNANCE_ROOT = Path(r"C:\Desktop\skills")
PROVIDER_DIR = REPO / "scripts" / "memory"
SETTINGS = Path.home() / ".dsh" / "settings.yaml"
MUTATION_LOCK_ROOT = Path.home() / ".dsh" / ".personal-ai-mutation"
MUTATION_LOCK_STALE_SECONDS = 6 * 60 * 60
MUTATION_LOCK_SCHEMA = "PERSONAL_AI_CANONICAL_MUTATION_LOCK_V1"
MUTATION_RECEIPT_SCHEMA = "PERSONAL_AI_CANONICAL_MUTATION_RECEIPT_V1"
MUTATION_AUDIT_SCHEMA = "PERSONAL_AI_CANONICAL_MUTATION_AUDIT_V1"
PROVENANCE_UNKNOWN = "UNKNOWN"
UNAUTHORIZED_OR_UNATTRIBUTED_CANONICAL_MUTATION = \
    "UNAUTHORIZED_OR_UNATTRIBUTED_CANONICAL_MUTATION"
LEGACY_UNATTRIBUTED_COMMIT = "LEGACY_UNATTRIBUTED_COMMIT"
PROVENANCE_REQUIRED_FIELDS = (
    "schema", "timestamp", "repo", "actor", "actor_type", "task_id", "run_id",
    "thread_id", "pid", "ppid", "process_start_time", "entrypoint", "operation",
    "base_head", "result_head", "remote_before", "remote_after", "owned_scope",
    "changed_files", "commit", "push_target", "mutation_lease_id",
)


class MutationOwnershipError(RuntimeError):
    """Raised when a canonical mutation cannot prove exclusive ownership."""

    def __init__(self, code: str, message: str, metadata: dict | None = None):
        super().__init__(message)
        self.code = code
        self.metadata = metadata or {}


def _path_key(path: Path) -> str:
    """Return a stable, case-insensitive absolute path key."""
    return os.path.normcase(str(path.resolve(strict=False)))


def _canonical_repository_roots() -> tuple[Path, ...]:
    return (CANONICAL_GOVERNANCE_ROOT, STATE_REPO)


def _is_canonical_mutation_repo(repo: Path, canonical_root: Path | None = None) -> bool:
    """Require an exact canonical checkout identity before acquiring a writer lease."""
    actual = _path_key(repo)
    roots = (canonical_root,) if canonical_root is not None else _canonical_repository_roots()
    return any(actual == _path_key(root) for root in roots)


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (ProcessLookupError, OSError):
        return False
    return True


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _provenance_value(value: object) -> object:
    """Keep missing provenance explicit instead of silently filling it with a guess."""
    return PROVENANCE_UNKNOWN if value is None or value == "" else value


def _process_start_time() -> str:
    """Return the current Windows process creation time when the OS exposes it."""
    if os.name != "nt":
        return PROVENANCE_UNKNOWN
    handle = None
    try:
        import ctypes
        from ctypes import wintypes

        class FileTime(ctypes.Structure):
            _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(FileTime), ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime), ctypes.POINTER(FileTime),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(0x1000, False, os.getpid())
        if not handle:
            return PROVENANCE_UNKNOWN
        creation = FileTime()
        exit_time = FileTime()
        kernel_time = FileTime()
        user_time = FileTime()
        if not kernel32.GetProcessTimes(
                handle, ctypes.byref(creation), ctypes.byref(exit_time),
                ctypes.byref(kernel_time), ctypes.byref(user_time)):
            return PROVENANCE_UNKNOWN
        ticks = (int(creation.high) << 32) | int(creation.low)
        epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
        return (epoch + timedelta(microseconds=ticks // 10)).isoformat()
    except Exception:  # noqa: BLE001 - provenance must degrade to explicit UNKNOWN
        return PROVENANCE_UNKNOWN
    finally:
        if handle:
            try:
                import ctypes
                ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
            except Exception:  # noqa: BLE001 - best-effort handle cleanup
                pass


def _default_entrypoint() -> str:
    value = os.environ.get("PERSONAL_AI_ENTRYPOINT")
    if value:
        return value
    try:
        return str(Path(sys.argv[0]).resolve(strict=False))
    except (IndexError, OSError):
        return PROVENANCE_UNKNOWN


def _default_thread_id() -> str:
    return os.environ.get("PERSONAL_AI_THREAD_ID") or os.environ.get("CODEX_THREAD_ID") \
        or PROVENANCE_UNKNOWN


def _default_actor_type() -> str:
    return os.environ.get("PERSONAL_AI_ACTOR_TYPE") or (
        "automated" if os.environ.get("PERSONAL_AI_TASK_ID") else "manual"
    )


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


class CanonicalMutationLock:
    """Crash-detectable, one-writer lease for a canonical repository."""

    def __init__(
        self,
        repo: Path,
        *,
        actor: str,
        trigger: str,
        task_id: str | None = None,
        actor_type: str | None = None,
        thread_id: str | None = None,
        entrypoint: str | None = None,
        process_start_time: str | None = None,
        mutation_lease_id: str | None = None,
        operation: str,
        scope: list[str] | None = None,
        run_id: str | None = None,
        lock_root: Path | None = None,
        receipt_root: Path | None = None,
        canonical_root: Path | None = None,
        stale_after: int = MUTATION_LOCK_STALE_SECONDS,
    ):
        self.repo = repo.resolve(strict=False)
        self.actor = actor
        self.trigger = trigger
        self.task_id = task_id or "personal-ai-sync"
        self.actor_type = actor_type or _default_actor_type()
        self.thread_id = thread_id or _default_thread_id()
        self.entrypoint = entrypoint or _default_entrypoint()
        self.process_start_time = process_start_time or _process_start_time()
        self.operation = operation
        self.scope = sorted(scope or [])
        self.run_id = run_id or uuid.uuid4().hex
        self.mutation_lease_id = mutation_lease_id or f"lease-{uuid.uuid4().hex}"
        self.lock_root = (lock_root or MUTATION_LOCK_ROOT).resolve(strict=False)
        self.receipt_root = (receipt_root or (self.lock_root / "receipts")).resolve(strict=False)
        self.canonical_root = canonical_root
        self.stale_after = stale_after
        digest = hashlib.sha256(_path_key(self.repo).encode("utf-8")).hexdigest()[:24]
        self.lock_path = self.lock_root / f"{digest}.lock.json"
        self.metadata = {
            "schema": MUTATION_LOCK_SCHEMA,
            "actor": actor,
            "actor_type": self.actor_type,
            "pid": os.getpid(),
            "ppid": os.getppid() if hasattr(os, "getppid") else PROVENANCE_UNKNOWN,
            "process_start_time": self.process_start_time,
            "entrypoint": self.entrypoint,
            "thread_id": self.thread_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "mutation_lease_id": self.mutation_lease_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "operation": operation,
            "repo": str(self.repo),
            "scope": self.scope,
        }
        self._held = False

    def _read_existing(self) -> dict:
        try:
            value = json.loads(self.lock_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise MutationOwnershipError(
                "UNKNOWN_LOCK",
                f"DEFER: mutation lock is unreadable: {self.lock_path}",
            ) from exc
        if not isinstance(value, dict):
            raise MutationOwnershipError(
                "UNKNOWN_LOCK",
                f"DEFER: mutation lock is not an object: {self.lock_path}",
            )
        if value.get("schema") != MUTATION_LOCK_SCHEMA:
            raise MutationOwnershipError(
                "UNKNOWN_LOCK",
                f"DEFER: mutation lock schema is unknown: {self.lock_path}",
                value,
            )
        recorded_repo = value.get("repo")
        if not isinstance(recorded_repo, str) or not recorded_repo \
                or _path_key(Path(recorded_repo)) != _path_key(self.repo):
            raise MutationOwnershipError(
                "LOCK_REPO_MISMATCH",
                f"DEFER: mutation lock belongs to another repository: {self.lock_path}",
                value,
            )
        return value

    def acquire(self) -> "CanonicalMutationLock":
        if not _is_canonical_mutation_repo(self.repo, self.canonical_root):
            raise MutationOwnershipError(
                "NON_CANONICAL",
                f"DEFER: writer ownership is denied for non-canonical restore/source {self.repo}",
            )
        self.lock_root.mkdir(parents=True, exist_ok=True)
        for _ in range(3):
            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                try:
                    os.write(fd, (json.dumps(self.metadata, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
                finally:
                    os.close(fd)
                self._held = True
                return self
            except FileExistsError:
                existing = self._read_existing()
                pid = existing.get("pid")
                started = _parse_timestamp(existing.get("started_at"))
                if not isinstance(pid, int) or isinstance(pid, bool) or started is None:
                    raise MutationOwnershipError(
                        "UNKNOWN_LOCK",
                        f"DEFER: mutation lock metadata is incomplete: {self.lock_path}",
                        existing,
                    )
                if _pid_is_alive(pid):
                    raise MutationOwnershipError(
                        "FOREIGN_LOCK",
                        f"DEFER: active mutation owner pid={pid} run_id={existing.get('run_id')}",
                        existing,
                    )
                age = (datetime.now(timezone.utc) - started).total_seconds()
                if age < self.stale_after:
                    raise MutationOwnershipError(
                        "RECENT_DEAD_LOCK",
                        f"DEFER: dead mutation owner lock is not stale yet (age={age:.1f}s)",
                        existing,
                    )
                try:
                    self.lock_path.unlink()
                except FileNotFoundError:
                    continue
        raise MutationOwnershipError("LOCK_RACE", f"DEFER: mutation lock acquisition raced: {self.lock_path}")

    def release(self) -> None:
        if not self._held:
            return
        try:
            existing = json.loads(self.lock_path.read_text(encoding="utf-8"))
            if existing.get("run_id") == self.run_id and existing.get("pid") == os.getpid():
                self.lock_path.unlink(missing_ok=True)
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
        finally:
            self._held = False

    def __enter__(self) -> "CanonicalMutationLock":
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()


def write_mutation_receipt(
    lock: CanonicalMutationLock,
    *,
    base: str,
    result: str,
    staged: list[str] | None = None,
    changed: list[str] | None = None,
    commit: str = PROVENANCE_UNKNOWN,
    base_head: str | None = None,
    result_head: str | None = None,
    remote_before: str | None = None,
    remote_after: str | None = None,
    push_target: str | None = None,
    operation: str | None = None,
) -> Path:
    """Persist mutation evidence and its process-to-commit provenance outside Git."""
    timestamp = datetime.now(timezone.utc).isoformat()
    staged = sorted(staged or [])
    changed = sorted(changed or [])
    commit = str(_provenance_value(commit))
    base_head = str(_provenance_value(base_head if base_head is not None else base))
    result_head = str(_provenance_value(result_head if result_head is not None else commit))
    remote_before = str(_provenance_value(remote_before))
    remote_after = str(_provenance_value(remote_after))
    push_target = str(_provenance_value(push_target))
    operation = str(_provenance_value(operation if operation is not None else lock.operation))
    ppid = lock.metadata.get("ppid", PROVENANCE_UNKNOWN)
    payload = {
        "schema": MUTATION_RECEIPT_SCHEMA,
        "provenance_schema": MUTATION_AUDIT_SCHEMA,
        "timestamp": timestamp,
        "created_at": timestamp,
        "repo": str(lock.repo),
        "actor": lock.actor,
        "actor_type": lock.actor_type,
        "trigger": lock.trigger,
        "task_id": lock.task_id,
        "run_id": lock.run_id,
        "thread_id": lock.thread_id,
        "pid": lock.metadata.get("pid", PROVENANCE_UNKNOWN),
        "ppid": ppid,
        "process_start_time": lock.process_start_time,
        "entrypoint": lock.entrypoint,
        "operation": operation,
        "base_head": base_head,
        "result_head": result_head,
        "remote_before": remote_before,
        "remote_after": remote_after,
        "owned_scope": list(lock.scope),
        "changed_files": changed,
        "commit": commit,
        "push_target": push_target,
        "mutation_lease_id": lock.mutation_lease_id,
        "base": base,
        "result": result,
        "staged": staged,
        "changed": changed,
        "ownership": {
            "canonical": True,
            "repo": str(lock.repo),
            "scope": lock.scope,
            "lock_path": str(lock.lock_path),
            "lock_run_id": lock.run_id,
        },
    }
    path = lock.receipt_root / (
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-"
        f"{lock.run_id}-{uuid.uuid4().hex[:8]}.json"
    )
    _write_json_atomic(path, payload)
    return path


def _is_canonical_governance_repo(repo: Path) -> bool:
    """Allow Task Scheduler registration only from the live canonical checkout."""
    if os.name != "nt":
        return False
    try:
        actual = os.path.normcase(str(repo.resolve()))
        expected = os.path.normcase(str(CANONICAL_GOVERNANCE_ROOT.resolve()))
        return actual == expected
    except OSError:
        return False

# 状态枚举（§6）
IN_SYNC = "IN_SYNC"
LOCAL_PENDING = "LOCAL_PENDING"
REMOTE_PENDING = "REMOTE_PENDING"
REMOTE_AHEAD = "REMOTE_AHEAD"
LOCAL_AHEAD = "LOCAL_AHEAD"
LOCAL_DIRTY = "LOCAL_DIRTY"  # legacy alias
DIVERGED = "DIVERGED"
CONFLICT = "CONFLICT"
BLOCKED = "BLOCKED"
BLOCKED_AUTH = "BLOCKED_AUTH"
BLOCKED_PRIVACY = "BLOCKED_PRIVACY"
OPTIONAL_NOT_INSTALLED = "OPTIONAL_NOT_INSTALLED"
UNKNOWN = "UNKNOWN"

# 正交工作区状态
WORKTREE_CLEAN = "CLEAN"
WORKTREE_DIRTY_SAFE = "DIRTY_SAFE"
WORKTREE_DIRTY_CONFLICT = "DIRTY_CONFLICT"
WORKTREE_DIRTY_BLOCKED = "DIRTY_BLOCKED"

NON_CANONICAL_PATTERNS = (
    ".verify", "tmp/", "temp/", ".tmp", ".log", ".bak", ".swp",
    "runtime/", "node_modules/", ".dsh/", ".dsh-context-lifecycle/",
    "base-dsh-", "dist/",
)

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


def _device_backup_root(state_repo: Path | None = None) -> Path | None:
    """解析 durability backup root（personal-ai-state/sync/this-device.yaml）。
    失败返回 None（该设备未配置备份 = NOT_APPLICABLE 语义）。"""
    if state_repo:
        rel = state_repo / "sync" / "this-device.yaml"
        if rel.is_file():
            try:
                import yaml  # noqa: PLC0415
                cfg = yaml.safe_load(rel.read_text(encoding="utf-8-sig")) or {}
                root = cfg.get("backup_root")
                return Path(root) if root else None
            except Exception:  # noqa: BLE001
                return None
        return None
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
        return p.returncode, (p.stdout + p.stderr).rstrip()
    except Exception as exc:  # noqa: BLE001
        return -1, str(exc)


def git(repo: Path, *args: str, timeout: int = 120) -> tuple[int, str]:
    return run(["git", "-C", str(repo), *args], timeout=timeout)


def _default_branch(repo: Path) -> str:
    rc, out = git(repo, "symbolic-ref", "--short", "HEAD")
    return out.strip() if rc == 0 and out.strip() else "main"


# ---------------------------------------------------------------- git classify
def _is_sync_eligible(repo: Path, rel_path: str, repo_name: str = "") -> bool:
    norm = rel_path.replace("\\", "/")
    if any(pat in norm for pat in NON_CANONICAL_PATTERNS):
        return False
    if repo_name == "personal-ai-state" or repo.name == "personal-ai-state":
        return norm.startswith(("state/", "sync/", "memory/", "projects/", "README"))
    if repo_name == "agent-tools" or repo == REPO or (repo / "registry").is_dir():
        if norm.startswith(("registry/", "scripts/", "dsh/", "skills/", "docs/", "tests/",
                            "state/", "tools/", "config/", "dsh-config/")):
            return True
        if norm in ("README.md", "SKILLS.md", "AGENTS.md", "package.json", "pnpm-lock.yaml", "LICENSE", ".gitignore"):
            return True
        return False
    return norm.startswith(("src/", "scripts/", "docs/", "tests/", "lib/"))


def uncommitted_files(repo: Path) -> list[tuple[str, str]]:
    p = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "-u"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0 or not p.stdout.strip():
        return []
    res = []
    for line in p.stdout.splitlines():
        if not line:
            continue
        code = line[:2]
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ")[-1].strip()
        res.append((code, path.replace("\\", "/")))
    return res


def remote_changed_files(repo: Path, branch: str) -> set[str]:
    rc, out = git(repo, "diff", "--name-only", f"HEAD..origin/{branch}")
    if rc != 0:
        return set()
    return {l.strip() for l in out.splitlines() if l.strip()}


def _normalise_scope_paths(paths: list[str] | tuple[str, ...]) -> list[str]:
    normalised = []
    for raw in paths:
        value = str(raw).replace("\\", "/").strip()
        if not value or value.startswith("/") or re.match(r"^[A-Za-z]:/", value):
            raise ValueError(f"invalid owned path: {raw}")
        parts = value.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise ValueError(f"invalid owned path: {raw}")
        normalised.append(value)
    return sorted(set(normalised))


def _path_in_scope(path: str, scope: list[str] | tuple[str, ...]) -> bool:
    value = path.replace("\\", "/")
    for owned in scope:
        prefix = owned[:-3] if owned.endswith("/**") else None
        if prefix is not None and (value == prefix or value.startswith(prefix + "/")):
            return True
        if value == owned:
            return True
    return False


def _staged_paths(repo: Path) -> list[str]:
    rc, out = git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMRTUXB")
    return sorted({line.strip().replace("\\", "/") for line in out.splitlines() if line.strip()}) if rc == 0 else []


def _mutation_lock_root_for_repo(repo: Path) -> Path:
    if _is_canonical_mutation_repo(repo):
        return MUTATION_LOCK_ROOT
    return repo.resolve(strict=False).parent / f".{repo.name}.personal-ai-mutation"


def _mutation_root_for_plane(name: str, repo: Path) -> Path | None:
    if name == "agent-tools":
        return CANONICAL_GOVERNANCE_ROOT if _path_key(repo) == _path_key(REPO) else repo
    if name == "personal-ai-state":
        return STATE_REPO if _path_key(repo) == _path_key(STATE_REPO) else repo
    if name.startswith("project:"):
        return repo
    return repo


def _mutation_lock_for_plane(
    name: str,
    repo: Path,
    results: dict,
    *,
    operation: str,
    scope: list[str],
) -> CanonicalMutationLock:
    return CanonicalMutationLock(
        repo,
        actor=results.get("actor", "personal-ai-sync"),
        trigger=results.get("trigger", "personal_ai_sync"),
        task_id=results.get("task_id", "personal-ai-sync"),
        actor_type=results.get("actor_type"),
        thread_id=results.get("thread_id"),
        entrypoint=results.get("entrypoint"),
        process_start_time=results.get("process_start_time"),
        run_id=results.get("run_id"),
        operation=operation,
        scope=scope,
        lock_root=_mutation_lock_root_for_repo(repo),
        canonical_root=_mutation_root_for_plane(name, repo),
    )


def _receipt_scope_is_valid(receipt: dict) -> bool:
    changed = receipt.get("changed_files")
    scope = receipt.get("owned_scope")
    if not isinstance(changed, list) or not isinstance(scope, list):
        return False
    scope_values = [str(item) for item in scope]
    if any(item in ("git-history", "repository") for item in scope_values):
        return True
    return all(_path_in_scope(str(path), scope_values) for path in changed)


def _receipt_structurally_valid(
    receipt: object,
    repo: Path,
    *,
    operation: str | None = None,
) -> bool:
    if not isinstance(receipt, dict):
        return False
    if receipt.get("schema") != MUTATION_RECEIPT_SCHEMA \
            or receipt.get("provenance_schema") != MUTATION_AUDIT_SCHEMA:
        return False
    if any(field not in receipt or receipt.get(field) in (None, "")
           for field in PROVENANCE_REQUIRED_FIELDS):
        return False
    recorded_repo = receipt.get("repo")
    if not isinstance(recorded_repo, str) or _path_key(Path(recorded_repo)) != _path_key(repo):
        return False
    ownership = receipt.get("ownership")
    if not isinstance(ownership, dict) or ownership.get("canonical") is not True:
        return False
    if receipt.get("result") not in {
            "COMMITTED", "MERGED", "PULLED", "PUSHED", "SCOPE_VIOLATION"}:
        return False
    if not _receipt_scope_is_valid(receipt):
        return False
    if operation is not None and receipt.get("operation") != operation:
        return False
    if receipt.get("result") == "PUSHED":
        if receipt.get("remote_before") == PROVENANCE_UNKNOWN \
                or receipt.get("remote_after") == PROVENANCE_UNKNOWN \
                or receipt.get("push_target") == PROVENANCE_UNKNOWN:
            return False
    return True


def _load_mutation_receipts(root: Path) -> list[tuple[Path, dict]]:
    if not root.is_dir():
        return []
    loaded = []
    for path in sorted(root.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            loaded.append((path, value))
    return loaded


def _receipt_covers_commit(repo: Path, receipt: dict, commit: str) -> bool:
    if not _receipt_structurally_valid(receipt, repo):
        return False
    if receipt.get("commit") == commit:
        return receipt.get("result") in {"COMMITTED", "MERGED", "PULLED", "PUSHED"}
    if receipt.get("result") != "PULLED":
        return False
    base = receipt.get("base_head")
    result = receipt.get("result_head")
    if base in (None, "", PROVENANCE_UNKNOWN) or result in (None, "", PROVENANCE_UNKNOWN):
        return False
    base_rc, _ = git(repo, "merge-base", "--is-ancestor", str(base), commit)
    result_rc, _ = git(repo, "merge-base", "--is-ancestor", commit, str(result))
    return base_rc == 0 and result_rc == 0


def validate_mutation_receipt(
    receipt_path: Path | str,
    repo: Path,
    *,
    commit: str | None = None,
    operation: str | None = None,
) -> bool:
    """Validate structural provenance and optional commit/push linkage."""
    try:
        value = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return False
    if not _receipt_structurally_valid(value, repo, operation=operation):
        return False
    return commit is None or _receipt_covers_commit(repo, value, commit)


def _commit_affected_files(repo: Path, commit: str) -> list[str]:
    rc, out = git(repo, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", commit)
    return sorted({line.strip().replace("\\", "/") for line in out.splitlines() if line.strip()}) \
        if rc == 0 else []


def _commit_provenance_record(repo: Path, commit: str) -> dict:
    record = {
        "commit": commit,
        "timestamp": PROVENANCE_UNKNOWN,
        "author": PROVENANCE_UNKNOWN,
        "affected_files": _commit_affected_files(repo, commit),
    }
    rc, out = git(repo, "show", "-s", "--format=%H%x00%ct%x00%an%x00%ae", commit)
    if rc != 0:
        record["error"] = out[-300:] if out else "cannot read commit metadata"
        return record
    parts = out.strip().split("\x00")
    if len(parts) >= 4:
        record["commit"] = parts[0] or commit
        try:
            record["timestamp"] = datetime.fromtimestamp(
                int(parts[1]), tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError):
            record["timestamp"] = PROVENANCE_UNKNOWN
        record["author"] = {"name": parts[2], "email": parts[3]}
    return record


def inspect_commit_provenance(
    repo: Path,
    commit: str | None = None,
    *,
    receipt_root: Path | None = None,
) -> dict:
    """Classify one existing commit without manufacturing historical evidence."""
    repo = repo.resolve(strict=False)
    if commit is None:
        rc, out = git(repo, "rev-parse", "HEAD")
        if rc != 0:
            return {"status": PROVENANCE_UNKNOWN, "error": out}
        commit = out.strip()
    root = receipt_root or (_mutation_lock_root_for_repo(repo) / "receipts")
    matching = [str(path) for path, receipt in _load_mutation_receipts(root)
                if _receipt_covers_commit(repo, receipt, commit)]
    record = _commit_provenance_record(repo, commit)
    record["status"] = "GOVERNED" if matching else LEGACY_UNATTRIBUTED_COMMIT
    record["receipt_paths"] = matching
    return record


def _provenance_audit_root_for_repo(repo: Path) -> Path:
    return _mutation_lock_root_for_repo(repo) / "provenance-audit"


def _provenance_state_path(repo: Path, audit_root: Path) -> Path:
    digest = hashlib.sha256(_path_key(repo).encode("utf-8")).hexdigest()[:24]
    return audit_root / f"{digest}.state.json"


def audit_canonical_commits(
    repo: Path,
    *,
    audit_root: Path | None = None,
    receipt_root: Path | None = None,
    previous_head: str | None = None,
    persist: bool = True,
) -> dict:
    """Audit new commits and persist review evidence without changing Git state."""
    repo = repo.resolve(strict=False)
    audit_root = (audit_root or _provenance_audit_root_for_repo(repo)).resolve(strict=False)
    receipt_root = (receipt_root or (_mutation_lock_root_for_repo(repo) / "receipts")) \
        .resolve(strict=False)
    checked_at = datetime.now(timezone.utc).isoformat()
    state_path = _provenance_state_path(repo, audit_root)
    result = {
        "schema": MUTATION_AUDIT_SCHEMA,
        "repo": str(repo),
        "checked_at": checked_at,
        "state_path": str(state_path),
        "previous_audited_head": previous_head or PROVENANCE_UNKNOWN,
        "current_head": PROVENANCE_UNKNOWN,
        "status": PROVENANCE_UNKNOWN,
        "result": PROVENANCE_UNKNOWN,
        "new_commits": [],
        "governed_commits": [],
        "unauthorized": [],
        "legacy_unattributed": [],
    }

    if not (repo / ".git").exists():
        result["error"] = "not a git repository"
        return result
    head_rc, head_out = git(repo, "rev-parse", "HEAD")
    if head_rc != 0 or not head_out.strip():
        result["error"] = head_out or "cannot resolve HEAD"
        return result
    head = head_out.strip()
    result["current_head"] = head

    state = {}
    if previous_head is None and state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            result["error"] = f"audit state unreadable: {exc}"
            return result
        previous_head = state.get("last_audited_head")
    if previous_head in (None, "", PROVENANCE_UNKNOWN):
        previous_head = None
    result["previous_audited_head"] = previous_head or PROVENANCE_UNKNOWN

    def persist_state(status: str, audit_result: str, *, event: dict | None = None) -> None:
        if not persist:
            return
        state_payload = {
            "schema": MUTATION_AUDIT_SCHEMA,
            "repo": str(repo),
            "last_audited_head": head,
            "last_checked_at": checked_at,
            "last_status": status,
            "last_result": audit_result,
            "last_unauthorized": result["unauthorized"],
        }
        _write_json_atomic(state_path, state_payload)
        if event is not None:
            event_path = audit_root / "events" / (
                f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-"
                f"{uuid.uuid4().hex}.json"
            )
            _write_json_atomic(event_path, event)

    if previous_head is None:
        legacy = inspect_commit_provenance(repo, head, receipt_root=receipt_root)
        if legacy.get("status") == LEGACY_UNATTRIBUTED_COMMIT:
            legacy["classification"] = LEGACY_UNATTRIBUTED_COMMIT
            result["legacy_unattributed"] = [legacy]
        result["status"] = "BASELINE_INITIALIZED"
        result["result"] = "PASS"
        try:
            persist_state(result["status"], result["result"])
        except (OSError, UnicodeError, TypeError, ValueError) as exc:
            result["status"] = PROVENANCE_UNKNOWN
            result["result"] = PROVENANCE_UNKNOWN
            result["error"] = f"audit evidence write failed: {exc}"
        return result

    if previous_head == head:
        result["status"] = "NO_CHANGE"
        result["result"] = "PASS"
        try:
            persist_state(result["status"], result["result"])
        except (OSError, UnicodeError, TypeError, ValueError) as exc:
            result["status"] = PROVENANCE_UNKNOWN
            result["result"] = PROVENANCE_UNKNOWN
            result["error"] = f"audit evidence write failed: {exc}"
        return result

    ancestor_rc, ancestor_out = git(repo, "merge-base", "--is-ancestor", str(previous_head), head)
    if ancestor_rc != 0:
        result["status"] = PROVENANCE_UNKNOWN
        result["result"] = PROVENANCE_UNKNOWN
        result["error"] = (
            "previous audited HEAD is not an ancestor of current HEAD; "
            f"previous={previous_head} current={head} detail={ancestor_out}"
        )
        return result

    commits_rc, commits_out = git(repo, "rev-list", "--reverse", f"{previous_head}..{head}")
    if commits_rc != 0:
        result["status"] = PROVENANCE_UNKNOWN
        result["result"] = PROVENANCE_UNKNOWN
        result["error"] = commits_out or "cannot enumerate new commits"
        return result

    receipts = _load_mutation_receipts(receipt_root)
    unauthorized = []
    governed = []
    commits = [line.strip() for line in commits_out.splitlines() if line.strip()]
    for commit in commits:
        record = _commit_provenance_record(repo, commit)
        record["previous_audited_head"] = previous_head
        record["current_head"] = head
        covered_by = [str(path) for path, receipt in receipts
                      if _receipt_covers_commit(repo, receipt, commit)]
        record["receipt_paths"] = covered_by
        if covered_by:
            record["classification"] = "GOVERNED"
            governed.append(record)
        else:
            record["classification"] = UNAUTHORIZED_OR_UNATTRIBUTED_CANONICAL_MUTATION
            unauthorized.append(record)
    result["new_commits"] = commits
    result["governed_commits"] = governed
    result["unauthorized"] = unauthorized
    if unauthorized:
        result["status"] = UNAUTHORIZED_OR_UNATTRIBUTED_CANONICAL_MUTATION
        result["result"] = "REVIEW"
    else:
        result["status"] = "CLEAN"
        result["result"] = "PASS"
    event = None
    if unauthorized:
        event = {
            "schema": MUTATION_AUDIT_SCHEMA,
            "event_type": UNAUTHORIZED_OR_UNATTRIBUTED_CANONICAL_MUTATION,
            "timestamp": checked_at,
            "repo": str(repo),
            "previous_audited_head": previous_head,
            "current_head": head,
            "commits": unauthorized,
            "action": "REVIEW_ONLY_NO_RESET_NO_REVERT",
        }
    try:
        persist_state(result["status"], result["result"], event=event)
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        result["status"] = PROVENANCE_UNKNOWN
        result["result"] = PROVENANCE_UNKNOWN
        result["error"] = f"audit evidence write failed: {exc}"
    return result


def _owned_commit_receipt(repo: Path, commit: str, receipt_root: Path | None = None) -> bool:
    root = receipt_root or MUTATION_LOCK_ROOT / "receipts"
    for _, receipt in _load_mutation_receipts(root):
        if (receipt.get("result") in ("COMMITTED", "MERGED")
                and receipt.get("commit") == commit
                and _receipt_structurally_valid(receipt, repo)):
            return True
    return False


def _local_ahead_commits(repo: Path, branch: str) -> list[str]:
    rc, out = git(repo, "rev-list", "--reverse", f"origin/{branch}..HEAD")
    return [line.strip() for line in out.splitlines() if line.strip()] if rc == 0 else []


def _commit_owned_files_locked(
    lock: CanonicalMutationLock,
    owned: list[str],
    *,
    allow_foreign_dirty: bool,
    message: str,
    validate: bool,
) -> tuple[bool, str]:
    entries = uncommitted_files(lock.repo)
    staged_before = set(_staged_paths(lock.repo))
    foreign_staged = sorted(path for path in staged_before if not _path_in_scope(path, owned))
    if foreign_staged:
        return False, f"DEFER: foreign staged paths present: {foreign_staged}"
    foreign_dirty = sorted(path for _, path in entries if not _path_in_scope(path, owned))
    if foreign_dirty and not allow_foreign_dirty:
        return False, f"DEFER: worktree contains foreign dirty paths: {foreign_dirty}"
    if not any(_path_in_scope(path, owned) for _, path in entries):
        return False, "no owned changes"

    rc, base = git(lock.repo, "rev-parse", "HEAD")
    if rc != 0:
        return False, f"cannot resolve mutation base: {base}"

    if validate:
        vscript = lock.repo / "scripts" / "validate_repo.py"
        if vscript.is_file():
            rc, out = run([sys.executable, str(vscript), "--strict"], cwd=lock.repo)
            if rc != 0:
                return False, f"canonical validation failed: {out[-200:]}"

    rc, out = git(lock.repo, "add", "--", *owned)
    if rc != 0:
        return False, f"owned staging failed: {out}"
    staged_after = _staged_paths(lock.repo)
    if not staged_after:
        return False, "no owned staged changes"
    foreign_after = sorted(path for path in staged_after if not _path_in_scope(path, owned))
    if foreign_after:
        return False, f"ABORT: staged scope violation: {foreign_after}"

    rc, diff = git(lock.repo, "diff", "--cached")
    if rc != 0:
        return False, f"staged diff failed: {diff}"
    for pattern in PRIVACY_PATTERNS:
        if re.search(pattern, diff):
            return False, f"privacy scan hit in staged diff: {pattern}"

    idargs = _identity_args(lock.repo)
    rc, out = git(lock.repo, *idargs, "commit", "-m", message)
    if rc != 0:
        return False, f"commit failed: {out}"
    rc, commit = git(lock.repo, "rev-parse", "HEAD")
    if rc != 0:
        return False, f"cannot resolve committed revision: {commit}"
    changed = changed_paths(lock.repo, f"{base.strip()}..{commit.strip()}")
    if any(not _path_in_scope(path, owned) for path in changed):
        write_mutation_receipt(lock, base=base.strip(), result="SCOPE_VIOLATION",
                               staged=staged_after, changed=changed, commit=commit.strip(),
                               base_head=base.strip(), result_head=commit.strip())
        return False, f"ABORT: committed scope violation: {changed}"
    try:
        receipt = write_mutation_receipt(lock, base=base.strip(), result="COMMITTED",
                                         staged=staged_after, changed=changed, commit=commit.strip(),
                                         base_head=base.strip(), result_head=commit.strip())
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        return False, f"COMMITTED_WITHOUT_RECEIPT: {exc}"
    if not validate_mutation_receipt(receipt, lock.repo, commit=commit.strip()):
        return False, f"COMMITTED_WITHOUT_VALID_RECEIPT: {receipt}"
    return True, f"committed {commit.strip()} receipt={receipt}"


def commit_owned_files(
    repo: Path,
    owned_files: list[str],
    *,
    actor: str = "personal-ai-sync",
    trigger: str = "explicit-owned-commit",
    task_id: str = "personal-ai-sync",
    actor_type: str | None = None,
    thread_id: str | None = None,
    entrypoint: str | None = None,
    process_start_time: str | None = None,
    run_id: str | None = None,
    operation: str = "owned-commit",
    allow_foreign_dirty: bool = False,
    validate: bool = True,
    message: str = "sync: commit owned canonical files",
    lock_root: Path | None = None,
    receipt_root: Path | None = None,
    canonical_root: Path | None = None,
    stale_after: int = MUTATION_LOCK_STALE_SECONDS,
) -> tuple[bool, str]:
    """Commit only an explicit owned-file scope while preserving foreign work."""
    try:
        owned = _normalise_scope_paths(owned_files)
    except ValueError as exc:
        return False, f"DEFER: {exc}"
    if not owned:
        return False, "DEFER: empty owned scope"
    lock = CanonicalMutationLock(
        repo,
        actor=actor,
        trigger=trigger,
        task_id=task_id,
        actor_type=actor_type,
        thread_id=thread_id,
        entrypoint=entrypoint,
        process_start_time=process_start_time,
        run_id=run_id,
        operation=operation,
        scope=owned,
        lock_root=lock_root,
        receipt_root=receipt_root,
        canonical_root=canonical_root,
        stale_after=stale_after,
    )
    try:
        with lock:
            return _commit_owned_files_locked(lock, owned,
                                              allow_foreign_dirty=allow_foreign_dirty,
                                              message=message, validate=validate)
    except MutationOwnershipError as exc:
        return False, f"{exc.code}: {exc}"


def auto_commit_eligible(repo: Path, c: dict) -> tuple[bool, str]:
    """Compatibility guard: AUTO_COMMIT is never an implicit dirty-tree action."""
    if c.get("dirty") or uncommitted_files(repo):
        return False, "DEFER: automatic commit requires a clean worktree; explicit owned commit required"
    return False, "DEFER: AUTO_COMMIT is disabled; use explicit owned commit policy"


def classify_repo(repo: Path, fetch: bool = True, repo_name: str = "") -> dict:
    """统一仓库状态分类（正交建模：sync_state + graph_state + worktree_state）。只读（除 fetch）。"""
    r = {
        "path": str(repo),
        "sync_state": UNKNOWN,
        "state": UNKNOWN,
        "graph_state": UNKNOWN,
        "worktree_state": WORKTREE_CLEAN,
        "ahead": 0,
        "behind": 0,
        "dirty": False,
        "pending_sync_changes": False,
        "local_only_preserved": False,
        "branch": "",
        "reason": "",
        "conflict_files": [],
        "eligible_canonical_changes": [],
        "uncommitted_files": [],
    }
    if not (repo / ".git").exists():
        r["sync_state"] = UNKNOWN
        r["state"] = UNKNOWN
        r["graph_state"] = UNKNOWN
        r["reason"] = "not a git repo / missing"
        return r
    r["branch"] = _default_branch(repo)

    # 1. 检查工作区状态
    entries = uncommitted_files(repo)
    uncommitted_paths = {p for _, p in entries}
    r["uncommitted_files"] = sorted(uncommitted_paths)
    r["dirty"] = bool(entries)

    has_conflict_markers = any(code in ("UU", "AA", "DD", "DU", "UD", "AU", "UA") for code, _ in entries)
    if has_conflict_markers:
        r["worktree_state"] = WORKTREE_DIRTY_BLOCKED
    elif entries:
        r["worktree_state"] = WORKTREE_DIRTY_SAFE
    else:
        r["worktree_state"] = WORKTREE_CLEAN

    r["eligible_canonical_changes"] = [p for code, p in entries if _is_sync_eligible(repo, p, repo_name)]

    # 2. 检查 Fetch & Graph 状态
    if fetch:
        frc, fout = git(repo, "fetch", "--quiet", "origin", timeout=90)
        if frc != 0:
            is_auth = "Permission" in fout or "denied" in fout or "fatal: Authentication failed" in fout
            r["graph_state"] = BLOCKED_AUTH if is_auth else UNKNOWN
            r["sync_state"] = BLOCKED_AUTH if is_auth else UNKNOWN
            r["state"] = BLOCKED_AUTH if is_auth else UNKNOWN
            r["reason"] = f"fetch failed: {fout.splitlines()[-1] if fout else frc}"
            return r

    rc, remote = git(repo, "rev-parse", f"origin/{r['branch']}")
    if rc != 0:
        r["sync_state"] = UNKNOWN
        r["state"] = UNKNOWN
        r["graph_state"] = UNKNOWN
        r["reason"] = f"no origin/{r['branch']}"
        return r
    r["remote_head"] = remote.strip()

    rc, ahead = git(repo, "rev-list", "--count", f"origin/{r['branch']}..HEAD")
    rc2, behind = git(repo, "rev-list", "--count", f"HEAD..origin/{r['branch']}")
    r["ahead"], r["behind"] = int(ahead or 0), int(behind or 0)

    if r["ahead"] == 0 and r["behind"] == 0:
        r["graph_state"] = IN_SYNC
        if r["eligible_canonical_changes"]:
            r["sync_state"] = LOCAL_PENDING
            r["state"] = LOCAL_PENDING
            r["pending_sync_changes"] = True
        else:
            r["sync_state"] = IN_SYNC
            r["state"] = IN_SYNC
            if r["worktree_state"] == WORKTREE_DIRTY_SAFE:
                r["local_only_preserved"] = True
    elif r["behind"] > 0 and r["ahead"] == 0:
        r["graph_state"] = REMOTE_AHEAD
        r["pending_sync_changes"] = True
        if r["dirty"] and not has_conflict_markers:
            rem_changed = remote_changed_files(repo, r["branch"])
            overlap = uncommitted_paths & rem_changed
            if overlap:
                r["worktree_state"] = WORKTREE_DIRTY_CONFLICT
                r["conflict_files"] = sorted(overlap)
                r["sync_state"] = CONFLICT
                r["state"] = CONFLICT
            else:
                r["worktree_state"] = WORKTREE_DIRTY_SAFE
                r["sync_state"] = REMOTE_PENDING
                r["state"] = REMOTE_PENDING
        else:
            r["sync_state"] = REMOTE_PENDING
            r["state"] = REMOTE_PENDING
    elif r["ahead"] > 0 and r["behind"] == 0:
        r["graph_state"] = LOCAL_AHEAD
        r["pending_sync_changes"] = True
        r["sync_state"] = LOCAL_PENDING
        r["state"] = LOCAL_PENDING
        if r["worktree_state"] == WORKTREE_DIRTY_SAFE and not r["eligible_canonical_changes"]:
            r["local_only_preserved"] = True
    else:
        r["graph_state"] = DIVERGED
        r["pending_sync_changes"] = True
        if r["dirty"] and not has_conflict_markers:
            rem_changed = remote_changed_files(repo, r["branch"])
            overlap = uncommitted_paths & rem_changed
            if overlap:
                r["worktree_state"] = WORKTREE_DIRTY_CONFLICT
                r["conflict_files"] = sorted(overlap)
                r["sync_state"] = CONFLICT
                r["state"] = CONFLICT
            else:
                r["sync_state"] = DIVERGED
                r["state"] = DIVERGED
        else:
            r["sync_state"] = DIVERGED
            r["state"] = DIVERGED

    if r["worktree_state"] == WORKTREE_DIRTY_BLOCKED:
        r["sync_state"] = CONFLICT
        r["state"] = CONFLICT

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
    """fetch → classify → action plan（正交建模状态机）。"""
    plan = []
    for name, c in classifications.items():
        graph = c.get("graph_state", c.get("state", UNKNOWN))
        worktree = c.get("worktree_state", WORKTREE_CLEAN if not c.get("dirty") else WORKTREE_DIRTY_SAFE)

        if graph == BLOCKED_AUTH:
            plan.append({"plane": name, "action": "REVIEW", "state": BLOCKED_AUTH,
                         "reason": c.get("reason", "auth blocked")})
        elif c.get("dirty") or worktree != WORKTREE_CLEAN:
            reason = "DEFER: canonical worktree is dirty; no pull/push/merge/commit/overwrite"
            if worktree == WORKTREE_DIRTY_BLOCKED:
                reason = "DEFER: unresolved merge conflict in canonical worktree"
            plan.append({"plane": name, "action": "REVIEW", "state": worktree,
                         "reason": reason})
        elif graph == REMOTE_AHEAD:
            plan.append({"plane": name, "action": "PULL", "state": REMOTE_AHEAD})
        elif graph == LOCAL_AHEAD:
            if mode == "push":
                plan.append({"plane": name, "action": "PUSH", "state": LOCAL_AHEAD})
            else:
                plan.append({"plane": name, "action": "REVIEW", "state": LOCAL_AHEAD,
                             "reason": "DEFER: push requires explicit push mode and ownership receipts"})
        elif graph == IN_SYNC:
            plan.append({"plane": name, "action": "NO ACTION", "state": IN_SYNC})
        elif graph == DIVERGED:
            plan.append({"plane": name, "action": "REVIEW", "state": DIVERGED,
                         "reason": "history diverged"})
        else:
            plan.append({"plane": name, "action": "REVIEW", "state": c.get("state", UNKNOWN),
                         "reason": c.get("reason", "")})
    return plan


def execute_plan(plan: list[dict], classifications: dict,
                 state_repo: Path | None, mode: str,
                 results: dict) -> None:
    """只执行确定安全动作（§22）；其余保持 REVIEW。全部幂等可重跑（§36）。"""

    def review(item: dict, reason: str, state: str | None = None) -> None:
        item["action"] = "REVIEW"
        item["executed"] = False
        item["reason"] = reason
        if state is not None:
            item["state"] = state

    for item in plan:
        name, action = item["plane"], item["action"]
        c = classifications[name]
        repo = Path(c["path"])
        if mode == "check":
            item["action"] = "NO ACTION" if action in ("PULL", "PUSH", "AUTO_COMMIT") else action
            continue
        if action == "PULL":
            if mode == "push":
                review(item, "push-only mode")
                continue
            try:
                lock = _mutation_lock_for_plane(
                    name, repo, results, operation="fast-forward-pull", scope=["git-history"])
                with lock:
                    if uncommitted_files(repo):
                        review(item, "DEFER: worktree became dirty before fast-forward pull",
                               WORKTREE_DIRTY_SAFE)
                        continue
                    current = classify_repo(repo, fetch=True, repo_name=name)
                    if current.get("graph_state") != REMOTE_AHEAD or current.get("dirty"):
                        review(item, "DEFER: repository state changed before locked fast-forward",
                               current.get("worktree_state", WORKTREE_CLEAN))
                        continue
                    base_rc, base = git(repo, "rev-parse", "HEAD")
                    rc, out = git(repo, "merge", "--ff-only", f"origin/{current['branch']}")
                    if rc != 0:
                        review(item, out.splitlines()[-1] if out else "fast-forward pull failed")
                        continue
                    head_rc, head = git(repo, "rev-parse", "HEAD")
                    changed = (changed_paths(repo, f"{base.strip()}..{head.strip()}")
                               if base_rc == 0 and head_rc == 0 else [])
                    receipt = write_mutation_receipt(
                        lock,
                        base=base.strip(),
                        result="PULLED",
                        staged=[],
                        changed=changed,
                        commit=head.strip(),
                        base_head=base.strip(),
                        result_head=head.strip(),
                        remote_before=current.get("remote_head", PROVENANCE_UNKNOWN),
                        remote_after=head.strip(),
                    )
                    if not validate_mutation_receipt(receipt, repo, commit=head.strip()):
                        review(item, f"PULLED_WITHOUT_VALID_RECEIPT: {receipt}")
                        continue
                    item["receipt"] = str(receipt)
                    item["executed"] = True
                    item["state"] = "PULLED"
                    c.update({"graph_state": IN_SYNC, "sync_state": IN_SYNC, "state": IN_SYNC,
                              "pending_sync_changes": False, "dirty": False,
                              "worktree_state": WORKTREE_CLEAN, "uncommitted_files": [],
                              "eligible_canonical_changes": []})
            except MutationOwnershipError as exc:
                review(item, f"{exc.code}: {exc}")
        elif action == "PUSH":
            if mode == "pull":
                review(item, "pull-only mode does not push")
                continue
            if mode != "push":
                review(item, "DEFER: push requires explicit push mode")
                continue
            try:
                lock = _mutation_lock_for_plane(
                    name, repo, results, operation="explicit-push", scope=["git-history"])
                with lock:
                    if uncommitted_files(repo):
                        review(item, "DEFER: worktree is dirty; explicit push is blocked",
                               WORKTREE_DIRTY_SAFE)
                        continue
                    current = classify_repo(repo, fetch=True, repo_name=name)
                    if current.get("graph_state") != LOCAL_AHEAD or current.get("dirty"):
                        review(item, "DEFER: repository is no longer clean and local-ahead",
                               current.get("worktree_state", WORKTREE_CLEAN))
                        continue
                    commits = _local_ahead_commits(repo, current["branch"])
                    receipt_root = _mutation_lock_root_for_repo(repo) / "receipts"
                    unowned = [commit for commit in commits
                               if not _owned_commit_receipt(repo, commit, receipt_root)]
                    if unowned:
                        review(item, f"DEFER: local commits lack canonical ownership receipts: {unowned}")
                        continue
                    if name == "agent-tools":
                        hits = privacy_scan(repo, f"origin/{current['branch']}..HEAD")
                        if hits:
                            review(item, f"privacy scan hit: {hits}", BLOCKED_PRIVACY)
                            c["sync_state"] = BLOCKED
                            c["state"] = BLOCKED
                            continue
                    if name.startswith("project:") and classifications[name].get("privacy_blocked"):
                        review(item, "public/privacy-blocked project cannot be pushed automatically", BLOCKED_PRIVACY)
                        c["sync_state"] = BLOCKED
                        c["state"] = BLOCKED
                        continue
                    local_base_rc, local_base = git(repo, "rev-parse", "HEAD")
                    base_rc, base = git(repo, "rev-parse", f"origin/{current['branch']}")
                    rc, out = git(repo, "push", "origin", current["branch"])
                    if rc != 0:
                        review(item, out.splitlines()[-1] if out else "push failed")
                        continue
                    head_rc, head = git(repo, "rev-parse", "HEAD")
                    changed = (changed_paths(repo, f"{base.strip()}..{head.strip()}")
                               if base_rc == 0 and head_rc == 0 else [])
                    remote_after_rc, remote_after = git(
                        repo, "rev-parse", f"origin/{current['branch']}"
                    )
                    receipt = write_mutation_receipt(
                        lock,
                        base=base.strip(),
                        result="PUSHED",
                        staged=[],
                        changed=changed,
                        commit=head.strip(),
                        base_head=local_base.strip() if local_base_rc == 0 else PROVENANCE_UNKNOWN,
                        result_head=head.strip() if head_rc == 0 else PROVENANCE_UNKNOWN,
                        remote_before=base.strip() if base_rc == 0 else PROVENANCE_UNKNOWN,
                        remote_after=remote_after.strip() if remote_after_rc == 0 else PROVENANCE_UNKNOWN,
                        push_target=f"origin/{current['branch']}",
                        operation="explicit-push",
                    )
                    if not validate_mutation_receipt(receipt, repo, commit=head.strip(),
                                                      operation="explicit-push"):
                        review(item, f"PUSHED_WITHOUT_VALID_RECEIPT: {receipt}")
                        continue
                    item["receipt"] = str(receipt)
                    item["executed"] = True
                    item["state"] = "PUSHED"
                    c.update({"graph_state": IN_SYNC, "sync_state": IN_SYNC, "state": IN_SYNC,
                              "pending_sync_changes": False})
            except MutationOwnershipError as exc:
                review(item, f"{exc.code}: {exc}")
        elif action == "AUTO_COMMIT":
            review(item, "DEFER: implicit AUTO_COMMIT is disabled; use explicit owned commit")
        elif action == "DEPLOY_FROM_MIRROR":
            review(item, "DEFER: dirty canonical source cannot be bypassed through a deployment mirror")
        elif action == "REVIEW" and item["state"] == DIVERGED and name == "personal-ai-state":
            _handle_state_divergence(item, repo, c, mode, results)


def _handle_state_divergence(item: dict, repo: Path, c: dict,
                             mode: str, results: dict) -> None:
    """Run the only allowed automatic merge behind the canonical writer lease."""
    scope = ["memory/records/**"]
    try:
        lock = _mutation_lock_for_plane(
            "personal-ai-state", repo, results,
            operation="deterministic-memory-merge", scope=scope)
        with lock:
            if uncommitted_files(repo):
                item["action"] = "REVIEW"
                item["reason"] = "DEFER: personal-ai-state became dirty before deterministic merge"
                item["executed"] = False
                return
            _handle_state_divergence_locked(item, repo, c, mode, results, lock)
    except MutationOwnershipError as exc:
        item["action"] = "REVIEW"
        item["executed"] = False
        item["reason"] = f"{exc.code}: {exc}"


def _handle_state_divergence_locked(item: dict, repo: Path, c: dict,
                                    mode: str, results: dict,
                                    lock: CanonicalMutationLock) -> None:
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

    base_rc, base = git(repo, "rev-parse", "HEAD")
    if base_rc != 0:
        item["reason"] = f"cannot resolve merge base revision: {base}"
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

    staged = _staged_paths(repo)
    if any(not _path_in_scope(path, ["memory/records/**"]) for path in staged):
        git(repo, "merge", "--abort")
        item["reason"] = f"ABORT: merge staged paths outside memory scope: {staged}"
        return
    rc, out = git(repo, *idargs, "commit", "--no-edit")
    if rc != 0:
        git(repo, "merge", "--abort")
        item["reason"] = f"merge commit 失败，已 abort: {out[-200:]}"
        return

    v = memory_merge_verify(repo)
    head_rc, head = git(repo, "rev-parse", "HEAD")
    changed = changed_paths(repo, f"{base.strip()}..{head.strip()}") if head_rc == 0 else []
    if any(not _path_in_scope(path, ["memory/records/**"]) for path in changed):
        write_mutation_receipt(lock, base=base.strip(), result="SCOPE_VIOLATION",
                               staged=staged, changed=changed, commit=head.strip(),
                               base_head=base.strip(), result_head=head.strip())
        item["action"] = "REVIEW"
        item["reason"] = f"ABORT: deterministic merge changed paths outside memory scope: {changed}"
        return
    receipt = write_mutation_receipt(lock, base=base.strip(), result="MERGED",
                                     staged=staged, changed=changed, commit=head.strip(),
                                     base_head=base.strip(), result_head=head.strip())
    if not validate_mutation_receipt(receipt, repo, commit=head.strip()):
        item["action"] = "REVIEW"
        item["reason"] = f"MERGED_WITHOUT_VALID_RECEIPT: {receipt}"
        return
    item["executed"] = True
    item["state"] = "MERGED"
    item["memory"] = v
    item["pushed"] = False
    item["push_required"] = True
    item["receipt"] = str(receipt)
    item["conflict_flags"] = flagged
    results["memory_merge"] = v
    if flagged:
        item["action"] = "REVIEW"
        item["reason"] = (f"CONFLICT_CONCURRENT_REVISION: {flagged}"
                          "（双方 revision 均保留并标记，后续 supersede 消解）")
    else:
        item["action"] = "REVIEW"
        item["reason"] = "deterministic memory merge committed locally; explicit push mode required"


def _audit_classified_repositories(classifications: dict) -> dict:
    """Audit every repository that the sync run treats as a managed plane."""
    audits = {}
    for name, classification in classifications.items():
        if name != "agent-tools" and name != "personal-ai-state" and not name.startswith("project:"):
            continue
        repo = Path(classification.get("path", ""))
        if not (repo / ".git").exists():
            continue
        try:
            audits[name] = audit_canonical_commits(repo)
        except (OSError, UnicodeError, TypeError, ValueError) as exc:
            audits[name] = {
                "schema": MUTATION_AUDIT_SCHEMA,
                "repo": str(repo),
                "status": PROVENANCE_UNKNOWN,
                "result": PROVENANCE_UNKNOWN,
                "error": f"provenance audit failed: {exc}",
            }
    return audits


# --------------------------------------------------------------------- sync
def run_sync(mode: str, detail: bool = False) -> dict:
    results: dict = {
        "mode": mode,
        "planes": {},
        "actions": [],
        "actor": os.environ.get("PERSONAL_AI_ACTOR", "personal-ai-sync"),
        "actor_type": os.environ.get("PERSONAL_AI_ACTOR_TYPE", _default_actor_type()),
        "trigger": os.environ.get("PERSONAL_AI_TRIGGER", "personal_ai_sync"),
        "task_id": os.environ.get("PERSONAL_AI_TASK_ID", "personal-ai-sync"),
        "run_id": os.environ.get("PERSONAL_AI_RUN_ID") or uuid.uuid4().hex,
        "thread_id": _default_thread_id(),
        "pid": os.getpid(),
        "ppid": os.getppid() if hasattr(os, "getppid") else PROVENANCE_UNKNOWN,
        "process_start_time": _process_start_time(),
        "entrypoint": _default_entrypoint(),
    }
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

    results["provenance"] = {
        "before": _audit_classified_repositories(classifications),
    }

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
    results["provenance"]["after"] = _audit_classified_repositories(classifications)

    # 受影响面判定（§7/§8/§23）：记录 pull/merge 产生的文件变化
    changed_at: list[str] = []
    changed_state: list[str] = []
    for item in plan:
        if item.get("state") not in ("PULLED", "MERGED", "PULLED_AND_PUSHED"):
            continue
        repo = Path(classifications[item["plane"]]["path"])
        rc, head = git(repo, "rev-parse", "HEAD")
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

    # 基于 desired-state 的下游收敛（不再仅依赖 pull 变更列表，而是直接检验期望状态 vs 实际状态）
    if mode in ("sync", "restore"):
        # 1. Skills 库状态检查与收敛
        skills_dest = Path.home() / ".dsh" / "skills"
        if SYNC_SKILLS.is_file():
            chk_rc, chk_out = run([sys.executable, str(SYNC_SKILLS), "--destination",
                                   str(skills_dest), "--check"])
            if chk_rc != 0:
                app_rc, app_out = run([sys.executable, str(SYNC_SKILLS), "--destination",
                                       str(skills_dest), "--apply"])
                results["skill_sync"] = "PASS" if app_rc == 0 else f"FAIL: {app_out[-200:]}"
            else:
                results["skill_sync"] = "PASS"

        # 2. DSH 运行时组合与 settings 期望状态检验与收敛
        if AIC.is_file():
            diff_rc, diff_out = run([sys.executable, str(AIC), "diff", "dsh"])
            if diff_rc != 0:
                app_rc, app_out = run([sys.executable, str(AIC), "apply", "dsh"], timeout=1800)
                if app_rc == 0:
                    post_rc, _ = run([sys.executable, str(AIC), "diff", "dsh"])
                    results["dsh_composition"] = "PASS" if post_rc == 0 else "DRIFT"
                else:
                    results["dsh_composition"] = f"FAIL: {app_out[-200:]}"
            else:
                results["dsh_composition"] = "PASS"

    if state_repo and (state_repo / "memory").is_dir():
        results["memory_refresh"] = memory_merge_verify(state_repo)

    results["runtime"] = runtime_status()
    results["secrets"] = check_secrets()

    # checkpoint（machine-local，可删，删除后可重新 discover）
    if writable or mode == "check":
        cp = load_checkpoint()
        cp.update({
            "device_id": os.environ.get("COMPUTERNAME", "unknown"),
            "last_sync": datetime.now(timezone.utc).isoformat(),
            "last_mode": mode,
            "per_repo_head": {n: _head(Path(c["path"])) for n, c in classifications.items()},
            "per_repo_sync_state": {
                n: c.get("sync_state", c.get("state", IN_SYNC)) for n, c in classifications.items()
            },
            "per_repo_worktree_state": {n: c.get("worktree_state", WORKTREE_CLEAN) for n, c in classifications.items()},
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
    if any(s in (CONFLICT, BLOCKED_PRIVACY, WORKTREE_DIRTY_BLOCKED) for s in states):
        return "REVIEW"
    if any(s == WORKTREE_DIRTY_CONFLICT and i.get("action") != "DEPLOY_FROM_MIRROR" for s, i in zip(states, plan)):
        return "REVIEW"
    if "REVIEW" in actions:
        return "REVIEW"
    if results.get("runtime", {}).get("status") == "DRIFT" \
            or results.get("runtime", {}).get("review"):
        return "REVIEW"
    if results.get("dsh_composition") == "DRIFT" or (
            isinstance(results.get("dsh_composition"), str)
            and results["dsh_composition"].startswith("FAIL")):
        return "REVIEW"
    if isinstance(results.get("skill_sync"), str) and results["skill_sync"].startswith("FAIL"):
        return "REVIEW"
    for phase in (results.get("provenance") or {}).values():
        for audit in (phase or {}).values():
            if audit.get("status") in (UNAUTHORIZED_OR_UNATTRIBUTED_CANONICAL_MUTATION,
                                        PROVENANCE_UNKNOWN):
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
    results = {
        "mode": "restore",
        "steps": [],
        "actor": os.environ.get("PERSONAL_AI_ACTOR", "personal-ai-sync"),
        "actor_type": os.environ.get("PERSONAL_AI_ACTOR_TYPE", _default_actor_type()),
        "trigger": os.environ.get("PERSONAL_AI_TRIGGER", "personal_ai_restore"),
        "task_id": os.environ.get("PERSONAL_AI_TASK_ID", "personal-ai-restore"),
        "run_id": os.environ.get("PERSONAL_AI_RUN_ID") or uuid.uuid4().hex,
        "thread_id": _default_thread_id(),
        "pid": os.getpid(),
        "ppid": os.getppid() if hasattr(os, "getppid") else PROVENANCE_UNKNOWN,
        "process_start_time": _process_start_time(),
        "entrypoint": _default_entrypoint(),
    }

    def step(name: str, ok: bool, note: str = "") -> None:
        results["steps"].append({"step": name, "ok": ok, "note": note})

    def clone_if_missing(target: Path, remote: str, label: str) -> None:
        if (target / ".git").exists():
            step(label, True, "already present")
            return
        try:
            if _is_canonical_mutation_repo(target):
                lock = CanonicalMutationLock(
                    target,
                    actor=results["actor"],
                    actor_type=results["actor_type"],
                    trigger=results["trigger"],
                    task_id=results["task_id"],
                    thread_id=results["thread_id"],
                    entrypoint=results["entrypoint"],
                    process_start_time=results["process_start_time"],
                    run_id=results["run_id"],
                    operation="restore-clone",
                    scope=["repository"],
                )
                with lock:
                    rc, out = run(["git", "clone", remote, str(target)], timeout=300)
            else:
                rc, out = run(["git", "clone", remote, str(target)], timeout=300)
            step(label, rc == 0, out[-200:] if rc else "")
        except MutationOwnershipError as exc:
            step(label, False, f"{exc.code}: {exc}")

    if not _is_canonical_mutation_repo(repo):
        step("canonical mutation ownership", True,
             "non-canonical restore source has no canonical writer identity")
    clone_if_missing(repo, agent_tools_remote, "clone agent-tools")
    clone_if_missing(state_repo, state_remote, "clone personal-ai-state")

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
            if _is_canonical_governance_repo(repo):
                try:
                    lock = CanonicalMutationLock(
                        repo,
                        actor=results["actor"],
                        actor_type=results["actor_type"],
                        trigger=results["trigger"],
                        task_id=results["task_id"],
                        thread_id=results["thread_id"],
                        entrypoint=results["entrypoint"],
                        process_start_time=results["process_start_time"],
                        run_id=results["run_id"],
                        operation="scheduler-registration",
                        scope=["scheduler:PersonalAI-Governance-Frequent",
                               "scheduler:PersonalAI-Governance-Weekly",
                               "scheduler:PersonalAI-Durability-Nightly",
                               "scheduler:PersonalAI-Sync-Check"],
                    )
                    with lock:
                        rc, out = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                       "-File", str(governance_tasks)], timeout=120)
                    step("governance tasks", rc == 0, out.splitlines()[-1] if out else "")
                except MutationOwnershipError as exc:
                    step("governance tasks", False, f"{exc.code}: {exc}")
            else:
                step("governance tasks", True,
                     "skipped: non-canonical restore source; scheduler registration requires C:\\Desktop\\skills")
        if (state_repo / "memory").is_dir():
            v = memory_merge_verify(state_repo)
            step("memory loadable", v["ok"], f"records={v.get('records')}")
    # DSH_SESSION_HISTORY plane（§9 事故契约）：备份计数 / live 计数 / 锚点 /
    # schema 探针；非 PASS|NOT_APPLICABLE 时总体 result 不得为 PASS。
    sh_root = sessions_root or (Path.home() / ".dsh" / "sessions")
    bk_root = backup_root if backup_root is not None else _device_backup_root(state_repo)
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


def run_provenance_audit() -> dict:
    """Run the read-only canonical commit audit used by scheduled governance checks."""
    results = {
        "mode": "audit",
        "actor": os.environ.get("PERSONAL_AI_ACTOR", "personal-ai-sync"),
        "actor_type": os.environ.get("PERSONAL_AI_ACTOR_TYPE", _default_actor_type()),
        "trigger": os.environ.get("PERSONAL_AI_TRIGGER", "personal_ai_provenance_audit"),
        "task_id": os.environ.get("PERSONAL_AI_TASK_ID", "personal-ai-provenance-audit"),
        "run_id": os.environ.get("PERSONAL_AI_RUN_ID") or uuid.uuid4().hex,
        "thread_id": _default_thread_id(),
        "pid": os.getpid(),
        "ppid": os.getppid() if hasattr(os, "getppid") else PROVENANCE_UNKNOWN,
        "process_start_time": _process_start_time(),
        "entrypoint": _default_entrypoint(),
        "provenance": {},
    }
    targets = {"agent-tools": REPO}
    if (STATE_REPO / ".git").exists():
        targets["personal-ai-state"] = STATE_REPO
    for name, repo in targets.items():
        try:
            results["provenance"][name] = audit_canonical_commits(repo)
        except (OSError, UnicodeError, TypeError, ValueError) as exc:
            results["provenance"][name] = {
                "schema": MUTATION_AUDIT_SCHEMA,
                "repo": str(repo),
                "status": PROVENANCE_UNKNOWN,
                "result": PROVENANCE_UNKNOWN,
                "error": f"provenance audit failed: {exc}",
            }
    audits = results["provenance"].values()
    results["result"] = "REVIEW" if any(
        audit.get("status") in (UNAUTHORIZED_OR_UNATTRIBUTED_CANONICAL_MUTATION,
                                 PROVENANCE_UNKNOWN)
        for audit in audits
    ) else "PASS"
    return results


# --------------------------------------------------------------------- output
def print_human(results: dict, detail: bool = False) -> None:
    print("Personal AI Sync\n")
    try:
        sys.path.insert(0, str(REPO / "scripts" / "aic"))
        import dsh_lifecycle
        lc = dsh_lifecycle.get_runtime_lifecycle(live_smoke=False)
        print(f"  CODE_REMOTE_SYNC             {lc.get('sourceRemote', {}).get('status', UNKNOWN)}")
        print(f"  DEPLOYMENT_SOURCE_SYNC       {lc.get('deploymentSource', {}).get('status', UNKNOWN)} "
              f"(clean={lc.get('deploymentSource', {}).get('clean', True)})")
        print(f"  DESIRED_STATE                {lc.get('desiredVsDeployed', UNKNOWN)}")
        print(f"  DEPLOYMENT_STATE             {lc.get('deployedReady', {}).get('status', UNKNOWN)}")
        print(f"  ACTIVE_PROCESS_STATE         {lc.get('deployedVsActive', UNKNOWN)}")
        restart = (f" ({lc.get('restartReason')})"
                   if lc.get('restartReason') and lc.get('restartReason') != 'NONE' else "")
        print(f"  RESTART_REQUIRED             {lc.get('restartRequired', 'NO')}{restart}")
        print(f"  LIVE_VALIDATION              {lc.get('liveValidation', {}).get('status', 'NOT_RUN')}")
        print()
    except Exception:
        pass
    for name, c in results.get("planes", {}).items():
        st = c.get("sync_state", c.get("state", UNKNOWN))
        wt = c.get("worktree_state", WORKTREE_CLEAN)
        note = c.get("note") or c.get("reason") or ""
        details = []
        if (wt == WORKTREE_DIRTY_SAFE and c.get("local_only_preserved", True)
                and not c.get("eligible_canonical_changes")):
            details.append("worktree: DIRTY_SAFE, local-only preserved")
        elif wt == WORKTREE_DIRTY_SAFE:
            details.append("worktree: DIRTY_SAFE")
        elif wt == WORKTREE_DIRTY_CONFLICT:
            details.append(f"worktree: DIRTY_CONFLICT ({c.get('conflict_files', [])})")
        elif wt == WORKTREE_DIRTY_BLOCKED:
            details.append("worktree: DIRTY_BLOCKED")
        if note:
            details.append(note)
        detail_str = f" — {', '.join(details)}" if details else ""
        print(f"  {name:22s} {st}{detail_str}")
    rt = results.get("runtime", {})
    if rt.get("status"):
        extra = f"（applied: {','.join(rt['applied'])}）" if rt.get("applied") else ""
        print(f"  {'runtime':22s} {rt['status']}{extra}")
    sec = results.get("secrets", {})
    if sec:
        print(f"  {'secrets':22s} {sec['status']}" +
              (f" — missing: {sec['missing']}" if sec.get("missing") else ""))
    for name, audit in (results.get("provenance") or {}).items():
        print(f"  {('provenance:'+name):22s} {audit.get('status', PROVENANCE_UNKNOWN)}")
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
                    choices=["check", "pull", "push", "sync", "restore", "audit"])
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.mode == "restore":
        results = run_restore(args.detail)
    elif args.mode == "audit":
        results = run_provenance_audit()
    else:
        results = run_sync(args.mode, args.detail)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print_human(results, args.detail)
    return {"PASS": 0, "REVIEW": 1, "BLOCKED": 2}.get(results.get("result", ""), 3)


if __name__ == "__main__":
    sys.exit(main())
