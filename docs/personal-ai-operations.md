# Personal AI — Operations Handoff（2026-08-28 起生效）

建设阶段已全部完成。本文档回答一个问题：**正常使用时你需要管什么。**
架构基线 Architecture V2.1 冻结；变更走 Change Management（见 §11），不再有 Phase 8。

---

## 1. Daily Use

正常使用 DSH / Codex / Claude / Gemini **不需要任何基础设施人工操作**。

你不需要管：模型配置同步（aic 管）、MCP 能力分发（capabilities.yaml 管）、
会话备份/broker 快照（计划任务管）、记忆写入（harness 会话自然产生）、
上下文注入（runtime hook 管）。

## 2. Health —— 怎么看状态

```powershell
python <REPO_ROOT>\scripts\governance\personal_status.py   # 一句话版（5 域）
python <REPO_ROOT>\scripts\governance\gov_status.py       # 详细版（11 域，逐条原因）
```

## 3. When Healthy

**什么都不要做。** 系统自己跑。

## 4. When Degraded

`gov_status.py` 每行都带原因。常见自查：

| 现象 | 查看 |
|---|---|
| DRIFT | `python scripts\aic\aic.py diff <target>` 会报出 file/field/expected/actual |
| RPO BREACHED | `python scripts\durability\rpo_check.py`；看 `<BACKUP_ROOT>\ledger\runs.jsonl` 最近行 |
| repos 风险 | `python scripts\durability\check_repos.py`（哪仓几个未推送） |

原则：治理只 **发现和提案**，不会偷偷改 canonical 把状态变绿。

## 5. When Blocked

区分三种 Blocked：

- **External blocker**（如 BACKUP_KEY_CUSTODY）：等外部条件（KMS/密码管理器/第二设备），不是系统故障，不用修。
- **Privacy blocker**（如 NOVEL_REPO_DURABILITY）：有意不 push，保护隐私优先。需要时走 §9 的 resolution plan。
- **Actual infrastructure failure**：`gov_status.py` 的 Harnesses/Control Plane 变 DEGRADED 且带具体 file/field——这才是要修的。

## 5.1 Automation result semantics（2026-09-03）

治理检查的子进程退出码保留原有含义：非零可以表示 DRIFT、RPO BREACHED、provider 不可达或其他治理 finding，
不自动等价于 runner 没有执行。Windows Task Scheduler 使用 `scripts/governance/runner_adapter.py` 作为边界，
记录两个独立维度：

| 字段 | 含义 |
|---|---|
| `execution_status` | `SUCCESS` 表示所有必需检查完成且日志、receipt、ledger 证据已写入；`FAILED`、`TIMEOUT`、`ENVIRONMENT_ERROR`、`ENTRYPOINT_ERROR`、`EVIDENCE_WRITE_ERROR` 表示执行完整性失败 |
| `governance_status` | `PASS`、`REVIEW`、`WARN`、`DEGRADED`、`BLOCKED`，表示检查发现，不表示 runner 崩溃 |
| scheduler process exit | 仅 execution failure 返回非零；完成但有 governance finding 的 Frequent/Weekly 返回 0，原始 child exit 和 finding 保存在 receipt/log/ledger |

`personal_ai_sync.py check` 的直接 CLI 契约仍是 `PASS=0`、`REVIEW=1`、`BLOCKED=2`；`run_sync_check.ps1` 只在
Windows Task Scheduler 边界翻译已验证的结构化结果，保持 `check` only，不执行 pull/push/sync/resolve。

## 6. Proposals —— 怎么审

所有治理提案在：`<DSH_HOME>\.evolution-inbox\proposals\gov-*.json`

每个含：type / evidence / severity / affected_ssot / recommended_action / safe_to_auto_apply。
批准 = 人工按 evidence 决策后执行对应 canonical 修改（如编辑 `registry/models.yaml`），
然后把该 proposal 的 `status` 改为 `applied` 或 `rejected`。**没有自动 admit。**

当前待审条目以本机 inbox 实际内容为准（在用未准入的 model_admission 提案，证据在提案里）。

## 7. Recovery —— 已验证的恢复路径

