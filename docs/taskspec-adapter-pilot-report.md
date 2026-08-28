# TASKSPEC_ADAPTER_PILOT Report

> 实验性质：最小可证伪实验。**不是**正式实施；结论允许 DROP（不留新组件）。
> 约束遵守：未新增 Skill / Agent framework / runtime 依赖 / 任务状态数据库；未重做 taskflow / Context Builder / Memory / routing。
> 证据目录（运行态，不进发布集）：`.taskflow/taskspec-pilot/`（adapter-spec.md、cases.jsonl、verdicts-A/B.jsonl、extract-cases.js）。
> 前置依赖：本实验建立在 AUTONOMOUS_INTENT_TO_COMPLETION Change（2026-08-29 完成）之上——该 Change 已建立字段几乎一一对应的自主执行契约，因此本实验的核心可证伪点是 **TaskSpec IR 的边际增益**。

## 1. 现有执行语义审计（5 载体共同表达的任务语义）

| 语义字段 | unified-taskflow anchor | 子代理实现契约 | execution-discipline 契约 | clarify-before-change | task-mode-router |
|---|---|---|---|---|---|
| source_request（原文锚） | ✗（隐式） | ✗（派工 prompt 即源） | goal objective 原文 | 决策记录"事实" | ✗ |
| objective | Intent | GOAL | INTENT | 一句话重述 | 依据 |
| done_when | Done-when P0/P1/P2 | EXIT（确定性可判） | DONE-WHEN | 验收标准 | ✗ |
| constraints | Critical Constraints / Soft Preferences | MUST PRESERVE / MUST NOT | CRITICAL CONSTRAINTS | 风险 | 禁止动作 |
| non_goals | Scope.Exclude | OUT OF SCOPE | NON-GOALS | 非目标 | 禁止动作 |
| autonomy | anchor 更新协议 | BLOCKED 七字段门禁 | AUTONOMY BOUNDARY（指向 clarify） | **意图解析策略（canonical）** | 允许动作 |
| current_state | checkpoint.md（Anchor Mirror + 滚动压缩） | 结构化结果回收 | CURRENT STATE | 状态 proceed/ask/blocked | 下一步 |
| valid_stop_conditions | 完成=逐项核对 | EXIT + BLOCKED 门禁 | **合法停止五类（canonical）** | blocked | ✗ |
| assumptions（条件字段） | Assumptions（可逆性标注） | 契约外缺失→BLOCKED | DECISION_RESOLVED | 假设登记 | ✗ |
| context_refs（条件字段） | Change Log / 引用文件表 | MAY READ / REFERENCE | ✗ | 证据 | ✗ |
| verification（条件字段） | 验收核对 / 最终 Re-grounding | Validation 角色 | DONE 定义内含验证证据 | 验证节 | 验证节 |

**审计结论**：

1. **8 个核心字段全部已有载体**。语义层面不存在空白——上一个 Change 刚把它们统一成契约。
2. **三个真实缝隙**（adapter 唯一可能有边际增益的地方）：
   - **G1 intake 显式化缺失**：字段语义是"执行期规则"，不是"接任务时的产出物"。意图误解类失败（C04/C05）的窗口在 intake，执行期规则管不到。
   - **G2 Medium 层无载体**：Small→minimal-implementation；Large→anchor/契约；Medium 只有 task-mode-router 的"短计划"一句话，无字段化产物。
   - **G3 source_request 原文锚不完整**：仅 decision-gates 闸门 0 给 checkpoint 级证据锚；任务 intake 级没有逐字原文锚（goal objective 是转述）。
3. **条件字段判定**：assumptions 已有 anchor 登记簿 + clarify 决策记录承载；context_refs 已有 MAY READ/REFERENCE/Change Log 承载；verification 已有 Validation 角色/验收核对承载——三者在审计中**均未发现真实案例证明需要新增**，pilot 中默认关闭，由 replay 复核。

## 2. 最小 TaskSpec 定义与 Adapter 规格

