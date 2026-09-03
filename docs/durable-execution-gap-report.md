# Personal AI Durable Execution & Event Plane Gap Audit Report

**Audit Identifier:** `PERSONAL_AI_DURABLE_EXECUTION_GAP_AUDIT`
**Audit Date:** 2026-09-03
**Mode:** READ-ONLY INVESTIGATION & MODELING (NO IMPLEMENTATION PERFORMED)
**Strict Policy:** No new watchdogs, supervisors, event buses, daemons, or runtime modifications.

---

## 0. 架构边界与基准原则 (Architecture Boundaries)

本审计严格在当前已冻结的 Personal AI 架构边界内开展：
1. **Personal AI 深度受管 (Deep-Managed)**：仅限 **DSH** (`@deepseek-ai/dsh` + AIC 托管 composition)。
2. **Claude Code / Codex / Gemini**：保持 **NATIVE_INDEPENDENT**，原生独立可运行，绝不强制依赖 Durable Execution Plane 启动或工作。
3. **Agent Switchboard**：保持 **OPTIONAL_TOOL**，绝不重新变为中心强制 Supervisor。
4. **personal-ai-state**：保持 **HARNESS_INDEPENDENT** 的持久化数据源。

---

## 1. 四状态域解耦与现状映射 (Orthogonal State Domains)

当前系统最核心的语义混淆在于将编排、任务、执行者与验收状态混为一谈（例如将 DSH `goal.status` 或 `Stop` hook 简单视为任务的最终结果）。

| 状态域 | 语义职责 | 理想状态取值 | 当前实现现状 | 存在的问题与状态冲突 (State Collisions) |
| :--- | :--- | :--- | :--- | :--- |
| **`ORCHESTRATION_STATE`** | Agent 对话/编排生命周期 | `RUNNING`, `WAITING`, `ROUND_LIMIT`, `ENDED`, `BLOCKED` | **已实现**：DSH `dsh-agent`、Cordis Fiber 状态、`goal-round-driver`（轮次计数）、`autonomous-execution-governor`（`agent_turns` 硬限制）。 | **与业务任务生命周期严重混淆**：当 Agent 达到轮次上限（`ROUND_LIMIT`）停止时，系统往往误将整个业务 Job 判定为 `BLOCKED` 或 `FAILED`，即使后台 Worker 仍在健康运行。 |
| **`JOB_STATE`** | 真正业务任务的生命周期 | `PENDING`, `READY`, `RUNNING`, `CHECKPOINTED`, `WAITING_EVENT`, `RECOVERING`, `COMPLETED`, `FAILED`, `CANCELLED` | **未解耦 / 碎片化**：DSH 内仅有 `dsh-goal` 的粗粒度 `ACTIVE/BLOCKED/COMPLETE`；Switchboard 内有 `work_registry.py` 的 `SPAWNING/ACTIVE/COMPLETED/FAILED`；Checkpoint 中仅有 `stop_reason`。 | **持久化 Registry 缺失**：没有统一地方能够跨 Harness 记录一个 Job 是否处于 `CHECKPOINTED`、`WAITING_EVENT` 或 `RECOVERING`。 |
| **`WORKER_STATE`** | 实际计算/执行单元的存活与退出 | `STARTING`, `ALIVE`, `HEARTBEAT_LOST`, `EXITED_0`, `EXITED_ERROR`, `KILLED`, `UNKNOWN` | **内存态局部实现**：DSH `dsh-jobs-local` 维护 `running/exitCode/signal`（纯内存 Map）；Switchboard `managed_claude.py` 跟踪子进程 PID。 | **非持久化且无心跳**：进程随 DSH 或终端退出而丢失；没有 worker 向 supervisor 报告存活的心跳，导致依赖人工轮询 PID。 |
| **`VALIDATION_STATE`** | 业务输出是否经独立校验放行 | `NOT_STARTED`, `RUNNING`, `PASS`, `REVIEW_REQUIRED`, `FAIL` | **局部规范**：`scripts/structured_output/contract.py` 定义了 `ValidationOutcome` 与 `ExecutionStatus`；`scripts/structured_output/validator.py` 执行校验。 | **自证风险 (Self-Certification)**：许多任务中 Agent 在自己生成的文本中自述 `PASS`，未经独立检验器重算即被上层当成 `COMPLETED`。 |

```text
ORCHESTRATION_STATE_IMPLEMENTATION=DSH (dsh-agent, cordis fiber, goal-round-driver, autonomous-execution-governor)
JOB_STATE_IMPLEMENTATION=FRAGMENTED (dsh-goal status vs work_registry vs checkpoint stop_reason)
WORKER_STATE_IMPLEMENTATION=MEMORY_ONLY (dsh-jobs-local Map & managed_claude PID list)
VALIDATION_STATE_IMPLEMENTATION=PARTIAL (scripts/structured_output/validator.py ValidationRecord)
STATE_COLLISIONS=HIGH (Orchestration round limit misjudged as Job blocked/failed; self-certified PASS treated as validated job completion)
```

---

## 2. Durable Job Registry 现状盘点 (Job Registry Inventory)

对当前代码库及运行环境中所有承担或疑似承担 Job Registry 职责的组件进行全景审计：

