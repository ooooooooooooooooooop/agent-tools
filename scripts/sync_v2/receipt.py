"""receipt.py — Human-readable (Section 28) and Machine-readable (Section 30) receipt formatters."""
from __future__ import annotations

from typing import Any, Dict
from .models import OverallStatus, PlaneStatus, SyncReceipt


def render_human_receipt(receipt: SyncReceipt) -> str:
    """Render the 6-section human-readable receipt defined in Section 28."""
    lines = []

    # 1. 一句话结果
    lines.append("## 1. 一句话结果")
    if receipt.overall == OverallStatus.PASS_NO_CHANGE:
        lines.append("本次没有发现需要同步的变化，当前系统已是最新状态。")
    elif receipt.overall == OverallStatus.PASS:
        updated_count = len(receipt.changes_applied)
        lines.append(f"同步完成，更新了 {updated_count} 项，均已生效。")
    elif receipt.overall == OverallStatus.PARTIAL_RESTART_REQUIRED:
        lines.append("同步基本完成，更新已部署，待下次正常重启 DSH 后自动生效。")
    elif receipt.overall == OverallStatus.PARTIAL:
        lines.append("同步部分完成：部分非核心资源尚未完全收敛，主功能正常。")
    elif receipt.overall == OverallStatus.REVIEW_REQUIRED:
        lines.append("同步遇到需要人工确认的问题，已安全暂停更新。")
    elif receipt.overall == OverallStatus.FAILED_ROLLED_BACK:
        lines.append("同步部署失败，旧版本状态已安全回滚恢复。")
    else:
        lines.append("同步失败，无法安全继续。")
    lines.append("")

    # 2. 本次更新了什么
    lines.append("## 2. 本次更新了什么")
    if receipt.changes_applied:
        for change in receipt.changes_applied:
            lines.append(f"* {change}")
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

    # 4. 系统如何处理 / 为什么这样取舍
    lines.append("## 4. 系统如何处理 / 为什么这样取舍")
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

    # 5. 当前状态表
    lines.append("## 5. 当前状态表")
    lines.append("| 项目 | 状态 | 当前结果 |")
    lines.append("| :--- | :--- | :--- |")

    # Table items mapping
    planes_order = [
        ("Personal AI State", "Canonical State Plane"),
        ("Agent Tools", "Agent Tools / Source Plane"),
        ("Deployment Mirror", "Deployment Mirror Plane"),
        ("DSH Config", "DSH Config Plane"),
        ("Plugins", "DSH Plugin Plane"),
        ("MCP", "MCP Plane"),
        ("Skills", "Skill Plane"),
        ("DSH Runtime", "Runtime Plane"),
        ("Model Safety", "Model Discovery / Safety Plane"),
        ("Durable Jobs", "Durable Job Plane"),
        ("Session History", "Session Continuity Plane"),
        ("Backup", "Backup / Recovery Plane"),
    ]

    for display_name, plane_key in planes_order:
        p_res = receipt.planes.get(plane_key)
        if p_res:
            lines.append(f"| {display_name} | {p_res.symbol} | {p_res.summary} |")

    lines.append("")
    lines.append("状态说明：✓ 已完成 / ○ 待重启 / △ 需要检查 / ✗ 失败")
    lines.append("")

    # 6. 需要用户做什么
    lines.append("## 6. 需要用户做什么")
    lines.append(receipt.action_required_from_user)
    lines.append("")

    return "\n".join(lines)
