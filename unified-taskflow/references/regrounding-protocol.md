# Re-grounding Protocol

强制回读机制，防止上下文膨胀导致的目标偏移。

## 触发条件（事件驱动）

以下**事件**发生时，**必须**在 checkpoint 中执行 re-grounding 核对：

| 事件 | 说明 |
|------|------|
| 文件创建 | 每创建一个新文件后 |
| 文件修改 | 每修改一个已有文件后（连续修改同一文件视为一次） |
| 子任务完成 | 完成一个逻辑子任务后 |
| 用户新指令 | 用户补充新信息或修改要求后 |
| 不确定性 | 遇到模糊决策、多种可行方案时 |
| 意图漂移 | 用户当前指令与 anchor.md Intent 语义不一致时 |

> 合并规则：连续 2 次文件操作可合并为一次 re-grounding。Debug 窗口内放宽为 4 次操作合并一次。

## Re-grounding 模式

### 完整模式（Full）

适用场景：
- **首次** re-grounding（任务启动后的第一次核对）
- anchor.md **版本变更**后（Version 递增）
- 检测到 **偏移** 后（上次判定为 ⚠️ 或 ❌）
- **最终完成**核对（任务 COMPLETED 前的最后一次）

使用完整的逐项核对清单（见下方「回读流程」）。

### 精简模式（Compact）

适用场景：
- 上次 re-grounding 以来 anchor.md **未变更**
- 无 Critical Constraint 违反
- 上次对齐判定为 ✅

精简格式：

```markdown
**Re-grounding**（anchor v??, compact）: ✅ 对齐 — [一句话说明]
Done-when 变化: P0-xxx 未开始→进行中
```

> 仅列出状态**发生变化**的 Done-when 条目，无变化时省略 Done-when 行。

## 回读流程（逐项核对清单 — 完整模式）

```text
1. 读取 anchor.md（记录当前 anchor version）
2. 逐项核对 — 不是自由文本判断，而是结构化清单：
   a. 对每条 Critical Constraint：是否违反？[未违反/已违反]
   b. 对每条 Done-when：当前状态？[未开始/进行中/已完成/偏移]
   c. 当前工作是否在 Scope.Include 范围内？[是/否]
   d. 是否触及了 Scope.Exclude？[否/是]
3. 输出对齐判定（写入 checkpoint.md）
```

### 核对输出格式（完整模式）

```markdown
**Re-grounding 核对**（基于 anchor v??）：

Critical Constraints:
- [约束1]: 未违反
- [约束2]: 未违反

Done-when:
| 条目 | 状态 |
|------|------|
| P0: [条目] | 进行中 |
| P1: [条目] | 未开始 |

Scope: 在范围内 / 触及排除项 [具体说明]

- **对齐判定**: ✅/⚠️/❌ — [一句话]
- **动作**: 继续 / 调整 / 暂停请示
```

## 意图漂移检测

当用户新指令的**语义方向**与 anchor.md Intent 不一致时，Agent 应：

1. **识别**：将用户新指令与 anchor.md Intent 进行语义比对
2. **标记**：在 checkpoint 中记录检测到的漂移方向
3. **主动确认**：向用户提出选择：
   - A. 修改 anchor.md Intent 以匹配新方向（更新 Version）
   - B. 当前指令为临时偏离，完成后回到原 Intent
   - C. 拆分为新任务
4. **禁止**：未经确认就跟随用户隐式改变方向

> 意图漂移检测的判定标准：新指令的核心动作/目标对象/预期产出与 Intent 描述的核心动作/目标对象/预期产出存在语义冲突。

## 偏移处理

| 偏移程度 | 动作 |
|----------|------|
| ✅ 对齐 | 继续执行 |
| ⚠️ 轻微偏移 | 记录偏移，自行调整回正轨，继续 |
| ❌ 明显偏移 | 立即暂停 -> 向用户报告偏移内容 -> 等待指示 |

判定规则：
- 任何 Critical Constraint 被违反 = ❌ 明显偏移
- 触及 Scope.Exclude = ❌ 明显偏移
- P0 Done-when 状态为"偏移" = ⚠️ 或 ❌（视严重程度）
- 仅 Soft Preferences 未满足 = ✅ 对齐（记录即可）
- 意图漂移检测触发 = ⚠️ 或 ❌（视偏离程度）

## Anchor Mirror 刷新

每次 re-grounding 时，同步刷新 checkpoint.md 顶部的 Anchor Mirror 区块：
- 复制 anchor.md 的 Intent 和 Critical Constraints
- 更新 Anchor Version 标记

> 可使用 `python scripts/task-lifecycle.py sync-mirror` 自动完成此操作。

## 与其他机制的关系

- **统一 Checkpoint 协议**: re-grounding 核对是 checkpoint 更新的内置步骤，不再是独立协议
- **3-Strike Protocol**: 连续失败时触发额外 re-grounding
- **Phase 0**: anchor.md 是核对的唯一参照物
