# Personal AI｜应用层试点 V0.1

版本：2026-09-10 / V0.1  
状态：Pilot Baseline  
范围：Personal AI / DSH / Agent / Harness / 路由 / 自动化 / 治理 / 平台 Research Lab

## 0. Pilot 边界

本 Pilot 将《03-认知与世界模型｜理论基线 V0.1》《04-跨项目认知协议｜V0.1》《05-跨项目认知协议｜一致性测试 V0.1》作为只读共享方法层。

它们：

- 不生成 Personal AI 项目事实；
- 不覆盖仓库、进程、运行记录和真实结果；
- 不提供新的工程授权；
- 不要求用户填写 State / H1 / Prediction 等模板；
- 与项目原始证据冲突时，以 Personal AI 原始证据为准。

Pilot 不建设新的平行 Research Lab、状态系统、Manager 平台或认知数据库。优先复用现有 Personal AI 状态、Agent Switchboard、Goal supervision、治理检查和 Research Lab 资产。

---

## 1. Pilot 目标

真正目标不是增加世界模型术语、Agent 数量、自治率或测试数量。

Pilot 要验证的是：

> 显式维护“当前状态—解释—预期—行动—真实结果—修订”是否能让 Personal AI 更可靠地理解当前局面、选择下一步、减少错误重开旧问题、减少硬编码推进，并降低用户承担的状态恢复和提示词中转工作。

成功主要看：

1. 是否选择了正确的真实问题；
2. 是否因为状态过期、错误因果关系或错误假设而跑偏；
3. 是否能在行动前提出可失败的预期；
4. 是否根据真实结果改变后续行动；
5. 是否减少用户纠偏和重复说明；
6. 方法本身的维护成本是否低于收益。

普通工程任务仍正常执行，不为形式而显式展开完整闭环。

---

## 2. 当前真实状态

“当前”在本文中指**截至目前能够恢复出的最新可靠项目证据**，不是声称已于 2026-09-10 对用户本机进行了现场检查。

## 2.1 当前有效状态

### A. 总目标

Personal AI 是控制层：

- 理解当前目标与系统状态；
- 决定下一步；
- 调用合适 Harness / Agent / Model；
- 根据真实执行结果继续、停止或改变计划；
- 减少“用户 → 网页 GPT → Agent”的人工状态转写。

DSH 是可替换 Harness，不反向定义 Personal AI。

### B. 状态与实现边界

当前长期架构边界仍有效：

- 声明式状态与运行派生状态分离；
- `personal-ai-state` 承担身份、偏好、目标、记忆等 declarative state；
- `agent-tools` 承担模型、路由、能力、运行治理等实现；
- 实际运行状态必须继续追到部署、进程加载和任务结果；
- 模型不得被隐式替换。

这些属于已有项目约束，但不能仅凭声明推断当前实现全部符合。

### C. 已具备的控制能力

已有真实运行历史支持：

- FIRST_AUTONOMOUS_PRODUCTION_LOOP = PASS；
- FIRST_GOAL_DRIVEN_PRODUCTION_LOOP = PASS；
- FIRST_DURABLE_CONTROL_PLANE_RUN = PASS。

因此不能再把 Personal AI 描述成“只有静态配置、完全不会自主推进”。

但这些 PASS 的适用边界是：

> 已经给定目标或工作对象后，系统能够进行受控、可验证、持久推进。

它们**没有证明**：

> 面对多个已有 Open Loops，系统已经能够从新鲜真实状态中自己选择最值得推进的那个。

### D. 运维状态的最新已知变化

较新的项目运行记录已经把早期状态继续推进：

- Personalization 曾从缺证据恢复为 CURRENT；
- Governance 中 `STALE_POLICY_SIGNAL` 已被处理，后来状态恢复；
- Durability 仍留下 `REPO_UNPUSHED` 一类状态一致性问题；
- 外部冷备仍至少存在 `BACKUP_KEY_CUSTODY` 未闭环。

这些是“最后已知状态”，不是 9 月 10 日现场重新运行后的状态。

### E. 当前远端并非完整实时 SSOT

