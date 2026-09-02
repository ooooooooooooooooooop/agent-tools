# 用户全局偏好（DSH 每个对话都遵循）

以下是用户对所有 DSH 对话的全局偏好，适用于任意工作区、任意会话。比其更具体的项目级指令优先于此处，但除非用户明确推翻，否则默认遵守以下偏好。

## 1. 省 token（默认基调）

- 能小步完成的不要大动作：优先用最小可逆改动满足目标，不做多余的抽象、整文件重写或无关重构。
- 输出精炼：报告结论与关键证据，不堆砌冗余过程。
- 避免无效轮询/空转：不为了"表现积极"而反复检查尚无结果的状态。
- 使用低成本档位与缓存友好的推进方式（见第 3 条）。

## 2. 有反馈再盯 + 定时兜底监督（事件驱动优先，定时兜底）

本原则优先用「等信号」而不是「固定热轮询」，既省 token 又不丢任务。

- 反馈/事件驱动（默认方式）：当且仅当出现可回应的反馈、新结果或 `material` 事件时才跟进。
  - 后台 job 用阻塞式收结果（`job_output(wait:true)`，完成时系统自动通知），不空转。
  - switchboard 管理的监督器/长任务用「事件驱动长轮询」（如 `wait_supervisor_event` 等 material 事件：turn_completed / stall / 失败 / 退出），而不是无脑每分钟扫一次。
  - 任务主动给出完成/新反馈信号时恢复正常推进，并结束本次监督，停止空耗。
- 定时兜底监督（防止丢失）：仍在需要时挂一个看门狗节奏，但**到点才查、不到点不空转**。
  - 依任务时长定检查间隔（分钟级起，可用 `dsh-schedule` 定时器/hook）；查出卡死、进程退出、超时或长期无进展，立刻告警、重试或回滚，不让任务悄悄丢失。
  - 只对「没有事件通道可等」的裸任务才退化为定时轮询；有事件/信号的优先走事件驱动。
  - **每次启动监督时，必须在回复中明确展示「下一次检查触发时间」（具体到时分秒，含时区）**，让用户知道什么时候会再来查，而不是黑箱到点执行。
- 用看门狗与超时（TTL/`stall_timeout`/`max_autonomous_actions`）兜高风险与权威部分，廉价/并行部分仍按第 3 条路由。

## 3. 能交给 switchboard / 便宜模型的优先

- 凡是能通过 agent-switchboard 路由出去的执行类工作（跨模型调度、廉价劳动力、批量小任务），优先路由到便宜/快的模型档（如 luna/cheap 档、gemini flash），省 token 且不占本对话上下文。
- 决定性、高风险或需要权威判断的部分仍由本对话/权威模型处理；廉价档只在明确的、可复核的低风险包上使用。
- 重活/可并行拆分的用 switchboard 或子代理并行推进，不独占本上下文。

### 搜索通道（重要，每个对话默认遵守）

- DSH 内置 `web_search` 工具无可用 API key，调用会报错浪费。需要检索最新信息时**不要调用内置 `web_search`**，改走 agent-switchboard 委派：用 `queue_cli_request` / `consult_*` 路由到有联网能力的后端（如 `codex_cli` + `gpt-5.6-luna` 便宜档），极窄探针 + 结构化回收，代价与第 3 条同档。

## 4. 网络资源获取偏好

- 能从国内网站/国内源直接获取的资源（软件包、文档、镜像、依赖等），尽量少用或不用代理，优先走国内直连源。
- 仅当资源在国内无法获取、访问过慢或失败时，才考虑代理/镜像兜底；能用国内替代源（如镜像站）解决的，优先用国内源。
- 涉及网络请求/下载时，先判断资源是否可从国内渠道获得，再决定是否走代理，避免不必要的代理开销。

## 5. 遇阻自救三连（默认行为，禁止直接记录阻塞）

遇到问题、失败、门禁拒绝或"似乎无法继续"时，**默认按以下顺序自动推进，不向用户抛回、不记录阻塞、不添加人工环节**：

1. **检查设计有没有问题**：先追代码/配置/流程本身——找入口、查门槛、查终态，用证据判定是"实现问题"还是"机制缺口"（grep 调用链、读源码、跑最小复现）。
2. **联网搜索解决方案**：内置 `web_search` 不可用时按已探测通道走 CLI worker（`queue_cli_request`，显式 `target_model` + `effort`，极窄探针，结构化回收），对照业界做法。
3. **真正推进问题**：把诊断变成系统层改动（代码/机制/门禁）+ 回归测试 + 验证生效，而不是写分析报告或加文档建议。

执行规则：
- 三连全部做完且确认无路可走（无可用工具、无替代路径、无新增证据、预算耗尽）之前，**禁止**使用 `update_goal action=blocked` 或报告 BLOCKED；
- "难度大 / 不确定 / 还有可用工作"不是阻塞理由；
- 方案不依赖人工确认/人工介入：能落到系统层（代码强制、无法绕过）就不写"应当"类建议；需要人工的只有确实不可自行裁决的方向分歧（一次问完）。

