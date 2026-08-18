---
name: agent-switchboard-ops
description: |
  以"管理者—执行者"模式运维 agent-switchboard MCP：受管 Claude supervisor 生命周期、跨模型委派（Codex/Claude/Gemini）、验收纪律与已知陷阱对策。
  用于存在任一可用委派通道（mcp__agent-switchboard__* 工具或宿主原生 subagent），且用户要求委派执行、监督长任务、跨模型审查、回收多 agent 会话成果，或询问"怎么让 Codex/Claude 帮我做"的场景。
  不用于单会话即可完成的普通修改；未安装 agent-switchboard 时仍可经原生 subagent 通道触发，但不要猜测工具名。
---

# Agent Switchboard 运维手册

本 Skill 是使用 `agent-switchboard` MCP（本地跨 agent 协作桥）的操作契约，全部经验来自真实生产使用的事故与验收记录。只负责编排规则；任务本身的修改纪律交给 `minimal-implementation`，范围澄清交给 `clarify-before-change`。

## 适用范围与触发边界

触发边界：存在任一可用委派通道即可触发；随后按任务类型进入相应路由：

1. 存在任一可用委派通道：`mcp__agent-switchboard__*` 工具，或宿主原生委派机制（如后台 subagent）；
2. 任务属于以下之一：跨模型委派（审查/实现/辩论）、受管长任务执行、监督或收取其他 agent 会话的成果、多窗口并行编排。

## 不适用场景

- 单会话可直接完成的小型或中型修改：不要为普通任务起 supervisor。
- 环境未安装 agent-switchboard 且也无原生委派通道：不要猜测工具名或要求用户临时安装，安装属于高风险操作，需用户明确授权。
- 纯只读问答：直接回答，不经 broker 绕道。

## 核心模式：管理者—执行者边界

- 会话管理者（你自己）只负责：定目标与阶段顺序、写完整派工提示词、审阅执行者证据、下返修指令、判定阶段是否通过、向用户汇报。
- 任务规模一律由 `task-mode-router` 判级，不由"我已经在做了"反推：判为中型及以上的动手操作（调查、读大量文件、编码、改配置、跑构建/测试/数据扫描、Git 提交）委派给受管执行者；判为小型的单会话修改可直接最小修改并验证。执行前记录所选路由与理由（写入派工单或 broker 记录）。
- 管理者允许做**有界、只读的独立验收**：复跑只读校验脚本、`git show`/`git diff --check`、读文件核对哈希/行号/输出。禁止实现、提交、扩大调查范围。
- 执行者自报的 PASS 不是验收依据；验收以独立证据为准（上述只读核验的结果）。
- 互不冲突的任务应主动并行多个受管窗口，无需用户提醒；存在同一文件或同一数据冲突风险的任务在同一窗口排队。
- 执行者窗口验收完成或确认不再需要后立即 `close_supervisor` / `stop_managed_claude_supervisor`，不得长期悬挂闲置窗口。

## 路由规则：确定性选择委派通道

按以下顺序判定，命中即停；执行前记录所选路由与理由：

1. **小型任务直接执行**：经 `task-mode-router` 判级为小型的单会话修改，不走任何委派，直接最小修改并验证。
2. **同厂劳动 native subagent 优先**：与当前会话同厂（同厂商/同宿主）的执行类劳动，优先使用宿主原生委派机制（如后台 subagent）。这不是"switchboard 失败后的降级"，而是 broker 自身要求的默认路由。
3. **异厂咨询/审查走 switchboard**：跨厂商模型的审查、评审、辩论、咨询，经 `route_agent_task` / `consult_*` / `queue_*_request` 走 broker。
4. **受管 Claude 长任务走 supervisor**：需要跨多轮、可归档续接的 Claude 长任务，用 `start_managed_claude_supervisor` 受管执行。
5. **逐级降级**：所需路由的真实调用失败时，才降级到下一可用通道（switchboard → 原生 subagent → 直接执行），并记录降级原因。可用性以**真实调用**为准：工具未出现在会话声明目录里不等于不可用（热重载可能迟注册），禁止凭目录清单下"无法使用"的结论。
6. **无可用执行者报 BLOCKED**：所有通道均不可用时，在验收报告中报 BLOCKED，说明已试通道与失败原因，不硬撑执行。

