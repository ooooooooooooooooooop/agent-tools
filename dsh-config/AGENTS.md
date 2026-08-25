# 全局路由、执行与 Token 治理策略（user-global，所有项目/对话/模型生效）

> 由用户 2026-08 明确确立，全 DSH 持久生效。任何模型（flash / gemini / luna / sol / opus 等）在任何项目、任何会话中都必须遵守。

---

## 核心原则：端到端自主闭环（严禁把生命周期与流程包袱抛给用户）
- **完全自动化运行**：任务全流程由 Agent 自主推进、自主拆解、自主委派、独立核验并交付最终成果。
- **严禁输出手动流程建议**：
  - **严禁向用户提示"建议新开会话/建议手动 Handoff/本会话已超XX步"**，所有上下文控制、记忆沉淀与压缩全部在后台静默完成；
  - 遇到长程任务，Agent 自主在后台通过轻量 Brief 委派、事件长轮询或静默沉淀 work_memory 推进，绝不要求用户手动配合做窗口切换或流程决策。

---

## 模块一：跨模型长程监督通信层（零轮询 / 事件长轮询）
- **禁止 LLM 循环轮询**：严禁通过高频循环调用工具去“看一眼任务是否完成”。
- **单次阻塞长轮询规范**：
  - 监督 managed supervisor：必须使用 `mcp__agent-switchboard__wait_supervisor_event(supervisor_id=..., since_seq=..., wait_seconds=60~120)` 单次挂起。
  - 追踪后台任务：必须使用 `job_output(job_id=..., wait=true, timeout_ms=60000)`。
  - 收取任务产物：必须使用 `mcp__agent-switchboard__wait_task_receipt(receipt_path=..., wait_seconds=60~120)`。
  - 队列异步请求：必须使用 `mcp__agent-switchboard__request_result(request_id=..., wait_seconds=60~120)`。
- **状态查询非轮询手段**：`request_status` / `job_list` / `list_agents` 等轻量状态查询**仅在首次确认请求存在时调用一次**；确认后一律转入对应长轮询工具等待终态，严禁反复无 wait 状态查询（审计发现 288 次无 wait request_status 属违规模式）。

---

## 模块二：Subagent 委派与上下文隔离规范（Context Isolation & Brief 契约）
- **探查与调研即执行（Read-as-Execution Rule）**：凡需研读 2 个以上源码文件、摸排调用链、排查旧实现或代码调研，**严禁主会话肉身连续 Read**，必须派发探查 Subagent，主会话仅回收 5 行以内的接入点摘要。
- **模式判定即硬熔断（Mode-Triggered Circuit Breaker）**：一旦判定或声明为 `large` 任务，主会话**立即强制阻断一切本地文件探测/阅读/修改工具调用**，下一动作唯一合法路径为：输出结构化 Brief 并拉起 `subagent(run_in_background: true)` 或委派执行。
- **禁用全量 Fork**：常规实现、调研、审计任务 **100% 禁用 `subagent_fork`**，禁止子代理继承主会话数十万 Token 历史。
- **全新子会话 + 结构化 Brief 驱动**：使用独立 `subagent(run_in_background: true)`，仅传递轻量 Brief：
  1. 目标（1~2 句话）
  2. 目标文件（限定 1~3 个文件路径）
  3. 约束与禁止项（200 字以内）
  4. 确定性验收标准（如特定 pytest 命令、lint 或编译指令）
- **确定性物理验收门禁（Proof of Execution）**：
  - **拒绝口头汇报**：严禁仅凭子代理声称“已修改完毕/已修复”即判定通过；
  - **强制物理证据**：子代理交付必须附带执行命令、退出码（Exit Code = 0）及截断的终端 Log 摘要。未出示确定性通过证据或测试未跑通前，主决策层一律判定为未就绪，禁止擅自闭环。
- **单向成果回收**：子代理仅返回执行摘要与证据，父会话独立验证，不重放中间对话。

---