| 组件 / 存储 | 存在 Job ID? | 是否 Durable? | 存活: Agent Exit | 存活: DSH Restart | 存活: OS Reboot | 可发现未完成任务? | 支持恢复 (Resume)? | 支持取消 (Cancel)? | 支持独立验证? | 所有权 (Owner) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`personal-ai-state/checkpoints`** | 是 (`task_id`) | **是** (Git JSON) | **是** | **是** | **是** | **否**（仅存快照，无实时活动队列） | **是** (`checkpoint.py resume`) | 否 | 是 (`validate`) | `scripts/autonomy/checkpoint.py` |
| **`DSH goals`** | 是 (`GoalId`) | **否** (Session 内部折叠) | 否 | 否 | 否 | **否** (跨会话不可见) | 否 | 是 (`pause/block`) | 否 | `@deepseek-ai/dsh-goal` |
| **`DSH sessions`** | 是 (`sessionId`) | **是** (`.jsonl.zstd`) | **是** | **是** | **是** | **否** (线性对话流，非任务表) | 仅能回放 | 否 | 否 | `dsh-session-persistence-jsonl` |
| **`DSH background jobs`** | 是 (`jobId`) | **否** (内存 Map) | 否 (随父 Agent 释放) | 否 | 否 | **否** (DSH 重启全部清空) | 否 | 是 (`job_kill`) | 否 | `@deepseek-ai/dsh-jobs-local` |
| **`autonomous governor`** | 是 (`taskId`) | 部分 (本地 JSON 审计) | 是 | 是 | 是 | **否** (仅为配额守卫，不调度) | 否 | 否 (只能熔断) | 否 | `autonomous-execution-governor` |
| **`Switchboard task state`** | 是 (`work_key`) | 部分 (磁盘 JSON/lock) | 是 | 是 | 是 | **否** (仅按 key 查询去重) | 否 (仅支持重试) | 否 | 否 | `mcp/agent-switchboard/work_registry.py` |
| **`ResultEnvelope`** | 是 (`task_id`) | **是** (输出 JSON 文件) | **是** | **是** | **是** | **否** (仅是终态结果载荷) | 否 | 否 | 是 (`validator.py`) | `scripts/structured_output/contract.py` |
| **`Workspace Provisioner`** | 是 (`task_id`) | **是** (`worktrees.json`) | **是** | **是** | **是** | **部分** (可发现孤立 worktree) | 否 (不可自动继续) | 是 (`cleanup`) | 否 | `scripts/workspace/provisioner.py` |
| **`Governance jsonl`** | 仅关联 | **是** (追加日志) | **是** | **是** | **是** | **否** (不可作为索引查询) | 否 | 否 | 否 | `scripts/governance/runner_adapter.py` |

### 核心问题研判：
> **“当前是否存在一个地方可以可靠回答：‘现在有哪些任务还没有完成？’”**
>
> **回答：不存在。**
>
> - `dsh-jobs-local` 是纯内存字典，DSH 一重启直接归零；
> - `checkpoints/` 仅在显式调用 save 或 Stop hook 时留下静态文件，若任务正在执行中或异常退出，没有 active 状态登记；
> - `worktrees.json` 仅记录 Git 目录层，不代表任务业务层；
> - `Switchboard` 仅作为可选 MCP 工具在部分场景被调用，且非统一入口。

```text
DURABLE_JOB_REGISTRY=MISSING
```

---

## 3. 任务标识体系审计 (Job Identity Hierarchy)

当前系统中散落了多种 ID，彼此边界与生命周期不一致：
- `goal_id`: DSH 会话内目标标识，会话结束即消亡。
- `session_id`: 对话线程 ID。一旦上下文溢出（345k context）或重启，DSH 会生成新 `session_id`。
- `agent_id` / `worker_id`: 执行线程/子进程 PID，为瞬态运行时实体。
- `task_id`: 在 `checkpoint.py`、`ResultEnvelope`、`provisioner.py` 中出现，作为字符串透传。
- `execution_id` / `attempt_id`: **无独立实体**，仅在局部代码中作为计数器（`resume_count: int` 或 `retry_count: int`）存在。

### 缺陷与风险：
当一个长任务需要重启或换模型重试时：
- 系统无法以稳定的 `JOB_ID` 挂接 `ATTEMPT_1`、`ATTEMPT_2`、`ATTEMPT_3`；
- 每次换会话重启，都被底层视为“完全另外一个全新的 Session”，导致历史上下文割裂、重试计数无法原子累计、产生并发冲突。

```text
CURRENT_IDENTITIES=goal_id, session_id, agent_id, worker_id, task_id, retry_count, resume_count
JOB_ID_EXISTS=PARTIAL (ad-hoc string in envelopes/checkpoints; no first-class lifecycle entity)
ATTEMPT_ID_EXISTS=NO (only integer counters, no structured attempt identity)
IDENTITY_COLLISIONS=YES (Session restart creates new session_id, leading to state fragmentation and lost lineage)
```

---

## 4. 单一写入者与租约保障 (Single Writer / Lease Enforcement)

所谓“不要启动第二个 scanner / 避免双重写冲突”，当前系统到底由什么保障？

### 审计发现：
1. **OS / 文件锁**：
   - DSH 本身对工作区文件、`session.jsonl.zstd` **没有任何操作系统文件锁**。
   - Switchboard 的 `work_registry.py` 内部实现了 `.lock` 文件，但由于 Switchboard 是 OPTIONAL 工具，原生 Claude Code、Codex、Gemini 和原生 DSH 均绕过该机制。
2. **当前真实保障机制**：
   - **完全依赖自然语言提示词与政策文本 (POLICY_ONLY)**！
   - 例如在 `AGENTS.md`、`README` 中要求：“运行前先用 Get-Process 检查是否存在 scanner 进程”。
   - 当多个 Agent、子代理或并行会话同时触发同一任务时，底层文件系统没有任何机制能够阻止第二个 Agent 打开文件进行破坏性写入。

```text
SINGLE_WRITER_ENFORCEMENT=POLICY_ONLY
```

---

## 5. 租约生命周期与脑裂分析 (Lease Lifecycle & Split-Brain Risk)

1. **现有机制**：仅在 `mcp/agent-switchboard/work_registry.py` 中存在雏形（`WorkLease`: `work_key`, `expires_at`, `state`）。
2. **回收与超时机制**：
   - 当 Worker 崩溃时，租约仅在内存/文件里标记过期时间戳（默认 300s）。
   - 没有主动回收机制；只有当下一个相同 `work_key` 的请求到来时，才被动检查 `is_expired()`。
3. **脑裂风险 (Split-Brain Risk)**：
   - **HIGH**。若前一个 Worker 因 GC 停顿、磁盘 I/O 慢或大模型推理缓慢而暂时无输出，超过 300s 租约过期后，新 Agent 抢占第二租约启动并覆写同一工作区文件；当前一个 Worker 唤醒写盘时，将造成灾难性的并发覆盖。

