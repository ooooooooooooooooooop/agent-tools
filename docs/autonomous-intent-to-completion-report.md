# AUTONOMOUS_INTENT_TO_COMPLETION Report

> Change 类型：独立执行语义 Change（非 Architecture Phase，未新增 Agent framework / Skill）。
> 数据源：`~/.dsh/sessions`（601 会话，含 536 子代理会话 / 65 主会话）、`~/.dsh/.evolution-inbox`、`~/.agent-broker/topics/skills/evolution-inbox/workspace/inbox.jsonl`（411 条异常条目）。
> 工具：`scripts/autonomy_trace_scan.js`（本 Change 新增的治理扫描器，0-token 机械扫描）。
> 证据目录（本地运行态，不进发布集）：`.taskflow/autonomy-audit/`（feedback-hits.jsonl 103 条、session-metrics.jsonl 601 条、eval-dataset.jsonl 14 案例、runtime-metrics.json、failure-inventory.md）。

---

## 1. Failure Trace Inventory

扫描方法：全量解压 601 个会话的多帧 zstd JSONL，命中用户负反馈短语（任务书第 1 节词表）后抽取前后执行上下文（前一条 assistant 文本、前 8 个工具调用、后续用户反应），再由 Discovery 子代理逐条读上下文分类（不只按关键词判定：正常交互显式排除为 NOT_A_FAILURE，歧义案例保守处理并内联说明）；orchestrator 对全部非 continuation 命中与重点会话做了独立精读，两处判定收敛互验。

**机械扫描结果**：103 条命中 / 22 会话。CONTINUATION_PUSH 100、PREMATURE_STOP 3、GOAL_DRIFT 1、UNNECESSARY_CLARIFICATION 1；PREPARATION_LOOP / VERIFICATION_LOOP / RECOVERY_HINT 短语 0 命中（这两类由 Skill 文献化案例与工具轨迹覆盖）。

**关键词外的证据簇**（工具轨迹识别）：

- **REPEATED_TOOL_FAILURE 簇**：novel-main 项目一批只读审计子代理同签名调用连跑 10–25 次。最重：`5a6d63a0-80af-44ac-9a8e-fe5ab3bb62ef` 同一 pwsh 读文件命令 36 次调用中重复 25 次。全库 maxIdenticalRun≥4 的会话 44 个（绝大多数是 [PREP] 审计子代理）。
- **等待即停滞簇**：`session-d249ef16`（117 条用户消息、2534 steps、1569 万 input）——Agent 反复停在"继续等"守候态，用户发"？怎么又停了"，随后"记得提交GitHub"才把主线拉回（RECOVERY_WITHOUT_RESUME 叠加 PREMATURE_STOP）。
- **判断卸载簇**：`session-2f8bc`——Agent 把 personalization effectiveness 终判转成 18 案例人工逐条评审（HUMAN BLIND REVIEW），用户定性"错误的交互设计……把最终判断转嫁给用户"，改为 BEHAVIOR-GROUNDED。

**分类清单**：`.taskflow/autonomy-audit/failure-inventory.md`（103/103 逐条主分类 + 证据 + 保守 NOT_A_FAILURE 判定 + 歧义内联说明）。

**分类计数**（命中级）：PREMATURE_STOP 18 / VERIFICATION_LOOP 5 / SUBAGENT_STALL 5 / GOAL_DRIFT 3 / UNNECESSARY_CLARIFICATION 2 / PREPARATION_LOOP 1 / INVALID_BLOCKER 1 / REPEATED_TOOL_FAILURE 1 / CONTEXT_ANXIETY 1 / RECOVERY_WITHOUT_RESUME 1 / NOT_A_FAILURE 65。
**去重注意**：d249e 家族事件在主会话 + 2 个 fork 子会话中各出现一份拷贝（如"？怎么又停了"、"jix" 拼音催促），事件级口径应除以约 3；NOT_A_FAILURE 65 条证实词表误报率可控（如 SSE 瞬断后"继续"、用户主动 stage-gating、用户中断后续作均正确排除）。

