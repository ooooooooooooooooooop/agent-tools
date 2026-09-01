# AUTOMATIC_EXECUTION_PROFILE_ADMISSION_REPORT

> 变更：Migration #8.1（Automatic Execution Profile Admission — AUTONOMOUS_EXECUTION_GOVERNANCE 关闭项）
> 日期：2026-08-31 · 范围：Personal AI 生态级能力（cross-project / cross-harness）
> 前提：AUTONOMOUS_EXECUTION_GOVERNANCE（Migration #8）已验收（PASS / 22-22 / 182 tests）——**本次不重新设计，只关闭"autonomous task 需显式声明 profile"的缺口**。

---

## 1. 关闭前的缺口（现状确认）

| 项 | Migration #8 后的状态 | 缺口 |
|---|---|---|
| profile 如何显式声明 | DSH governor patch `config.taskId + config.profile`（或 AE_GOV_TASK_ID/AE_GOV_PROFILE）；checkpoint.py `--profile` | 用户必须知道并手写内部 profile 名 |
| 未声明 profile 时 | governor 零副作用观察；checkpoint.py 拒绝（`--profile 或 --auto-admit` 缺一即 INVALID） | 明确 autonomous task 无任务声明时无 hard enforcement |
| hard enforcement 是否因此不生效 | 是——observation mode 无预算约束 | **UNKNOWN 可能 ≈ UNBOUNDED** |
| 已存在的 task classifier / admission | 无（grep 全仓零命中） | 需新建（Personal AI 资产，非项目） |
| 可复用 | switchboard receipt / usage ledger / checkpoint durable state / AEG 预算与 hook 矩阵 | 全部复用，不重复建设 |

## 2. Automatic Profile Admission（实现）

**Canonical 规则（SSOT-first）**：`registry/autonomous-execution-governance.yaml#profile_admission` 新增节：

- `flow`: TASK → admission → profile → budget → model_tier_policy → first expensive call（`admission_before_first_call: true`）
- `safe_default: AUTONOMOUS_STANDARD`；`unknown_not_unbounded: true`
- `classification_signals`：7 类行为特征（bulk_repetition / bulk_verb / campaign / research / reasoning_critical / interactive / scope_small），权重在 canonical
- `decision_rules`：确定性优先级（interactive → campaign → bulk → critical → research → safe default）
- `bulk_detection`：`bulk_workload=true` ⇒ 强制 batch（manifest→workers→artifacts→aggregate），禁主 Agent 逐 item
- `escalation`：有向 graph（STANDARD→RESEARCH/CAMPAIGN、CAMPAIGN↔BULK、INTERACTIVE→STANDARD、CRITICAL 无出口）+ 约束（不重置累计 usage / 不自动提额 / 不绕过 hard cap / reason 必填 / 写 receipt+checkpoint / consumed≥90% 拒绝 widening / **禁止 Agent 自行选更宽松 profile**）
- `cross_harness`：admission 结果 = durable state，各 harness 可读；无法完整 enforcement 的 harness 获得正确 profile projection

**Classifier（Personal AI 资产）**：`scripts/autonomy/profile_admission.py`（deterministic，规则从 canonical 读取，项目禁止复制）：

- `run --task --project --objective [--declare]`：分类 + 落盘 `personal-ai-state/checkpoints/<task>.admission.json` + ledger `kind=admission`（含 confidence/bulk_workload/reasons/admitted_before_first_call）
- `status` / `escalate`（graph+reason+consumed 校验，写 checkpoint 与 admission 记录 + ledger `kind=escalation`，拒绝时非零退出并说明原因）
- 特征捕获：数量/重复结构（含 magnitude 提取，如 "100 个"→100）、同构动词、阶段语义、研究语义、推理关键性、交互性、scope；`--declare`（batch_size/campaign/bulk/reasoning/research/autonomy/expected_provider_calls）为强证据。**不是纯关键词匹配**（数值规模 + declare 叠加 + 确定性优先级）。

