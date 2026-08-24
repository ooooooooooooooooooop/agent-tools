# 示例：受管执行者的完整生命周期

场景：委派一个需要改代码并跑回归的中型任务。

## 1. 启动

```text
start_managed_claude_supervisor(
  project=<项目名>,
  supervisor_id="fix-parser-20260818",
  objective="修复 parser 在空输入下的崩溃，回归全绿",
  policy="红线：不改公共 API；里程碑：复现→修复→回归；验收：新增用例通过且全套件绿",
  decision_mode="record_only",
  permission_mode="acceptEdits",
  allowed_tools=["Bash(git add:*)", "Bash(git commit:*)", "Read"],  # 可选：命令级硬白名单
  mcp="none"  # 可选：硬禁用全部 MCP（如 memory），inherit 为默认
)
```

要点：默认用 broker 的保守默认 `acceptEdits` 启动，保持最小权限；仅当用户明确授权且目标范围受控（红线已写入 policy）时才升级为 `bypassPermissions`——受管窗口无人点批准，升级前必须确认授权来源。遇到认证类失败或被要求破坏性操作时停止上报，不得自行升级绕过。`objective` 要具体到可验收。

**优先用硬约束代替软约束**：需要 git 写等操作时，先尝试 `allowed_tools`（命令级白名单，引擎强制拒绝白名单外命令，不需要人工批准也不全放行）；`bypassPermissions` 是最后手段。只读/文件类任务用 `mcp="none"` 硬禁用无关 MCP（如 memory server），比在 policy 里写"禁止调用"可靠——工具不存在就不可能被调用。

## 2. 切片派工

```text
send_to_managed_claude_session(supervisor_id=..., prompt="切片 1：写出最小复现用例并确认失败")
```

一次一个可验收增量，不一次性发完整大目标。

## 3. 追踪

```text
wait_supervisor_event(supervisor_id=..., since_seq=<上次事件序号>, wait_seconds=180)
```

用长轮询等材料事件（turn_completed / api_retry_exhausted / stall_timeout）。看到 rate_limit 类事件：报告用户并暂停，不要无限重试。禁止另起 cron 巡检。

## 4. 验收

对照 policy 独立核验：管理者做有界只读验收——复跑只读校验/回归命令、`git show` 核对 diff 范围、确认未碰红线；不实现、不提交、不扩大调查。执行者自报"已完成"不算数。

## 5. 关闭归档

```text
close_supervisor(supervisor_id=..., archive_summary="修复 parser 空输入崩溃：新增 2 用例，回归 183/183 绿；未改公共 API")
```

摘要进入 topic memory，下一个会话可用 `get_work_memory` 直接续接。
