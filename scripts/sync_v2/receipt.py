"""receipt.py — Human-readable and Machine-readable receipt formatters for Personal AI Sync V3."""
from __future__ import annotations

from typing import Any, Dict
from .models import OverallStatus, PlaneStatus, ResourceCategory, SyncReceipt


def render_human_receipt(receipt: SyncReceipt) -> str:
    """Render the human-readable receipt with clear separation between Convergence, Safety, and Health."""
    lines = []

    # 1. 一句话结果
    lines.append("## 1. 一句话结果")
    if receipt.overall == OverallStatus.PASS_NO_CHANGE:
        lines.append("本次没有发现需要同步的变化，当前系统已是最新状态。")
    elif receipt.overall == OverallStatus.PASS:
        updated_count = len(receipt.changes_applied)
        lines.append(f"同步完成，更新了 {updated_count} 项，均已生效。")
    elif receipt.overall == OverallStatus.PASS_WITH_HEALTH_WARNINGS:
        if receipt.changes_applied:
            lines.append(f"同步完成，更新了 {len(receipt.changes_applied)} 项；但存在需要关注的健康状态。")
        else:
            lines.append("同步完成，没有新的收敛变化；但存在需要关注的健康状态。")
    elif receipt.overall == OverallStatus.PASS_WITH_HEALTH_FAILURE:
        if receipt.changes_applied:
            lines.append(f"同步收敛已完成，更新了 {len(receipt.changes_applied)} 项；但检测到健康异常，需要后续处理。")
        else:
            lines.append("同步收敛已完成，没有新的收敛变化；但检测到健康异常，需要后续处理。")
    elif receipt.overall == OverallStatus.PARTIAL_RESTART_REQUIRED:
        lines.append("同步基本完成，更新已部署，待下次正常重启 DSH 后自动生效。")
    elif receipt.overall == OverallStatus.PARTIAL_WITH_HEALTH_WARNINGS:
        lines.append("同步部分完成：部分非核心资源尚未完全收敛，主功能正常；且存在需要关注的健康状态。")
    elif receipt.overall == OverallStatus.PARTIAL:
        lines.append("同步部分完成：部分非核心资源尚未完全收敛，主功能正常。")
    elif receipt.overall == OverallStatus.REVIEW_REQUIRED:
        lines.append("同步遇到需要人工确认的问题，已安全暂停更新。")
    elif receipt.overall == OverallStatus.FAILED_ROLLED_BACK:
        lines.append("同步部署失败，旧版本状态已安全回滚恢复。")
    else:
        lines.append("同步失败，无法安全继续。")
    lines.append("")

    # 2. 本次实际更新
    lines.append("## 2. 本次实际更新")
    if receipt.changes_applied:
        for change in receipt.changes_applied:
            lines.append(f"* {change}")
    elif receipt.overall in (
        OverallStatus.PARTIAL,
        OverallStatus.PARTIAL_WITH_HEALTH_WARNINGS,
        OverallStatus.PARTIAL_RESTART_REQUIRED,
        OverallStatus.REVIEW_REQUIRED,
        OverallStatus.FAILED,
    ):
        lines.append("本次未产生已应用的磁盘更新；系统仍有待对齐或待收敛项。")
    else:
        lines.append("本次没有发现需要同步的变化，当前系统已是最新状态。")
    lines.append("")

    # 3. 遇到的问题
    lines.append("## 3. 遇到的问题")
    if receipt.issues_encountered:
        for issue in receipt.issues_encountered:
            lines.append(f"* {issue}")
    else:
        lines.append("本次同步未遇到异常。")
    lines.append("")

    # 4. 怎么处理 / 为什么这样取舍
    lines.append("## 4. 怎么处理 / 为什么这样取舍")
    if receipt.tradeoff_decisions:
        for item in receipt.tradeoff_decisions:
            title = item.get("title", "系统处理")
            action = item.get("action", "")
            reason = item.get("reason", "")
            lines.append(f"### {title}")
            if action:
                lines.append(f"操作：{action}")
            if reason:
                lines.append(f"原因：{reason}")
            lines.append("")
    else:
        lines.append("按既定基线与幂等规则执行，未发生特殊决策分流。")
        lines.append("")

    # 5. 同步结果 (Convergence Planes)
    lines.append("## 5. 同步结果")
    lines.append("| 项目 | 状态 | 结果 |")
    lines.append("| :--- | :--- | :--- |")

    convergence_order = [
        ("Personal AI State", "Canonical State Plane"),
        ("Agent Tools", "Agent Tools / Source Plane"),
        ("Deployment Mirror", "Deployment Mirror Plane"),
        ("Presets", "DSH Preset Plane"),
        ("DSH Config", "DSH Config Plane"),
        ("Plugins", "DSH Plugin Plane"),
        ("MCP", "MCP Plane"),
        ("Skills", "Skill Plane"),
        ("DSH Runtime", "Runtime Plane"),
    ]

    for display_name, plane_key in convergence_order:
        p_res = receipt.planes.get(plane_key)
        if p_res:
            lines.append(f"| {display_name} | {p_res.symbol} | {p_res.summary} |")

    lines.append("")

    # 6. 安全与健康检查 (Safety Gates & Health Observability)
    lines.append("## 6. 安全与健康检查")
    lines.append("| 检查项 | 状态 | 当前观测 |")
    lines.append("| :--- | :--- | :--- |")

    observability_order = [
        ("Model Safety", "Model Discovery / Safety Gate"),
        ("Durable Jobs", "Durable Job Health"),
        ("Session History", "Session Continuity Health"),
        ("Backup", "Backup / Recovery Health"),
    ]

    for display_name, plane_key in observability_order:
        p_res = receipt.planes.get(plane_key)
        if p_res:
            lines.append(f"| {display_name} | {p_res.symbol} | {p_res.summary} |")

    # Disaster recovery readiness is tracked separately from local backup health:
    # off-device backup + external key custody are external durability conditions
    # and never mask a healthy local backup (nor does a local failure imply DR).
    backup_res = receipt.planes.get("Backup / Recovery Health")
    if backup_res:
        dr = backup_res.details.get("FULL_DR_READINESS")
        if dr:
            symbol = "✓" if dr == "READY" else "△"
            note = "；".join(backup_res.details.get("FULL_DR_NOTES", [])) or \
                "外部密钥托管/off-device 备份未建立（不阻塞本地备份健康）"
            lines.append(f"| Disaster Recovery | {symbol} | {dr}: {note} |")

    lines.append("")
    lines.append("状态说明：✓ 正常/已对齐 / ○ 待重启 / △ 需要关注 / ✗ 失败")
    lines.append("")

    # 7. 需要用户做什么
    lines.append("## 7. 需要用户做什么")
    lines.append(receipt.action_required_from_user)
    lines.append("")

    return "\n".join(lines)