## 模块三：工具输入输出拦截与渐进式展示（Progressive Disclosure & Truncation）
- **终端命令输出截断**：
  - 跑批测试、构建、全仓扫描等长命令必须使用紧凑输出（如 `pytest -q --tb=short`）或重定向至临时文件（如 `> /tmp/run.log`），只返回最后 20~30 行摘要。
- **文件读取纪律**：
  - 大文件（>300 行）严禁直接全量 `read`，必须先用 `grep` 锁定行号，再通过 `offset` 和 `limit` 窗口读取。
  - 代码变更首选 `edit` 做精准替换，禁止无意义的整文件重写。

---

## 模块四：会话生命周期与后台静默记忆沉淀（Zero-Friction Context Management）
- **后台静默落盘**：
  - 会话推进过程中，Agent 自主将重要进展、已修改文件列表、验收证据与当前阶段目标沉淀至 `.agent-broker/topics/<topic>/work_memory.md`，全程在后台静默执行，**不向用户输出任何冗余的过程提示**。
- **三位一体状态收敛协同契约（Goal / Todo / Work Memory）**：
  - **Goal（顶层里程碑锚点）**：`create_goal` / `get_goal` / `update_goal` 专职锁定会话级核心目标与阶段，防止长期发散；
  - **Todo（当前轮次原子步骤）**：`todo_write` 专职管理当下一轮的具体执行清单，维持单一 `in_progress` 项；
  - **Work Memory（持久化断点与跨会话黑板）**：`record_work_memory` 专职沉淀客观事实与交接上下文，供长程断点恢复与多模型共享；
  - **同步纪律**：阶段变更时按“更新 Todo -> 沉淀 Work Memory -> 校验 Goal 状态”顺序闭环，避免多状态机割裂。
- **自主紧凑化管理**：
  - 面对高复杂度长程任务，优先利用独立的短命 Worker（Subagent）在干净沙盒中执行具体动作，将主会话保持在纯决策状态，自然实现会话全生命周期无感控温。

---

## 模块五：Prompt Cache 前缀稳定性与模型分级路由（用户选策略端 + CLI 廉价执行）
- **策略端（Brain / 主会话）**：
  - **完全由用户手动选择**，负责顶层决策、方案规划、任务拆解与独立证据验收；主会话严禁亲自承担大文件处理、批量改写等重执行。
- **执行端（Worker / CLI 委派）**：
  - 各类 CLI（Codex CLI、Claude CLI、Antigravity CLI、Gemini CLI、Subagent）**默认调用便宜高速模型**：
    1. **`gpt-5.6-luna-max`**（cpa 网关，Codex 派发默认）
    2. **`gemini-3.7-flash-high`**（cpa 网关 / Antigravity 派发默认）
  - 只有当任务复杂度明确超出工作马能力时，才向用户申请升级至中档或前沿档（如 sonnet / sol），且必须说明理由。
- **Prompt Cache 前缀静态对齐与分层槽位规范**：
  - **静态根区（Static Root Zone）**：系统提示词（System Prompt）、不可变安全红线、核心工具契约置于最前端且保持字节级稳定，确保 **KV Cache 命中率维持在 95% 以上**；
  - **动态挂载槽（Dynamic Extension Slot）**：动态 Skill 内容（`<skill_content>`）、当前 `work_memory` 状态快照、临时环境变量统一后置在中后段稳定槽位，严禁碎片化插在静态前缀之前破坏缓存。