| 事故 | 路径（全部物理验证过） |
|---|---|
| Session 误删 | 从 `<BACKUP_ROOT>\sessions\daily-<date>\` 找回，三方哈希一致（T4） |
| Broker 损坏 | 用 `<BACKUP_ROOT>\broker\broker-*.sqlite` 最新 verified 快照替换（T3，integrity_check=ok） |
| 仓库丢失 | `git clone` 远端恢复到最近 push 点；未推送部分从 <BACKUP_ROOT> 无（→ 所以 check_repos 的 UNPUSHED_DURABILITY_RISK 要重视） |
| DSH 丢失 | canonical（registry/）+ personal-ai-state 已推送远端；另一 Harness 仅凭 `.ai/state/` + Context Package 即可接手（Phase5 实测） |
| 本机磁盘全毁 | **BLOCKED_BY_KEY_CUSTODY**：密文在，Gen2 key 不在 → 不可恢复（诚实状态，见 §10） |

### 7.1 Nightly Backup 生产恢复闭环与状态判定（SSOT 契约）

1. **Canonical 任务入口与执行账户**：
   - Windows 计划任务名称：`PersonalAI-Durability-Nightly`，Daily 03:30 触发。
   - 运行账户：`<USER>` (InteractiveToken，禁止 ephemeral/Temp/bootstrap-drill 入口依赖)。
   - Action Target：`C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -File "<REPO_ROOT>\scripts\durability\run_nightly.ps1"`。
   - 调度主控：`scripts/durability/run_backup.py`（生成唯一 Run Identity 并编排各子任务）。
2. **实际 Destination**：
   - `<BACKUP_ROOT>`（与 `personal-ai-state/sync/this-device.yaml#backup_root` 严格一致）。
3. **真实一致性模型（Consistency Model & Boundaries）**：
   - 采用 `composite-per-dataset-transactional-and-stable-copy` 模型，不虚构跨全部文件系统和数据库的全局瞬时原子锁：
     - **事务级快照 (ACID Transaction Snapshot)**：`durable_jobs.db`、`broker/state.sqlite`、`broker/cc-switch.db` 通过 SQLite Online Backup API（`sqlite3.backup`）在并发写安全状态下生成点时间事务快照，并通过 `PRAGMA integrity_check`。
     - **单文件稳定副本一致性 (Stable Copy Consistency)**：`.credentials.yaml`、各类 configs 与全部 sessions 文件在各自捕获窗口内通过 copy-before/after SHA-256 审计与并发 mutation 重试，保证文件内容无撕裂。
     - **运行时间语义**：明确区分 `run_started_at`、各组件捕获窗口 `component_capture_intervals`、`run_finished_at`。
     - **满足恢复契约的判定依据**：Personal AI 各子系统松耦合（Job 状态机由持久数据库维护，Session 为独立追加日志，凭据为静态配置），单组件事务一致性与稳定副本足以保证隔离恢复后各真实 reader/loader 正常工作。
4. **Run-level Evidence**：
   - 每次运行绑定唯一 `run_id`（`nightly-YYYYMMDD-HHMMSS-<nonce>`），关联 `actor`、`scheduled_task`、`task_version` (git sha)、`config_version` (device cfg sha256)、`run_started_at`、`component_capture_intervals`、`destination`。
   - 产物 Manifest：`<BACKUP_ROOT>\runs\<run_id>\manifest.json`，并将结构化 run 记录写入 `<BACKUP_ROOT>\ledger\runs.jsonl`。
5. **隔离恢复验收（Isolated Restore Verification）**：
   - 脚本：`scripts/durability/restore_check.py`，必须且仅从备份产物恢复到临时隔离沙箱，禁止生产原始文件补齐。
   - **全量覆盖要求（Zero Sampling）**：本次 run manifest 中声明的**全部 session 文件**（例如 838 个）全部逐一完成：
     - 从 backup artifact 恢复到隔离沙箱；
     - SHA-256 / manifest 完整性匹配；
     - zstd 完整流式解压；
     - JSONL 结构反序列化与事件合法性检验；
     - 严禁抽样 PASS 推导全量 PASS；统计 `manifest_total`、`restored_count`、`integrity_verified_count`、`reader_verified_count` 必须严格一致。
   - **其他验证项**：
     - 数据库完整性（`PRAGMA integrity_check == ok`）；
     - Durable Jobs 真实读取链路：`from jobs.registry import DurableJobRegistry` 实例化并成功读取恢复后的 jobs 记录与未完成计数；
     - Credentials 真实读取链路：使用原生 Node `@deepseek-ai/dsh-credentials-local` 及 YAML Loader 校验结构（version 1, refs/records），零明文泄露；
     - 恢复验收全程无外部网络调用、无真实 job 启动、无生产副作用。
