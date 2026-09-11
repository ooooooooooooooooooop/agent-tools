# CASE 4: Personal AI Durable Execution & Event Plane Gap Audit (Architecture & Ambiguous Reasoning)

## 1. Case Metadata
- **Case ID**: `CASE_04_ARCHITECTURE_DURABLE_EXECUTION_GAP`
- **Archetype**: Architecture or ambiguous-problem reasoning
- **Historical Occurrence**: 2026-09-03 11:00:00+08:00
- **Primary Source Artifacts**: `docs/durable-execution-gap-report.md`, `docs/durable-execution-gap-index.json`

---

## 2. Frozen Case Input Packet (Pre-Cutoff Facts Only)

### USER_GOAL
"在过去数周的长任务推进中，反复出现任务停摆、中断后状态丢失、或者 Agent 报告完成但实际上根本没做完的问题。
我现在问系统一个最基本的问题：
'现在有哪些任务还没有完成？'
居然没有任何一个组件可以可靠回答！
请对 Personal AI 整个系统的持久化执行与事件平面进行一次系统性架构审计与根因解构：
1. 为什么我们的任务会丢？为什么 DSH 报 BLOCKED 但实际上底层任务还在跑？为什么 Agent 自言自语一个 PASS 就被当成完成？
2. 梳理当前所有组件（DSH goals, sessions, checkpoints, Switchboard state, background jobs, ResultEnvelope, Workspace provisioner）的职责冲突；
3. 严格在现有架构边界内思考：DSH 深度受管，Claude/Codex/Gemini 保持原生独立，Switchboard 保持可选工具，personal-ai-state 保持跨 harness 持久化；
4. 绝对不要为了'显得有办法'而拍脑袋发明一套庞大的常驻守护进程（Daemon）、中心化监控总线或外部重型框架；
5. 给出清晰的状态域解耦模型与权威审计报告。"

### TIME_CUTOFF
`2026-09-03T11:00:00+08:00`

### KNOWN_FACTS_AT_CUTOFF
1. DSH 会话内部有 `goal`（ACTIVE/BLOCKED/COMPLETE），但一旦会话关闭或轮次耗尽，该 goal 在跨会话视角下完全不可见。
2. Switchboard 有 `work_registry.py`，但在 DSH 原生运行或子代理运行中根本不被调用。
3. `dsh-jobs-local` 是纯内存 Map，DSH 一旦重启或崩溃，所有后台 job ID 全部丢失。
4. `checkpoints/` 目录下有历史 checkpoint 文件，但只有静态快照，没有实时的活动任务队列。
5. 多个历史会话（如 `session-d249ef16` 停滞、`session-e30ae08e` 误判阻塞）存在任务生命周期混淆的物理记录。

### AVAILABLE_EVIDENCE_AT_CUTOFF
1. `registry/` 下的各类治理协议与 schema。
2. `scripts/autonomy/checkpoint.py`、`scripts/structured_output/contract.py`、`scripts/workspace/provisioner.py`。
3. `mcp/agent-switchboard/` 源码（包括 `work_registry.py`, `managed_claude.py`）。
4. DSH 会话日志与存储目录结构（`~/.dsh/storages/`）。

### REPO_STATE
- 分支：`main`。
- 没有 `docs/durable-execution-gap-report.md` 或相关解耦报告。
- 系统各组件状态定义碎片化，各说各话。

### RUNTIME_STATE
- Windows 11 环境。
- DSH、Switchboard、Claude Code、Codex 并存。

### USER_CONSTRAINTS
1. **READ-ONLY INVESTIGATION**: 本任务是纯粹的架构分析与解构，**禁止在此阶段编写任何常驻守护进程、后台看门狗、或修改 upstream DSH 源码**。
2. **Preserve Native Independence**: Claude Code 和 Codex 必须能脱离 Switchboard / DSH 原生独立运行，不能被强制收编为必须经由中心调度器启动的从属 Worker。
3. **No Phantom Platforms**: 不允许建议用户引入 Celery、Temporal、Kafka 等外部庞大中间件。

### AVAILABLE_TOOLS
- File inspection: `read`, `glob`, `grep`
- Static analysis / search: `pwsh`
- Task state: `todo_write`

### KNOWN_DECISIONS
- 保持 personal-ai-state 作为跨 Harness 的文件系统 SSOT。

### KNOWN_UNCERTAINTIES
1. 究竟该如何从数学与架构上严格区分"编排轮次到了"和"任务真正失败了"？
2. 在没有中心数据库的前提下，如何回答"当前有哪些未完成任务"？

---

## 3. Acceptance Design

### DETERMINISTIC_ACCEPTANCE
1. **Zero Runtime Mutation**: 本次任务结束后，`git status` 显示生产代码（`scripts/`, `mcp/`, `registry/`, `dsh-config/`）无未经授权的实现性改动；未新增任何 background daemon。
2. **Exhaustive Component Inventory**: 审计覆盖至少 7 个关键持久化与任务相关组件（`personal-ai-state/checkpoints`, `DSH goals`, `DSH sessions`, `DSH background jobs`, `autonomous governor`, `Switchboard task state`, `ResultEnvelope`, `Workspace Provisioner`），并逐一分析其生存周期（Agent Exit, DSH Restart, OS Reboot）。
3. **Formal Decoupling Model**: 明确提出正交的四状态域模型（或等价清晰模型），彻底解耦编排生命周期（Orchestration）、业务任务生命周期（Job）、计算单元存活（Worker）与输出验证（Validation）。
4. **Definitive Answer to Core Question**: 明确、毫不含糊地回答"当前是否存在一个地方可以可靠回答'现在有哪些任务还没有完成？'"（答案必须基于事实证据，不能含糊其辞）。

### SEMANTIC_OUTCOME_ACCEPTANCE
- 深刻洞察并精准表述出当前 Personal AI 的核心结构性矛盾：把 Agent 对话编排的单轮终止（如 round limit）误判为业务任务的失败；以及 Agent 自评 PASS（self-certification）与独立校验的脱节。
- 架构设计方案完全符合“轻量、基于文件、无常驻守护进程、保持异构 Harness 原生独立”的用户边界。

---

## 4. Isolated Hidden Future (STRICTLY PROHIBITED FROM RUNTIME INPUT)

> **WARNING**: The contents of this section must NOT be leaked into the agent's context during evaluation runs.

### ACTUAL_ROOT_CAUSE / HISTORICAL RESOLUTION
- 历史产出物：`docs/durable-execution-gap-report.md` 和结构化索引 `docs/durable-execution-gap-index.json`。
- 核心模型：四状态域解耦（`ORCHESTRATION_STATE`, `JOB_STATE`, `WORKER_STATE`, `VALIDATION_STATE`）。
- 核心结论：`DURABLE_JOB_REGISTRY=MISSING`（明确回答：不存在这样一个可靠地方）。
- 历史方案提炼出基于文件系统的轻量级 Job Manifest 与 Claim/Heartbeat 契约，避免了重型后台常驻进程。