```text
LEASE_IMPLEMENTATION=PARTIAL_SWITCHBOARD_ONLY (mcp/agent-switchboard/work_registry.py)
LEASE_EXPIRY=PASSIVE_TIMESTAMP_CHECK (300s default TTL, checked on next incoming request)
LEASE_RECOVERY=PASSIVE (no active supervisor sweeps dead worker leases)
SPLIT_BRAIN_RISK=HIGH (slow worker + lease expiration + second worker spawn => concurrent workspace mutation)
```

---

## 6. 心跳机制审计 (Heartbeat & Liveness)

1. **真实心跳通道**：
   - **不存在**。没有 Worker -> Periodic Heartbeat -> Durable Supervisor State 的标准协议。
2. **现状替代方式（人工心跳）**：
   - Agent 在长任务执行中，反复发起 Bash/Pwsh 工具调用，通过 `ps`、`Get-Process` 查 PID，或用 `Get-ChildItem` 查看输出文件修改时间，或不断 grep 日志。
   - 这种方式消耗了大量的 Agent 对话轮次和 Token（详见第 11 节），且极易误判（例如 Windows 下 PID 复用、或者 Worker 正在高负载计算而未刷新文件时间）。

```text
HEARTBEAT_SOURCE=NONE (relies on agent manual polling via Get-Process/file-timestamp)
FREQUENCY=NONE
DURABLE=false
LAST_SEEN=NONE
STALE_THRESHOLD=NONE
FALSE_POSITIVE_RISK=HIGH (PID reuse or heavy computation mistaken for dead worker)
HEARTBEAT=MISSING
```

---

## 7. 检查点机制审计：叙事性 vs 机器恢复性 (Checkpoint Reality)

1. **当前 Checkpoint 本质分析**：
   - 查看 `personal-ai-state/checkpoints/` 与 `registry/checkpoint-schema.yaml`：
     - 包含：`objective`（字符串）、`completed_actions`（字符串数组）、`current_state`（自由对象）、`unresolved_blockers`（字符串数组）、`next_executable_action`（自然语言字符串指令）。
   - **判定结论**：当前 Checkpoint 是 **“方便 Agent / 人类理解的叙事性检查点 (Narrative Checkpoint)”**，其设计目的是让后继的大模型阅读一段 Markdown/JSON 摘要后知道“大概做到哪一步”。
2. **机器恢复检查点 (Machine Checkpoint) 缺口**：
   - 缺少：精确输入游标（`input_cursor`）、批次索引（`manifest_index`）、数据分区号（`current_partition`）、源码与输入精确哈希（`source_hashes`）、算法版本号、底层锁版本。
   - **机器无法在不启动大模型阅读理解的情况下，仅凭几行数据原样无缝恢复 I/O 流**。

```text
CAN_RESUME_DETERMINISTICALLY=NO (requires LLM re-interpretation of narrative strings)
IDEMPOTENT=NO (resume depends on LLM understanding; may repeat unrecorded sub-steps)
SOURCE_BOUND=PARTIAL (protocol_hash checks schema, but does not bind input data bytes)
OUTPUT_BOUND=NO
SCHEMA_VERSIONED=YES (schema_version: 1)
VALIDATED=PARTIAL (schema validated, but machine execution vector fields are absent)
```

---

## 8. 各种中断与重启下的行为矩阵 (Restart Policy Matrix)

| 异常场景 | 当前系统实际行为 | 自动恢复 (Auto Resume)? | 人工恢复 (Manual Resume)? | 数据损坏风险 (Data Loss)? | 重复执行风险 (Duplicate Execution)? |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Worker 正常退出 (0)** | 进程结束，若 Agent 在前台等待则收到通知；若为后台作业则结果暂留内存 | 否 | 需用户触发 | 低 | 低 |
| **Worker 异常退出 (!=0)** | 记录非零退出码；无后续动作；等待用户干预 | 否 | 需用户排查 | 低 | 中 (重跑可能导致非幂等写入) |
| **Agent 轮次耗尽 (Round Limit)** | Agent 对话静默结束；后台计算进程继续在 OS 运行但无人监听；Goal 被判定为 BLOCKED | **否** | 需用户新开会话询问 | 低 (进程在算) | **高 (新 Agent 误判未运行而重跑)** |
| **DSH 重启 (DSH Restart)** | 内部 `dsh-jobs-local` 清空；所有未结任务失去管理把柄变成孤儿进程或被杀 | **否** | 需人工通过进程管理器清理 | 中 | **高 (丢失内存状态导致重跑)** |
| **Windows 重启** | 所有进程终止；Task Scheduler 仅能触发治理自检，无业务任务恢复队列 | **否** | 需人工重新想起任务并启动 | 中 (未落盘状态丢失) | 中 |
| **模型临时报错 (429/Timeout)** | 本地拦截或重试耗尽后抛出错误，Agent 当前 turn 失败并停住 | **否** | 需人工重发 prompt | 低 | 低 |

### 关键案例深度模拟：
> 当 **`ORCHESTRATION_STATE = ENDED_ROUND_LIMIT`**，而 **`JOB_STATE = RUNNING`**，**`WORKER_STATE = ALIVE`**：
> - **当前系统会严重误判！**
> - DSH 会话界面会显示“轮次已达上限”或将 Goal 标为“已阻塞/暂停”，完全忽略底层 Worker 仍在正常计算的事实。用户若此时开启新会话询问，新 Agent 会以为任务已死，从而启动第二套任务，直接造成计算浪费与写冲突！

---

## 9. 恢复监督器审计 (Recovery Supervisor Audit)

谁负责发现“任务未完成 + Worker 已死”，并自动验证 Checkpoint、获取租约、重启 Worker？

- **DSH**：无后台扫描机制（纯反应式 harness）。
- **Windows Task Scheduler**：当前仅配置了 `run_governance_frequent.ps1` 进行上游依赖比对与备份检查，完全没有业务 Job 的巡检逻辑。
- **autonomous governor**：仅是 Tool 调用内部的计数看门狗，进程退出后不运行。
- **Switchboard**：仅在有活动客户端连接时工作，无脱机守护能力。
- **personal_ai_sync**：仅做 Git 代码树与会话备份一致性校验。
- **AIC**：仅做配置部署与运行时组合发布。
- **Agent**：只有在用户输入新的 Prompt 时才介入。