见 `.taskflow/taskspec-pilot/adapter-spec.md`。要点：

- 8 字段 IR（source_request/objective/done_when/constraints/non_goals/autonomy/current_state/valid_stop_conditions）；条件字段默认关闭。
- 分层：Small 不生成；Medium 仅对话内 runtime TaskSpec（不落盘）；Large 映射到 anchor/契约（视图而非第二存储）。
- Renderer 只做映射：main/discovery/implementation/validation → 现有契约直接复用（implementation 就是 GOAL/OWN/…/EXIT/BLOCKED 快照的填充值来源）。
- Simple-task guard：四个条件全满足时零新增步骤、零额外工具调用。

## 3. 案例选择（16 案例，全部来自真实历史 sessions）

| 案例 | 会话 | 失败类型（源自 failure-inventory 分类） |
|---|---|---|
| C01 | session-2f8bc098 | 不必要澄清 + 目标漂移（18 案例人工评审转嫁终判） |
| C02 | session-d249ef16 | 过早停止 + 续推（守候态停摆，117 条用户消息） |
| C03 | session-e30ae08e | 复杂 NL 接管 + 过早停止 + 无效阻塞（"你是来解决问题的，不是记录阻塞的"） |
| C04 | session-67e77472 | 意图误解（"我并没有见到你调用opus5"）+ 工具故障拖住 |
| C05 | session-d1f928b9 | 意图误解（持久机制被替换成对话内防再犯清单） |
| C06 | session-d49762c0 | 无效阻塞（证据新鲜度混淆） |
| C07 | session-70758179 | 恢复后未回主线 + 上下文焦虑（v8） |
| C08 | session-b1e697f9 | 续推 ×13（交接任务长时间需用户推送） |
| C09 | session-e40dbab0 | 意图重锚巨型会话（3011 万 input / 1738 步） |
| C10 | session-33b69ec9 | 复杂实现任务委派约束丢失（17 子代理 309 万 input 空转） |
| C11 | session-df592341 | 过早停止（资料搜集半途停顿） |
| C12 | session-b730de68 | done-when 不明确（"内容太少，根本没信息量"） |
| C13 | session-b55ce2dc | **负面对照**（Architecture V2 分阶段推进，多数命中判 NOT_A_FAILURE） |
| C14 | session-d5431e41 | **负面对照**（评测任务正常阶段推进） |
| C15 | session-c2149a9c | **简单任务守卫**（"谁在用gemini啊"，3 步 1 消息） |
| C16 | session-f0cd5578 | **简单任务守卫**（web search 额度配置问题，9 步） |

选择依据：覆盖任务书要求的六类（意图误解 C04/C05/C09；目标漂移 C01；约束遗漏 C10；不必要澄清 C01；done-when 不明确 C12；复杂 NL 接管 C03/C07/C08）+ 2 个负面对照（防"一切皆增益"偏差）+ 2 个简单任务守卫。

## 4. Shadow Replay 结果

方法：两个独立评审子代理各判 8 案例，强制"基线扣除"（先判当前体系是否已覆盖，再判 adapter 边际），7 指标三值评分 + 机制归因 + 未使用字段清单。判定全文：`.taskflow/taskspec-pilot/verdicts-A.jsonl`、`verdicts-B.jsonl`。

**逐案例结论**：

| 案例 | baseline_covers | 净改善指标数 | 机制 |
|---|---|---|---|
| C01 | true | 0 | 意图/边界/验收契约已覆盖 |
| C02 | true | 0 | 恢复/持续推进/合法停止契约已覆盖 |
| C03 | true | 0 | 只读授权/负空间/恢复契约已覆盖 |
| C04 | **false** | 0 | 基线缝隙（intake 未把"用 opus5"锚定为硬约束），但失败主因是执行期 CLI/网络故障，IR 管不到；current_state 字段无判别作用 |
| C05 | true | 0 | 审计/变更边界与持久化修复纪律已覆盖 |
| C06 | true | 0 | Evidence Freshness 语义已覆盖 |
| C07 | true | 0 | checkpoint 恢复/主线续接/阶段验收已覆盖 |
| C08 | true | 0 | 续接语义/剩余目标/持续执行契约已覆盖 |
| C09–C14 | true | 0 | 含两个负面对照：健康轨迹不被 adapter 改变 |
| C15/C16 | true（守卫命中） | 0 | simple-task guard 生效：零新增步骤、零新增延迟 |

