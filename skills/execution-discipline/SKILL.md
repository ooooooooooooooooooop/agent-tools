---
name: execution-discipline
description: 在 DSH 长程/多工具会话中强制五条执行纪律，防止高频反模式重演：禁止无 wait 轮询、禁止把门禁拒绝当交付物抛回决策、外部研究先探测通道再极窄探针、探查派子代理不肉身 Read、子代理派发带进度回报协议。用于接手长任务、等待子代理/后台任务、发起外部研究、收到 INVALID/FAIL/PARTIAL 门禁结果、收到"重复工具调用"系统警告、需要读取多个源码文件或大文件，以及任何需要"执行层不空转、不惊群、不绕门禁"的执行场景。不用于单轮小修改或无需持久化的临时问答。
version: 0.1.0
triggers:
  - "接手/恢复长任务（.taskflow、goal、跨轮委派）"
  - "等待 subagent / 后台任务 / CLI 请求返回"
  - "发起外部研究、文献调研、跨模型长推理"
  - "收到门禁拒绝（INVALID / FAIL / PARTIAL）"
  - "收到重复工具调用系统警告"
  - "需要读取 ≥2 个源码文件或 >300 行大文件"
not_for:
  - "单轮小修改"
  - "无需持久化的临时问答"
depends_on:
  - task-mode-router
  - minimal-implementation
---

# 执行纪律（Execution Discipline）

> 本 skill 由一次真实长程会话审计（session-`<id>`）提炼而成。那次会话 13 小时、72 轮、12 次压缩、30 次无 wait 轮询、外部研究惊群空转约 90 分钟、多次把决策抛回用户，最终在 goal 8/8 耗尽时仍未完成。**这些错误全部有现成规则可依，缺的是执行时把它们当硬门禁。** 本 skill 把五条铁律固化为"执行点自查"，任何新会话在匹配场景下加载后必须逐条对照。

## 五条铁律

### 铁律一：等通知，不轮询（零轮询）

- 子代理 / 后台任务 / CLI 请求完成时，系统会**主动推送完成通知**，无需查询。
- 派发后本轮立即收尾结束，等通知进入下一轮。
- 确需确认请求存在时，`list_agents` / `job_list` / `request_status` / `get_cli_requests` **只允许调用一次**；确认后转入单次长轮询：`job_output(job_id, wait=true, timeout_ms=60000)`、`request_result(request_id, wait_seconds=60~120)`、`wait_task_receipt(receipt_path, wait_seconds=60~120)`、`wait_supervisor_event(...)`。
- **收到"重复相同工具调用"系统警告 = 必须立即改变策略**：先读上次结果，换参数/换工具/换方案，绝不在同一调用上再试。

### 铁律二：门禁结果是诊断信号，不是交付物（自主闭环）

- INVALID / FAIL / PARTIAL 意味着"继续修"，不是"可以交差了"。
- 收到门禁拒绝后先回答三个问题再决定下一步：**根因在哪（数据/构造/门禁本身）？修哪里能过？需要什么证据证明修好了？**
- **禁止**向用户输出"是否继续？/你怎么看 A/B/C？/待决策"这类抛回；只有存在确实不可自行裁决的方向分歧时才问，且一次问完。
- 措辞从"待决策"改为"我将继续做 X，因为 Y"。
- 用户写死的门禁（如"B/C 失败则停止"）**不允许**在执行端悄悄重定义语义；确需调整，必须显式向用户说明理由并获批。

### 铁律三：外部研究：先探测 → 极窄探针 → 单次接管（防惊群）

按顺序执行，缺一不可：
1. **先探测通道可用性**：`list_providers`（模型接口层：中转/登录态，如 CPA）与 `list_cli_backends`（前端 CLI 壳层，如 codex_cli/claude_code）**是两个不同抽象层，不是同级通道**：CLI 壳可任意匹配任意 provider 上的任意模型。探测时先确认"用哪个模型接口"，再确认"用哪个 CLI 壳去跑"，不要把它们并列成候选。确认目标通道真实存在且已配置，**再**发起请求。
2. **通道必须对照 `routing_preferences` 选**：按任务类型查偏好表（research/coding → cpa、codex_cli；fast/general → cpa），从偏好列表内选通道；选偏好之外的通道必须说明理由。**禁止**只凭"可用"就选。
3. **每次派发必须显式指定模型档位**：`queue_cli_request` / `route_agent_task` / `consult_*` 调用必须传 `target_model`（或 `model_policy`）与 `effort`，禁止只传 token_budget；省略档位 = broker 默认前沿档（最贵）。默认工作马档（`gpt-5.6-luna`），中档/前沿档须向用户说明理由。
2. **任务拆成极窄探针**：单次生成 <10 秒、Prompt ≤100 字、要求结构化输出事实；严禁"大而全的宏大综述命题"。
3. **异步提交**：`queue_cli_request` / `queue_codex_request` 拿 `request_id`，然后**单次** `request_result(wait_seconds=60~120)` 阻塞收取。
4. **超时不惊群**：MCP 超时（-32001）≠ 通道挂掉，Worker 仍在跑——先 `get_cli_requests` 查在途，再 `request_result` 接管；**严禁**同一时间并发切换 4~5 个通道。
5. **拿到足够证据立即收敛**：多路结果重合 ≥80% 部分直接锁定，只对单一差异点做一次定向核实，然后拍板；**严禁**证据已足仍反复核验。

### 铁律四：探查交给子代理，主会话只收摘要（Read-as-Execution）