```text
RECOVERY_SUPERVISOR=MISSING (Current recovery completely depends on human return and prompt re-injection)
```

---

## 10. 事件面现状与持久性审计 (Event Plane Audit)

1. **现有事件源**：
   - DSH Cordis EventEmitter：纯内存（`ctx.on`），DSH 进程重启事件全部丢失。
   - Session Events：落盘到 `session.jsonl.zstd`，持久化程度高，但仅记录单次对话的序列化历史，不作为跨任务的事件总线。
   - Subprocess Events：OS/Node 层的进程退出事件，纯瞬态。
   - Task Scheduler：定时拉起特定脚本，属于单向唤醒信号，无事件载荷与持久化队列。
2. **统一业务执行事件状态**：
   - `JOB_CREATED`, `JOB_STARTED`, `WORKER_STARTED`, `HEARTBEAT`, `CHECKPOINT_WRITTEN`, `WORKER_EXITED`, `JOB_BLOCKED`, `JOB_COMPLETED`, `VALIDATION_REQUIRED`, `VALIDATION_COMPLETED`, `LEASE_EXPIRED`, `RECOVERY_REQUIRED`
   - **以上业务级事件全部未统一抽象与落盘**。

```text
EVENT_SOURCES=Cordis EventEmitter (memory), Session log (durable jsonl.zstd), Task Scheduler (timer only)
EVENT_DURABILITY=MEMORY_ONLY_FOR_ACTIVE_STREAM
EVENT_REPLAY=SESSION_LOG_ONLY (no job execution event replay)
EVENT_CONSUMER=AD_HOC (no unified subscriber or consumer group)
EVENT_LOSS_RISK=HIGH (process crash loses all unhandled execution events)
```

---

## 11. 轮询损耗审计 (Polling Audit)

通过对此前长任务执行记录（如后台子代理监控、外部工具等待、多渠道烟测）的历史会话分析：
- **`POLLING_ROUNDS`**：在一次典型的 30-40 轮长会话中，有 **18 到 25 轮（占比高达 60%~70%）** 完全被消耗在如下无效轮询中：
  - 调用 PowerShell 查 PID (`Get-Process`)；
  - 检查特定输出文件大小和 Hash (`Get-FileHash`)；
  - 查看日志尾部是否有新增行 (`Get-Content -Tail`)。
- **`USEFUL_DECISION_ROUNDS`**：实际产生业务决策与代码写入的轮次仅为 **6 到 10 轮**。
- **`IDLE_OBSERVATION_ROUNDS`**：空等与状态确认占绝大多数。
- **改进结论**：无需重型复杂的分布式消息队列，仅通过**完成态标记文件落盘通知 + 进程退出唤醒**，即可消除 80% 以上的无谓 Agent 轮询消耗。

---

## 12. 证据层级与自证风险审计 (Evidence Plane & Self-Certification)

建立严格的证据层级体系：
- **L0 CLAIM**：Agent 自然语言自述（“我已经全部测试通过，没有问题”）。
- **L1 ARTIFACT**：本地文件存在或 JSON 中某字段存在（如目录下存在 `report.json`）。
- **L2 OBSERVED**：独立读取并解析了该 Artifact 的结构。
- **L3 REPRODUCED**：独立执行校验脚本或重算散列（如重新运行测试套件，或从文件字节计算 SHA-256 并比对）。
- **L4 PHYSICAL_EXTERNAL**：操作系统、Git 仓库对象库、网络端口监听等不可伪造的外部物理事实（如 `git cat-file` 校验 commit，或 HTTP 返回 200）。

### 审计发现的高风险点：
- 当前多个环节存在 **L1/L0 越权冒充 L3/L4 的自证漏洞**：
  - 某个历史阶段的 `PRE_STATE.json` 中，文件自身写着 `ready: true`，但实际上并没有任何 L3 验证器运行过，也没有关联生成该结果的代码 commit；
  - Agent 在 `ResultEnvelope` 的 `summary` 或 `status` 字段直接输出 `PASS`，调用方若未验证 `validations` 数组中的具体 exit code 和测试命令证据，就会直接放行进入下游。

```text
ARTIFACT=ResultEnvelope / Checkpoint / PRE_STATE.json
CLAIMED_STATE=PASS / READY / COMPLETED
CURRENT_MIN_EVIDENCE=L1 (JSON artifact exists with self-claimed status)
REQUIRED_MIN_EVIDENCE=L3 (Independent execution/re-calculation) + L4 (Git commit / file hash)
SELF_CERTIFICATION_RISK=HIGH (Self-reported PASS can bypass validation if consumers only inspect high-level status string)
```

---

## 13. 最终裁决权审计 (Final Adjudication Authority)

- **现状漏洞**：
  - `ResultEnvelope` 可以声明 `status: PASS`；
  - `checkpoint.json` 可以声明 `stop_reason: completed`；
  - `work_registry.py` 可以更新 `STATE_COMPLETED`；
  - `PRE_STATE.json` 可以声明 `ready: true`。
  - **当前没有任何集中裁决机制，多个文件与角色都可以独立宣称终态完成**！
- **正确模型**：
  - 必须形成单向漏斗：
    $$\text{Raw Execution Outputs} \longrightarrow \text{Independent Validator (L3)} \longrightarrow \text{Final Adjudicator} \longrightarrow \text{Canonical Job State Mutation}$$

```text
MULTIPLE_STATE_AUTHORITIES=YES (ResultEnvelope, checkpoint.json, PRE_STATE.json, work_registry can independently assert completion)
FINAL_ADJUDICATION=MISSING (Lacks single authoritative validator gate before mutating canonical job state)
```

---

## 14. 制品归属与血统审计 (Artifact Ownership / Lineage)

以典型的配置与状态文件（如 `04_shadow_enablement_v2/PRE_STATE.json`）为例：
- **能否回答以下问题？**
  - 它是由哪个 `JOB_ID` 产生的？—— **否**
  - 是哪一次重试（`ATTEMPT_ID`）产生的？—— **否**
  - 是哪个 Worker 进程（`WRITER_ID`）在什么授权路径下写入的？—— **否**
  - 它的输入制品与其 SHA-256 来源是什么？—— **否**
  - 它基于哪个算法版本或模型生成？—— **否**
