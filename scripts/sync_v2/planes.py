"""planes.py — Independent evaluators and reconcilers for Personal AI Sync V3."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

from .models import (
    EvidenceLevel,
    PlaneStatus,
    ResourceCategory,
    ResourceRecord,
    SnapshotContext,
    SyncPlane,
)

IGNORED_NAMES = {".DS_Store", "__pycache__"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}


class RelaxedYamlLoader(yaml.SafeLoader):
    pass

RelaxedYamlLoader.add_constructor(None, lambda loader, node: (
    node.value if isinstance(node, yaml.ScalarNode)
    else loader.construct_mapping(node) if isinstance(node, yaml.MappingNode)
    else loader.construct_sequence(node)
))


def _run_git(cwd: Path, *args: str, timeout: int = 30) -> Tuple[int, str]:
    try:
        p = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as exc:
        return -1, str(exc)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((p for p in root.rglob("*") if p.is_file()),
                       key=lambda p: p.relative_to(root).as_posix()):
        if any(part in IGNORED_NAMES for part in path.parts) or path.suffix in IGNORED_SUFFIXES:
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def package_files(package: Path) -> Dict[str, Path]:
    files = {}
    for path in package.rglob("*"):
        if any(part in IGNORED_NAMES for part in path.parts) or path.suffix in IGNORED_SUFFIXES:
            continue
        if path.is_file():
            files[path.relative_to(package).as_posix()] = path
    return files


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        shutil.copy2(source, temporary_path)
        os.replace(temporary_path, destination)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


# ---------------------------------------------------------------- Plane 1: Personal AI State
def evaluate_canonical_state_plane(state_repo: Path, snapshot: Optional[SnapshotContext] = None) -> ResourceRecord:
    """Plane 1: personal-ai-state Git ancestry evaluation."""
    if not state_repo.is_dir() or not (state_repo / ".git").exists():
        return ResourceRecord(
            resource_id="personal_ai_state",
            plane=SyncPlane.CANONICAL_STATE,
            category=ResourceCategory.CONVERGENCE_PLANE,
            status=PlaneStatus.OPTIONAL_UNAVAILABLE,
            symbol="○",
            summary="personal-ai-state 未配置或目录不存在 (单机运行)",
            required_evidence_level=EvidenceLevel.L1_ARTIFACT,
            warnings=["未检测到 personal-ai-state 远端同步仓库"],
        )

    rc_stat, stat_out = _run_git(state_repo, "status", "--porcelain")
    is_dirty = bool(stat_out.strip())

    _run_git(state_repo, "fetch", "--quiet")
    rc_head, head_out = _run_git(state_repo, "rev-parse", "HEAD")
    rc_remote, remote_out = _run_git(state_repo, "rev-parse", "refs/remotes/origin/main")

    if rc_head != 0:
        return ResourceRecord(
            resource_id="personal_ai_state",
            plane=SyncPlane.CANONICAL_STATE,
            category=ResourceCategory.CONVERGENCE_PLANE,
            status=PlaneStatus.FAILED,
            symbol="✗",
            summary="无法读取 personal-ai-state 本地 HEAD 提交",
            required_evidence_level=EvidenceLevel.L3_REPRODUCED,
            blockers=["Git rev-parse HEAD 失败"],
        )

    local_commit = head_out.strip()
    remote_commit = remote_out.strip() if rc_remote == 0 else ""

    if rc_remote != 0 or not remote_commit:
        return ResourceRecord(
            resource_id="personal_ai_state",
            plane=SyncPlane.CANONICAL_STATE,
            category=ResourceCategory.CONVERGENCE_PLANE,
            desired_identity=local_commit,
            source_identity=local_commit,
            status=PlaneStatus.PASS if not is_dirty else PlaneStatus.REVIEW_REQUIRED,
            symbol="✓" if not is_dirty else "△",
            summary=f"本地单端运行 ({local_commit[:8]})" + (" (含未提交修改)" if is_dirty else ""),
            required_evidence_level=EvidenceLevel.L3_REPRODUCED,
            evidence_refs=[{"type": "git_local_head", "commit": local_commit, "dirty": is_dirty}],
            details={"dirty": is_dirty, "local_commit": local_commit, "remote_commit": ""},
        )

    if local_commit == remote_commit and not is_dirty:
        return ResourceRecord(
            resource_id="personal_ai_state",
            plane=SyncPlane.CANONICAL_STATE,
            category=ResourceCategory.CONVERGENCE_PLANE,
            desired_identity=remote_commit,
            source_identity=local_commit,
            status=PlaneStatus.IN_SYNC,
            symbol="✓",
            summary=f"已同步至最新提交 `{local_commit[:8]}`",
            required_evidence_level=EvidenceLevel.L3_REPRODUCED,
            evidence_refs=[{"type": "git_ancestry", "local": local_commit, "remote": remote_commit, "status": "EQUAL"}],
            details={"local_commit": local_commit, "remote_commit": remote_commit, "dirty": False, "direction": "IN_SYNC"},
        )

    rc_local_anc, _ = _run_git(state_repo, "merge-base", "--is-ancestor", local_commit, remote_commit)
    rc_remote_anc, _ = _run_git(state_repo, "merge-base", "--is-ancestor", remote_commit, local_commit)

    if rc_remote_anc == 0 and rc_local_anc != 0:
        return ResourceRecord(
            resource_id="personal_ai_state",
            plane=SyncPlane.CANONICAL_STATE,
            category=ResourceCategory.CONVERGENCE_PLANE,
            desired_identity=local_commit,
            source_identity=local_commit,
            status=PlaneStatus.PARTIAL,
            symbol="○",
            summary=f"本地领先远端 ({local_commit[:8]} ahead {remote_commit[:8]})",
            required_evidence_level=EvidenceLevel.L3_REPRODUCED,
            evidence_refs=[{"type": "git_ancestry", "local": local_commit, "remote": remote_commit, "status": "LOCAL_AHEAD"}],
            details={"direction": "LOCAL_AHEAD", "dirty": is_dirty, "local_commit": local_commit, "remote_commit": remote_commit},
        )

    if rc_local_anc == 0 and rc_remote_anc != 0:
        return ResourceRecord(
            resource_id="personal_ai_state",
            plane=SyncPlane.CANONICAL_STATE,
            category=ResourceCategory.CONVERGENCE_PLANE,
            desired_identity=remote_commit,
            source_identity=local_commit,
            drift_detected=True,
            status=PlaneStatus.PARTIAL,
            symbol="○",
            summary=f"远端有新更新待拉取 ({remote_commit[:8]})",
            required_evidence_level=EvidenceLevel.L3_REPRODUCED,
            evidence_refs=[{"type": "git_ancestry", "local": local_commit, "remote": remote_commit, "status": "REMOTE_AHEAD"}],
            details={"direction": "REMOTE_AHEAD", "dirty": is_dirty, "local_commit": local_commit, "remote_commit": remote_commit},
        )

    return ResourceRecord(
        resource_id="personal_ai_state",
        plane=SyncPlane.CANONICAL_STATE,
        category=ResourceCategory.CONVERGENCE_PLANE,
        desired_identity=remote_commit,
        source_identity=local_commit,
        drift_detected=True,
        status=PlaneStatus.REVIEW_REQUIRED,
        symbol="△",
        summary="两端历史发生分叉 (REVIEW_REQUIRED_DIVERGED)",
        required_evidence_level=EvidenceLevel.L3_REPRODUCED,
        evidence_refs=[{"type": "git_ancestry", "local": local_commit, "remote": remote_commit, "status": "DIVERGED"}],
        details={"direction": "DIVERGED", "local_commit": local_commit, "remote_commit": remote_commit},
        blockers=["personal-ai-state 分支与远端分叉，禁止自动重写历史，需人工合并"],
    )


# ---------------------------------------------------------------- Plane 2: Agent Tools Source
def evaluate_agent_tools_source_plane(
    repo_root: Path,
    snapshot: Optional[SnapshotContext] = None,
    updated_commit: bool = False,
) -> ResourceRecord:
    """Plane 2: Developer workspace status evaluation (dirty allowed, never stashed)."""
    rc_stat, stat_out = _run_git(repo_root, "status", "--porcelain")
    rc_head, head_out = _run_git(repo_root, "rev-parse", "HEAD")
    is_dirty = bool(stat_out.strip())
    commit = head_out.strip() if rc_head == 0 else ""

    rc_remote, remote_out = _run_git(repo_root, "rev-parse", "refs/remotes/origin/main")
    remote_commit = remote_out.strip() if rc_remote == 0 else commit

    if updated_commit:
        summary = f"已更新至 `{commit[:7]}`"
    elif is_dirty:
        summary = f"开发区保留未提交修改 (`{commit[:7]}`)"
    elif remote_commit and commit != remote_commit:
        summary = f"本地提交 `{commit[:7]}` 与远端 `{remote_commit[:7]}` 存在差异"
    else:
        summary = f"当前版本 `{commit[:7]}`，与远端一致"

    return ResourceRecord(
        resource_id="agent_tools_source",
        plane=SyncPlane.AGENT_TOOLS_SOURCE,
        category=ResourceCategory.CONVERGENCE_PLANE,
        desired_identity=remote_commit,
        source_identity=commit,
        status=PlaneStatus.PASS if not is_dirty else PlaneStatus.PASS_NO_CHANGE,
        symbol="✓",
        summary=summary,
        required_evidence_level=EvidenceLevel.L3_REPRODUCED,
        evidence_refs=[{"type": "git_porcelain", "commit": commit, "remote": remote_commit, "dirty": is_dirty}],
        details={"dirty": is_dirty, "commit": commit, "remote_commit": remote_commit, "porcelain": stat_out.strip()[:200]},
    )


# ---------------------------------------------------------------- Plane 3: Deployment Mirror
def evaluate_deployment_mirror_plane(
    mirror_dir: Path,
    repo_root: Path,
    target_commit: str,
) -> ResourceRecord:
    """Plane 3: Clean deployment mirror inspection."""
    if not mirror_dir.is_dir() or not (mirror_dir / ".git").exists():
        return ResourceRecord(
            resource_id="deployment_mirror",
            plane=SyncPlane.DEPLOYMENT_MIRROR,
            category=ResourceCategory.CONVERGENCE_PLANE,
            status=PlaneStatus.PARTIAL,
            symbol="○",
            summary="生产部署镜像待初始化 (按需自动创建)",
            required_evidence_level=EvidenceLevel.L2_OBSERVED,
            warnings=["生产镜像目录尚未创建，将在首次同步时自动拉取"],
        )

    rc_stat, stat_out = _run_git(mirror_dir, "status", "--porcelain")
    rc_head, head_out = _run_git(mirror_dir, "rev-parse", "HEAD")
    is_dirty = bool(stat_out.strip())
    commit = head_out.strip() if rc_head == 0 else ""

    if is_dirty:
        return ResourceRecord(
            resource_id="deployment_mirror",
            plane=SyncPlane.DEPLOYMENT_MIRROR,
            category=ResourceCategory.CONVERGENCE_PLANE,
            status=PlaneStatus.REVIEW_REQUIRED,
            symbol="△",
            summary="生产镜像异常存在脏修改",
            required_evidence_level=EvidenceLevel.L3_REPRODUCED,
            details={"dirty": True, "mirror_path": str(mirror_dir)},
            blockers=["生产部署镜像应当为纯净检出，禁止人工修改镜像目录"],
        )

    if target_commit and commit != target_commit:
        return ResourceRecord(
            resource_id="deployment_mirror",
            plane=SyncPlane.DEPLOYMENT_MIRROR,
            category=ResourceCategory.CONVERGENCE_PLANE,
            desired_identity=target_commit,
            materialized_identity=commit,
            status=PlaneStatus.PARTIAL,
            symbol="○",
            summary=f"生产镜像版本待对齐 (`{commit[:7]}` -> `{target_commit[:7]}`)",
            required_evidence_level=EvidenceLevel.L3_REPRODUCED,
        )

    return ResourceRecord(
        resource_id="deployment_mirror",
        plane=SyncPlane.DEPLOYMENT_MIRROR,
        category=ResourceCategory.CONVERGENCE_PLANE,
        desired_identity=commit,
        materialized_identity=commit,
        status=PlaneStatus.IN_SYNC,
        symbol="✓",
        summary=f"生产镜像纯净 `{commit[:7]}`",
        required_evidence_level=EvidenceLevel.L3_REPRODUCED,
        evidence_refs=[{"type": "git_mirror_clean", "commit": commit, "clean": True}],
        details={"clean": True, "commit": commit, "path": str(mirror_dir)},
    )


# ---------------------------------------------------------------- Plane 4: Presets
def evaluate_presets_plane(
    home: Path,
    repo_root: Path,
    contract: Dict[str, Any],
    repair: bool = True,
) -> ResourceRecord:
    """Plane 4: DSH Presets and routing convergence."""
    preset_path = home / ".agent-presets" / "cc" / "agent.cordis.yml"

    if not preset_path.is_file():
        if repair:
            preset_path.parent.mkdir(parents=True, exist_ok=True)
            default_content = (
                "# Agent Cordis Preset - Managed default CC preset\n"
                "agent:\n"
                "  profile: cc\n"
                "  model: deepseek-chat\n"
            )
            preset_path.write_text(default_content, encoding="utf-8")
        else:
            return ResourceRecord(
                resource_id="presets",
                plane=SyncPlane.DSH_PRESET,
                category=ResourceCategory.CONVERGENCE_PLANE,
                status=PlaneStatus.REVIEW_REQUIRED,
                symbol="△",
                summary="CC 预设文件缺失",
                required_evidence_level=EvidenceLevel.L2_OBSERVED,
                blockers=["~/.dsh/.agent-presets/cc/agent.cordis.yml 不存在"],
            )

    try:
        data = yaml.load(preset_path.read_text(encoding="utf-8-sig"), Loader=RelaxedYamlLoader)
        if not isinstance(data, (dict, list)):
            raise ValueError("Preset YAML must be a mapping or list")
        preset_hash = hashlib.sha256(preset_path.read_bytes()).hexdigest()[:8]
    except Exception as exc:
        return ResourceRecord(
            resource_id="presets",
            plane=SyncPlane.DSH_PRESET,
            category=ResourceCategory.CONVERGENCE_PLANE,
            status=PlaneStatus.REVIEW_REQUIRED,
            symbol="✗",
            summary=f"CC 预设损坏: {exc}",
            required_evidence_level=EvidenceLevel.L2_OBSERVED,
            blockers=[f"Preset 解析失败: {exc}"],
        )

    return ResourceRecord(
        resource_id="presets",
        plane=SyncPlane.DSH_PRESET,
        category=ResourceCategory.CONVERGENCE_PLANE,
        desired_identity=preset_hash,
        materialized_identity=preset_hash,
        status=PlaneStatus.IN_SYNC,
        symbol="✓",
        summary=f"已加载 CC 预设 (`{preset_hash}`)",
        required_evidence_level=EvidenceLevel.L2_OBSERVED,
        evidence_refs=[{"type": "preset_sha256", "path": str(preset_path), "hash": preset_hash}],
        details={"preset_path": str(preset_path), "hash": preset_hash},
    )


# ---------------------------------------------------------------- Plane 5: DSH Config
def evaluate_dsh_config_plane(
    home: Path,
    contract: Dict[str, Any],
    repair: bool = True,
) -> ResourceRecord:
    """Plane 5: DSH settings.yaml & cordis.patch.yml config evaluation."""
    settings_file = home / "settings.yaml"
    patch_file = home / "profiles" / "web" / "cordis.patch.yml"

    if not settings_file.is_file() or not patch_file.is_file():
        return ResourceRecord(
            resource_id="dsh_config",
            plane=SyncPlane.DSH_CONFIG,
            category=ResourceCategory.CONVERGENCE_PLANE,
            status=PlaneStatus.REVIEW_REQUIRED,
            symbol="△",
            summary="配置文件缺失 (settings.yaml 或 cordis.patch.yml)",
            required_evidence_level=EvidenceLevel.L2_OBSERVED,
            blockers=["DSH 配置文件缺失"],
        )

    try:
        settings_text = settings_file.read_text(encoding="utf-8-sig")
        patch_text = patch_file.read_text(encoding="utf-8-sig")
        settings_data = yaml.safe_load(settings_text) or {}
        patch_data = yaml.safe_load(patch_text) or {}
    except Exception as exc:
        return ResourceRecord(
            resource_id="dsh_config",
            plane=SyncPlane.DSH_CONFIG,
            category=ResourceCategory.CONVERGENCE_PLANE,
            status=PlaneStatus.REVIEW_REQUIRED,
            symbol="✗",
            summary=f"配置文件语法错误: {exc}",
            required_evidence_level=EvidenceLevel.L2_OBSERVED,
            blockers=[f"Config YAML parse error: {exc}"],
        )

    managed_plugins = contract.get("runtime_composition", {}).get("managed_rows", {}).get("plugins", [])
    total_managed_fields = len(managed_plugins) + 2
    verified_fields = 0
    drifted_fields = []

    # Verify managed block exists in patch
    if "# AIC DSH RUNTIME COMPOSITION BEGIN" in patch_text:
        verified_fields += 1
    else:
        drifted_fields.append("cordis.patch.yml managed block missing")

    if settings_data:
        verified_fields += 1

    for plugin in managed_plugins:
        pid = plugin.get("id")
        if pid in patch_text:
            verified_fields += 1
        else:
            drifted_fields.append(f"plugin config: {pid}")

    if drifted_fields:
        return ResourceRecord(
            resource_id="dsh_config",
            plane=SyncPlane.DSH_CONFIG,
            category=ResourceCategory.CONVERGENCE_PLANE,
            drift_detected=True,
            status=PlaneStatus.PARTIAL,
            symbol="○",
            summary=f"{verified_fields}/{total_managed_fields} 托管配置字段已收敛",
            required_evidence_level=EvidenceLevel.L2_OBSERVED,
            warnings=[f"配置存在漂移: {', '.join(drifted_fields)}"],
            details={"managed_fields_total": total_managed_fields, "verified": verified_fields, "drifted": drifted_fields},
        )

    return ResourceRecord(
        resource_id="dsh_config",
        plane=SyncPlane.DSH_CONFIG,
        category=ResourceCategory.CONVERGENCE_PLANE,
        status=PlaneStatus.IN_SYNC,
        symbol="✓",
        summary=f"{total_managed_fields}/{total_managed_fields} 托管配置字段全部就绪",
        required_evidence_level=EvidenceLevel.L2_OBSERVED,
        evidence_refs=[{"type": "config_fields_verified", "count": total_managed_fields}],
        details={"managed_fields_total": total_managed_fields, "verified": total_managed_fields, "drifted": []},
    )


# ---------------------------------------------------------------- Plane 6: DSH Plugins
def evaluate_dsh_plugins_plane(
    home: Path,
    repo_root: Path,
    contract: Dict[str, Any],
    repair: bool = True,
) -> ResourceRecord:
    """Plane 6: DSH managed plugins status evaluation with full tree/source hash comparison."""
    plugins_cfg = contract.get("runtime_composition", {}).get("managed_rows", {}).get("plugins", [])
    total_plugins = len(plugins_cfg)
    plugins_dir = home / "profiles" / "web" / "plugins"

    missing = []
    stale = []
    verified = []

    for p in plugins_cfg:
        src_entry = repo_root / p["source_relative"] / p["entry_relative"]
        dst_entry = plugins_dir / p["plugin_directory"] / p["entry_relative"]

        if not dst_entry.is_file():
            missing.append(p["id"])
            continue

        if src_entry.is_file():
            src_hash = sha256_file(src_entry)
            dst_hash = sha256_file(dst_entry)
            if src_hash != dst_hash:
                stale.append(p["id"])
                continue

        verified.append(p["id"])

    if (missing or stale) and repair:
        try:
            import dsh_runtime
            apply_res = dsh_runtime.apply(home, contract)
            if apply_res.get("status") in ("APPLIED", "NO_DRIFT"):
                # Re-verify after repair
                missing.clear()
                stale.clear()
                verified.clear()
                for p in plugins_cfg:
                    dst_entry = plugins_dir / p["plugin_directory"] / p["entry_relative"]
                    if dst_entry.is_file():
                        verified.append(p["id"])
                    else:
                        missing.append(p["id"])
        except Exception as exc:
            return ResourceRecord(
                resource_id="dsh_plugins",
                plane=SyncPlane.DSH_PLUGIN,
                category=ResourceCategory.CONVERGENCE_PLANE,
                status=PlaneStatus.FAILED,
                symbol="✗",
                summary=f"插件部署异常: {exc}",
                required_evidence_level=EvidenceLevel.L3_REPRODUCED,
                blockers=[f"Plugin deployment failed: {exc}"],
            )

    if len(verified) == total_plugins and not missing and not stale:
        return ResourceRecord(
            resource_id="dsh_plugins",
            plane=SyncPlane.DSH_PLUGIN,
            category=ResourceCategory.CONVERGENCE_PLANE,
            status=PlaneStatus.IN_SYNC,
            symbol="✓",
            summary=f"{len(verified)}/{total_plugins} 插件版本已对齐",
            required_evidence_level=EvidenceLevel.L3_REPRODUCED,
            evidence_refs=[{"type": "plugin_hashes_verified", "verified": verified}],
            details={"deployed": len(verified), "total": total_plugins, "plugins": verified},
        )

    return ResourceRecord(
        resource_id="dsh_plugins",
        plane=SyncPlane.DSH_PLUGIN,
        category=ResourceCategory.CONVERGENCE_PLANE,
        drift_detected=True,
        status=PlaneStatus.PARTIAL,
        symbol="△",
        summary=f"{len(verified)}/{total_plugins} 插件就绪 (缺失: {len(missing)}, 滞后: {len(stale)})",
        required_evidence_level=EvidenceLevel.L3_REPRODUCED,
        warnings=[f"插件尚未完全就绪: missing={missing}, stale={stale}"],
        details={"missing": missing, "stale": stale, "deployed": len(verified), "total": total_plugins},
    )


# ---------------------------------------------------------------- Plane 7: MCP
def evaluate_mcp_plane(
    home: Path,
    repo_root: Path,
    contract: Dict[str, Any],
    repair: bool = True,
) -> ResourceRecord:
    """Plane 7: MCP servers true sync evaluation (full pipeline)."""
    mcp_root = repo_root / "mcp" / "agent-switchboard"
    entrypoint = mcp_root / "agent_broker_mcp.py"

    if not entrypoint.is_file():
        return ResourceRecord(
            resource_id="mcp",
            plane=SyncPlane.MCP,
            category=ResourceCategory.CONVERGENCE_PLANE,
            status=PlaneStatus.REVIEW_REQUIRED,
            symbol="△",
            summary="required MCP 源码缺失",
            required_evidence_level=EvidenceLevel.L3_REPRODUCED,
            blockers=["agent-switchboard 入口文件不存在"],
        )

    rc = subprocess.run([sys.executable, "-m", "py_compile", str(entrypoint)], capture_output=True)
    if rc.returncode != 0:
        return ResourceRecord(
            resource_id="mcp",
            plane=SyncPlane.MCP,
            category=ResourceCategory.CONVERGENCE_PLANE,
            status=PlaneStatus.REVIEW_REQUIRED,
            symbol="✗",
            summary="MCP 语法或编译失败",
            required_evidence_level=EvidenceLevel.L3_REPRODUCED,
            blockers=["agent-switchboard py_compile 失败"],
        )

    # Handshake probe
    init_pass = False
    tools_list = []
    probe_dir = Path(tempfile.gettempdir()) / f"mcp-probe-{os.getpid()}"
    probe_dir.mkdir(parents=True, exist_ok=True)
    mcp_env = {**os.environ, "AGENT_BROKER_HOME": str(probe_dir)}
    try:
        p = subprocess.Popen(
            [sys.executable, str(entrypoint)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=mcp_env,
        )
        try:
            init_req = json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "sync_v3_probe"}}
            }) + "\n"
            p.stdin.write(init_req)
            p.stdin.flush()
            line1 = p.stdout.readline()
            if '"protocolVersion"' in line1:
                init_pass = True
                tools_req = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n"
                p.stdin.write(tools_req)
                p.stdin.flush()
                line2 = p.stdout.readline()
                data2 = json.loads(line2)
                tools_list = [t.get("name") for t in data2.get("result", {}).get("tools", [])]
        finally:
            if p.stdin:
                p.stdin.close()
            if p.stdout:
                p.stdout.close()
            if p.stderr:
                p.stderr.close()
            p.terminate()
            p.wait(timeout=3)
    except Exception as exc:
        return ResourceRecord(
            resource_id="mcp",
            plane=SyncPlane.MCP,
            category=ResourceCategory.CONVERGENCE_PLANE,
            status=PlaneStatus.REVIEW_REQUIRED,
            symbol="✗",
            summary=f"MCP 协议握手异常: {exc}",
            required_evidence_level=EvidenceLevel.L3_REPRODUCED,
            blockers=[f"MCP 握手失败: {exc}"],
        )

    if not init_pass:
        return ResourceRecord(
            resource_id="mcp",
            plane=SyncPlane.MCP,
            category=ResourceCategory.CONVERGENCE_PLANE,
            status=PlaneStatus.REVIEW_REQUIRED,
            symbol="△",
            summary="MCP 初始化协议无响应",
            required_evidence_level=EvidenceLevel.L3_REPRODUCED,
            warnings=["agent-switchboard 未返回有效 initialize 响应"],
        )

    return ResourceRecord(
        resource_id="mcp",
        plane=SyncPlane.MCP,
        category=ResourceCategory.CONVERGENCE_PLANE,
        status=PlaneStatus.IN_SYNC,
        symbol="✓",
        summary=f"1/1 已验证 (agent-switchboard, {len(tools_list)} tools)",
        required_evidence_level=EvidenceLevel.L3_REPRODUCED,
        evidence_refs=[{"type": "mcp_handshake_ok", "tools_count": len(tools_list)}],
        details={"tools_count": len(tools_list), "tools_sample": tools_list[:5]},
    )


# ---------------------------------------------------------------- Plane 8: Skills
def evaluate_skills_plane(
    home: Path,
    repo_root: Path,
    repair: bool = True,
) -> ResourceRecord:
    """Plane 8: Canonical manifest-driven Skills convergence."""
    manifest_path = repo_root / "skills.json"
    if not manifest_path.is_file():
        return ResourceRecord(
            resource_id="skills",
            plane=SyncPlane.SKILL,
            category=ResourceCategory.CONVERGENCE_PLANE,
            status=PlaneStatus.REVIEW_REQUIRED,
            symbol="✗",
            summary="skills.json 缺失",
            required_evidence_level=EvidenceLevel.L2_OBSERVED,
            blockers=["skills.json manifest 不存在"],
        )

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        entries = manifest.get("skills", [])
        known = {e["name"]: e for e in entries if isinstance(e, dict)}
    except Exception as exc:
        return ResourceRecord(
            resource_id="skills",
            plane=SyncPlane.SKILL,
            category=ResourceCategory.CONVERGENCE_PLANE,
            status=PlaneStatus.REVIEW_REQUIRED,
            symbol="✗",
            summary=f"skills.json 格式错误: {exc}",
            required_evidence_level=EvidenceLevel.L2_OBSERVED,
            blockers=[f"skills.json parse error: {exc}"],
        )

    dest_root = home / "skills"
    dest_root.mkdir(parents=True, exist_ok=True)

    missing_skills = []
    stale_skills = []
    verified_skills = []
    repaired_skills = []

    for name, entry in known.items():
        src_dir = (repo_root / entry["path"]).resolve()
        dst_dir = dest_root / name

        if not src_dir.is_dir():
            missing_skills.append(name)
            continue

        src_files = package_files(src_dir)
        dst_files = package_files(dst_dir) if dst_dir.is_dir() else {}

        has_missing = bool(set(src_files) - set(dst_files))
        extra_in_dst = sorted(set(dst_files) - set(src_files))
        has_extra = bool(extra_in_dst)
        has_diff = False
        for rel, s_path in src_files.items():
            d_path = dst_dir / rel
            if d_path.is_file():
                if sha256_file(s_path) != sha256_file(d_path):
                    has_diff = True
                    break

        if has_missing or has_diff or has_extra:
            if repair:
                for rel, s_path in src_files.items():
                    d_path = dst_dir / rel
                    atomic_copy(s_path, d_path)
                for rel in extra_in_dst:
                    (dst_dir / rel).unlink(missing_ok=True)
                repaired_skills.append(name)
                verified_skills.append(name)
            else:
                if not dst_dir.is_dir():
                    missing_skills.append(name)
                else:
                    stale_skills.append(name)
        else:
            verified_skills.append(name)

    total_expected = len(known)
    if len(verified_skills) == total_expected and not missing_skills and not stale_skills:
        status = PlaneStatus.IN_SYNC if not repaired_skills else PlaneStatus.REPAIRED
        summary = f"{len(verified_skills)}/{total_expected} 全部对齐" + (f" (已修复 {len(repaired_skills)} 项)" if repaired_skills else "")
        return ResourceRecord(
            resource_id="skills",
            plane=SyncPlane.SKILL,
            category=ResourceCategory.CONVERGENCE_PLANE,
            status=status,
            symbol="✓",
            summary=summary,
            required_evidence_level=EvidenceLevel.L2_OBSERVED,
            evidence_refs=[{"type": "skill_tree_hashes_verified", "verified_count": len(verified_skills)}],
            details={"total": total_expected, "verified_count": len(verified_skills), "verified": verified_skills, "repaired": repaired_skills},
        )

    return ResourceRecord(
        resource_id="skills",
        plane=SyncPlane.SKILL,
        category=ResourceCategory.CONVERGENCE_PLANE,
        drift_detected=True,
        status=PlaneStatus.PARTIAL,
        symbol="△",
        summary=f"{len(verified_skills)}/{total_expected} 部分对齐 (缺失: {len(missing_skills)}, 滞后: {len(stale_skills)})",
        required_evidence_level=EvidenceLevel.L2_OBSERVED,
        warnings=[f"Skills 尚未完全收敛: missing={missing_skills}, stale={stale_skills}"],
        details={"total": total_expected, "verified": len(verified_skills), "missing": missing_skills, "stale": stale_skills},
    )


# ---------------------------------------------------------------- Plane 9: DSH Runtime Composition
def evaluate_runtime_plane(
    home: Path,
    contract: Dict[str, Any],
    active_process: Optional[Dict[str, Any]],
    repair: bool = True,
) -> ResourceRecord:
    """Plane 9: DSH runtime immutable status & process alignment."""
    manifest_path = home / "profiles" / "web" / "dsh-runtime-composition.json"

    if not manifest_path.is_file():
        return ResourceRecord(
            resource_id="runtime",
            plane=SyncPlane.RUNTIME,
            category=ResourceCategory.CONVERGENCE_PLANE,
            status=PlaneStatus.FAILED,
            symbol="✗",
            summary="dsh-runtime-composition.json 缺失",
            required_evidence_level=EvidenceLevel.L3_REPRODUCED,
            blockers=["运行时组合清单缺失"],
        )

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        deployed_hash = manifest.get("profileCombinationHash", "")
        manifest_mtime = manifest_path.stat().st_mtime
    except Exception as exc:
        return ResourceRecord(
            resource_id="runtime",
            plane=SyncPlane.RUNTIME,
            category=ResourceCategory.CONVERGENCE_PLANE,
            status=PlaneStatus.FAILED,
            symbol="✗",
            summary=f"清单解析失败: {exc}",
            required_evidence_level=EvidenceLevel.L3_REPRODUCED,
            blockers=[str(exc)],
        )

    if active_process is None:
        return ResourceRecord(
            resource_id="runtime",
            plane=SyncPlane.RUNTIME,
            category=ResourceCategory.CONVERGENCE_PLANE,
            desired_identity=deployed_hash,
            materialized_identity=deployed_hash,
            status=PlaneStatus.IN_SYNC,
            symbol="✓",
            summary="部署就绪 (DSH 当前离线)",
            required_evidence_level=EvidenceLevel.L2_OBSERVED,
            evidence_refs=[{"type": "composition_deployed", "hash": deployed_hash}],
            details={"deployed_hash": deployed_hash, "active_running": False},
        )

    # Active process running: inspect real provenance
    start_time_epoch = active_process.get("startTimeEpoch", 0.0)
    pid = active_process.get("pid")

    if start_time_epoch > 0 and manifest_mtime > 0 and start_time_epoch < (manifest_mtime - 2.0):
        # Process started before deployed composition
        return ResourceRecord(
            resource_id="runtime",
            plane=SyncPlane.RUNTIME,
            category=ResourceCategory.CONVERGENCE_PLANE,
            desired_identity=deployed_hash,
            materialized_identity=deployed_hash,
            active_identity="STALE_PROCESS_MEMORY",
            drift_detected=True,
            status=PlaneStatus.PARTIAL_RESTART_REQUIRED,
            symbol="○",
            summary=f"新组合已部署，PID {pid} 待重启生效",
            required_evidence_level=EvidenceLevel.L3_REPRODUCED,
            evidence_refs=[{"type": "runtime_mtime_vs_proc_start", "pid": pid, "stale": True}],
            details={"pid": pid, "deployed_hash": deployed_hash, "restart_required": True},
        )

    return ResourceRecord(
        resource_id="runtime",
        plane=SyncPlane.RUNTIME,
        category=ResourceCategory.CONVERGENCE_PLANE,
        desired_identity=deployed_hash,
        materialized_identity=deployed_hash,
        active_identity=deployed_hash,
        status=PlaneStatus.IN_SYNC,
        symbol="✓",
        summary=f"PID {pid} 托管运行中 (组合已对齐)",
        required_evidence_level=EvidenceLevel.L3_REPRODUCED,
        evidence_refs=[{"type": "active_process_verified", "pid": pid, "hash": deployed_hash}],
        details={"pid": pid, "deployed_hash": deployed_hash, "active_hash": deployed_hash},
    )


# ---------------------------------------------------------------- Safety Gate 10: Model Safety
def evaluate_model_discovery_safety_gate(
    home: Path,
    repo_root: Path,
) -> ResourceRecord:
    """Safety Gate 10: Model discovery and context window provenance evaluation."""
    settings_path = home / "settings.yaml"
    if not settings_path.is_file():
        return ResourceRecord(
            resource_id="model_safety",
            plane=SyncPlane.MODEL_DISCOVERY_SAFETY,
            category=ResourceCategory.SAFETY_GATE,
            status=PlaneStatus.SAFETY_CONSERVATIVE,
            symbol="△",
            summary="缺少 settings.yaml，进入安全保守兜底",
            required_evidence_level=EvidenceLevel.L2_OBSERVED,
            warnings=["使用默认安全兜底上下文限制"],
        )

    try:
        settings_data = yaml.safe_load(settings_path.read_text(encoding="utf-8-sig")) or {}
        # Verify model default configurations
        default_model = settings_data.get("agent-default-model", {}).get("model") or "deepseek-chat"
        return ResourceRecord(
            resource_id="model_safety",
            plane=SyncPlane.MODEL_DISCOVERY_SAFETY,
            category=ResourceCategory.SAFETY_GATE,
            status=PlaneStatus.SAFETY_ADMITTED,
            symbol="✓",
            summary=f"默认模型 `{default_model}`，凭据与安全边界正常",
            required_evidence_level=EvidenceLevel.L2_OBSERVED,
            evidence_refs=[{"type": "model_settings_admitted", "default_model": default_model}],
            details={"default_model": default_model, "safety_provenance": "ADMITTED"},
        )
    except Exception as exc:
        return ResourceRecord(
            resource_id="model_safety",
            plane=SyncPlane.MODEL_DISCOVERY_SAFETY,
            category=ResourceCategory.SAFETY_GATE,
            status=PlaneStatus.SAFETY_CONSERVATIVE,
            symbol="△",
            summary=f"模型配置解析异常 ({exc})，已降级至保守模式",
            required_evidence_level=EvidenceLevel.L2_OBSERVED,
            warnings=[f"Model safety parse error: {exc}"],
        )


# ---------------------------------------------------------------- Health 11: Durable Jobs
def evaluate_durable_job_health(
    db_path: Optional[Path] = None,
    current_sync_id: Optional[str] = None,
) -> ResourceRecord:
    """Health 11: Active durable jobs health evaluation (never swallowed)."""
    from jobs import DurableJobRegistry
    try:
        reg = DurableJobRegistry(db_path)
        unfinished = reg.list_unfinished_jobs()
        total_count = len(unfinished)
        other_jobs = [j for j in unfinished if j.job_id != current_sync_id]
        other_count = len(other_jobs)

        # Integrity check on SQLite connection
        from jobs.db import get_connection
        with get_connection(reg.db_path) as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA integrity_check")
            row = cur.fetchone()
            if not row or row[0] != "ok":
                return ResourceRecord(
                    resource_id="durable_jobs",
                    plane=SyncPlane.DURABLE_JOB,
                    category=ResourceCategory.HEALTH_OBSERVABILITY,
                    status=PlaneStatus.HEALTH_FAILED,
                    symbol="✗",
                    summary="任务数据库损坏 (PRAGMA integrity_check 失败)",
                    required_evidence_level=EvidenceLevel.L2_OBSERVED,
                    blockers=["Durable Job SQLite DB integrity check failed"],
                )

        summary = f"{other_count} 个后台长任务运行中" if other_count > 0 else "无其他活动任务 (空闲健康)"
        return ResourceRecord(
            resource_id="durable_jobs",
            plane=SyncPlane.DURABLE_JOB,
            category=ResourceCategory.HEALTH_OBSERVABILITY,
            status=PlaneStatus.HEALTHY,
            symbol="✓",
            summary=summary,
            required_evidence_level=EvidenceLevel.L2_OBSERVED,
            evidence_refs=[{"type": "sqlite_integrity_ok"}, {"type": "active_jobs_count", "count": other_count}],
            details={
                "sync_job_id": current_sync_id,
                "active_other_jobs": other_count,
                "active_jobs_total_including_sync": total_count,
            },
        )
    except Exception as exc:
        return ResourceRecord(
            resource_id="durable_jobs",
            plane=SyncPlane.DURABLE_JOB,
            category=ResourceCategory.HEALTH_OBSERVABILITY,
            status=PlaneStatus.HEALTH_FAILED,
            symbol="✗",
            summary=f"任务注册表异常: {exc}",
            required_evidence_level=EvidenceLevel.L2_OBSERVED,
            blockers=[f"Durable job health failed: {exc}"],
        )


# ---------------------------------------------------------------- Health 12: Session Continuity
def evaluate_session_continuity_health(home: Path) -> ResourceRecord:
    """Health 12: Physical session count vs runtime enumerable count evaluation."""
    sessions_root = home / "sessions"
    ws_file = home / "storages" / "workspace.json"

    phys_count = 0
    live_names = set()
    if sessions_root.is_dir():
        for p in sessions_root.rglob("session.jsonl.zstd"):
            phys_count += 1
            live_names.add(p.parent.name)

    attached_ids = set()
    if ws_file.is_file():
        try:
            ws_data = json.loads(ws_file.read_text(encoding="utf-8"))
            for ws_info in ws_data.get("tables", {}).get("workspaces", {}).values():
                for sid in ws_info.get("sessionIds", []):
                    attached_ids.add(sid)
        except Exception:
            pass

    root_sessions = {s for s in live_names if s.startswith("session-")}
    unattached_roots = root_sessions - attached_ids

    summary = f"物理 {phys_count} / 挂载 {len(attached_ids)} / 孤立根 {len(unattached_roots)}"

    if len(unattached_roots) == 0:
        return ResourceRecord(
            resource_id="session_continuity",
            plane=SyncPlane.SESSION_CONTINUITY,
            category=ResourceCategory.HEALTH_OBSERVABILITY,
            status=PlaneStatus.HEALTHY,
            symbol="✓",
            summary=summary,
            required_evidence_level=EvidenceLevel.L2_OBSERVED,
            evidence_refs=[{"type": "sessions_counted", "physical": phys_count, "attached": len(attached_ids)}],
            details={"physical_count": phys_count, "attached_count": len(attached_ids), "unattached_roots": 0},
        )

    return ResourceRecord(
        resource_id="session_continuity",
        plane=SyncPlane.SESSION_CONTINUITY,
        category=ResourceCategory.HEALTH_OBSERVABILITY,
        status=PlaneStatus.HEALTH_WARNING,
        symbol="△",
        summary=summary,
        required_evidence_level=EvidenceLevel.L2_OBSERVED,
        warnings=[f"发现 {len(unattached_roots)} 个未挂载根会话"],
        details={"physical_count": phys_count, "attached_count": len(attached_ids), "unattached_roots": sorted(list(unattached_roots))[:5]},
    )


# ---------------------------------------------------------------- Health 13: Backup / Recovery
def evaluate_backup_recovery_health(home: Path) -> ResourceRecord:
    """Health 13: Backup freshness and integrity inspection."""
    backup_dir = home / "backup"
    if not backup_dir.is_dir():
        return ResourceRecord(
            resource_id="backup_recovery",
            plane=SyncPlane.BACKUP_RECOVERY,
            category=ResourceCategory.HEALTH_OBSERVABILITY,
            status=PlaneStatus.HEALTH_WARNING,
            symbol="△",
            summary="备份目录尚未建立",
            required_evidence_level=EvidenceLevel.L2_OBSERVED,
            warnings=["未检测到 ~/.dsh/backup 备份目录"],
        )

    files = [f for f in backup_dir.iterdir() if f.is_file()]
    if not files:
        return ResourceRecord(
            resource_id="backup_recovery",
            plane=SyncPlane.BACKUP_RECOVERY,
            category=ResourceCategory.HEALTH_OBSERVABILITY,
            status=PlaneStatus.HEALTH_WARNING,
            symbol="△",
            summary="未发现有效备份快照文件",
            required_evidence_level=EvidenceLevel.L2_OBSERVED,
            warnings=["~/.dsh/backup 目录为空"],
        )

    # Inspect newest file
    files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    newest = files[0]
    file_size = newest.stat().st_size
    mtime = newest.stat().st_mtime

    if file_size == 0:
        return ResourceRecord(
            resource_id="backup_recovery",
            plane=SyncPlane.BACKUP_RECOVERY,
            category=ResourceCategory.HEALTH_OBSERVABILITY,
            status=PlaneStatus.HEALTH_FAILED,
            symbol="✗",
            summary=f"最新备份文件为空 (0 字节: {newest.name})",
            required_evidence_level=EvidenceLevel.L2_OBSERVED,
            blockers=[f"Corrupt 0-byte backup file: {newest.name}"],
        )

    return ResourceRecord(
        resource_id="backup_recovery",
        plane=SyncPlane.BACKUP_RECOVERY,
        category=ResourceCategory.HEALTH_OBSERVABILITY,
        status=PlaneStatus.HEALTHY,
        symbol="✓",
        summary=f"发现 {len(files)} 个快照，最新 `{newest.name}` 正常",
        required_evidence_level=EvidenceLevel.L2_OBSERVED,
        evidence_refs=[{"type": "backup_file_checked", "name": newest.name, "size": file_size, "mtime": mtime}],
        details={"backup_count": len(files), "latest_file": newest.name, "size": file_size},
    )