GitHub 上的 `personal-ai-state/state/goals.md` 仍写着旧的 Migration #3/#4 以及旧的 `novel-main` 状态。

这与之后已经发生的 Personal AI 工作和当前项目边界不一致，因此：

> 当前远端 `goals.md` 是真实存在的 formal artifact，但内容已陈旧；“文件是 canonical 路径”不等于“内容是当前事实”。

这也是当前世界模型必须显式保存“证据新鲜度”的直接原因。

---

## 2.2 历史已完成结果

以下结果保留，不因当前 Pilot 重开：

### DSH Runtime Compatibility

已完成最终对抗验收并关闭。

复开条件应是：

- 新版本产生新的 service contract 变化；
- 或出现新的实际失败证据。

不能因为“开始世界模型 Pilot”重新调查。

### Codex executable / Agent Broker 路径事故

已完成现场修复与回归验证并关闭。

核心经验保留：

> 文件存在 ≠ 当前目标 runtime identity 正确。

### Nightly backup / 本地恢复

早期 scheduler 指向临时 drill 路径、备份停滞的状态已经被后续真实修复替代。

较新的本地闭环完成了真实 schedule / reader / restore 验证；冷备密钥托管仍是独立事项。

### Writer provenance

历史写入归属问题最终不是通过“猜出作者”解决，而是裁定历史证据不足部分为 `HISTORICALLY_UNRESOLVABLE`。

这个历史问题不应继续消耗工程资源。

后续真正有价值的是约束未来 writer provenance，而不是不断追查无法恢复的过去。

### 结果验真

历史 Sync / validator 曾出现硬编码、tautological PASS 和 exception-swallowing 等问题；后续已经建立更强的 counterexample / live acceptance / real-result verification 路线。

因此：

> “曾经发现假通过”是重要反例，但不能推出“当前所有检查仍是假通过”。

---

## 2.3 已被替代结论

### R1

旧结论：

> Nightly backup 当前失败，部分新 session 无法恢复。

已被后续真实本地 backup closure 替代。

保留旧事故作为 causal evidence，不再作为当前状态。

### R2

旧状态：

> Personalization = UNKNOWN / NO_EVIDENCE。

已有后续真实提取和检查把它推进为 CURRENT。

### R3

旧状态：

> Governance DEGRADED，原因是 CAPABILITY_DRIFT / STALE_POLICY_SIGNAL。

相关 stale policy 已被重新裁定和处理，不能继续作为当前故障重复修复。

### R4

旧的 Sync V2 “PASS 就代表真实收敛”。

已经被真值审计推翻。

历史审计发现过：

- unconditional backup PASS；
- file-exists-only validator；
- hardcoded runtime/session 文本；
- exception swallowing；
- tautological regression tests；
- 缺 snapshot consistency。

以后 PASS 必须回到真实消费者、运行态和任务结果。

### R5

远端 `goals.md` 当前显示的旧 Migration 路线。

该文件仍存在，但内容不能覆盖之后更晚的真实项目状态和用户当前目标。

### R6

原 Cognitive Leverage Pilot 的大型平台化方向。

已有项目材料只证明它曾收敛到准备/研究阶段，没有证明应另建一套平台。

当前应用层 Pilot 直接使用真实 Personal AI 工作，不重复建设该平台。

---

## 2.4 当前假设

以下全部是待现实检验的模型，不是项目事实。

### H1｜Thin World-Model Control Layer

当前主要缺口不是执行能力，而是：

> 新鲜状态恢复 + Open Loop 识别 + 下一步选择 + 结果后重裁定。

在现有系统上增加很薄的应用层认知控制即可显著改善推进质量，不需要新增开放式 Manager Agent。

### H2｜Existing Controls Are Enough

Goal supervisor、personal_status、durability、Sync、现有 Agent 路由事实上已经足够。

当前问题主要是没有正确组合这些能力；显式 World Model 可能只是增加维护成本。

### H3｜Live-State Plumbing Is the Real Bottleneck