- **判定结论**：当前制品缺少完整的血统追踪头，无法向后溯源其生产链路。

```text
ARTIFACT_LINEAGE=MISSING (Artifacts lack embedded job_id, attempt_id, writer_id, and input_hashes)
```

---

## 15. 工作区授权与隔离能力审计 (Authorized Write Root)

审计当前已有代码 `scripts/workspace/execution_contract.py` 与 `provisioner.py`：
- **已有成熟能力**：
  - `WorkspaceMode.ISOLATED_WORKTREE` 严格支持独立分支、独立目录工作区；
  - 具备 `primary_repo`, `write_scopes`, `additional_dirs` 的授权边界定义；
  - `provisioner.py` 会记录 `worktrees.json` 与 `provenance.jsonl`，严格禁止未经校验的 `AUTO_MERGE`；
  - 具备 `is_git_dirty` 保护，防止污染用户工作树。
- **复用决策**：
  - **100% 具备作为 Durable Execution 写入归属权（Write Ownership）的基础**。
  - **坚决复用，严禁新建第二套 Worktree 管理器**！

```text
AUTHORIZED_WRITE_ROOT=AVAILABLE_FOR_REUSE (scripts/workspace/execution_contract.py & provisioner.py)
```

---

## 16. DSH 原生能力复用矩阵 (DSH Native Capabilities)

| DSH 原生能力 | 针对 Durable Execution 可复用部分 | 核心局限 (Limitations) | 持久性 (Durability) | 存活: DSH 重启 |
| :--- | :--- | :--- | :--- | :--- |
| **Continuable Subagent** | 会话内多 Agent 任务委托与消息接力 | 局限于单次会话与内存，无法跨重启自动续跑 | 内存态 | 否 |
| **Background Execution (`dsh-jobs-local`)** | 本地 Bash / Pwsh 后台进程托管 | `LocalJobRegistry` 是纯内存 Map；重启后进程变为孤儿 | 内存态 | 否 |
| **Session Persistence (`dsh-session-persistence-jsonl`)** | 极其健壮的会话事件持久化（`.jsonl.zstd`） | 仅按线性和会话组织对话日志，不具备 Job 队列语义 | **文件级强持久** | **是** |
| **Workflow (`dsh-workflow-worker-thread`)** | 多 Agent 确定性流水线编排脚本 | 仅负责同一任务内部的 worker 派发，无跨重启恢复机制 | 脚本持久，执行内存 | 否 |
| **Cordis Lifecycle** | 插件热重载、依赖注入、服务生命周期解耦 | 进程内服务总线，不可作为跨进程/跨主机执行平面 | 内存态 | 否 |
| **Tools Guard (`autonomous-execution-governor`)** | 单调配额拦截（Turns, Runtime, Loops） | 只能作为熔断与拦截点，无正向任务调度能力 | 部分持久（审计日志） | 是 |
| **Context Lifecycle (`dsh-context-lifecycle`)** | 超大上下文的 `DSH_HANDOFF_V1` 摘要导出与归档 | 仅负责上下文压力管理，不负责通用业务任务调度 | **文件级强持久** | **是** |

```text
DSH_NATIVE_REUSE=HIGH (Reuse session persistence, context handoff, tools.guard, and subprocess mechanics)
CANONICAL_JOB_STATE_IN_DSH_ONLY=FORBIDDEN (Canonical job state must reside outside DSH memory, independent of DSH restarts)
```

---

## 17. Windows Task Scheduler 适用性评估

当前系统已通过 `scripts/governance/register_governance_tasks.ps1` 成功注册了高频自检和夜间备份任务。

- **适合承担的职责**：
  1. **低频恢复心跳 (Recovery Tick)**：例如每 5~15 分钟触发一次极轻量的扫描脚本，检查是否存在未完成但 Worker 已死亡的任务；
  2. **孤儿进程与 Worktree 垃圾回收**；
  3. **机器开机自动唤醒恢复**。
- **坚决不适合承担的职责**：
  1. 亚秒级的实时事件调度；
  2. 复杂的进程间通信总线；
  3. 替代业务层 Job 状态机。
- **架构决策**：**不需要新造任何驻留式 Windows Service (Daemon)**，现有的 Windows Task Scheduler 触发器足以承担恢复心跳。

---

## 18. personal-ai-state/checkpoints 角色判定

- **当前职责**：`personal-ai-state/checkpoints/<task_id>.json` 当前本质是 **跨 Harness 的检查点参考资产（Reference / Handoff Artifact）**。
- **技术特征**：
  - 依赖 Git 进行分布式同步；
  - 写入频率低（任务阶段结束或中断时写一次）；
  - 格式为面向大模型摘要的 JSON。
- **判定结论**：
  - **绝不能将其强行改造成高频、并发的实时 Job 状态机或锁管理器**（Git 仓库不适合毫秒级锁与频繁高并发写冲突）。
  - 它应继续保持其作为 **长任务阶段性 Checkpoint 资产库** 的定位。

---

## 19. 结构化输出复用判定 (Existing Structured Output)

`scripts/structured_output/contract.py` 中定义的 `ResultEnvelope`：
- 已拥有完整的：`task_id`, `status` (`PASS/PARTIAL/FAILED/BLOCKED/REVIEW_REQUIRED/NO_CHANGE`), `harness`, `summary`, `workspace_mode`, `artifacts`, `validations`, `blockers`, `provenance`, `next_action`。
- **结论：100% 完全复用，严禁为 Durable Execution 重造第二套结果契约**。

```text
STRUCTURED_OUTPUT_REUSE=FULL (scripts/structured_output/contract.py ResultEnvelope)
```

---

## 20. 工作区隔离能力复用判定 (Existing Workspace Isolation)

`scripts/workspace/execution_contract.py` 与 `provisioner.py`：
- 已实现 `ISOLATED_WORKTREE`、Attempt 目录隔离、权限范围限制（`write_scopes`）、`worktrees.json` 账本与 `provenance.jsonl` 审计流。
- **结论：100% 完全复用，严禁新建第二套 Worktree 管理工具**。

