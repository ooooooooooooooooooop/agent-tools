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
  permission_mode="bypassPermissions"
)
```

要点：`permission_mode` 必须足够（受管窗口无人点批准）；`objective` 要具体到可验收。

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

对照 policy 独立核验：亲自复跑回归命令、核对 diff 范围、确认未碰红线。执行者自报"已完成"不算数。

## 5. 关闭归档

```text
close_supervisor(supervisor_id=..., archive_summary="修复 parser 空输入崩溃：新增 2 用例，回归 183/183 绿；未改公共 API")
```

摘要进入 topic memory，下一个会话可用 `get_work_memory` 直接续接。