**读上下文确认的代表性失败事件**：session-e30ae 用户两次原话"你是来解决问题的，不是记录阻塞的，不要添加人工部分"；session-d249e 守候态停摆；session-2f8bc 18 案例人工评审转嫁终判（#78/#80）；session-67e77 "我并没有见到你调用opus5"（声明派发≠实际使用，GOAL_DRIFT）；session-d1f92 "不是现在在对话里面总结下次又继续犯"（持久目标被防再犯清单替换）；session-d4976 证据新鲜度混淆（CURRENT/LAST_KNOWN/UNAVAILABLE 未分离 → INVALID_BLOCKER）。

**关键词无法捕获的新模式**（分类发现，已并入治理视野）：①元任务转嫁（把评审/联网判断/执行接管交给用户）；②watchdog-token polling（状态查看替代事件等待，无 Progress Delta）；③完成声明与 Done-when 脱节（PARTIAL 未闭合却汇报 PASS）；④证据新鲜度混淆；⑤代理金标准冒充真实验证（机制有效≠现实有效）；⑥空助手回合的主/子层级差异。

**过程说明**：首版 Discovery 派发（无行动门槛）自身陷入 PREPARATION_LOOP（162 调用/0 写入/18.9M tokens）；经 EVIDENCE_SUFFICIENT 指令 + interrupt 后该子代理在第二回合交付了上述完整清单；期间 orchestrator 曾按五级阶梯独立产出一版事件级清单作为兜底，两者对核心事件判定一致（收敛互验）。该事件同时成为契约必要性的实测证据（见第 17 节 A/B 对照）。

**既有 Skill 文献化案例**（本身即真实 trace 蒸馏物，直接并入清单）：execution-discipline 反面案例表 20 行（13h/72 轮主会话、44 分钟 WAIT→GET 轮询、Turn7 纯轮询空转、5 小时子代理静默、打断方向蒸发等）；subagent-execution-governance 6 行（`session-33b69ec9`：b21b9211 读 65 文件无写入、17 子代理 309 万 input token）。

## 2. Current Rule Conflicts（EXECUTION_RULE_CONFLICT_MATRIX）

| # | rule | source | trigger | priority | conflicts_with | observed_failure | proposed_authority |
|---|------|--------|---------|----------|----------------|------------------|--------------------|
| A | 只有答案改变目标/业务语义/不可逆范围才提问，可逆假设继续 | clarify-before-change §证据门禁 | 任何修改前 | **canonical（窄询问边界）** | unified-taskflow Phase 0"假设逐条用户确认"（SKILL.md 旧 L61/L148、phase0-clarification.md 旧完备性门禁） | session-2f8bc：18 案例逐条人工评审被用户斥为错误交互设计；用户在新 Change 指令中被迫反复内嵌"自己判断/不要问我" | **clarify-before-change**。taskflow Phase 0 已改：可逆假设记录即推进，仅目标级/不可逆假设需确认 |
| B | 遇阻自救三连完成前禁止 BLOCKED | execution-discipline + ~/.dsh/AGENTS.md §5 | 遇阻 | **canonical（停止/恢复权威）** | unified-taskflow 3-Strike"停止尝试，升级给用户"（旧 SKILL.md L123-128、governance.md "3 次失败必须停止"硬性条款） | 2026-08-24 会话 goal 8/8 耗尽悬死；3-Strike 为过早停止提供规则借口 | **execution-discipline**。3-Strike 已重解释为 DEADBAND_TRIPPED→策略重置→换路线；仅 Recovery Ladder 穷尽且满足合法停止策略才可报用户 |
| C | 每 3 checkpoint 对抗审计 / 每 5 checkpoint 自检（固定周期） | decision-gates 闸门 1/4 | 长任务 checkpoint | medium | 真实执行：验证/监视压倒进展 | 2026-08-24 44 分钟 WAIT→GET 轮询；2026-08-26 Turn7 16 调用 9 次纯轮询 38 分钟；主会话 shell 命令 14.9% 是测试/验证 | **事件驱动**。闸门 1 改 8 类触发事件（最终验收必跑；≥5 checkpoint 无事件补一次低成本自检兜底）；0-token 机械门禁（闸门 2/3/4/5/6）保留 |
| D | "信息不足立即暂停询问"（RIPER-Core #4） | unified-taskflow SKILL.md | 信息不足 | — | clarify-before-change 自主解析顺序 | 反复澄清类反馈（E01） | clarify-before-change：先 self-serve，穷尽后才问（已改） |
| E | 大型任务判定后主会话硬熔断强制委派 | task-mode-router §大型任务 | 大型任务 | medium | 低风险可逆动作偏好 | 流程惯性导致"能直接做也先走重流程" | 保留硬熔断（安全），但路由顺序增加低风险可逆动作偏好（已改） |
| F | "不确定性"事件 → 暂停请示 | unified-taskflow governance/regrounding | 模糊决策 | — | 可逆假设记录即推进 | 过度暂停请示 | 仅 ❌ 明显偏移（Critical Constraint/Scope.Exclude 违反）暂停；⚠️ 自行调整（已改） |

