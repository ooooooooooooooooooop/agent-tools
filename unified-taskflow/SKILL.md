---
name: unified-taskflow
description: |
  重型复杂任务管理系统。仅在用户明确要求使用 unified-taskflow、taskflow、.taskflow，或任务确实需要跨多轮/多文件/多阶段的 anchor、checkpoint、design、恢复与验收追踪时使用。不要用于小修、普通优化、普通重构、单个 skill 小改、日常 bug fix，或 task-mode-router / minimal-implementation 已足够处理的任务。
---

# Unified Taskflow v4.3

> 更新：2026-08-10

> [!CAUTION]
> **核心规则**
> 1. 只有通过触发门禁后，才启动 unified-taskflow；不要把普通任务流程化
> 2. 启动后，任务管理使用项目根目录下的 `.taskflow/` 目录，**不使用**内置 artifact 目录
> 3. 禁止跨阶段推理 — 执行时不能跳回规划，规划时不能偷跑代码
> 4. 如不确定是否需要启动，先用 `task-mode-router` 判断；仍不确定时询问用户，不默认触发

> **规则优先级**（冲突时按此顺序裁决）：
> **Safety > Correctness > Efficiency > Completeness**

## 触发判断

**触发**：
- 用户明确说“使用 unified-taskflow / taskflow / .taskflow”。
- 任务跨多轮、多文件、多阶段，且需要可恢复的执行记录。
- 任务需要明确的 anchor、checkpoint、design、验收标准和回滚路径。
- 用户要求长期复盘、审计轨迹、阶段冻结或复杂交接。

**不触发**：
- 简单问答、单行修改、单文件小修。
- 普通 skill description 小改、文案调整、同步用户级 skill。
- 普通“优化 / 完善 / 重构 / 更稳定”，除非用户明确要求重型流程。
- `clarify-before-change`、`task-mode-router`、`minimal-implementation` 已足够控制风险的任务。

**路由关系**：
- 先由 `task-mode-router` 判断任务模式。
- 小任务和中等任务默认不用 `.taskflow/`。
- 只有大型、长任务、可审计交接任务才进入本 skill。
- 进入本 skill 后，仍遵守 `minimal-implementation`，不为了流程完整性扩大实现范围。

## 按需加载

- 进入 Phase 0 → 读取 [phase0-clarification.md](references/phase0-clarification.md)
- 需要交互决策 → 读取 [interaction-design.md](references/interaction-design.md)
- 治理规则细节 → 读取 [governance.md](references/governance.md)
- Re-grounding 细节 → 读取 [regrounding-protocol.md](references/regrounding-protocol.md)

## 工作流

### Phase 0: 理解快照（Understanding Snapshot）

**目的**：确保 Agent 正确理解用户需求，生成防幻觉锚点。

1. **确认已通过触发门禁**；未通过时退回普通任务流程
2. **禁止**立即创建文档或代码
3. Agent 输出**理解快照**：
   - 用户意图（一句话）
   - 识别到的歧义点
   - Agent 的假设（显式列出，用户逐条确认）
4. 用户确认或修正
5. **完备性门禁**（详见 phase0-clarification.md）
6. 写入 `anchor.md`（北极星文件，含版本号）

> 问题框架（参考，不强制全部使用）：边界 / 约束 / 优先级 / 风险
> 详见 [phase0-clarification.md](references/phase0-clarification.md)

### 执行（Elastic Execution）

弹性深度 — 根据任务复杂度自然展开，无固定档位：

- **重型任务最小路径**：anchor.md → checkpoint.md → 执行 → 验收核对
- **需要设计时**：anchor.md → design.md → checkpoint.md → 执行 → 验收核对
- **不做**：为低风险小任务创建 `.taskflow/`、anchor、checkpoint 或 design

执行期间遵守：
- **统一 Checkpoint 协议**：事件驱动的 checkpoint 更新 + 内置 re-grounding 核对（见下方）
- **3-Strike Protocol**：同一问题 3 次失败后升级给用户

### 完成（Completion）

1. 最终 Re-grounding — 逐项核对 anchor.md 的所有 Done-when 条目
2. 向用户报告完成状态
3. 归档任务（移入 archive/）

> 兼容说明：历史 `index.json` 可能使用 `archived` 状态；读取、list 和 catch-up 必须保留兼容显示，新写入使用 `completed` 或 `abandoned`。

## 运行机制

### 统一 Checkpoint 协议（事件批次 + Re-grounding）

将进度记录和对齐验证合并为单一机制，减少协议数量，提高遵从率。

**触发事件**（事件驱动，替代模糊的步骤计数）：