主要问题不是“不会推理下一步”，而是当前 canonical / local / runtime 状态不能稳定被同一决策入口获取和确认。

如果状态输入不可靠，再聪明的选择器也只会更稳定地基于错误状态行动。

### H4｜A Richer Planner Is Eventually Required

薄控制层可能只能解决简单 Open Loop 选择。

面对大量依赖、冲突目标和长期任务，最终可能仍需要 Manager/Executor 或更强 planner。

当前没有证据支持直接跳到 H4。

---

## 2.5 未知

截至本基线仍明确未知：

1. 2026-09-10 此刻用户本机 `agent-tools` 的真实 HEAD；
2. 本机未推送 diff 的精确内容；
3. 当前 DSH / Broker / Codex 的实际进程和 loaded runtime；
4. 9 月 7 日之后 `BACKUP_KEY_CUSTODY` 是否已处理；
5. `REPO_UNPUSHED` 是否仍然存在；
6. 未推送本地实现中是否已经出现跨 Open Loop selector；
7. 当前所有状态检查重新运行后的五域结果。

这些未知不得由聊天摘要补全。

---

## 3. Personal AI 项目 Adapter

## State

Personal AI 的 `State` 不是一句“系统健康”。

它至少包括与当前目标相关的：

- 当前用户目标及 protected boundaries；
- active / blocked / completed Open Loops；
- declarative state；
- repo / commit / working-tree 状态；
- 当前部署版本；
- 进程实际加载版本；
- runtime capability；
- health / durability / governance 状态；
- external blockers；
- 最新已验证结果；
- 每项证据的来源、对象、时间和 freshness；
- 已关闭事故及其复开条件。

State 必须允许 `CURRENT / LAST_KNOWN / STALE / UNAVAILABLE` 这样的证据状态。

---

## Model

Personal AI 的 `Model` 是对系统“为什么会产生当前状态，以及某行动会影响什么”的压缩关系模型。

核心结构包括：

`声明`
→ `生成/同步`
→ `部署`
→ `进程加载`
→ `能力暴露`
→ `Agent/Harness 调用`
→ `任务结果`

以及：

`状态来源/记忆`
→ `上下文`
→ `决策`

`模型路由`
→ `实际执行模型`
→ `能力/成本/结果`

`durability`
→ `状态是否能跨故障继续`

重要问题可以维护 H1/H2/H3，但不得为了协议形式制造假竞争。

---

## Action

Personal AI 中的 `Action` 是会改变证据状态、系统状态或现实结果的操作，例如：

- 只读调查；
- 获取运行状态；
- 执行 verifier；
- 运行真实任务；
- 修改代码；
- 修改声明；
- Sync / reconcile；
- 部署；
- reload / restart；
- dispatch Agent / Harness；
- rollback；
- push durable state；
- 停止并标记 blocker。

Action 不等于“写一份分析报告”。

---

## Prediction

`Prediction` 是 Action 前可失败的预期。

例如：

> 如果 broker stale-path 是根因，修复 resolver 后，新启动 capability probe 应解析到当前 Desktop executable，`codex_smoke_ok` 恢复，而其他路由不应改变。

或者：

> 如果当前真正缺的是跨 Open Loop selector，那么仓库应已有 Goal 内 criterion dispatch，但不存在基于最新多源 State 选择哪个真实 Open Loop 的生产入口。

Prediction 必须在 Observation 前形成。

---

## Observation

Personal AI 中可以承担强 Observation 的包括：

- 实际命令输出；
- git HEAD / diff / push 状态；
- 文件真实内容；
- PID / process identity；
- loaded config；
- runtime capability；
- log/event；
- verifier 输出；
- regression / live test；
- browser/runtime E2E；
- backup/restore 实物结果；
- Agent 实际任务结果。

历史报告可以记录过去 Observation，但不是当前现场 Observation。

“AI 认为已经修好”不是 Observation。

---

## Update

根据 Observation 更新：

1. 当前 State；
2. 对因果关系的 Model；
3. 竞争模型权重；
4. Open Loop 优先级；
5. 下一 Action。

修订可以是：