- 需研读 ≥2 个源码文件 / 摸排调用链 / 排查旧实现 → 派探查 subagent（`subagent(run_in_background: true)`，轻量 Brief），主会话只回收 ≤5 行接入点摘要。
- 大文件（>300 行）先用 `grep` 锁定行号，再 `read(offset, limit)` 窗口读取；禁止全量。
- **禁止**用 `pwsh` 把大 JSON / 大表全量打印回主会话上下文（会引发压缩风暴、上下文丢失）。
- 一旦判定/声明为 `large` 任务，立即阻断本地文件探测/阅读/修改，下一动作只能是结构化 Brief + 委派。

### 铁律五：派发带进度回报协议（防静默卡死）

- 派发 subagent 时要求：**步骤 0 先回报工作目录与沙箱探针**，之后每文件/每阶段回报，静默超 5 分钟必须主动报告。
- 同一任务重派前，先派一个最小 smoke test 验证通道真实可用；不要第二次派给同一个可能卡死的通道。
- 注意 subagent 有自己的沙箱临时目录（`$env:TEMP` 与 `%TEMP%\dsh-xxx` 不同），验证产物路径以 subagent 报告为准。

## 回合开始前自查（每次新 goal round / 长等待 / 大研究前，10 秒过一遍）

1. 我是不是在轮询？（→ 应等通知，最多查一次）
2. 我是不是又要问用户"要不要继续"？（→ 应自己定下一步）
3. 我要发的任务，通道查过吗？探针够窄吗？
4. 我读的文件是不是该交给子代理？
5. 我派出去的活儿，有进度回报协议吗？
6. 我是不是在重复调用同一工具？（→ 先读上次结果再换策略）

## 反面案例速查（本 skill 来源，遇到同类情况直接对照）

| 场景 | 当时的错误 | 现在该怎么做 |
|------|-----------|-------------|
| 等两个审计子代理 | 22 次 `list_agents` 无 wait，被系统警告后仍继续 | 派发后结束本轮等通知；至多查一次 |
| 4 作者 pilot 得 INVALID | 输出"门禁正确拒绝"+"待决策：是否继续？"，用户批评"你不是来拒绝的，你是来解决问题的" | 定位根因（碎情境键→数据现实→作者选择）自主迭代 |
| 外部研究两轮 | 未探测通道就把请求发给未安装的 Antigravity CLI / 未配置的 Gemini，MCP 超时后并发换 4~5 个通道，证据足够仍重复核验 | 先 `list_providers`；极窄探针；单次 `request_result` 接管；共识直接收敛 |
| 通道选型错误 | research 任务硬选 `claude_code`（偏好是 cpa/codex_cli），且未传档位触发前沿档 | 派发前先查 `routing_preferences` 按任务类型选通道，再显式指定 `target_model`+`effort` |
| 省略档位派发 | `queue_cli_request` 只传 token_budget 没传 target_model/effort，broker 默认路由到前沿档（最贵） | 每次派发必填 target_model（或 model_policy）与 effort；禁止只传 token_budget |
| 内置工具失败误当通道问题 | `web_search` 报 "Insufficient Balance" 被说成"通道余额不足"，与 switchboard 通道混淆 | `web_search` 是 DSH 内置工具，余额独立于 switchboard；失败就写"内置 web_search 余额耗尽"，另走已实测可用的 `queue_cli_request`（codex_cli/cpa）通道 |
| Phase 1C 子代理返回乱码 | 未重新派发、未报告，直接重定义门禁语义继续 | 结果不可读 = 未完成 = 重新派发或主会话直接核查 |
| v8 实施子代理 | 5 小时静默无产出，三次重派仍失败，goal 8/8 耗尽 | 派发时带进度回报协议；重派前 smoke test 通道 |
| 主会话读 3 个源码文件 + 260KB JSON | 肉身全量 Read，触发 12 次上下文压缩 | 派探查子代理；grep + offset/limit 窗口 |

## 业界对照验证（2026-08-22 web 研究回收，与五条铁律一致）

| 铁律 | 业界做法 | 来源 |
|------|---------|------|
| 一 等通知不轮询 | Anthropic Agent Teams mailbox：子代理 `SendMessage` 主动投递，主代理无需轮询 | https://code.claude.com/docs/en/agent-teams |
| 三 防惊群 | 指数退避：LangGraph `RetryPolicy` / CrewAI `max_retries`；在途接管：LangGraph Durable Execution | https://docs.langchain.com/oss/python/langgraph/use-graph-api#add-retry-policies / https://docs.langchain.com/oss/python/langgraph/durable-execution |
| 三 熔断 | Circuit Breaker 模式：连续 N 次失败后快速失败、冷却后重试（框架多需外置） | https://learn.microsoft.com/azure/architecture/patterns/circuit-breaker |
| 四 上下文 | auto-compact（Claude Code）/ LangGraph checkpoint（按 thread_id 恢复）/ Mem0·Zep 结构化记忆 | https://code.claude.com/docs/en/how-claude-code-works / https://langchain-ai.github.io/langgraph/concepts/persistence/ / https://docs.mem0.ai/migration/platform-v2-to-v3 / https://help.getzep.com/v2/memory |

**熔断补充（业界有、本 skill 此前缺）**：同一工具/通道**连续失败 3 次**即熔断——停止重试、记入 checkpoint、切换方案或等待冷却后再试；严禁在同一通道上无限退避重试。与铁律一"收到重复调用警告立即换策略"联动。

## 与相邻 skill 的分工

- `decision-gates`：决策层不跑偏（checkpoint 证据锚、对抗审计、成本比对）——管"决策正确性"。
- 本 skill：执行层不空转（零轮询、不惊群、不绕门禁、不肉身读、不静默派发）——管"执行纪律"。
- 两者可叠加使用：长任务先按本 skill 自查执行姿势，再按 decision-gates 落 checkpoint。