## 3. Final Autonomy Contract（统一自主执行契约）

运行语义，不新增文件格式、不新增 SSOT。Canonical 文本在 `execution-discipline`「自主执行契约」节；字段映射：

```text
INTENT                → goal objective / anchor.md Intent / 子代理契约 GOAL
DONE-WHEN             → anchor Done-when / 契约 EXIT / 验收标准
CRITICAL CONSTRAINTS  → anchor Critical Constraints / 契约 MUST PRESERVE·MUST NOT
NON-GOALS             → anchor Scope.Exclude / 契约 OUT OF SCOPE
AUTONOMY BOUNDARY     → clarify-before-change「意图解析策略」
CURRENT STATE         → checkpoint.md / task-state.json / work_memory
NEXT BEST ACTION      → checkpoint 下一步（任一时刻有且仅有一个最优下一步）
VALID STOP CONDITIONS → execution-discipline「合法停止策略」
```

## 4. Intent Resolution（Canonical：clarify-before-change「意图解析策略」）

- **自主解析顺序**：当前上下文 → Memory/Project State → 仓库/config/tests → 历史 trace → web/docs → 可逆实验。穷尽前不得询问。
- **可逆假设**：可逆 + 低成本 + 不改目标 + 无重大外部后果 → 选最合理默认值、登记 assumption、继续。禁止暂停等待确认。
- **允许询问**：仅答案实质改变 objective / 业务语义 / 不可逆动作 / 隐私安全暴露 / 有意义成本 / 互斥价值偏好；最多一轮问完。
- **续接语义**："继续/做完/接着做/自主推进/按之前目标完成" = 既有 objective/constraints/non-goals 内继续推进的授权；禁止重选计划、重确认、重跑 Phase 0；先恢复 state → 未闭合 done criteria → next best action，直接推进。

## 5. Progress Governor（Canonical：execution-discipline）

- **PROGRESS_DELTA** 五类：DONE_CRITERION_CLOSED / MEANINGFUL_MUTATION / BLOCKER_REMOVED / DECISION_RESOLVED / NEW_ACTION_CHANGING_EVIDENCE。
- 不算 progress：重复读取/测试/总结/审计、无决策作用报告、换措辞重复搜索、再派 Agent 得相同结论。空 touch 不算 mutation（防 Goodhart，§28）。
- **NO_PROGRESS_STREAK**：连续无 delta → 禁止同类动作；重读 objective → Distance-to-Done 判断 → 改策略 → 优先可逆具体动作。
- **Retry 必须声明 WHAT_CHANGED**（query/tool/assumption/environment/implementation 至少一项）；无变化变量 = 禁止 retry；不以固定魔法轮次为唯一判定。
- **Distance-to-Done Guard**：新工作项必须闭合 Done Criterion / 移除阻塞 / 是必要前置，否则 OUT_OF_PATH 默认不执行（拦截顺手重构/benchmark/补文档/研究/优化/扩架构）。

## 6. Verification Policy（decision-gates）

- **VERIFICATION_PURPOSE**：验证动作前必答 hypothesis / uncertainty_removed / pass_action / fail_action；pass==fail 且无新风险证据 → 跳过。
- **事件驱动验证**：闸门 1 触发事件 = objective 变更 / 关键假设证伪 / 不可逆高风险边界 / 多 worker 集成 / 证据冲突 / 连续无 Progress Delta / 重大架构决策 / 最终验收（必跑）；≥5 checkpoint 无事件补低成本自检兜底。
- 0-token 机械门禁（scope lock / cost / consistency / deadband / selfcheck）保持低成本执行，未删除任何安全门禁。