**用户成本偏好**：计费 token 与订阅额度并存时，在任务适配、真实可用性和授权边界均满足的前提下优先 MCP 路由（省钱）；不得以此绕过最小权限、认证失败停机或破坏性操作上报规则。

降级通道的能力差异要如实告知用户：宿主 subagent 没有 broker 的事件流、归档和跨会话续接能力；长任务可建议用户安装 agent-switchboard（安装属高风险操作，需明确授权）。

## 工作流程：受管执行者生命周期

1. **启动**：`start_managed_claude_supervisor`，提供具体 `objective`、明确 `policy`（里程碑、安全红线、验收规则）、稳定 `supervisor_id`。默认 `decision_mode=record_only`，除非用户要求 Codex 决策。
2. **派工**：`send_to_managed_claude_session` 发送有界任务切片（一次一个可验收的增量），不发开放式大目标。
3. **追踪**：用 `wait_supervisor_event` 长轮询等待材料事件；进度可见性来自 wait 本身。禁止自建 cron 或轮询循环做监控。
4. **验收**：对照 objective 与 policy 独立核验证据，不接受执行者自述。
5. **关闭归档**：`close_supervisor` 写入归档摘要（做了什么、证据、风险、下一步），供跨会话续接。

## 跨模型委派规则

- **模型档位是派工单的必填项，禁止依赖环境默认值**——broker 在省略模型/档位时默认路由到前沿档（最贵），这个默认在任何新环境都存在；`set_model_default` 之类的环境配置只是局部补丁，不是约束。默认档使用工作马档（如 Codex `gpt-5.6-luna`、Claude haiku 档），审查和实现都默认使用工作马档；任务判断量确实超出工作马档时，才使用中档（balanced，如 terra/medium、sonnet/medium），且派工前须向用户说明理由；前沿档（如 sol/max、fable/max）仅在用户明确点名时使用。验收报告须核对派工单是否含显式档位；发现省略档位的派工，记为偏差。本条优先于本节其他默认模型选择表述；已有审查/评审/辩论规则继续执行，但其中“最强模型”仅指用户明确允许的档位，未明确点名前沿档时一律使用工作马档。
- 审查/评审/辩论类任务优先派给当前最强模型（如 Codex 的 `gpt-5.6-sol` max 档），`mode=read-only`；评审意见作为返修指令来源。
- Gemini 路由：经 `consult_gemini` / `route_agent_task(target_agent="gemini")` 走 Gemini CLI 或 Gemini API；适用咨询类任务，与 Codex/Claude 同属异厂咨询通道。
- **权限默认最小**：broker 默认的受管权限模式是 `acceptEdits`，实现类委派先以默认最小权限启动；`bypassPermissions` 仅在用户明确授权且目标范围受控（有界目录、红线已写入 policy）时才升级使用。
- worker 拒绝执行有两类成因，分别处理：会话模式受限（落在"仅提供建议"的只读会话）→ 检查权限模式与路由，不要盲目升级权限；提示词缺少授权声明 → 在派工提示词开头补显式授权声明，模板见 `examples/delegate-implementation.md`。
- **遇到认证失败（凭据缺失/过期/配额鉴权错误）或被要求执行破坏性操作（删除数据、重置历史、覆盖未知产物）时，必须停止并上报用户**，不得自行升级权限或重试绕过。
- 大证据用 `store_shared_context` 压缩传递，接收方按需 `retrieve_shared_context`，不要把完整转录粘贴进提示词。
- 排队类请求（`queue_codex_request` 等）用 `request_result` 长轮询收取，并在汇报中区分"请求的模型"与"实际应答的模型"。

## 已知陷阱与对策

