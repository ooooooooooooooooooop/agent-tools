# 示例：长程会话中的五条铁律（反面案例 → 正确姿势）

> 场景：一次 13 小时、72 轮的长程会话，暴露 30 次无 wait 轮询、外部研究惊群空转约 90 分钟、多次把决策抛回用户。以下按铁律逐条对照"当时的错误"与"现在该怎么做"。

## 铁律一：等通知，不轮询

```text
❌ 当时：等待两个审计子代理时连续 list_agents 22 次（无 wait），被系统警告后仍继续
✅ 现在：派发后结束本轮等系统通知；确需确认时状态查询至多一次，
   然后转入单次长轮询：job_output(job_id, wait=true, timeout_ms=60000)
   或 request_result(request_id, wait_seconds=60~120)
```

## 铁律二：门禁结果是诊断信号，不是交付物

```text
❌ 当时：4 作者 pilot 得 INVALID，输出"门禁正确拒绝 + 待决策：是否继续？"
✅ 现在：收到 INVALID/FAIL/PARTIAL 先回答三个问题再决定下一步：
   根因在哪（数据/构造/门禁本身）？修哪里能过？需要什么证据证明修好了？
   措辞从"待决策"改为"我将继续做 X，因为 Y"
```

## 铁律三：外部研究：先探测 → 极窄探针 → 单次接管

```text
❌ 当时：未探测通道就把请求发给未安装的 Antigravity CLI / 未配置的 Gemini，
   MCP 超时后并发切换 4~5 个通道，证据足够仍重复核验
✅ 现在：① list_providers / list_cli_backends 探测 → ② 按 routing_preferences 选通道
   → ③ 任务拆成 ≤100 字极窄探针 → ④ 异步提交拿 request_id
   → ⑤ 单次 request_result(wait_seconds=60~120) 接管 → ⑥ 共识 ≥80% 直接收敛
```

## 铁律四：探查交给子代理，主会话只收摘要

```text
❌ 当时：主会话肉身 Read 3 个源码文件 + 260KB JSON，触发 12 次上下文压缩
✅ 现在：需研读 ≥2 个源码文件 / 摸排调用链 → 派探查 subagent（轻量 Brief），
   主会话只回收 ≤5 行接入点摘要；大文件先 grep 锁定行号再 offset/limit 窗口读
```

## 铁律五：派发带进度回报协议

```text
❌ 当时：v8 实施子代理静默 5 小时无产出，三次重派仍失败，goal 8/8 耗尽
✅ 现在：派发时要求步骤 0 先回报工作目录与沙箱探针，之后每阶段回报，
   静默超 5 分钟必须主动报告；重派前先跑最小 smoke test 验证通道
```

## 熔断补充（业界有、本 skill 固化）

```text
同一工具/通道连续失败 3 次即熔断：停止重试、记入 checkpoint、
切换方案或等待冷却后再试；严禁在同一通道上无限退避重试。
```

## 产物落点

```text
work_memory.md          # 断点沉淀（Goal/Todo/Work Memory 三位一体）
checkpoints/            # 长任务里程碑锚点（decision-gates 叠加使用）
request ledger          # 跨 agent 请求台账（request_id → answer → timing）
```
