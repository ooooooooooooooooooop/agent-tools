---
name: agent-switchboard-ops
description: |
  以"管理者—执行者"模式运维 agent-switchboard MCP：受管 Claude supervisor 生命周期、跨模型委派（Codex/Claude/Gemini）、验收纪律与已知陷阱对策。
  用于当前环境已挂载 mcp__agent-switchboard__* 工具，且用户要求委派执行、监督长任务、跨模型审查、回收多 agent 会话成果，或询问"怎么让 Codex/Claude 帮我做"的场景。
  不用于单会话即可完成的普通修改，也不用于未安装 agent-switchboard 的环境。
---

# Agent Switchboard 运维手册

本 Skill 是使用 `agent-switchboard` MCP（本地跨 agent 协作桥）的操作契约，全部经验来自真实生产使用的事故与验收记录。只负责编排规则；任务本身的修改纪律交给 `minimal-implementation`，范围澄清交给 `clarify-before-change`。

## 适用范围与触发边界

触发条件（需同时满足）：

1. 工具目录中存在 `mcp__agent-switchboard__*` 工具；
2. 任务属于以下之一：跨模型委派（审查/实现/辩论）、受管长任务执行、监督或收取其他 agent 会话的成果、多窗口并行编排。

## 不适用场景

- 单会话可直接完成的小型或中型修改：不要为普通任务起 supervisor。
- 环境未安装 agent-switchboard：不要猜测工具名或要求用户临时安装，安装属于高风险操作，需用户明确授权。
- 纯只读问答：直接回答，不经 broker 绕道。

## 核心模式：管理者—执行者边界

- 会话管理者（你自己）只负责：定目标与阶段顺序、写完整派工提示词、审阅执行者证据、下返修指令、判定阶段是否通过、向用户汇报。
- 一切动手操作（调查、读大量文件、编码、改配置、跑构建/测试/数据扫描、Git 提交）委派给受管执行者；管理者不直接修改项目文件。
- 执行者自报的 PASS 不是验收依据；验收以独立证据为准（管理者亲自复跑检查、核对文件哈希/行号/输出）。
- 互不冲突的任务应主动并行多个受管窗口，无需用户提醒；存在同一文件或同一数据冲突风险的任务在同一窗口排队。
- 执行者窗口验收完成或确认不再需要后立即 `close_supervisor` / `stop_managed_claude_supervisor`，不得长期悬挂闲置窗口。

## 降级规则：无 Switchboard 工具时

环境中没有 `mcp__agent-switchboard__*` 工具时，管理者—执行者边界仍然成立，执行者角色由宿主原生委派机制承担（如后台 subagent）；管理者仍只做派工、验收、归档，不直接动手。

- 可用性以**真实调用**为准：工具未出现在会话的声明目录里不等于不可用（热重载可能迟注册）。下"无法使用"的结论前必须先实际调用一次；调用失败再降级。禁止凭目录清单假设工具不可用。
- 降级路径是权宜方案：宿主 subagent 没有 broker 的事件流、归档和跨会话续接能力，长任务应建议用户安装 agent-switchboard（安装属高风险操作，需明确授权）。
- 单会话可直接完成的小型修改不走任何委派，直接最小修改并验证。

## 工作流程：受管执行者生命周期

1. **启动**：`start_managed_claude_supervisor`，提供具体 `objective`、明确 `policy`（里程碑、安全红线、验收规则）、稳定 `supervisor_id`。默认 `decision_mode=record_only`，除非用户要求 Codex 决策。
2. **派工**：`send_to_managed_claude_session` 发送有界任务切片（一次一个可验收的增量），不发开放式大目标。
3. **追踪**：用 `wait_supervisor_event` 长轮询等待材料事件；进度可见性来自 wait 本身。禁止自建 cron 或轮询循环做监控。
4. **验收**：对照 objective 与 policy 独立核验证据，不接受执行者自述。
5. **关闭归档**：`close_supervisor` 写入归档摘要（做了什么、证据、风险、下一步），供跨会话续接。

## 跨模型委派规则

- 审查/评审/辩论类任务优先派给当前最强模型（如 Codex 的 `gpt-5.6-sol` max 档），`mode=read-only`；评审意见作为返修指令来源。
- 实现类任务委派时必须确认目标会话具备写权限与执行授权。无头 worker 常落在"仅提供建议"的受限会话中，会诚实地拒绝执行——这不是失败借口，而是提示词缺少授权声明。
- 实现类派工提示词开头必须包含显式授权声明，模板见 `examples/delegate-implementation.md`。
- 大证据用 `store_shared_context` 压缩传递，接收方按需 `retrieve_shared_context`，不要把完整转录粘贴进提示词。
- 排队类请求（`queue_codex_request` 等）用 `request_result` 长轮询收取，并在汇报中区分"请求的模型"与"实际应答的模型"。

## 已知陷阱与对策

| 陷阱 | 症状 | 对策 |
|---|---|---|
| 权限模式不足 | 受管会话读项目根外文件时空转，等不到人工批准 | 启动 supervisor 时用 `bypassPermissions`（或等价的全授权模式），受管窗口无人可点批准 |
| 客户端超时假死 | 大上下文请求被客户端掐断，但上游实际健康 | 为执行者客户端配置长超时（如 Claude 的 `API_TIMEOUT_MS=600000`），注意只对新进程生效 |
| 上游配额耗尽 | 事件流反复 `api_retry` / `api_retry_exhausted` 且含 `rate_limit` | 报告用户并暂停，切换上游/接口后重试；禁止无限重试 |
| 多窗口消息串线 | 中断一条消息时它可能已送达另一窗口 | 中断前确认目标窗口；返修时先核对提交归属再决定 reset |
| 闲置窗口悬挂 | supervisor 完成但长期不关闭 | 验收通过即 `close_supervisor`，摘要归档进 topic memory |
| 工具可用性臆断 | 声明目录里没有某工具就断言"物理上无法使用" | 先做一次真实调用，失败再降级；目录清单不是可用性证据 |

## 输出契约

派工时输出派工单；验收时输出验收报告：

```text
派工单：目标 / 边界与红线 / 授权范围 / 产物落点 / 验收标准 / 回报方式
验收报告：结论（PASS | PARTIAL | BLOCKED）/ 独立核验证据 / 偏差与风险 / 下一步
```

向用户汇报时必须包含：活跃 supervisor 列表及其状态（来自 `list_managed_claude_supervisors` 或 wait 返回），不凭空估计进度。

## 安全边界与非目标

- 不修改用户级或全局 MCP/模型配置，除非用户明确要求。
- 不使用 `send_to_claude_session` 前台控制他人终端，除非用户明确要求驱动人工会话。
- 不抓取私人会话内容；快照走 `request_context_snapshot` 的本地授权通道。
- 不代替执行者写项目代码；管理者的写入仅限 broker 记录（`record_work_memory` / `record_agent_event`）。

## 验证方式

交接或收尾前完成以下可观察检查：

1. `initialize` + `tools/list` 能连上 broker（新环境先冒烟）。
2. 每个启动过的 supervisor 都有终态：closed / stopped，且有归档摘要。
3. 每个排队请求都有终态：completed / error / cancelled，结果已用 `request_result` 收取。
4. 验收报告中的每条证据都可复核（路径、行号、哈希、命令输出）。
5. 无悬挂窗口、无未收取的排队请求、无管理者越权写入项目文件。

相关示例见 `examples/`。