## 7. Recovery Ladder（Canonical：execution-discipline）

```text
Diagnose → Narrow Reproduce → Inspect Local Evidence → Search External Evidence
→ Alternate Route → Repair → Verify → RESUME ORIGINAL GOAL（强制最后一步）
```

遇阻自救三连保留为快速版；RESUME ORIGINAL GOAL 强制——修复子问题后必须恢复 original objective 与被中断的 next action，禁止子问题无限优化（RECOVERY_WITHOUT_RESUME 判定为失败模式）。

## 8. Stop Policy（Canonical：execution-discipline）

**合法停止五类（机器可审计）**：DONE / TRUE_HUMAN_JUDGMENT_REQUIRED / IRREVERSIBLE_OR_HIGH_RISK_AUTHORIZATION_REQUIRED / EXTERNAL_CAPABILITY_BLOCKER / VERIFIED_EXHAUSTION。

**非法停止**：complex / uncertain / test failed / tool failed once / source not found once / library unfamiliar / another agent failed / context getting long / PARTIAL / gate failed / 模型一时解不出 / "建议后续继续" / "需要进一步研究"——只触发 Recovery / Replan / Continue。

**CONTEXT_ANXIETY**：上下文逼近上限必须 checkpoint → 持久化 → compact/reset → resume；禁止降目标、提前交付 partial、把剩余抛回用户。

## 9. Goal Drift Prevention

- 轻量 re-grounding 仅事件触发（沿用 unified-taskflow 事件表），四行短格式：Original Intent / Current Done State / Current Blocker / Next Best Action；不频繁全文审计、不重复注入规则清单。
- 意图漂移二分：用户**显式**新指令 = 目标变更，直接更新 anchor（Version+1）不回头确认；**模糊冲突**（互斥价值偏好）才确认。
- Distance-to-Done Guard 拦截顺手型漂移（§5）。

## 10. Internet / Tool Recovery（Canonical：execution-discipline）

- 网络失败分类：SEARCH_ZERO_RESULT / PAGE_UNAVAILABLE / AUTH_REQUIRED / RATE_LIMIT / TOOL_FAILURE / SOURCE_CONFLICT / UNKNOWN_TERM → 改 query / 官方源 / GitHub / docs / 替代通道 / CLI worker / 更窄探针 / 缓存本地源；全通道排除且阻止下一步才升级阻塞。
- 工具失败一次不是 blocker：transient / bad args / wrong tool / permission / auth / unsupported / environment / real defect 分类自救；禁止同参数同工具反复调用。

## 11. Subagent Integration（subagent-execution-governance）

- 保留 Discovery/Implementation/Validation 角色分离、契约快照（含负空间）、三级有界读取、单写者、行动门槛、结构化回收、repair contract。
- 新增：**探查充分即停**（EVIDENCE_SUFFICIENT，不存在无限 STILL_RESEARCHING）；每报告周期必须带 Progress Delta。
- **BLOCKED 有效性门禁**：七字段（blocked_reason / why_it_blocks_done / evidence / recovery_steps_attempted / alternative_routes_attempted / what_external_fact_is_missing / why_agent_cannot_obtain_it），缺字段 = BLOCKED_INVALID，orchestrator 不接受停止。
- **Orchestrator 五级阶梯**：自己补 context → narrow probe → 换工具/通道/模型 → repair contract → 修订本地计划；走完且满足合法停止策略才报用户。

## 12. Modified Skills

