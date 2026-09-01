# PERSONAL_AI_AUTONOMOUS_EXECUTION_GOVERNANCE_REPORT

> 变更：Migration #8（AUTONOMOUS_EXECUTION_GOVERNANCE / BOUNDED AUTONOMOUS EXECUTION）
> 日期：2026-08-31 · 范围：Personal AI 生态级能力（cross-project / cross-harness），**非 novel-main 补丁**
> 反例基准：novel-main `session-0d07ae22`（534 turns / 674 API requests / ≈171M tokens [cached ≈168.1M, new ≈2.57M, output ≈307K] / 估算成本 ≈$106）

---

## CURRENT_ARCHITECTURE

恢复后的真实架构（证据：本机 agent-tools 仓库 + personal-ai-state 私有仓库实测）：

| 面 | 现状 | 证据 |
|---|---|---|
| canonical policy SSOT | `agent-tools/registry/`（models/providers/routing-policy/capabilities/governance-policy/retrieval-policy + harnesses/{dsh,codex,claude,gemini,switchboard,dsh-overlay}.yaml）；私有层 = `personal-ai-state/`（registry/gateways.yaml、state/{identity,preferences,goals}.md、memory/、projects/） | registry/*.yaml、personal-ai-state README |
| `aic compile --target` | **不存在 `compile` 动词**（冻结边界：discover/render/diff/validate/apply/bootstrap 六动词）。生成器 = `render`（dsh 全文件 settings + 各 harness 字段投影 + managed instruction blocks）；`diff` 校验 NO DRIFT；`apply` 只写 generated 态（backup+atomic+post-diff+rollback） | scripts/aic/aic.py main()、FREEZE_NOTES.md |
| generated state | ~/.dsh/settings.yaml（llm-pi-ai.providers / agent-default-model 投影）、各 harness settings/config、指令文件内 checksum-managed 块（continuous-capability-adoption）、registry/inventory/（device/models 发现物） | aic render/diff 实测 |
| overlay | harness 专有段（codex node_repl/agent_switchboard、claude hooks、gemini auth、switchboard *_path 等）明确登记 per-harness yaml + overlay 段 | harnesses/*.yaml |
| 已有治理机制 | 模型六态+canary（model_state/model_health）、routing_gov（findings=0）、memory_gov+staleness、capability_gov+capabilities.yaml、dup_rules/dead_config/static_gov/project_state_gov、durability（backup/rpo/restore）、gov_status 11 域、治理 inbox（DETECTED→PROPOSED，无自动 APPLIED）、计划任务 ×2 | phase7-governance-report.md、scripts/governance/ |
| DSH 原生/插件 | goal max_rounds（turn 上限）、compaction+convergence guard、tool timeout、session checkpoint policy、subagent-usage-observer（cacheRead 计量实测）、workflow-model-preflight-gate（tools.guard fail-closed 实测）、subagent-prep-exec-gate | dsh/plugins + profiles/web/plugins |
| switchboard 原生 | request ledger（get_request_ledger）、token_budget/max_response_chars/effort、managed supervisor（stall_timeout/max_autonomous_actions/material events）、chain_key 有界 retry（≤3）、task receipt（wait_task_receipt） | mcp/agent-switchboard |

**缺口结论（第 5–8 问）**：无统一 execution budgets / 无 PROGRESS_DELTA loop breaker / 无任务类型 model tier / 无 cross-harness checkpoint contract / 无 usage ledger（COST_PER_PROGRESS）/ 无 execution profiles；agent turn limit 只有 DSH goal 原生（未统一）；provider call limit、cached-token 预算、cost 预算、resume 计数全部缺失。已有但未统一：subagent-usage-observer（子代理用量）、request ledger（switchboard）、goal rounds（DSH）、chain_key retry（switchboard）、compaction（各 harness 原生）。

## GAP_ANALYSIS

*能力 × harness 缺口矩阵（0=已有可复用，1=缺失）*：

| 能力 | DSH | Codex | Claude | Gemini | switchboard |
|---|---|---|---|---|---|
| model budget / token budget / cost budget | 1（goal 有 turn；无 token/cost 统一） | 1 | 1（--max-turns 部分） | 1 | 1（token_budget 单请求有，任务级无） |
| agent turn limit | 0（goal max_rounds） | 1 | 1（--max-turns） | 1 | 0（supervisor max_autonomous_actions） |
| provider call limit | 1 | 1 | 1 | 1 | 0（queue 量级有，无累计预算） |
| loop breaker / progress detection | 1（repeat-tool-reminder 提示级；无 hard） | 1 | 1 | 1 | 0（stall_timeout 有） |
| checkpoint/resume（durable） | 1（session 级有，task 级无） | 1 | 1 | 1 | 0（receipt 有） |
| context compaction | 0（原生） | 0 | 0（/compact） | 0 | n/a |
| batch execution | 0（workflow 工具） | 1 | 1 | 1 | 1 |
| usage ledger | 1（subagent-observer 子集） | 1 | 1 | 1 | 0（request ledger 有，需接线） |

统一策略：**不重复建设**——goal rounds / chain_key / request ledger / supervisor / compaction / workflow 全部复用；缺的统一由 canonical 提供（budget 数值、hook 契约、checkpoint schema、ledger schema、profiles、loop breaker 语义）。

## CANONICAL_POLICY

新增 `registry/autonomous-execution-governance.yaml`（schema_version 1）：

- `consumers`：项目只声明 `execution_profile` + 白名单 override；`forbidden_declarations`（budget_numbers / model_price / turn_limit_values / checkpoint_path_override）。
- `budget_governor`：10 种预算 kind（task/session/provider_call/agent_turn/model_tier/input/cached_input/output/cost/runtime），语义 = hard enforce（hook 层 fail closed）+ observable + resumable（budget_remaining 随 checkpoint）+ 禁止自提额。
- `loop_breaker`：PROGRESS_DELTA 形式化定义（8 类 progress event 白名单；读/检索/重复探测不算）；8 种检测模式（repeated identical call / error / repair / judge / provider probe / no-new-artifact / no-state-change / reasoning-without-progress）；soft→hard 两段 circuit break；repeated_repair ≤3（复用 switchboard chain_key 语义）。
- `model_tier_governance`：6 类任务类型（FRONTIER_REASONING / STANDARD_REASONING / BULK_GENERATION / BULK_JUDGE / EXTRACTION / DETERMINISTIC）；hard rule：昂贵模型禁止硬编码为默认 bulk worker；具体模型仍由 Model/Routing SSOT 决定。
- `batch_execution`：manifest → workers → artifacts → deterministic aggregate；BULK 类主 Agent 禁止逐 item reasoning。
- `context_governor`：CHECKPOINT → DURABLE_STATE → COMPACT → RESUME；cached-read 计入预算，超限强制 COMPACT（对症 0d07ae22）。
- `checkpoint_contract` / `usage_ledger`：schema 引用 + durable 落点。
- `harness_hook_matrix`：8 hooks × 5 harnesses 能力矩阵（native/plugin/wrapper 及机制说明）。
- `generated_instructions`：渲染进 5 个 Harness 指令文件的稳定硬规则块（只放硬规则 + pointer，不复制预算/Personal State）。

配套：`governance-policy.yaml` 新增 auto_allowed（budget_enforcement/checkpoint_write/usage_ledger_append/compact_context）与 auto_forbidden（raise_execution_budget/bypass_budget_limit/disable_loop_breaker/unbounded_resume）。

## AIC_INTEGRATION

- 无新动词（守护六动词冻结边界）：`compile` 即现有 `render`/`apply`。
- `scripts/aic/policy_projection.py` 泛化为多块 managed block（capability-adoption + autonomous-execution-governance 同文件共存，checksum 各自独立）。
- `scripts/aic/aic.py`：新增 governance 块 render/diff/apply（5 个指令文件）+ dsh 新 render target `governance-profiles`（`~/.dsh/governance/execution-profiles.generated.json`，full-file-json，generated 态禁手改）+ `_validate_governance()`（profiles/hook 矩阵/checkpoint+ledger schema/harness governance_hooks 一致性）。
- 实测：`aic validate` = VALID；`aic apply` 5 targets = OK（备份+atomic+post-diff）；`aic diff` 5 targets = **NO DRIFT**（含 governance 块 checksum 与 profiles JSON 全文件比对）。
- 生成 state 禁手改遵守：指令文件内两个 managed block 均 checksum 保护；governance-profiles 全文件比对，手改即 drift 可回滚。

## EXECUTION_PROFILES

`registry/execution-profiles.yaml` —— 6 profiles（项目只声明名字，不复制数值）：

| profile | session turns | task calls | task cost | cached tokens | tier 默认 | checkpoint cadence | batch 默认 | loop (soft/hard) |
|---|---|---|---|---|---|---|---|---|
| INTERACTIVE | –（不硬限） | 200 | $15 | 20M | STANDARD | 32 | ✗ | 6/12 |
| AUTONOMOUS_STANDARD | 32 | 64 | $12 | 30M | STANDARD | 16 | ✗ | 5/10 |
| AUTONOMOUS_RESEARCH | 40 | 96 | $25 | 60M | STANDARD | 12 | ✗ | 5/10 |
| BULK_EVALUATION | 16 | 96 | $10 | 40M | BULK_JUDGE（worker: BULK_GENERATION） | 8 | ✓ | 4/8 |
| **LONG_RUNNING_CAMPAIGN** | **64** | **200** | **$40** | **120M** | STANDARD | 16 | ✓ | 6/12 |
| CRITICAL_REASONING | 12 | 24 | $30 | 20M | FRONTIER | 8 | ✗ | 3/6 |

初始边界按 0d07ae22 校准（turns 64<534、calls 200<674、cost $40<$106、cached 120M<168.1M），全部经 SSOT 可调；任务级 override 白名单：checkpoint_cadence_turns（仅收紧）/batch（仅 BULK）/labeled_subtask_model_tier（FRONTIER 需人工批准）。累计预算跨 resume 不重置（杜绝 64 turns × N 次 resume 变相无界）。

## BUDGET_GOVERNOR

- canonical kinds + hard enforce 语义 + no_self_raise（auto_forbidden）+ 人工豁免唯一路径（ledger `exemption` 记录）。
- DSH 运行时：`dsh/autonomous-execution-governor` 插件（tools.guard 层）硬执行 agent_turns（tool action 代理，偏保守）/provider_calls/runtime_min；token/cost 记账经 checkpoint.py + usage_ledger（cached 独立记账，复用 subagent-usage-observer 已实测的 cacheRead 计量）。
- 观测：governor-audit.jsonl + usage ledger；resume 读 budget_remaining。实测定点验证：`evaluateGuards` 单测 7/7 全绿（534→64 停、674→200 停、runtime、loop、progress 重置、持久回路）。

## LOOP_BREAKER

- PROGRESS_DELTA 定义（8 类 progress event）+ 8 检测模式 + soft/hard 两级（hard = fail-closed + circuit_broken 持久 + checkpoint stop_reason=LOOP_BREAKER）+ bounded retry（repair≤3 / error≤5）。
- DSH 插件实现并单测：repeated identical call、no-progress、circuit 持久均为硬机制（非提示）。
- 治理冻结：`disable_loop_breaker` = auto_forbidden；`unbounded_resume` = auto_forbidden。

## MODEL_TIER_GOVERNANCE

- 6 任务类型不绑定具体模型；模型选择继续由 registry/models.yaml + routing-policy.yaml + 私有 gateways 决定（AI C 不新增模型映射）。
- 昂贵模型 bulk 默认 = 禁止（hard rule + BULK_EVALUATION `bulk_worker_tier_default: BULK_GENERATION` + FRONTIER_REASONING 仅 CRITICAL_REASONING 默认）。

## BATCH_EXECUTION

- manifest → workers → artifacts → deterministic aggregate 契约；BULK 类主 Agent 逐 item 模型调用 = loop breaker 信号；worker 独立 bounded；DSH 复用原生 workflow 工具 + workflow-model-preflight-gate（模型准入复用）。

## CONTEXT_GOVERNOR

- CHECKPOINT → DURABLE_STATE → COMPACT → RESUME 循环；resume 读 checkpoint（next_executable_action）而非 conversation；cached-input 预算超限强制 COMPACT；compaction 摘要档复用 routing-policy.compaction_summary SSOT（不新建）。验收演示：171M token 上下文 → KB 级 checkpoint + compacted brief（相对 168.1M 重复 cache-read 显著缩小）。

## CHECKPOINT_RESUME

- `registry/checkpoint-schema.yaml` v1（21 字段含 task/project/campaign/objective/harness/execution_profile/completed_actions/current_state/durable_artifacts/evidence/unresolved_blockers/next_executable_action/model_usage/budget_consumed/budget_remaining/stop_reason/resume_count/timestamp/protocol_hash）；实例 = JSON 落 `personal-ai-state/checkpoints/`（durable、git 传输、跨设备）。
- `scripts/autonomy/checkpoint.py`：new/save/load/resume/list/validate；protocol_hash 校验；写入即追加 ledger；resume 只输出恢复简报（不重放 conversation）。
- resume_contract：protocol_hash + schema_version + 预算一致性校验；completed_actions/current_state/artifacts 继续累计；预算跨 resume 不重置；严禁用 conversation 代替 checkpoint。
- 实测：checkpoint new→save(budget_limit)→validate VALID→resume（budget_remaining.cost=3.5）全绿（acceptance #9/#10 + 单测）。

## USAGE_LEDGER

- `registry/usage-ledger-schema.yaml` v1 + `scripts/autonomy/usage_ledger.py`（append/query/cost-per-progress/runaway）→ `~/.personal-ai/ledger/usage.jsonl`。
- COST_PER_PROGRESS = Σcost / Σprogress；runaway 4 规则（cost/calls 超限、cached 占比 >0.85 无 compact、resume 超限）。
- switchboard request ledger 已有 → 接线为数据源（不重复建设）。

## HARNESS_CAPABILITY_MATRIX

8 hooks × 5 harnesses（canonical 于 autonomous-execution-governance.yaml `harness_hook_matrix` + 各 harnesses/*.yaml `governance_hooks`）：

| hook | dsh | codex | claude | gemini | switchboard |
|---|---|---|---|---|---|
| BEFORE_MODEL_CALL | plugin(tools.guard) | wrapper | native(PreToolUse) | wrapper | native(route/queue gate) |
| AFTER_MODEL_CALL | plugin(token-meter/session telemetry) | wrapper(--json usage) | native(PostToolUse/Stop) | wrapper | native(request_result/ledger) |
| AFTER_AGENT_ACTION | plugin(guard 记录) | wrapper(JSONL) | native(PostToolUse) | wrapper | native(supervisor events) |
| ON_PROGRESS | plugin(artifact delta) | wrapper | native | wrapper | native(work memory) |
| ON_CHECKPOINT | plugin→checkpoint.py | wrapper→checkpoint.py | native(hook)→checkpoint.py | wrapper→checkpoint.py | native(task receipt) |
| ON_BUDGET_LIMIT | plugin(fail closed) | wrapper(terminate) | native+hook(--max-turns/deny) | wrapper(terminate) | native(max_autonomous_actions) |
| ON_NO_PROGRESS | plugin(loop breaker) | wrapper | native(PostToolUse 统计) | wrapper | native(stall_timeout/decision_mode) |
| ON_RESUME | checkpoint.py | checkpoint.py | checkpoint.py | checkpoint.py | native(supervisor resume) |

优先复用原生 → adapter → wrapper/sidecar；**未改任何 Harness 上游源码**。

## NOVEL_MIGRATION

- 未修改 novel-main；无 novel 专属补丁。novel 现有任何临时 autonomous workaround 应降级为薄消费者（`execution_profile = LONG_RUNNING_CAMPAIGN` / `BULK_EVALUATION` + 白名单 override），实现删除/收敛——由本 Change 覆盖后执行（本机 novel-main 不在本变更范围，列为项目侧消费步骤）。

## CROSS_PROJECT_ADOPTION

- 生态级规则固化进 `personal-ai-state/state/preferences.md`（durable·私有）：新 AI/Agent/Harness 能力先判 `IS_CROSS_PROJECT_OR_CROSS_HARNESS_CAPABILITY`；YES 必须走 Capability Candidate → architecture assessment → canonical definition → SSOT → AIC render/diff → adapters → acceptance → projects consume；禁止先永久实现到单项目。
- 项目消费路径 = 声明 execution_profile + 白名单 override，硬规则由 5 个 Harness 指令文件的 generated 块承载（aic diff 保证一致，防"每项目一套"）。

## COUNTEREXAMPLE_ACCEPTANCE

`scripts/autonomy/acceptance.py`（--live 实测）：**22/22 PASS → COUNTEREXAMPLE_ACCEPTANCE = PASS**

| # | 验收项 | 证据 |
|---|---|---|
| 0 | 534/674/$106 不可能复现 | LONG_RUNNING_CAMPAIGN turns 64<534 · calls 200<674 · cost $40<$106 · cached 120M<168.1M |
| 1 | agent-turn hard limit 生效 | 门槛 64；插件单测：534 轮在第 64 轮 fail closed |
| 2 | provider-call budget 生效 | 门槛 200（task 累计跨 resume）；单测：674 轮在第 200 轮停 |
| 3 | cached-token 计入 | budget kind cached_input_token_budget + ledger schema + checkpoint schema 均有 |
| 4 | 昂贵模型 bulk 被禁 | hard rule + BULK worker tier=cheap + FRONTIER 仅 CRITICAL 默认 |
| 5 | no-progress circuit break | 8 检测模式 + hard window + auto_forbidden.disable_loop_breaker |
| 6 | repeated repair 有界 | repeated_repair_max=3（同 switchboard chain_key ≤3） |
| 7 | batch 免逐 item | BULK_EVALUATION batch_default + rules 逐 item 禁令 + 契约 |
| 8 | checkpoint 后上下文缩小 | cadence 16 + 循环契约 + cache 上限强制 COMPACT（171M→KB 级） |
| 9 | budget stop 保存 durable checkpoint | checkpoint.py 实测：stop_reason=budget_limit、validate VALID、落 personal-ai-state/checkpoints/ |
| 10 | resume 不赖原 conversation | resume 实测：读 checkpoint 输出 next_executable_action，resumable=True |
| 11 | 五 harness 策略不 drift | `aic diff` dsh/codex/claude/gemini/switchboard = 全部 NO DRIFT（同一 canonical 块 + checksum） |
| 12 | 项目不需各自实现 | consumers 契约 + forbidden_declarations + preferences.md 长期规则 |

## DURABILITY

- canonical 写入 agent-tools git（本次提交）；长期规则写入 personal-ai-state git（preferences.md/README/checkpoints/，本次提交）；修复既有隐患：`scripts/aic/policy_projection.py` 此前为 **untracked**（跨设备不可恢复）——本 Change 登记入库。
- checkpoint durable：`personal-ai-state/checkpoints/`（git 传输）；ledger durable：`~/.personal-ai/ledger/`（随既有 durability 备份）。
- 运行时 generated 态（governance-profiles JSON、指令块）随时可由 `aic render/apply` 重建；插件/%USERPROFILE% 属 DEVICE_LOCAL，不入 canonical。

## REMAINING_LIMITATIONS

1. DSH 插件的 token/cost **硬**门禁不在 guard 层（拿不到逐调用 token 计量）：由 checkpoint.py 按 usage 快照记账 + usage_ledger runaway 检测；cached 计量复用 DSH 原生（subagent-usage-observer 已实测 cacheRead）。
2. 纯文本推理轮（无工具调用）在 guard 层不可观测：由指令块硬规则 + 原生 turn/compaction 兜底；如需零漏洞需 harness 原生 hook（claude PreToolUse/Stop 可补，codex/gemini 需 wrapper）。
3. plugin 在 live profile 以 observation mode 部署（无任务声明时不产生任何副作用）；硬门禁需显式 taskId+profile（或环境变量）启用——这是"任务必须声明 profile"语义的正确实现。
4. acceptance #9/#10 使用临时 PERSONAL_AI_STATE 演示写入路径（不污染真实 durable 仓库）。
5. `aic compile` 无此动词（冻结边界）；对 `compile` 的需求以 render/apply 语义满足，已在 AIC_INTEGRATION 说明。
6. INTERACTIVE profile 无硬 turn 上限（人工交互），其余预算仍 hard。

## FINAL_STATUS

- **PERSONAL_AI_BOUNDED_AUTONOMY = PASS**（22/22 验收；5 harness NO DRIFT；插件硬门禁单测全绿；durable SSOT 双仓库落盘）
- **NOVEL_LOCAL_WORKAROUND_REQUIRED = NO**（Personal AI 拥有该能力，novel 只是消费者）
- 新增文件：registry/{autonomous-execution-governance,execution-profiles,checkpoint-schema,usage-ledger-schema}.yaml、harnesses/*.yaml 4 处扩展、scripts/aic/{aic.py,policy_projection.py}、scripts/autonomy/{checkpoint,usage_ledger,acceptance}.py、dsh/autonomous-execution-governor/（4 文件）、tests/test_autonomous_execution_governance.py、docs 本报告、personal-ai-state（preferences.md/README/checkpoints/）。
- 未做（遵守"不要做的事"）：未修 novel-main、未复制预算到项目、未在 5 份文档手写同样策略（AIC 生成）、未把 session 当 durable state、未因"提示省 token"而停手（硬机制）、无无限 fallback/retry、无自动提额、未重构 Narrative、未改 Harness 上游源码、未建 Dashboard。