```text
WORKSPACE_REUSE=FULL (scripts/workspace/provisioner.py WorktreeProvisioner)
```

---

## 21. 模型与上下文治理复用判定 (Context Governance Reuse)

已在 DSH 托管层成功落地的组件：
1. `dsh/context-pressure-guard`：基于 1.08 系数、16384 安全边距、65536 输出上限的本地准入裁决；
2. `dsh-context-lifecycle`：大上下文（如 345k）到达阈值时导出的 `DSH_HANDOFF_V1` 结构化摘要载荷与源会话只读锁定；
3. `TokenMeter`：单调可靠的 Token 测算与未配对流安全降级。
- **结论：100% 直接复用为 Job Recovery 启动与换路由重试前的前置门禁，无需重复实现**。

```text
CONTEXT_HANDOFF_REUSE=FULL (dsh-context-pressure-guard & dsh-context-lifecycle)
```

---

## 22. Switchboard 职责与解耦边界 (Switchboard Boundaries)

- **当前地位**：`OPTIONAL_TOOL`，提供跨 Agent 委托协作、工作量去重（`work_registry.py`）等能力。
- **严正边界**：
  - Switchboard 内部的 `work_registry.py` 是优秀的参考模式；
  - 但**绝不强制要求 Claude Code、Codex、Gemini 经过 Switchboard 运行**；
  - 未来的 Durable Execution 核心契约必须是文件/脚本层面的 Harness-neutral 设计，Switchboard 仅作为客户端工具之一接入。

```text
SWITCHBOARD_REUSE=CLIENT_TOOL_ONLY (Reuse task dispatch and dedup patterns optionally)
SWITCHBOARD_MANDATORY=NO (Claude/Codex/Gemini must never be forced through Switchboard)
```

---

## 23. 故障域隔离分析 (Failure Domains)

最小架构必须保证：
1. **Durable Execution Plane 发生故障**：
   - 仅导致 DSH 的无人值守长任务自动恢复功能失效；
   - **Claude Code 保持 100% 可用 (AVAILABLE)**；
   - **Codex 保持 100% 可用 (AVAILABLE)**；
   - **Gemini 保持 100% 可用 (AVAILABLE)**；
   - 用户仍可通过各 Harness 的原生界面进行交互工作；
   - `personal-ai-state` 的 Git 历史与 durable 数据不发生写损坏。

```text
FAILURE_DOMAIN=ISOLATED_TO_DURABLE_PLANE (Failure degrades autonomous job recovery only; native harnesses remain fully functional)
```

---

## 24. 扫描器案例实证映射 (Scanner Case Study)

**真实场景映射**：
当后台 Scanner 任务执行到第 27 步时，前端 Agent 对话触达轮次上限（`round limit reached`），用户界面停住，同时由于长时间运行触发了主机重启：
- **理想系统应有的记录**：
  - `ORCHESTRATION_STATE`: `ENDED_ROUND_LIMIT`
  - `JOB_STATE`: `RUNNING`（扫描任务仍在进行）
  - `WORKER_STATE`: `ALIVE`（如果进程仍在跑）或 `HEARTBEAT_LOST`（如果主机重启）
  - `VALIDATION_STATE`: `NOT_STARTED`
  - `LEASE`: `VALID`（直至超时）或 `EXPIRED`
  - `LAST_HEARTBEAT`: 机器重启前的最后心跳时间戳
  - `CHECKPOINT`: 已持久化的第 26 步扫描索引游标
  - `NEXT_EVENT`: `RECOVERY_REQUIRED`
- **当前系统实际表现**：
  - 缺少统一的 `JOB_STATE`，Agent 会话停下即显示任务中断；
  - 缺乏机器级输入游标，重新拉起时 Agent 只能从头扫描或全量重算；
  - 缺乏防双写租约，用户新起一个 Agent 时，新 Agent 很容易盲目启动“第二个 Scanner 进程”，引发冲突。

---

## 25. 影子使能案例实证映射 (Shadow Enablement Case Study)

**真实场景映射**：
针对历史出现的 `04_shadow_enablement_v2/PRE_STATE.json`：
- **`STATE_AUTHORITY_GAP`**：该 JSON 文件内部自行包含 `ready: true` 声明，没有任何上层裁判器对其内容真实性背书。
- **`EVIDENCE_LEVEL_GAP`**：该制品属于 L1 Artifact，却被消费方当作已经过 L3/L4 验证的准入凭据。
- **`ARTIFACT_LINEAGE_GAP`**：文件元数据中完全没有 `job_id`, `attempt_id`, `writer_pid` 以及输入依赖项的 SHA-256，无法追溯是由哪次任务的哪个 Worker 在哪个时刻基于什么代码生成的。

---

## 26. 缺口严重性分级 (Gap Classification)

### P0 缺口（致命缺陷：双写、静默永久停滞、状态失真）
1. **`GAP-P0-01: SAME_JOB_DUAL_WRITER_RISK`**：缺乏跨 Harness 的物理锁/原子租约，仅靠自然语言提示词防止双写（`SINGLE_WRITER_ENFORCEMENT=POLICY_ONLY`）。
2. **`GAP-P0-02: JOB_SILENT_PERMANENT_STALL`**：当会话达到轮次限制或异常闪退时，无自动恢复监督器，长任务静默永久停止（`RECOVERY_SUPERVISOR=MISSING`）。
3. **`GAP-P0-03: ORCHESTRATION_JOB_CONFLATION`**：将 Agent 编排状态直接等同于业务 Job 状态，导致活着运行的 Worker 被误判为失败。
4. **`GAP-P0-04: MULTIPLE_STATE_AUTHORITIES`**：多处制品可独立自证 `PASS/READY`，缺乏单一权威最终裁决漏斗（`MULTIPLE_STATE_AUTHORITIES=YES`）。
5. **`GAP-P0-05: DURABLE_JOB_REGISTRY_ABSENT`**：无中心机制能回答“当前有哪些未完成任务”（`DURABLE_JOB_REGISTRY=MISSING`）。