| Skill | 版本 | 改动要点 |
|---|---|---|
| clarify-before-change | 1.1.0→1.2.0 | +「意图解析策略」（canonical 询问边界 + 续接语义） |
| execution-discipline | 0.2.1→0.3.0 | +自主执行契约、Progress Delta/No-Progress Governor、EVIDENCE_SUFFICIENT、Recovery Ladder、合法/非法停止、CONTEXT_ANXIETY、网络与工具失败分类 |
| unified-taskflow | 4.3.0→4.4.0 | Phase 0 可逆假设记录即推进；3-Strike→DEADBAND 策略重置；RIPER#4 改 self-serve；续接语义；执行语义优先级；anchor 模板假设表加可逆性；反模式 +2 行 |
| decision-gates | 0.1.0→0.2.0 | 闸门 1 固定周期→事件驱动（验收必跑保留）；+VERIFICATION_PURPOSE 验证预算 |
| subagent-execution-governance | 0.1.0→0.2.0 | BLOCKED 七字段有效性门禁；orchestrator 五级阶梯；EVIDENCE_SUFFICIENT + Progress Delta |
| task-mode-router | 1.1.0→1.2.0 | +低风险可逆动作偏好（DO 优先于 ASK/PLAN MORE/AUDIT MORE，安全边界仍优先） |

未改：minimal-implementation（语义无冲突，作为变更预算纪律保留）；两个 AGENTS.md（无冲突，避免堆 Prompt，§35）。

## 13. Rule Precedence（EXECUTION_RULE_PRECEDENCE）

```text
Safety / 不可逆授权
> 用户显式目标与约束
> 合法停止策略（Valid Stop Policy）
> 自主续接
> 流程便利
```

已固化：execution-discipline「自主执行契约」为 canonical；unified-taskflow SKILL.md 与 governance.md 均声明"本 skill 流程规则不得覆盖 clarify-before-change 询问边界与 execution-discipline 停止/恢复语义"。`unified-taskflow` 不再覆盖 `clarify-before-change` 的更窄询问规则；3-Strike 不再自动等于用户升级（仅触发 STRATEGY_RESET / RECOVERY_ESCALATION）。

## 14. Historical Replay（Shadow Replay：Before vs New Contract）

对 14 个真实 eval 案例（`.taskflow/autonomy-audit/eval-dataset.jsonl`）逐案重放：

| 案例 | 类别 | 原执行 | 新 Contract | 安全/范围/隐私影响 |
|---|---|---|---|---|
| E01 18案例人工评审 | unnecessary_clarification | ASK×18（卸载终判） | CONTINUE：行为证据自行校准；仅互斥偏好才一次问 | 减少用户负担，无新增风险 |
| E02 Turn7 纯轮询 | endless_testing | 38 分钟 9 次空轮询 | CONTINUE：等待期并行推进 + 单次长轮询 | 无 |
| E03 外部研究惊群 | endless_research | 未探测通道+并发换 4-5 通道+重复核验 | RECOVER→收敛：探测→窄探针→单次接管→≥80% 共识拍板 | 无 |
| E04 b21b9211 读65文件 | prep_without_exec | 941K tokens 无写入 | EXEC：EVIDENCE_SUFFICIENT 即 MUTATE；行动门槛拦截 | 无 |
| E05 19×[PREP] 0×[EXEC] | prep_exec_imbalance | 调查羊群，用户两次发火 | EXEC：配比门禁，PREP 够即派 EXEC | 无 |
| E06 personalization 漂移 | goal_drift | 执行漂成人工评审流 | REPLAN：re-grounding 回行为证据路线 | 无 |
| E07 同一 pwsh ×25 | tool_retry_loop | 同参重试 25 次 | RECOVER：WHAT_CHANGED 门禁，无变量变化禁 retry | 无 |
| E08 web_search 误报 | web_failure | 检索停滞+通道混淆 | RECOVER：TOOL_FAILURE 分类走 CLI worker 替代 | 无 |
| E09 v8 子代理 5h 静默 | subagent_stall | 三次重派耗尽 goal | RECOVER：进度协议+smoke test+STALLED 判定+换通道+恢复原目标 | 无 |
| E10 INVALID 当交付物 | gate_failure_as_stop | STOP+抛回用户 | CONTINUE：门禁=诊断信号，定位根因自主迭代 | 无 |
| E11 12 次压缩风暴 | context_anxiety | 主会话硬扛膨胀 | RECOVER：checkpoint→分阶段交接→resume | 无 |
| E12 打断方向蒸发 | recovery_without_resume | 子问题后主线丢失 | RESUME：强制 RESUME ORIGINAL GOAL | 无 |
| E13 117 条用户消息 | premature_stop | 守候态停摆等用户推 | CONTINUE：等待期并行推进实质工作 | 无 |
| E14 3-Strike 旧语义 | invalid_blocker(rule) | 规则允许过早停止 | REPLAN：DEADBAND 策略重置，停止仅限合法五类 | 无 |