<!-- aic:continuous-capability-adoption:begin sha256=6450caf9e8571afbb484771ee6eca06b6e5e8857818d715ef9947cac9cb846db -->
## CONTINUOUS_CAPABILITY_ADOPTION

- **Policy version**: 1
- **Owner**: Personal AI
- **Review cadence**: weekly discovery/evaluation + event-driven review after material Harness/AI-infrastructure changes
- **Scope**: Harness native capabilities; Agent/Subagent/Workflow orchestration; model invocation and routing; Memory/Long-term Memory; context management/compaction; MCP/Plugin/Tool ecosystems; Browser/Computer Use; multi-agent collaboration; evaluation/observability/provenance; reliability, safety, and cost optimization; and future equivalent foundational AI capabilities.

Personal AI must continuously discover and evaluate mature upstream-native capabilities that may solve real current problems or improve capability, efficiency, quality, stability, safety, auditability, or cost. Prefer established upstream capabilities from DSH, Codex, Claude, Gemini, and other formal dependencies over duplicating equivalent functionality inside Personal AI, unless a documented gap remains.

Discovery is not adoption. A candidate must have explicit user or governance value, evidence, compatibility and cost/risk assessment, and a reversible verification path before admission. “New” alone is never an adoption reason; a currently working system is never a reason to freeze permanently on an obsolete capability set.

After approval, adoption must use the existing capability inventory/registry, AIC render/apply/diff path, deployment/recovery path, drift/governance checks, provenance, and necessary compatibility tests. Discovery output stays generated/proposal-only and must not mutate canonical registries or routing automatically. The target state is continuous use of the best mature and valuable AI/Harness ecosystem capabilities while remaining stable, compatible, rollbackable, and auditable.
<!-- aic:continuous-capability-adoption:end -->

<!-- aic:autonomous-execution-governance:begin sha256=de5e44d15c5eee9c99c539850f2c299f6f4b4fed1b21ebdb289bf0195a63346d -->
## AUTONOMOUS_EXECUTION_GOVERNANCE (PERSONAL AI · generated — do not edit)

本块由 Personal AI canonical SSOT 生成（registry/autonomous-execution-governance.yaml
+ execution-profiles.yaml + checkpoint-schema.yaml + usage-ledger-schema.yaml），
由 `aic render/diff` 管理；手改即 drift，会被 `aic diff` 检出并回滚。

- 项目（novel 等）不得自带 autonomous governance 实现；**用户无需声明或手写
  execution_profile**——任务的 profile 由 Personal AI 自动 admission（分类依据行为特征：
  批量/重复、campaign、研究、推理关键性、交互），项目只消费 admission 结果。
- 硬规则（稳定）：
  1. 无界自主执行被禁止：任何自主运行必须处于一个 execution_profile，预算 hard
     enforceable（Harness hook 层 fail closed），超限保存 durable checkpoint
     （stop_reason=BUDGET_LIMIT），并计入 Usage Ledger。
  2. 预算数字来自 Personal AI canonical（execution-profiles.yaml）；项目不得复制或
     修改预算，agent 禁止自动提高自身预算（唯一路径 = 人工批准 + ledger 豁免记录）。
  3. 重复结构化工作（bulk / 批处理）默认走 batch：manifest → workers → result
     artifacts → deterministic aggregate；BULK 类 profile 中主 Agent 禁止逐 item
     reasoning。
  4. 昂贵模型（FRONTIER_REASONING tier）禁止被硬编码为默认 bulk worker；模型选择
     由 Personal AI Model/Routing SSOT（registry/models.yaml + routing-policy.yaml）
     决定。
  5. 长时间任务必须 CHECKPOINT → COMPACT → RESUME；Checkpoint 写入 Personal AI
     durable state（personal-ai-state/checkpoints/），resume 读 checkpoint 而非原
     conversation；cache-read token 计入预算，超限即触发 COMPACT。
  6. 无进展循环（PROGRESS_DELTA = 0 持续 N 轮；重复相同工具调用 / 错误 / 修复 /
     judge / provider probe）自动 circuit break，stop_reason=LOOP_BREAKER 落
     checkpoint；repeated repair 有界（≤3）。
  7. 所有模型调用计入 Usage Ledger（task/project/harness/model/calls/input/
     cached/output/cost/progress），支持 COST_PER_PROGRESS 与 runaway 检测。
  8. **profile admission 自动且先于首次昂贵调用**：UNKNOWN ≠ UNBOUNDED——无法分类的
     autonomous task 进入安全默认 AUTONOMOUS_STANDARD，绝不落入无约束观察模式；
     profile widening 受 canonical 规则约束（evidences + reason + receipt，禁止
     Agent 自行选更宽松 profile；不重置累计 usage、不绕过 hard cap）。
- Policy pointer（本机全文）：agent-tools 仓库 registry/autonomous-execution-governance.yaml
  （含 harness_hook_matrix）、registry/execution-profiles.yaml、registry/checkpoint-schema.yaml、
  registry/usage-ledger-schema.yaml。
<!-- aic:autonomous-execution-governance:end -->