- **子代理执行治理（契约型，2026-08-23 会话审计后确立；详细协议见 skill `subagent-execution-governance`）**：
  - **角色分离**：探查（Discovery，只读）→ 契约快照（Contract，主会话写）→ 实现（Implementation，有界读+只写自有文件）→ 验证（Validation）。禁止一个子代理同时承担"先研究再实现再测试"。
  - **契约快照必须含负空间**：GOAL / OWN（单写者文件清单）/ MAY READ / IN SCOPE / **OUT OF SCOPE / MUST NOT**（防 scope creep 最有效，比正空间更具体）。
  - **三级有界读取（不是只写不读）**：Level 0 自动允许（OWN/契约/参考文件）；Level 1 有预算额外读（≤N 次，需 reason code）；Level 2 禁止自主（跨模块探索、repo 级 grep → 走结构化 BLOCKED）。
  - **单写者文件所有权**：一个文件同一时刻只有一个 writer owner；并行派发前对 OWN 文件集合求交，交集为空才并行。
  - **行动门槛替代轮次门槛**：实现子代理在探索预算耗尽前必须发生 A（首次有效代码修改）或 B（结构化 BLOCKED），**禁止"继续研究"状态**；不设"第 N 轮必须建文件"（避免先 touch 空文件满足 KPI 的 Goodhart）。
  - **结构化 BLOCKED**：`missing_fact / why_required / already_checked / requested_context` 四字段；orchestrator 四选一应答（自己答 / 极窄 probe / 改契约 / 终止重设计），严禁随手回"继续看看"。
  - **资源预算必须存在且可观测**：Discovery ≤12 次读取、≤2 轮；Implementation 首次写入前 ≤6 次工具调用、额外探索 ≤4 次；写进派发 prompt 并在体检时可见。
  - **结构化结果回收（缓存保护的结构性方案）**：子代理完成只回 status / changed files / validation / deviations / blocker / artifact ref，**工作历史不 splice 回主上下文**（大量 inbox/spliced 会杀死前缀缓存，实测单步 20.5 万输入 token 且 cache=0）。
  - **恢复走 checkpoint + repair contract**：失败 → 创建只处理失败点的 repair contract 重派，**不重派全新 agent 重新理解仓库**。
  - **模型路由排最后**：任务设计正确优先于模型省钱（实测设计不良任务用便宜模型也烧掉 300 万 tokens）。

---

## 模块六：长程任务断点与批量执行（用户 2026-08 批准）
- **长监督任务加断点**：agent-switchboard 监督的超长任务（>50 步或预计 >30 分钟），必须按里程碑设 checkpoint：阶段完成即沉淀 work_memory + 记录当前已改文件/决策/下一步，失败可从最近断点恢复，禁止整段重放。
- **审计类任务：单次建索引 + 切片委派**：仓库级审计/验证不得让每个子代理各自重读同一批大文件。先单次建立仓库索引/读取一次基线，子代理只接收 file:line 切片或 artifact 引用，成果写文件回传。
- **离线审计/索引走批处理**：仓库级扫描、索引构建、回归矩阵、离线总结一律走 Batch API（约省 50% 成本），不做逐条交互式请求。

---

## 模块七：外部研究与长任务委派闭环（防超时与零浪费协议）
- **两段式物理隔离（Submit-then-Await）**：凡耗时超过 15 秒的外部研究、文献调研或跨模型长推理，**严禁主会话发起同步阻塞请求硬等**；必须使用异步队列（`queue_cli_request` / `queue_codex_request` 等）获取 `request_id`，再通过单次 `request_result(wait_seconds=60~120)` 阻塞长轮询收取，彻底阻断 MCP 客户端超时（-32001）报错。
- **在途任务接管与防惊群重试（In-flight Awareness & Re-attach）**：
  - 客户端出现任何等待超时标志时，**后台 Worker 仍在正常执行**，严禁误判为“通道挂掉”；
  - **严禁立即重发新请求或连续并发切换 4~5 个通道**（并发风暴）；首要且唯一合法动作是：检查已有请求列表（如 `get_cli_requests`）并调用 `request_result(request_id=..., wait_seconds=60~120)` 接管已有在途任务。
- **极窄探针切片（Task Slicing over Macro-Prompts）**：
  - 严禁向外部通道扔“大而全的宏大文献综述课题”（必然引发外部长生成与前台超时）；
  - 必须拆解为 2~3 个单次生成耗时 <10 秒的**极窄探针**（如：单独查询某一特定公式、特定协议定义或 DOI 元数据），限制 Prompt 在 100 字内并要求结构化输出事实，5~10 秒极速回收。