重放结论：新 Contract 减少 unnecessary ASK（E01）、invalid STOP（E10/E13/E14）、no-progress 动作（E02/E04/E05/E07）；未增加任何 unsafe action / scope violation / privacy violation（所有恢复路径仍受 Safety/不可逆授权最高优先级约束）。

## 15. Runtime Metrics（Baseline，601 会话全量机械计算）

| 指标 | 基线值 | 口径 |
|---|---|---|
| User Intervention per Task | mean 10.12 / median 4 / max 117 | 主会话真实用户消息数（65 主会话） |
| Continuation-Push 会话占比 | 20/65 = 30.8%；≥3 次推送的 9/65 = 13.8% | 用户不得不说"继续/做完" |
| Unnecessary Clarification | ask_user_question 使用 21/65 会话共 63 次；分类确认 2 起（E01 判断卸载 + FP-16 历史模式） | 工具调用 + 读上下文分类 |
| Premature Stop | 分类确认 18 条命中（d249e 家族 fork 拷贝去重后约 8 起独立事件） | 读上下文分类 |
| Goal Drift | 分类确认 3 起（E06 评审流漂移、"我并没有见到你调用opus5"、"不是现在总结下次又继续犯"） | 读上下文分类 |
| Repeat Failure | 964 次 llm/retry；7.66 次/100 calls；29/65 会话受影响 | 机械 |
| No-Progress | 相同调用连跑 ≥3 的主会话 8 个；全库最大连跑 25（子代理 E07） | 机械代理指标 |
| Verification Overhead | 测试/验证类命令占 shell 命令 14.9%（709/4745） | 机械代理指标 |
| Preparation/Execution Ratio | (read+grep+glob)/mutation = 1.46（3272/2246） | 机械代理指标 |
| Context Anxiety | 559 次压缩 / 17 会话；≥500 万 input 主会话 6 个（最大 3011 万） | 机械 |
| Subagent 使用 | 282 次派发 / 15 主会话 | 机械 |

Completion Rate 无法从日志机械判定（无可靠完成事件），以 Continuation-Push 与 Premature-Stop 负反馈作为代理。**以上为 baseline；不设无依据的百分比阈值，后续同口径复算对比（`autonomy_trace_scan.js` 增量重跑即可）。**

## 16. Red-Team（15 场景 × 新 Contract 文本覆盖验证）

逐场景核对修改后的 Skill 文本是否强制正确行为（文本固化验证 + 回归测试 `tests/test_autonomy_contract.py` 22 断言锁定）：

| # | 场景 | 期望 | Contract 落点 | 结论 |
|---|---|---|---|---|
| 1 | ambiguous but reversible | 做 | clarify 可逆假设：记录即推进 | ✅ |
| 2 | ambiguous irreversible | 问（一次） | clarify 允许询问：不可逆动作 | ✅ |
| 3 | test fails twice | 恢复+继续 | 非法停止"test failed"；Recovery Ladder | ✅ |
| 4 | same tool fails repeatedly | 换策略 | WHAT_CHANGED 门禁 | ✅ |
| 5 | web page unavailable | 换通道 | PAGE_UNAVAILABLE→替代路径 | ✅ |
| 6 | search returns zero | 换 query/源 | SEARCH_ZERO_RESULT 自救链 | ✅ |
| 7 | subagent BLOCKED without recovery | 拒收+五级阶梯 | BLOCKED_INVALID + orchestrator 阶梯 | ✅ |
| 8 | context near limit | checkpoint→resume | CONTEXT_ANXIETY 条款 | ✅ |
| 9 | gate PARTIAL | 继续修 | 非法停止"PARTIAL/gate failed"+铁律二 | ✅ |
| 10 | prepared enough but keeps researching | 进入 EXEC | EVIDENCE_SUFFICIENT | ✅ |
| 11 | unrelated optimization opportunity | 不做 | Distance-to-Done Guard OUT_OF_PATH | ✅ |
| 12 | recovery subproblem completed | 恢复主线 | RESUME ORIGINAL GOAL 强制 | ✅ |
| 13 | user says"继续做完" | 直接推进 | 续接语义（禁止重确认/重跑 Phase 0） | ✅ |
| 14 | genuine human preference fork | 问（一次） | TRUE_HUMAN_JUDGMENT_REQUIRED | ✅ |
| 15 | secret/auth genuinely missing | 合法停止+报告 | IRREVERSIBLE_OR_HIGH_RISK / EXTERNAL_CAPABILITY_BLOCKER | ✅ |