### P1 缺口（效率损耗、线索残缺）
1. **`GAP-P1-01: POLLING_TOKEN_WASTE`**：Agent 消耗 60%~70% 的对话轮次和 Token 在无谓的 `Get-Process` 与文件轮询上。
2. **`GAP-P1-02: NARRATIVE_ONLY_CHECKPOINTS`**：Checkpoint 缺乏机器确定性恢复字段（游标、分区、输入输出哈希），只能靠 LLM 重新揣摩上下文。
3. **`GAP-P1-03: ARTIFACT_LINEAGE_INCOMPLETE`**：输出文件未绑定 `job_id`、`attempt_id` 与上游源哈希。

### P2 缺口（体验与展现）
1. **`GAP-P2-01: CLI_INSPECTION_UX`**：缺乏单命令统一查看当前所有活动 Job 状态与租约的终端工具。

---

## 27. 组件复用 / 扩展 / 新增矩阵 (Reuse Matrix)

| 组件目标 | 决策 | 关联现有组件与落地方式 |
| :--- | :--- | :--- |
| **Durable Job Registry** | **`NEW_REQUIRED`** | 在 `~/.personal-ai/jobs/`（或 DSH 配置目录外）建立轻量原子注册表（无外部依赖，纯 JSON/SQLite），由脚本层驱动。 |
| **Job State Machine** | **`NEW_REQUIRED`** | 固化严格的 9 状态状态机规范（`PENDING` $\rightarrow$ `COMPLETED`），彻底剥离 Agent 对话轮次。 |
| **Atomic Lease** | **`EXTEND`** | 借鉴并抽取 `mcp/agent-switchboard/work_registry.py` 的 `WorkLease` 文件锁与 TTL 逻辑，使其成为所有 Harness 通用的无锁竞争规范。 |
| **Heartbeat** | **`EXTEND`** | 规范化 Worker 写入心跳时间戳文件的轻量约定，彻底淘汰 Agent 主动 `Get-Process` 轮询。 |
| **Checkpoint** | **`EXTEND`** | 完全复用 `registry/checkpoint-schema.yaml` 与 `scripts/autonomy/checkpoint.py`，在其基础上方扩展 `machine_state` 向量字段。 |
| **Event Dispatcher** | **`EXTEND`** | **不建 Event Bus**。通过本地追加型 `events.jsonl` + 任务完成触发标记，实现无依赖事件沉淀。 |
| **Recovery Supervisor**| **`MERGE`** | **不建守护进程 (Daemon)**。直接将“死任务巡检”合并入现有的 Windows Task Scheduler 定时任务（`scripts/governance/runner_adapter.py`）。 |
| **Final Adjudicator** | **`REUSE`** | 复用 `scripts/structured_output/validator.py` 与 `contract.py`，严格由裁判器断言后才允许更新 Job 终态。 |
| **Artifact Lineage** | **`EXTEND`** | 在现有的 `ResultEnvelope.artifacts` 结构中补充 `job_id`、`attempt_id`、`source_hashes` 必填项。 |

---

## 28. 最小目标架构定义 (Minimal Target Architecture)

**极简原则：坚决避免构造 Kubernetes 式的沉重平台。**

```text
               ┌─────────────────────────────────────────────────────────┐
               │           Windows Task Scheduler (现有定时器)           │
               │            (每 10 分钟触发轻量 Recovery Tick)           │
               └───────────────────────────┬─────────────────────────────┘
                                           │ 扫描未完成且超时 Job
                                           ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        Minimal Durable Execution Core (无常驻守护)                      │
│                                                                                        │
│   1. Durable Job Registry  : ~/.personal-ai/jobs/<job_id>.json (状态/租约/尝试记录)    │
│   2. Atomic File Lease     : <job_id>.lock (排他防双写 + TTL)                           │
│   3. Machine Checkpoint    : 扩展 checkpoint.py (保存游标与哈希)                        │
│   4. Output & Validation   : 严格复用 ResultEnvelope + validator.py (L3 验毕才准 COMPLETED)│
└──────────────────────────────────────────┬─────────────────────────────────────────────┘
                                           │ 派发或恢复执行
                 ┌─────────────────────────┴─────────────────────────┐
                 ▼                                                   ▼
┌──────────────────────────────────┐               ┌──────────────────────────────────┐
│        DSH (Deep-Managed)        │               │   Claude / Codex / Gemini        │
│   - 原生 Session Persistence     │               │   - 原生独立运行 (NATIVE)        │
│   - Context Lifecycle / Handoff  │               │   - 仅通过读取/写入标准制品交互  │
│   - Workspace Isolation (复用)   │               │   - 绝不安装强制 Hook / 守护进程 │
└──────────────────────────────────┘               └──────────────────────────────────┘
```

- **不需要独立的 Event Bus**：追加型 `events.jsonl` 完全足够。
- **不需要常驻后台 Daemon**：Task Scheduler 定时拉起恢复脚本完全足够。
- **不需要第二套工作区管理器**：现有的 `scripts/workspace/provisioner.py` 完全足够。

---

## 29. 各 Harness 对接边界 (Harness Integration Target)

1. **DSH**：
   - 深度集成：支持在 DSH 内部通过工具创建 Job、获取租约、启动后台任务、写机器检查点、并在上下文超限时无缝衔接 `DSH_HANDOFF_V1` 迁移。
2. **Claude Code / Codex / Gemini**：
   - **NATIVE_INDEPENDENT**：
   - 不强制安装任何拦截 Hook；
   - 不要求其常驻在任何 Supervisor 之后；
   - 仅通过标准的命令行参数、输入/输出文件（读取 `job_spec.json`，写出 `result_envelope.json`）参与任务；
   - 保持随时随地可以直接由人类在终端原生启动使用。

---

## 30. 最终标准化审计键值输出 (Final Standardized Key-Value Output)