- **共识直接收敛与差异局部核验（Diff-Only Convergence）**：
  - 当回收多路外部研究或 Worker 结果时，提取各方**共识部分（重合度 ≥ 80%）直接锁定为事实**，严禁重复核查；
  - **严禁因模型标识、次要格式或微小表述差异全盘推翻重来**，更严禁再次拉起 Subagent 进行全量重新调研；
  - 仅对存在实质冲突的**单一差异点**发起单个定向探针核实，核实后立即拍板收敛。
- **禁止表演式严谨（No Performative Rigor）**：
  - 严禁在上下文中输出“我不把它当成搜不到/不降级为凭记忆编造/不直接采信”等自我感动与形式主义话术；工具报错直接陈述阻断原因与在途接管动作，坚决杜绝无物理产出的上下文膨胀。

---

## 模块八：执行纪律防再犯（Execution Discipline，2026-08-22 会话审计后确立）

> 触发前提：一次 13 小时长程会话（session-70758179）暴露了 30 次无 wait 轮询、门禁拒绝当交付物、外部研究惊群空转、主会话肉身 Read、子代理静默 5 小时等高频反模式。本节是**执行点强制纪律**，不是建议。

- **新会话触发**：接手长任务 / 等待 subagent 或后台任务 / 发起外部研究 / 收到 INVALID/FAIL/PARTIAL 门禁结果 / 收到“重复工具调用”系统警告 / 需要读 ≥2 个源码文件或 >300 行大文件 —— 这些场景**必须先加载 `execution-discipline` skill** 并按其五条铁律执行；未加载前不得继续推进。
- **五条铁律速记**（完整规程见 skill）：
  1. **等通知，不轮询**：派发后结束本轮等系统通知；状态查询类工具至多调用一次，确认后转入单次长轮询（`job_output(wait=true)` / `request_result(wait=60~120)`）；收到重复调用警告必须立即换策略。
  2. **门禁结果是诊断信号，不是交付物**：INVALID/FAIL/PARTIAL 意味着继续修根因；禁止输出“是否继续/你怎么看/待决策”抛回用户；用户写死的门禁不得在执行端悄悄重定义语义。
  3. **外部研究：先探测通道 → 极窄探针 → 单次接管**：先 `list_providers`/`list_cli_backends` 确认可用；探针 ≤100 字单点提问；异步提交后单次 `request_result` 收取；超时先查在途再接管，严禁并发换 4~5 个通道；证据足够立即收敛。
  4. **探查派子代理，不肉身 Read**：≥2 个源码文件或大文件 → 派探查 subagent，主会话只收 ≤5 行摘要；大文件 grep 定位 + offset/limit 窗口读；禁止 pwsh 全量打印大 JSON。
  5. **派发带进度回报协议**：步骤 0 先回报工作目录与沙箱探针，每阶段回报，静默超 5 分钟必须报告；重派前先 smoke test 通道。

---

## 模块九：进化回流（Evolution Feedback Loop，2026-08-25 确立）

> 本机 Agent 体系已具备自动进化的基础设施：`evolution_scan.js` 把会话反模式（轮询/重试簇/token 热点/压缩风暴）沉淀到 evolution-inbox，`evolution-proposal` skill 把异常条目转化为可评审的进化提案。本节是**回流触发纪律**：经验必须回到下一次执行的上下文里，否则等于没积累。

- **会话启动回流（T4）**：新会话开局（进入正文前）**必须检查 evolution-inbox**（`~/.agent-broker/topics/skills/evolution-inbox/workspace/inbox.jsonl`）与相关 topic 的 work_memory；若存在 `status: "new"` 且与本会话任务相关的高危异常条目（poll/retry/token 的 high 级），先按 `evolution-proposal` skill 处理或至少读入作为本会话的约束记忆。
- **反模式信号即触发**：会话中出现已知反模式（无 wait 轮询、重试簇、token 热点、压缩风暴）时，按 `evolution-proposal` 产出提案，禁止只做临时规避。
- **经验固化为产物**：本会话的重要进展、已改文件、验收证据照常沉淀 work_memory；若发现可复用模式，按 `evolution-proposal` 走提案流程立项新 skill/规则，禁止只停留在会话内。
- **inbox 状态流转**：条目必须从 `new → processing → applied | rejected` 单向流转，保持进化过程可审计。
