"""engine.py — Core Personal AI Sync V3 Convergence Engine."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "aic"))
sys.path.insert(0, str(ROOT / "scripts" / "jobs"))

import aic
from jobs import DurableJobRegistry, LeaseDeniedError, JobState
from .models import (
    EvidenceLevel,
    OverallStatus,
    PlaneStatus,
    ResourceCategory,
    ResourceRecord,
    SnapshotContext,
    SyncPlane,
    SyncReceipt,
)
from .planes import (
    _run_git,
    evaluate_agent_tools_source_plane,
    evaluate_backup_recovery_health,
    evaluate_canonical_state_plane,
    evaluate_deployment_mirror_plane,
    evaluate_dsh_config_plane,
    evaluate_dsh_plugins_plane,
    evaluate_durable_job_health,
    evaluate_mcp_plane,
    evaluate_model_discovery_safety_gate,
    evaluate_presets_plane,
    evaluate_runtime_plane,
    evaluate_session_continuity_health,
    evaluate_skills_plane,
)
from .receipt import render_human_receipt


def _find_live_dsh_process() -> Optional[dict]:
    """Query Win32_Process for the live running DSH Web host process with start time."""
    if os.name != "nt":
        return None
    try:
        ps_cmd = (
            'Get-CimInstance Win32_Process | '
            'Where-Object { $_.CommandLine -like "*base-dsh*" -and $_.CommandLine -like "*web*" } | '
            'Select-Object ProcessId, CommandLine, CreationDate | ConvertTo-Json'
        )
        p = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=20,
            errors="replace",
        )
        if p.returncode != 0 or not p.stdout.strip():
            return None
        data = json.loads(p.stdout)
        if isinstance(data, list):
            data = next((item for item in data if "bin.js web" in item.get("CommandLine", "")), data[0])

        cdate_str = str(data.get("CreationDate", ""))
        epoch_ms = 0
        if "/Date(" in cdate_str:
            import re
            m = re.search(r"/Date\((\d+)\)/", cdate_str)
            if m:
                epoch_ms = int(m.group(1))

        return {
            "pid": int(data.get("ProcessId")),
            "commandLine": str(data.get("CommandLine", "")),
            "startTimeEpoch": epoch_ms / 1000.0 if epoch_ms else 0.0,
        }
    except Exception:
        return None


class SyncEngine:
    """The central convergence engine coordinating Convergence Planes, Safety Gates, and Health Observability."""

    def __init__(
        self,
        home: Optional[Path] = None,
        repo_root: Optional[Path] = None,
        state_repo: Optional[Path] = None,
        db_path: Optional[Path] = None,
    ) -> None:
        self.home = home or Path.home() / ".dsh"
        self.repo_root = repo_root or ROOT
        self.state_repo = state_repo or (Path.home() / "personal-ai-state")
        self.mirror_dir = self.home / ".deployment-mirror" / "agent-tools"
        self.db_path = db_path
        self.registry = DurableJobRegistry(self.db_path)

    def run(
        self,
        *,
        request_restart: bool = False,
        check_only: bool = False,
    ) -> Tuple[SyncReceipt, str]:
        """Execute synchronization across all planes.

        Returns:
            (SyncReceipt, human_readable_markdown)
        """
        sync_id = f"sync-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

        # 1. Acquire single-writer sync job lease
        lease_acquired = False
        try:
            self.registry.create_job(
                job_id=sync_id,
                job_type="personal_ai_sync",
                authorized_root=str(self.home),
                created_by="sync_v3",
            )
            self.registry.start_attempt(
                job_id=sync_id,
                writer_id=f"sync_worker_{os.getpid()}",
                worker_type="sync_engine",
                worker_identity={"pid": os.getpid(), "host": "local"},
                ttl_seconds=120.0,
            )
            lease_acquired = True
        except LeaseDeniedError:
            receipt = SyncReceipt(
                sync_id=sync_id,
                timestamp=now_iso,
                overall=OverallStatus.FAILED,
                issues_encountered=["另一个同步进程当前正在运行 (SYNC_ALREADY_RUNNING)"],
                action_required_from_user="等待正在运行的同步操作完成，禁止并发运行两个同步写入者。",
                metadata={"blockers": ["SYNC_ALREADY_RUNNING"]},
            )
            return receipt, render_human_receipt(receipt)
        except Exception:
            pass

        try:
            changes_applied = []
            issues = []
            tradeoffs = []
            warnings = []
            blockers = []
            restart_required = False
            restart_reason = "NONE"

            # 2. Remote Fetch & Snapshot Context Freeze
            _run_git(self.repo_root, "fetch", "--quiet")
            rc_rem, rem_out = _run_git(self.repo_root, "rev-parse", "refs/remotes/origin/main")
            if rc_rem != 0:
                rc_rem, rem_out = _run_git(self.repo_root, "rev-parse", "HEAD")
            accepted_remote_commit = rem_out.strip() if rc_rem == 0 else "UNKNOWN"

            if self.state_repo.is_dir() and (self.state_repo / ".git").exists():
                _run_git(self.state_repo, "fetch", "--quiet")
                _, st_rem_out = _run_git(self.state_repo, "rev-parse", "refs/remotes/origin/main")
                state_remote_commit = st_rem_out.strip()
            else:
                state_remote_commit = ""

            snapshot = SnapshotContext(
                sync_id=sync_id,
                snapshot_id=f"snap-{accepted_remote_commit[:8]}",
                remote_fetch_at=now_iso,
                accepted_remote_commit=accepted_remote_commit,
                personal_ai_state_commit=state_remote_commit,
                started_at=now_iso,
            )

            # Contract from AIC
            contract = aic.adapter_contract()

            # 3. CONVERGENCE PLANES EVALUATION & REPAIR
            # Plane 1: Personal AI State
            p1_res = evaluate_canonical_state_plane(self.state_repo, snapshot)
            if not check_only and p1_res.details.get("direction") == "REMOTE_AHEAD":
                if not p1_res.details.get("dirty"):
                    subprocess.run(["git", "-C", str(self.state_repo), "pull", "--ff-only"], capture_output=True)
                    changes_applied.append(f"personal-ai-state 已快进拉取最新远端提交 ({p1_res.details.get('remote_commit')[:8]})")
                    p1_res = evaluate_canonical_state_plane(self.state_repo, snapshot)
            elif not check_only and p1_res.details.get("direction") == "LOCAL_AHEAD":
                if not p1_res.details.get("dirty"):
                    subprocess.run(["git", "-C", str(self.state_repo), "push"], capture_output=True)
                    changes_applied.append(f"personal-ai-state 本地提交已同步推送到远端 ({p1_res.details.get('local_commit')[:8]})")
                    p1_res = evaluate_canonical_state_plane(self.state_repo, snapshot)
            elif p1_res.details.get("direction") == "DIVERGED":
                issues.append("personal-ai-state 与远端存在分叉 (REVIEW_REQUIRED_DIVERGED)")
                tradeoffs.append({
                    "title": "保留分叉状态等待人工确认",
                    "action": "未自动执行 git merge 或 rebase",
                    "reason": "canonical state 分叉涉及核心数据所有权，禁止机器自动覆盖或合流。",
                })

            # Plane 2: Agent Tools Source
            p2_res = evaluate_agent_tools_source_plane(self.repo_root, snapshot)
            is_dev_dirty = p2_res.details.get("dirty", False)
            if is_dev_dirty:
                tradeoffs.append({
                    "title": "保留本地开发区修改",
                    "action": "未执行 git stash / reset / clean",
                    "reason": "开发工作区属于用户，生产部署改从独立 clean deployment mirror 构建，本地修改不阻塞生产，亦不污染生产。",
                })

            # Plane 3: Deployment Mirror
            if not check_only:
                try:
                    import dsh_lifecycle
                    dsh_lifecycle.ensure_deployment_mirror(self.home, self.repo_root, accepted_remote_commit)
                except Exception as exc:
                    warnings.append(f"生产部署镜像刷新异常: {exc}")

            p3_res = evaluate_deployment_mirror_plane(self.mirror_dir, self.repo_root, accepted_remote_commit)

            # Plane 4: Presets
            p4_res = evaluate_presets_plane(self.home, self.repo_root, contract, repair=not check_only)

            # Plane 5: DSH Config
            p5_res = evaluate_dsh_config_plane(self.home, contract, repair=not check_only)

            # Plane 6: DSH Plugins
            p6_res = evaluate_dsh_plugins_plane(self.home, self.repo_root, contract, repair=not check_only)

            # Plane 7: MCP
            p7_res = evaluate_mcp_plane(self.home, self.repo_root, contract, repair=not check_only)

            # Plane 8: Skills
            p8_res = evaluate_skills_plane(self.home, self.repo_root, repair=not check_only)
            if p8_res.status == PlaneStatus.REPAIRED:
                changes_applied.append(f"Skills 已收敛同步 ({p8_res.summary})")

            # Plane 9: DSH Runtime Composition
            active_proc = _find_live_dsh_process()
            p9_res = evaluate_runtime_plane(self.home, contract, active_proc, repair=not check_only)

            import dsh_runtime
            insp = dsh_runtime.inspect(self.home, contract)
            if insp["status"] == "DRIFT" and not check_only:
                try:
                    res_apply = dsh_runtime.apply(self.home, contract)
                    if res_apply.get("status") in ("APPLIED", "NO_DRIFT"):
                        changes_applied.append(f"DSH 托管运行时已部署新版本 (composition={res_apply.get('profileCombinationHash')[:8]})")
                        restart_required = True
                        restart_reason = "RUNTIME_COMPOSITION_UPDATED"
                        p9_res.summary = "新版本已部署，待重启生效"
                        p9_res.symbol = "○"
                        p9_res.status = PlaneStatus.PARTIAL_RESTART_REQUIRED
                except Exception as exc:
                    issues.append(f"DSH 运行时部署回滚: {exc}")

            # 4. SAFETY GATES
            # Gate 10: Model Discovery / Safety
            g10_res = evaluate_model_discovery_safety_gate(self.home, self.repo_root)

            # 5. HEALTH OBSERVABILITY
            # Health 11: Durable Jobs
            h11_res = evaluate_durable_job_health(self.db_path, current_sync_id=sync_id)

            # Health 12: Session Continuity
            h12_res = evaluate_session_continuity_health(self.home)

            # Health 13: Backup / Recovery
            h13_res = evaluate_backup_recovery_health(self.home, state_repo=self.state_repo)

            # Collect Resources Map
            resources_map: Dict[str, ResourceRecord] = {
                p1_res.plane.value: p1_res,
                p2_res.plane.value: p2_res,
                p3_res.plane.value: p3_res,
                p4_res.plane.value: p4_res,
                p5_res.plane.value: p5_res,
                p6_res.plane.value: p6_res,
                p7_res.plane.value: p7_res,
                p8_res.plane.value: p8_res,
                p9_res.plane.value: p9_res,
                g10_res.plane.value: g10_res,
                h11_res.plane.value: h11_res,
                h12_res.plane.value: h12_res,
                h13_res.plane.value: h13_res,
            }

            for r in resources_map.values():
                warnings.extend(r.warnings)
                blockers.extend(r.blockers)

            # Controlled restart if requested
            if p9_res.status == PlaneStatus.PARTIAL_RESTART_REQUIRED:
                restart_required = True
                restart_reason = "RUNTIME_COMPOSITION_UPDATED"

            if restart_required:
                active_jobs_count = h11_res.details.get("active_other_jobs", 0)
                if request_restart:
                    if active_jobs_count > 0:
                        tradeoffs.append({
                            "title": "延迟 DSH 重启以保护活动任务",
                            "action": "暂缓重启 (RESTART_DEFERRED_ACTIVE_JOBS)",
                            "reason": f"检测到底层有 {active_jobs_count} 个运行中 Durable Job，禁止为了更新切断后台长任务。",
                        })
                        p9_res.summary = f"已部署，有 {active_jobs_count} 个任务运行中，重启已延迟"
                    else:
                        try:
                            subprocess.run(
                                ["powershell.exe", "-NoProfile", "-File", str(ROOT / "scripts" / "aic" / "dsh_desktop_restart.ps1")],
                                capture_output=True,
                                timeout=60,
                            )
                            changes_applied.append("DSH 已完成托管重启并加载新组合")
                            restart_required = False
                            p9_res.summary = "托管运行中 (新组合已激活)"
                            p9_res.symbol = "✓"
                            p9_res.status = PlaneStatus.PASS
                        except Exception as exc:
                            issues.append(f"DSH 重启失败: {exc}")
                else:
                    tradeoffs.append({
                        "title": "默认非破坏性同步",
                        "action": "已部署新状态，未强制重启 DSH",
                        "reason": "常规同步保持静默，更新已安全落盘，下次正常启动 DSH 时自动加载，不打断当前工作流。",
                    })

            # 6. Final Adjudication
            convergence_resources = [r for r in resources_map.values() if r.category == ResourceCategory.CONVERGENCE_PLANE]
            safety_resources = [r for r in resources_map.values() if r.category == ResourceCategory.SAFETY_GATE]
            health_resources = [r for r in resources_map.values() if r.category == ResourceCategory.HEALTH_OBSERVABILITY]

            # Convergence Status
            if any(r.status == PlaneStatus.FAILED for r in convergence_resources):
                convergence_status = "FAILED"
            elif any(r.status == PlaneStatus.REVIEW_REQUIRED for r in convergence_resources):
                convergence_status = "REVIEW_REQUIRED"
            elif any(r.status == PlaneStatus.PARTIAL_RESTART_REQUIRED for r in convergence_resources):
                convergence_status = "PARTIAL_RESTART_REQUIRED"
            elif any(r.status == PlaneStatus.PARTIAL for r in convergence_resources):
                convergence_status = "PARTIAL"
            elif changes_applied:
                convergence_status = "REPAIRED"
            else:
                convergence_status = "IN_SYNC"

            # Safety Status
            if any(r.status == PlaneStatus.SAFETY_BLOCKED for r in safety_resources):
                safety_status = "BLOCKED"
            elif any(r.status == PlaneStatus.SAFETY_CONSERVATIVE for r in safety_resources):
                safety_status = "CONSERVATIVE"
            else:
                safety_status = "ADMITTED"

            # Health Status
            if any(r.status == PlaneStatus.HEALTH_FAILED for r in health_resources):
                health_status = "FAILED"
            elif any(r.status == PlaneStatus.HEALTH_WARNING for r in health_resources):
                health_status = "WARNING"
            else:
                health_status = "HEALTHY"

            # Overall Status Synthesis
            if convergence_status == "FAILED" or safety_status == "BLOCKED":
                overall = OverallStatus.FAILED
            elif convergence_status == "REVIEW_REQUIRED":
                overall = OverallStatus.REVIEW_REQUIRED
            elif convergence_status == "PARTIAL_RESTART_REQUIRED":
                overall = OverallStatus.PARTIAL_RESTART_REQUIRED
            elif convergence_status == "PARTIAL":
                overall = OverallStatus.PARTIAL
            elif not changes_applied:
                overall = OverallStatus.PASS_NO_CHANGE
            else:
                overall = OverallStatus.PASS

            action_required = "无需你额外操作。"
            if restart_required:
                action_required = "下次正常启动 DSH 后新配置自动生效；如需立即生效可回复“同步并重启”。"
            elif overall == OverallStatus.REVIEW_REQUIRED:
                action_required = "请查看上方遇到的问题进行人工核对。"

            receipt = SyncReceipt(
                sync_id=sync_id,
                timestamp=now_iso,
                overall=overall,
                snapshot=snapshot,
                convergence_status=convergence_status,
                safety_status=safety_status,
                health_status=health_status,
                planes=resources_map,
                changes_applied=changes_applied,
                issues_encountered=issues,
                tradeoff_decisions=tradeoffs,
                action_required_from_user=action_required,
                metadata={
                    "sync_job_id": sync_id,
                    "active_other_jobs": h11_res.details.get("active_other_jobs", 0),
                    "active_jobs_total_including_sync": h11_res.details.get("active_jobs_total_including_sync", 1),
                    "developer_workspace_dirty": is_dev_dirty,
                    "restart_required": restart_required,
                    "restart_reason": restart_reason,
                    "warnings": warnings,
                    "blockers": blockers,
                },
            )

            # Record in Durable Job Registry
            if lease_acquired:
                self.registry.record_result_envelope(sync_id, f"{sync_id}_att_1", "in_memory_receipt")
                self.registry.final_adjudicate(
                    sync_id,
                    f"{sync_id}_att_1",
                    "sync_v3_truth_engine",
                    {"status": overall.value, "convergence": convergence_status},
                    [],
                )

            return receipt, render_human_receipt(receipt)

        finally:
            if lease_acquired:
                try:
                    self.registry.lease_mgr.revoke_or_expire_lease(sync_id, reason="sync_finished")
                except Exception:
                    pass


def run_sync(*, request_restart: bool = False, check_only: bool = False) -> Tuple[SyncReceipt, str]:
    """Top-level invocation helper."""
    engine = SyncEngine()
    return engine.run(request_restart=request_restart, check_only=check_only)