```text
CURRENT_DURABLE_EXECUTION_CAPABILITIES=DSH_SESSION_PERSISTENCE, CONTEXT_LIFECYCLE_HANDOFF, WORKSPACE_ISOLATION_WORKTREE, STRUCTURED_RESULT_ENVELOPE, NARRATIVE_CHECKPOINT_SCHEMA
ORCHESTRATION_STATE=RUNNING, WAITING, ROUND_LIMIT, ENDED, BLOCKED (Implemented in DSH Agent/Fiber; conflated with Job lifecycle)
JOB_STATE=PENDING, READY, RUNNING, CHECKPOINTED, WAITING_EVENT, RECOVERING, COMPLETED, FAILED, CANCELLED (Missing separate durable registry)
WORKER_STATE=STARTING, ALIVE, HEARTBEAT_LOST, EXITED_0, EXITED_ERROR, KILLED, UNKNOWN (Memory-only in dsh-jobs-local Map)
VALIDATION_STATE=NOT_STARTED, RUNNING, PASS, REVIEW_REQUIRED, FAIL (Partial in scripts/structured_output/validator.py)
DURABLE_JOB_REGISTRY=MISSING
JOB_IDENTITY=PARTIAL (task_id exists as loose string; no durable entity)
ATTEMPT_IDENTITY=NO (only resume_count/retry_count integers; no first-class attempt entity)
SINGLE_WRITER=POLICY_ONLY (guaranteed by prompt/documentation only; no cross-harness OS/file lock)
LEASE=PARTIAL_SWITCHBOARD_ONLY (work_registry.py has file lock, but not unified or cross-harness)
HEARTBEAT=MISSING (relies on agent manual polling via Get-Process and file timestamps)
CHECKPOINT=NARRATIVE_ORIENTED (checkpoint.py stores human/LLM-oriented text; lacks machine execution cursor/hashes)
RESTART_POLICY=UNSUPERVISED_ORPHANING (round-limit or crash leaves background jobs running without monitor or recovery)
RECOVERY_SUPERVISOR=MISSING (relies on human returning to re-prompt the agent)
EVENT_SOURCES=Cordis EventEmitter (memory), Session log (durable jsonl.zstd), Task Scheduler (timer only)
EVENT_DURABILITY=MEMORY_ONLY_FOR_ACTIVE_STREAM
EVENT_REPLAY=SESSION_LOG_ONLY (no job execution event replay)
POLLING_DEPENDENCY=HIGH (60-70% of long-session agent turns wasted on Get-Process/file-stat polling)
EVIDENCE_TYPING=L0_TO_L4_DEFINED_BUT_NOT_FORMALLY_ENFORCED
SELF_CERTIFICATION_RISK=HIGH (Agent-written summary with PASS accepted without mandatory L3 validator execution)
FINAL_ADJUDICATION=MISSING (Multiple artifacts can assert READY/PASS independently)
ARTIFACT_LINEAGE=MISSING (Artifacts lack embedded job_id, attempt_id, writer_id, and input_hashes)
AUTHORIZED_WRITE_ROOT=AVAILABLE_FOR_REUSE (scripts/workspace/execution_contract.py & provisioner.py)
DSH_NATIVE_REUSE=HIGH (Session persistence, context handoff, tools.guard, and subprocess mechanics)
STRUCTURED_OUTPUT_REUSE=FULL (scripts/structured_output/contract.py ResultEnvelope)
WORKSPACE_REUSE=FULL (scripts/workspace/provisioner.py WorktreeProvisioner)
CONTEXT_HANDOFF_REUSE=FULL (dsh-context-pressure-guard & dsh-context-lifecycle)
SWITCHBOARD_REUSE=CLIENT_TOOL_ONLY
SWITCHBOARD_MANDATORY=NO
SCANNER_CASE=SIMULATED_VULNERABLE (Round limit stops agent => scanner runs unmonitored => second agent risks launching duplicate scanner)
SHADOW_CASE=EVIDENCE_AUTHORITY_GAP (PRE_STATE.json self-claimed ready without validator proof or writer lineage)
DUPLICATE_COMPONENTS=dsh-jobs-local vs managed_claude PID tracking; checkpoint stop_reason vs goal status vs ResultEnvelope status
COMPONENTS_TO_MERGE=Recovery scan merged into Task Scheduler frequent runner; validator.py merged into Final Adjudicator
COMPONENTS_TO_REMOVE=In-memory uncoordinated goal blockers; manual agent PID polling loops
NEW_COMPONENTS_TRULY_REQUIRED=1. Minimal Durable Job Registry (JSON/SQLite), 2. Atomic Work Lease & Single Writer Lock, 3. Machine-Extended Checkpoint Vector, 4. Task Scheduler Recovery Tick
P0_GAPS=GAP-P0-01 (Single Writer Policy-Only), GAP-P0-02 (Silent Permanent Job Stall), GAP-P0-03 (Job Registry Absent), GAP-P0-04 (Multiple State Authorities), GAP-P0-05 (Orchestration/Job Conflation)
P1_GAPS=GAP-P1-01 (Polling Token Waste), GAP-P1-02 (Narrative-Only Checkpoint), GAP-P1-03 (Artifact Lineage Incomplete)
P2_GAPS=GAP-P2-01 (CLI Inspection UX)
MINIMAL_TARGET_ARCHITECTURE=Minimal Durable Job Registry + Atomic File Lease + Extended Machine Checkpoint + Task Scheduler Recovery Tick (No Daemon, No Event Bus)
IMPLEMENTATION_ORDER=1. Minimal Job Registry & State Schema, 2. Atomic File Lease & Single Writer, 3. Machine Checkpoint Extension, 4. Task Scheduler Recovery Tick, 5. ResultEnvelope Lineage Binding
FAILURE_DOMAIN=ISOLATED_TO_DURABLE_PLANE (Claude/Codex/Gemini remain 100% available and independent)
OVERALL_DURABLE_EXECUTION_MATURITY=FOUNDATIONAL_COMPONENTS_PRESENT_BUT_COORDINATION_PLANE_MISSING
```

---

## 31. 本轮交付物索引

- **机器可读索引文件**：`docs/durable-execution-gap-index.json`
- **正式审计长文报告**：`docs/durable-execution-gap-report.md`
- **状态**：**本轮仅调查、建模、识别重复与缺口，严格未做任何实现代码，等待决策。**