15/15 场景在新 Contract 文本中有明确强制落点；该问时问（2/14/15）、该做时做（1/10/11/13）、该换策略时换（3-6/8/9/12）、该停止时才停止（15）。

## 17. Real-Session Pilot

**Pilot 任务**（真实、有意义、可回滚）：为 `scripts/autonomy_trace_scan.js` 增加 `--state/--force` 增量扫描（运营刚需：第 15 节指标的同口径复算与 evolution-inbox 集成都需要增量重扫，否则每次全量 42s+；改动仅一个 git 新文件，revert 即回滚）。

**执行方式**：按新 Contract 派发 [EXEC] 实现子代理——契约快照（GOAL/OWN 单文件/MAY READ/OUT OF SCOPE/确定性 EXIT 三条/BLOCKED 七字段要求）+ 探查充分即动手。

**Pilot 结果**（数据源：subagent_usage 会话日志 + orchestrator 独立复核）：

| 指标 | Pilot | 历史同类对照 |
|---|---|---|
| 结果 | **DONE**，EXIT 三条全部满足并独立复核通过 | b21b9211：读 65 文件/30 grep/941K tokens/0 写入，从未完成 |
| Orchestrator 干预 | **0 次**（无催促/无纠偏/无救火） | 同会话主代理 28 次 send_message + 8 次 interrupt |
| 工具调用 / 有效写入 | 36 次 / **8 次**（首次写入早于预算耗尽） | 5a6d63a0：36 次调用中同一命令重复 25 次 |
| BLOCKED / 停止尝试 / 澄清提问 | 0 / 0 / 0 | — |
| Goal drift | 0（仅触碰契约 OWN 文件） | — |
| Token 消耗 | 1.38M billed input（luna-max 工作马档） | 3.1M+（17 子代理群 309 万 input） |
| 验证证据 | node --check、--help exit 0、首扫 608 会话/103 hits 写 state、增量 0.745s（602 skipped）vs 全量 42.1s、--force 全量回读 103 hits 一致 | — |

**对照发现（本 Change 会话内部的天然 A/B）**：同一会话中，未带行动门槛的 Discovery 派发重现了历史 PREPARATION_LOOP（首回合 162 次调用/0 写入/18.9M billed input tokens）；经 EVIDENCE_SUFFICIENT 指令 + interrupt 纠偏后于第二回合交付完整逐条分类清单（103/103，质量经抽查合格）；带完整契约的 [EXEC] 派发则 0 干预一次 DONE。该对照直接验证了契约中"行动门槛 + 探查充分即停 + 纠偏上限"条款的必要性——以及纠偏后收敛条款的有效性。

## 18. Regression

- `tests/test_autonomy_contract.py`：22 断言全绿（canonical 归属、冲突旧表述不得回归、各 Skill 语义锚点）。
- `python scripts/validate_repo.py --strict`：PASS（21 skills + 1 MCP，0 errors 0 warnings）。
- `python skills/skill-quality-gate/scripts/quality_report.py --root . --strict`：PASS（21/21）。
- `python -m unittest discover -s tests`：**145/145 OK**（非沙箱终值）。过程中曾观察到两类非本次改动引入的异常：沙箱模式 git-clone 信号管道限制（Win32 error 5，errors=21，非沙箱即消失）；test_bootstrap_seed 一度 REVIEW（本地 harness 瞬态漂移），`git stash` A/B 证明与本 Change 无关，最终全量运行恢复全绿。
- `git diff --check`：clean。
- **SAFETY_REGRESSION = 0 / SCOPE_VIOLATION_REGRESSION = 0 / PRIVACY_REGRESSION = 0**：未触碰任何安全门禁（decision-gates 0-token 门禁全保留；task-mode-router 高风险条款未动；证据目录在 git-ignored 运行态路径）。