- 参数/置信度变化；
- 结构关系变化；
- 增加遗漏变量；
- 改变 State 表征；
- 必要时产生 H4。

没有新的真实 Observation，不进行“已验证”“已证明”“模型已更新”的表述。

---

## 4. 当前 Open Loops

按现有真实材料恢复，而不是为 Pilot 创造实验：

## OL-1｜跨 Open Loop 的自主下一步选择

状态：OPEN  
优先级：最高

已经证明系统能在给定任务内部推进；尚未证明能从多个未完成事项中自主选择下一步。

这是用户当前指出的核心缺口：

> 不能继续只是硬编码任务。

---

## OL-2｜Current-State Freshness / Canonical Divergence

状态：OPEN / 部分事实未知

远端 `personal-ai-state` 仍保存旧 goal 内容，而后续本地真实工作已经超前。

需要解决的不是简单“把文件更新一下”，而是：

> 自治决策时如何知道某状态来源是否足够新，可以作为行动依据。

这与 OL-1 是直接依赖关系。

---

## OL-3｜Durability 的外部密钥托管

状态：OPEN / 外部 blocker

本地恢复已经形成可信闭环后，仍保留 off-device / custody root 问题。

这是实际可靠性 Open Loop，但需要真实外部条件，不适合作为本 Pilot 第一闭环。

---

## OL-4｜应用层 Pilot 的累计真实证据

状态：OPEN

不是新建实验平台。

从今后的真实 Personal AI 工作中自然累计：

- 事前判断；
- 实际结果；
- 是否需要用户纠偏；
- 方法是否改变行动；
- 是否出现额外认知负担。

多轮真实证据之后才判断 V0.1 是否值得升级。

---

## 5. Pilot 竞争假设

主竞争关系：

- **P-H1**：显式但薄的 State/Model/Prediction/Update 层能明显改善真实推进。
- **P-H2**：现有工程流程本来已经足够，显式模型主要增加形式成本。
- **P-H3**：收益主要来自改善状态可观测性，而不是世界模型本身。
- **P-H4**：薄层不足，需要更复杂的 planner / Manager-Executor。

首轮不预设 P-H1 胜出。

---

## 6. 运行规则

用户继续正常用自然语言提出目标。

AI 内部负责：

1. 恢复与当前任务相关状态；
2. 判断真正 blocker；
3. 选择最小有效 Action；
4. 必要时形成事前 Prediction；
5. 执行；
6. 读取真实 Observation；
7. 更新下一步。

仅以下情况显式展示完整闭环：

- 重要架构判断；
- 存在实质竞争解释；
- 真实方法验证；
- 重大故障；
- 关键模型修订。

普通 bug、配置修改和查询不显式套框架。

---

## 7. 更新与版本规则

V0.1 从本轮起冻结。

### 普通任务

只更新任务状态，不升级 Pilot。

### 重要 Observation

记录：

- 新 Observation；
- 它支持/削弱了什么模型；
- 被替代的结论；
- 新的未知；
- 下一步变化。

### V0.2 条件

以下情况之一持续出现，才考虑升级：

- World Model 明显减少错误行动；
- 竞争预测真实地区分了方案；
- 状态 freshness 成为系统性核心变量；
- 当前 Adapter 无法表达反复出现的重要问题；
- 显式协议持续增加负担却没有收益；
- 出现 H4 级新机制。

不直接覆写 V0.1。

---

## 8. 当前协议适配问题

首轮发现一个 Personal AI 特有的重要适配点：

> `State` 不能只有“事实内容”，必须包含 **freshness / runtime applicability**。

原因不是共享协议规定，而是 Personal AI 自己已经出现：

- canonical 文件存在但内容陈旧；
- source 完成但尚未 deploy；
- deploy 完成但 runtime 未加载；
- 文件存在但不是当前 executable identity；
- 历史 PASS 不代表当前运行态。

因此 Personal AI Adapter 对共享 `State` 的具体化必须增加：

`content + source + object + environment + version + freshness + runtime applicability`

这是项目 Adapter 的领域细化，不修改共享协议本身。