| 事件 | 说明 |
|------|------|
| 关键文件创建/修改 | 完成一个影响验收、设计、数据或行为的文件操作批次后触发 |
| 子任务完成 | 每完成一个逻辑子任务 |
| 用户新指令 | 用户补充信息或修改要求 |
| 不确定性 | 遇到模糊决策或多种可行方案 |
| 意图漂移 | 用户当前指令与 anchor.md Intent 语义不一致时 |

**每次 Checkpoint 更新包含**：
1. 做了什么 + 发现了什么
2. **Re-grounding 逐项核对**（对 anchor.md 的 Critical Constraints 和 Done-when 逐条检查状态）
3. 刷新 checkpoint.md 顶部的 **Anchor Mirror**（复制 Intent + Critical Constraints）
4. 下一步计划

**滚动压缩**：checkpoint.md 保留最近 3 条完整记录，旧记录压缩为一行摘要移入「历史摘要」区。

> 详见 [regrounding-protocol.md](references/regrounding-protocol.md)

### 脚本边界

- `scripts/task-lifecycle.py` 使用 `assets/templates/` 作为 anchor、checkpoint、design 的唯一模板源；不要在脚本中复制第二份模板。
- 新任务名必须是安全的单一路径组件（字母/数字/`-`/`_`，最多 64 个字符）；不接受绝对路径、`..` 或路径分隔符。
- 可用 `--project-path <path>` 指定项目根目录；`list` 和 `status` 可附加 `--json` 生成机器可读状态。默认仍为当前工作目录。
- `index.json` 写入经过 schema/lifecycle 校验并采用原子替换；历史 `archived` 状态继续只读兼容。

### 3-Strike Protocol

同一问题连续失败 3 次：
1. Strike 1 — 记录问题和尝试方案
2. Strike 2 — 换一个方向，记录
3. Strike 3 — **停止尝试**，升级给用户，提供已排除方案列表

### RIPER-Core 思维规则

1. 根因解优先 — 禁止用配置/降级掩盖问题
2. 显式因果链 — Why → Condition → Limitation
3. 无魔法数字 — 常数必须来自输入/约束
4. 明确变量 — 信息不足立即暂停询问

## 反模式清单（Anti-Patterns）

> 禁止性指令的遵从率通常高于义务性指令。以下是**不要做**的事：

| 反模式 | 说明 | 正确做法 |
|--------|------|----------|
| 自由文本对齐 | 用"我觉得还在正轨"代替逐项核对 | 必须对 Critical Constraints 和 Done-when 逐条输出状态 |
| Anchor 静默修改 | 未告知用户就修改 anchor.md | 任何 anchor 修改必须用户确认，并更新 Version 和 Change Log |
| Checkpoint 堆积 | 无限追加 checkpoint 不压缩 | 超过 3 条完整记录时，压缩旧记录为摘要 |
| 跳过 Phase 0 | 直接开始执行不做理解快照 | 复杂任务必须先输出理解快照，用户确认后写入 anchor.md |
| 硬约束降级 | 把 Critical Constraint 当 Soft Preference 处理 | Critical Constraint 违反 = 立即暂停请示 |
| 假设隐含 | 不列出假设就开始执行 | 所有假设写入 anchor.md 的 Assumptions 表，用户逐条确认 |
| 忽略意图漂移 | 用户隐式改变方向时不确认就跟着走 | 检测到用户指令与 anchor.md Intent 不一致时，主动确认是否修改 Intent |

## 工作目录

```text
.taskflow/
├── index.json
├── active/[task-name]/
│   ├── anchor.md          # 北极星文件（必须，含版本号）
│   ├── checkpoint.md      # 校验点记录（必须，含 Anchor Mirror）
│   └── design.md          # 技术设计（按需）
└── archive/               # 已归档任务
```

## 交互原则

- 选择题优先，3-4 个选项
- 每个选择点有推荐选项
- 可选参考：[interaction-design.md](references/interaction-design.md)

## 引用文件

| 文件 | 用途 |
|------|------|
| [phase0-clarification.md](references/phase0-clarification.md) | Phase 0 理解快照 + 完备性门禁 |
| [regrounding-protocol.md](references/regrounding-protocol.md) | Re-grounding 逐项核对规则 |
| [governance.md](references/governance.md) | 目录隔离、生命周期、治理规范 |
| [interaction-design.md](references/interaction-design.md) | 交互设计原则（可选参考） |
| [anchor.md 模板](assets/templates/anchor.md) | Grounding Anchor 模板（分层 + 版本号） |
| [checkpoint.md 模板](assets/templates/checkpoint.md) | 校验点记录模板（Anchor Mirror + 滚动压缩） |
| [design.md 模板](assets/templates/design.md) | 技术设计模板（按需） |