| 陷阱 | 症状 | 对策 | 来源 |
|---|---|---|---|
| 权限模式误配 | 受管会话读项目根外文件时空转，等不到人工批准；或 worker 落在只读会话拒绝执行 | 默认用 broker 的保守默认 `acceptEdits` 启动；仅当用户明确授权且目标范围受控时才升级 `bypassPermissions`（受管窗口无人可点批准）；遇到认证类失败停止上报，不得自行升级绕过 | broker 实现依据（默认 `acceptEdits`）+ 来自 2026-08-18 生产复盘，本仓库无可复核测试 |
| 客户端超时假死 | 大上下文请求被客户端掐断，但上游实际健康 | 为执行者客户端配置长超时（如 Claude 的 `API_TIMEOUT_MS=600000`），注意只对新进程生效 | 来自 2026-08-18 生产复盘，本仓库无可复核测试 |
| 上游配额耗尽 | 事件流反复 `api_retry` / `api_retry_exhausted` 且含 `rate_limit` | 报告用户并暂停，切换上游/接口后重试；禁止无限重试 | broker 实现/测试依据（supervision 事件流含 `api_retry_exhausted`） |
| 多窗口消息串线 | 中断一条消息时它可能已送达另一窗口 | 中断前确认目标窗口；返修时先核对提交归属再决定 reset | 来自 2026-08-18 生产复盘，本仓库无可复核测试 |
| 闲置窗口悬挂 | supervisor 完成但长期不关闭 | 验收通过即 `close_supervisor`，摘要归档进 topic memory | broker 实现依据（`close_supervisor` 归档语义） |
| 工具可用性臆断 | 声明目录里没有某工具就断言"物理上无法使用" | 先做一次真实调用，失败再降级；目录清单不是可用性证据（热重载可能迟注册） | 来自 2026-08-18 生产复盘（热重载迟注册），本仓库无可复核测试 |
| 省略模型档位 | 省略 `model_policy`/`effort` 的派工被路由到前沿档，悄悄烧额度 | 派工单必填显式档位；环境默认值（如 `set_model_default`）只是局部补丁，不作约束 | 2026-08-18 生产复盘，本仓库无可复核测试 |
| worker 越权改 git 状态 | 受管 worker 切换分支或动暂存区，管理者的提交落到非预期分支 | 派工红线显式禁止一切 git 写操作（含 checkout/switch）；提交前必查 `git branch --show-current` 与 `git status`；提交后核对远端实际落点 | 2026-08-18 生产复盘，本仓库无可复核测试 |

## 输出契约

派工时输出派工单；验收时输出验收报告：

```text
派工单：目标 / 边界与红线 / 授权范围 / 所选路由与理由 / 产物落点 / 验收标准 / 回报方式
验收报告：结论（PASS | PARTIAL | BLOCKED）/ 执行机制 / 收据 / 状态 / 独立核验证据 / 偏差与风险 / 下一步
```

- **执行机制**：实际使用的路由（switchboard supervisor / switchboard 队列 / 原生 subagent / 直接执行）。
- **收据**：该路由的产物标识——supervisor 为 `supervisor_id` 与归档摘要；队列为 `request_id` 与终态；原生 subagent 为其返回的最终报告；直接执行为改动文件清单。
- **状态**：该路由的终态（closed/stopped、completed/error/cancelled、子代理已结算、验证已通过）。

向用户汇报时按路由分支给出状态，不凭空估计进度：

- switchboard supervisor 路径：活跃 supervisor 列表及其状态（来自 `list_managed_claude_supervisors` 或 wait 返回）。
- switchboard 队列路径：排队请求终态（`request_result` 收取结果）。
- 原生 subagent / 直接执行路径：supervisor=N/A、队列=N/A，汇报子代理结算结果或直接执行的验证输出，**不要编造 broker 侧对象**。

## 安全边界与非目标

- 不修改用户级或全局 MCP/模型配置，除非用户明确要求。
- 不使用 `send_to_claude_session` 前台控制他人终端，除非用户明确要求驱动人工会话。
- 不抓取私人会话内容；快照走 `request_context_snapshot` 的本地授权通道。
- 不代替执行者写项目代码；管理者的写入仅限 broker 记录（`record_work_memory` / `record_agent_event`）。

## 验证方式

交接或收尾前完成以下可观察检查，按实际路由选择适用项：

**通用（所有路由）**：

1. 验收报告中的每条证据都可复核（路径、行号、哈希、命令输出）。
2. 所选路由与理由已记录；发生降级时降级原因已记录。
3. 无悬挂执行者、无未收取的结果、无管理者越权写入项目文件。

**switchboard supervisor 路径**：

4. `initialize` + `tools/list` 能连上 broker（新环境先冒烟）。
5. 每个启动过的 supervisor 都有终态：closed / stopped，且有归档摘要。

**switchboard 队列路径**：

6. 每个排队请求都有终态：completed / error / cancelled，结果已用 `request_result` 收取。

**原生 subagent / 直接执行路径**：

7. supervisor=N/A、队列=N/A；核验子代理最终报告或直接执行的测试/校验输出，不要求 broker 侧对象。

相关示例见 `examples/`。