6. **必做反证（Negative Testing）**：
   - 人为在隔离副本中移除必需恢复对象（`--tamper-remove durable_jobs` 或 `--tamper-remove credentials` 或 `--tamper-remove broker`），验收结果必须严格返回 FAIL (Exit Code 1)，证明拦截门禁真实敏感。
7. **状态语义防 False-Pass**：
   - 状态检查（`scripts/governance/durability_gov.py`）严格区分最近一次真实 backup run 结果与最近一次 restore verification 结果和截点；
   - 严禁凭 task enabled、destination 存在、旧备份存在或历史 receipt 推导“当前正常”；只有两者均 OK 且针对同一 `run_id` 匹配时才判定 PASS。

## 8. Known External Blockers

- **BACKUP_KEY_CUSTODY = WAITING_FOR_CUSTODY_ROOT**：Gen3 架构 READY，实现 DEFERRED。等 Cloud KMS / 密码管理器 / 硬件根 / 可信第二设备出现再启动 `GEN3_KEY_CUSTODY_MIGRATION`。**在那之前 FULL_DR_READINESS 永远 = PARTIAL，这是设计而非故障。**
- **NOVEL_REPO_DURABILITY = BLOCKED_PRIVACY**：见 §9。

## 9. private-repo privacy resolution plan（计划，未执行）

```text
1. 以 origin/master 为基拉新分支 clean-state-<date>
2. 从本地 master 三个 commit 提取【最终净状态】（当前工作树即已脱敏版本）
3. 在干净分支上生成单个新 commit：仅含脱敏后 .ai/state/state.md（diff 61 行已审计为净）
4. 对该 commit 做 privacy audit（CPA/Antigravity/agent-broker/路径/token 全模式扫描 = 0 命中）
5. 正常 push / PR 进 public remote
6. 本地旧 master（含敏感历史）保留为本机存档，不推送；推送完成后本地可切到干净分支
```
效果：项目状态进 remote，敏感历史永不进 public remote。**未经你单独要求不会执行。**

## 10. Backup Gen3 触发条件

出现以下任一 → 启动独立 `GEN3_KEY_CUSTODY_MIGRATION`：Cloud KMS 可用 / 密码管理器托管 / 硬件密钥 / 可信第二设备。此前不做任何临时绕行（不买服务、不上传 key、不引依赖）。

## 11. Future Change Management

`CONTINUOUS_CAPABILITY_ADOPTION` 的偏好 canonical 位于 private `personal-ai-state/state/preferences.md`。现有 weekly governance 运行 `upstream_capability_review.py`：先用 `aic discover --propose-admissions` 更新 generated inventory，再对已安装 Harness 版本变化建立 proposal-only 评估证据。`discovery ≠ adoption`；任何正式纳入仍走下述 change 流程，并进入 capabilities registry、AIC deployment/recovery、drift 检查与兼容性验证。AIC 只把该 canonical policy 渲染成各 Harness 静态指令文件中的 checksum-managed generated block；`AGENTS.md` / `CLAUDE.md` / `GEMINI.md` 不是新 canonical。`scripts/governance/register_governance_tasks.ps1` 仅允许从 canonical `<REPO_ROOT>` 幂等复用 Windows Task Scheduler 注册并回读 frequent/weekly runner；restore/bootstrap 在临时或测试副本中不得触碰 live scheduler，也不为 AIC 增加 scheduler。

### 11.0 Canonical Mutation Ownership

所有 Personal AI canonical repository 的写操作必须经过 `scripts/personal_ai_sync.py` 的
`CanonicalMutationLock`：先以 `actor / pid / run_id / started_at / operation / repo / scope` 原子取得机器本地
lease，再重新检查工作区。只要 working tree 不是 `CLEAN`，自动 pull、push、merge、restore overwrite 和 commit
一律 `REVIEW/DEFER`；不得 reset、checkout、stash、whole-repo stage 或把 foreign dirty 内容带入提交。

