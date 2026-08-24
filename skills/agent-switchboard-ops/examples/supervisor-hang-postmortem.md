# 示例：supervisor 悬挂复盘（2026-08-24 反模式）

来源：接管"严格好论文榜"长任务的真实会话审计。该会话纪律执行基本合格（状态冻结、无越权修分、provenance 污染被识别并修复、独立验收闭环），但收尾环节系统性失败，说明**规则存在 ≠ 执行拦截**。

## 事故事实

- 同一天启动的 supervisor 中 **9 个在验收完成后仍存活**：`leaderboard-reground`、`leaderboard-source-freeze`、`anchor-receipt-delta-audit`、`formula-config-provenance-repair`、`formula-config-repair-verifier`、`impact-fullscope-contract`、`impact-gold-rootcause-audit`、`rpu-fullscan-contract-discovery`、`taskflow-dashboard-update`，全部 `attention_required`、daemon+claude 存活。
- 其中 2 个已产出最终报告（`impact-gold-rootcause-audit`、`taskflow-dashboard-update`），结果从未被收集归档。
- 2 个处于失败态仍挂着（`formula-config-provenance-repair` BLOCKED、`formula-config-repair-verifier` 误写文件）。
- 执行者反复误用工具：memory MCP、Grep 非法参数、OWN 路径误写到 `.taskflow/`、reference 路径猜错——管理者 8+ 次 interrupt 纠偏。
- 紧急事件打断 320 合同设计后，方向丢失，未恢复。

## 根因（不是"忘了"，是机制缺失）

1. 收尾依赖管理者"记得 close"，无回合级自查兜底；
2. 派工提示词每次现写，已知陷阱未模板化，同类错误反复踩；
3. 紧急打断不登记待恢复方向，事件结束后方向蒸发。

## 正确做法（防复发）

**启动时**：`stall_timeout_seconds=300~600` 必填；只读/文件类任务 `mcp="none"` 硬禁用无关 MCP。

**回合结束时**（每个 goal round / 长等待后）：

```text
list_managed_claude_supervisors()   # 只查一次
# 对每个本项目存活 supervisor：
#   任务已完成 → close_supervisor(supervisor_id, archive_summary=...)
#   attention_required 且无待处理命令 → 先 get_managed_claude_supervisor 收结果，再 close
#   事件流停滞且无命令 → stop_managed_claude_supervisor + checkpoint 记录续接
```

**打断方向**：任何 interrupt/停写，先在 todo 保留 pending 项并写入 checkpoint 的"待恢复方向"清单；下一轮先处理该清单。

**派工模板**（固化陷阱，见 `delegate-implementation.md`）：

- 禁 memory MCP → 用 `mcp="none"` 而不是 policy 文字；
- OWN 必须绝对路径，写前 `glob` 确认目标不存在或可覆盖；
- reference 文件以 receipt 登记字段为准，禁止猜路径；
- BLOCKED 必须结构化（原因码 + 已尝试 + 未动文件）。

## 验收标准

- 会话结束时 `list_managed_claude_supervisors` 中本项目无存活 supervisor（或全部有归档摘要）；
- 每个启动过的 supervisor 有终态：closed / stopped；
- 打断的方向在 checkpoint 有登记，下一轮可恢复。