## 5. 指标聚合

```text
失败案例净改善：0/12（12 个失败案例无任何指标净改善）
基线覆盖率：   15/16（唯一例外 C04，且 adapter 同样无法修复）
intent_fidelity        +0 / 0:16 / −0
completion             +0 / 0:16 / −0
user_intervention      +0 / 0:16 / −0
goal_drift             +0 / 0:16 / −0
unnecessary_clarification +0 / 0:16 / −0
constraint_loss        +0 / 0:16 / −0
over_specification     +0 / 0:15 / −1   （C04 current_state 无判别作用）
未使用字段：current_state ×1；其余字段均在推演中映射到现有载体
```

**机制归因**：历史失败的病灶分两类——①语义类（意图误解/漂移/不必要澄清/无效阻塞）：已被 2026-08-29 的 AUTONOMOUS Change 契约覆盖（clarify 意图解析策略 + 执行契约 + anchor 门禁）；②执行期机械类（CLI/网络故障、守候态、空转）：intake 期 IR 无论多显式都无法修复，归 execution-discipline 的 Recovery/Progress 机制管。TaskSpec IR 位于 intake，对两类都无边际杠杆。

**Simple-task guard 验证**：C15（"谁在用gemini啊"，3 步）/C16（web search 配置问题，9 步）均零新增流程与延迟，守卫有效。

## 6. A-E 回答

**A. TaskSpec Adapter 是否带来真实增益？**
**否。** 0/12 失败案例净改善；7 指标全中性；唯一非零信号是 over_specification −1。

**B. 增益来自哪里？**
无可归属增益。唯一确认的基线缝隙（C04：用户显式指定的模型约束未在 intake 被锚定为硬约束）也无法由 IR 修复——该案例的失败主因是执行期 CLI/网络故障。

**C. 是否有现有框架已经完全覆盖？**
**是，15/16。** 刚完成的 AUTONOMOUS_INTENT_TO_COMPLETION Change 已把 TaskSpec 8 字段的语义全部固化进现有载体（clarify-before-change 意图解析策略 / execution-discipline 执行契约 / unified-taskflow anchor / 子代理契约快照 / task-mode-router 分层），且带 22 条文本回归断言。再叠加 IR 是同一语义的第二层表达，违反"TaskSpec 不是新 SSOT"的自身原则。

**D. 哪些部分值得保留为定制语义？**
**不留任何组件。** 唯一值得沉淀的是一个 intake 微习惯而非新 IR：**把用户显式指定的技术/模型/路线约束逐字锚定进硬约束载体**（clarify-before-change 决策记录 / anchor Critical Constraints 已能承载）。该习惯可在未来出现真实新证据时，以一句话级微调并入 clarify-before-change——本实验不据此改动任何 Skill。

**E. 是否值得正式实施？**
**否。** 满足任务书预设的 DROP 条件。

## 7. 结论

```text
TASKSPEC_ADAPTER_PILOT = DROP
```

- 不留下新组件：`.taskflow/taskspec-pilot/` 整体属设备运行层（git 忽略、不进发布集），作为实验证据保留备查；仓库发布集仅新增本报告。
- 未新增 Skill / framework / runtime 依赖 / 状态数据库；未重做 taskflow / Memory / routing。约束全数遵守。
- 方法学诚实声明：shadow replay 是判断性重放而非真实执行，判定由同族模型完成；但结果方向是保守方向（反对增加框架层），假阴风险低。若未来在**当前契约下**仍出现 intake 级意图误解的真实失败，可凭新证据重开本议题。

完成后停止。