显式 commit 只能使用声明的 owned paths，并在 commit 前验证 cached diff 完全属于该 scope；成功后把
`actor / trigger / task_id / base / result / staged / changed / commit / ownership` receipt 写到
`~/.dsh/.personal-ai-mutation/receipts/`，不写入 canonical repo。`SYNC` 不隐式 commit/push；只有显式
`push` 模式且所有 ahead commits 都有有效 ownership receipt 才允许 push。临时/测试 restore 源不得取得
canonical writer identity，也不得注册 live Scheduler。完整协议以已加载的
`skills/publish-and-reuse/references/personal-ai-lifecycle-sync.md` §19–§20 为准。

### 11.0.1 Canonical Writer Provenance

从本轮起，所有新 canonical mutation receipt 还必须带上：
`schema / timestamp / repo / actor / actor_type / task_id / run_id / thread_id / pid / ppid /
process_start_time / entrypoint / operation / base_head / result_head / remote_before / remote_after /
owned_scope / changed_files / commit / push_target / mutation_lease_id`。无法可靠观察的字段写 `UNKNOWN`，不从
commit author、时间相近或 prompt 反推。

`commit_owned_files`、锁内 pull/merge、显式 push 都按
`actor → task/run/thread → process → lease → commit → receipt → push receipt` 写机器本地证据。
Frequent、Sync-Check 和 sync mutation 每次检查上次 audited HEAD 之后的新 commit；没有有效 receipt 的新 commit
记录为 `UNAUTHORIZED_OR_UNATTRIBUTED_CANONICAL_MUTATION`，包含 commit 时间、author、affected files、previous /
current HEAD，并只转 `REVIEW`，不 reset/revert/force-push。首次建立基线时，已有的无 receipt HEAD 只能标记为
`LEGACY_UNATTRIBUTED_COMMIT`，不得补造 receipt。审计状态位于
`~/.dsh/.personal-ai-mutation/provenance-audit/`，不进入 canonical Git，也不新增 hook、ACL、daemon 或 scheduler。

未来一切基础设施变化 = 独立 change，走：

```text
Need → Impact → Proposal → Implementation → Verification → Regression
```

示例：新增 provider/model/harness、换机器、DSH 升级、MemoryProvider 替换、KMS 上线。
没有 Phase 8/9/10。

## 11.1 DSH Managed Upgrade Lifecycle（正式入口）

DSH 的 managed composition 升级收敛到版本化模型：`CURRENT_ACCEPTED / PREVIOUS_ACCEPTED / CANDIDATE`。
用户日常启动方式不变；生产升级永远 `AUTO_PRODUCTION_UPGRADE=FORBIDDEN`，accept/switch 仅显式执行。

```text
check        → 当前/previous/candidate 状态 + 上游版本发现 + AIC 门禁
prepare      → 在隔离 home 构建候选 composition（deterministic，hash 记录）
validate     → 硬门禁：inspect PASS / node+base smoke / 5 插件 AVAILABLE·LOADABLE·CONFIG / 行为回归
propose      → UPGRADE_PROPOSAL=READY（等待用户 cutover 决策）
accept       → 显式切换：B 变 current、A 变 previous（本地，不联网）
rollback     → previous 恢复 current（本地，不重新下载）
observe      → post-launch 契约（10 项；--check-only 可在未启动时预检）
```

- 唯一 durable 状态：`~/.dsh/profiles/web/dsh-managed-state.json`（由 canonical contract + manifest 可重建；非平行 SSOT；`aic diff dsh` 仍为 drift 权威）。
- launcher（dsh-launch-web.ps1）版本无关：经 state→manifest 解析 base/node/entry，切换后无需改 launcher。
- 足迹：`~/.dsh/.dsh-lifecycle/{candidates,proposals,observations,evidence.jsonl}`（机器本地，可重建）。

## 12. Final Validation Snapshot（2026-08-28）

见本文件同目录 phase7-governance-report.md §24-25 与下方 Handoff §12（会话输出）。