**接线**：
- `checkpoint.py new --auto-admit`（无 `--profile` 时自动 admission，profile 写入 durable checkpoint + admission 记录）——任务创建即绑定，先于首个执行动作。
- DSH governor（`dsh/autonomous-execution-governor`）：`taskId` 声明 = autonomous task；`profile` 可省略 → 首个 guard 自动调 classifier（objective 缺省 → UNKNOWN → safe default STANDARD）后绑定 hard enforcement；显式 profile 仍兼容；无 taskId = 普通会话零副作用（非 autonomous 义务）。live profile 已更新（autoAdmit: true + admissionScript 路径）。
- switchboard / codex / claude / gemini：通过 durable admission 记录 + Context Package / receipt 携带 `execution_profile`（复用既有通道），指令文件 projection 提供稳定规则（Execution Profile Admission 硬规则 8 已投影）。

## 3. 验收（`scripts/autonomy/admission_acceptance.py`，14/14 = PASS）

| 项 | 断言 | 结果 |
|---|---|---|
| A 明确 bug | → AUTONOMOUS_STANDARD（bounded） | PASS |
| B 复杂研究 | → AUTONOMOUS_RESEARCH | PASS |
| C 100 Judge pair | → BULK_EVALUATION + bulk_workload + 逐 item 禁令 | PASS |
| D 多阶段 campaign | → LONG_RUNNING_CAMPAIGN | PASS |
| E 模糊任务 | → safe default STANDARD（UNKNOWN ≠ UNBOUNDED；无 unbounded profile）| PASS |
| F admission 先于首个执行 | auto-admit 落盘 + admission record 早于 usage record + 绑定后 hard policy | PASS |
| G resume 不重置累计 | budget_consumed 原样保留、remaining 按新 profile 重算、resume_count+1 | PASS |
| H 不可自改 profile 绕开 | weak reason 拒、非 graph 目标拒、拒绝后 profile 不变；合法 evidence escalation 成功但 **usage 不重置** + reason 记录 | PASS（H+H2）|
| I 五 harness 无 drift | `aic diff` ×5 = NO DRIFT（块含 admission 规则 8，sha256 校验） | PASS |

## 4. CPA / Gateway Future Boundary（调查，不实现）

CPA（127.0.0.1:8317 local relay，openai-compat）与 cc-switch 均为本地 gateway：**无 request/resolved model/tokens/cost 的 usage hook**（registry 与 docs 零命中）。接入路径（未来）：gateway 暴露 usage 事件或 usage 端点 → 上游读 request/resolved model/input/cached/output/cost → 折算 `cost_budget` 实现 Personal AI global hard budget（复用 AEG budget kinds）。本轮**未修改 CPA 上游**。

**GATEWAY_USAGE_HARD_ENFORCEMENT = FUTURE_CAPABILITY**

## 5. Durable SSOT

- `registry/autonomous-execution-governance.yaml`（profile_admission 节 + 生成块规则 8）、`registry/usage-ledger-schema.yaml`（kinds + admission/escalation）、`scripts/autonomy/profile_admission.py`、`checkpoint.py --auto-admit`、governor autoAdmit、tests、本报告 — 提交 agent-tools。
- `personal-ai-state/state/preferences.md`（"用户无需声明 execution_profile" 长期规则）— 提交 personal-ai-state。
- 项目只消费 admission 结果；无 per-project classifier 复制。

## 6. 最终状态

- **AUTOMATIC_PROFILE_ADMISSION = PASS**（14/14）
- **AUTONOMOUS_TASK_WITHOUT_PROFILE_CAN_RUN_UNBOUNDED = NO**
- **USER_MUST_DECLARE_PROFILE = NO**
- 冻结：Autonomous Execution Governance 随本 Change 冻结；未扩建 Dashboard / 治理层 / Novel 专用机制。
- 回归：191 tests OK；validate_repo PASS；git diff --check clean。