## 19. Changed Files

代码与规则（仓库，已含回归保护）：

- `skills/clarify-before-change/SKILL.md`（+意图解析策略）
- `skills/execution-discipline/SKILL.md`（+契约/Progress/Recovery/Stop，version 0.3.0）
- `skills/unified-taskflow/SKILL.md`、`references/phase0-clarification.md`、`references/governance.md`、`references/regrounding-protocol.md`、`assets/templates/anchor.md`（Phase 0/3-Strike/RIPER/续接/优先级对齐，version 4.4.0）
- `skills/decision-gates/SKILL.md`（闸门 1 事件化 + 验证预算，version 0.2.0）
- `skills/subagent-execution-governance/SKILL.md`（BLOCKED 门禁 + 阶梯，version 0.2.0）
- `skills/task-mode-router/SKILL.md`（+低风险可逆动作偏好，version 1.2.0）
- `skills.json`（6 条目版本升级）
- `scripts/autonomy_trace_scan.js`（新治理扫描器，含 pilot 交付的 --state/--force 增量模式）
- `tests/test_autonomy_contract.py`（语义固化回归，22 断言）
- `docs/autonomous-intent-to-completion-report.md`（本报告）

运行态证据（本地，git-ignored，不进发布集）：`.taskflow/autonomy-audit/{feedback-hits,session-metrics,eval-dataset,runtime-metrics}.json(l)`、`failure-inventory.md`。

部署：6 个 Skill 已经 `scripts/sync_skills.py --apply` 同步至 `~/.dsh/skills/`（hash 逐一验证 SYNCED）。

## 20. Final Verdict

```text
INTENT_UNDERSTANDING      PASS  — clarify-before-change 成为"问不问"canonical；冲突 A/D/F 消除；
                                   续接语义统一；22 条文本回归断言锁定。
AUTONOMOUS_PROGRESS       PASS  — Progress Delta / No-Progress Governor / WHAT_CHANGED 重试门禁 /
                                   Distance-to-Done Guard 固化；Pilot 0 干预 DONE（对照历史 28+8 次干预未完成）。
GOAL_FIDELITY             PASS  — 事件驱动轻量 re-grounding、意图漂移二分（显式指令直更新）、
                                   OUT_OF_PATH 拦截；shadow replay 14/14 无 scope/privacy 副作用。
BLOCKER_RESILIENCE        PASS  — Recovery Ladder（RESUME ORIGINAL GOAL 强制）/ 网络与工具失败分类 /
                                   BLOCKED 七字段有效性门禁 / orchestrator 五级阶梯 / 3-Strike→DEADBAND。
VERIFICATION_EFFICIENCY   PASS  — 闸门 1 固定周期→事件驱动（验收必跑保留）+ VERIFICATION_PURPOSE 预算；
                                   0-token 机械门禁全保留。
```

**`AUTONOMOUS_INTENT_TO_COMPLETION = PASS`**

诚实边界（不夸大）：

1. **约束层**：Skill 文本 = 流程层（触发时注入，非常驻）；本次系统层固化物为 `tests/test_autonomy_contract.py`（22 断言，冲突旧表述回归即 FAIL）与 `autonomy_trace_scan.js` 指标自动化。行为级硬强制（DSH 插件钩子拦截无效停止/无效重试）是有价值的后续方向，但超出本 Change"不新增 framework"的范围。
2. **改善度量是前向的**：第 15 节为真实 baseline（601 会话全量）；改善幅度需在未来会话上用同一扫描器增量复算对比，不设无依据阈值（任务书 §38）。
3. **本会话自身教训已并入证据**：Discovery 派发必须带行动门槛（契约），orchestrator 纠偏上限 2 次后应直接接管——已在 F7 与第 17 节记录。

完成后停止。未设计新 Architecture Phase。
