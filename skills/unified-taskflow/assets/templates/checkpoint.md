# 校验点记录

## Anchor Mirror（每次 checkpoint 更新时刷新）

> 从 anchor.md 复制核心约束，利用首位效应防止遗忘
> 可使用 `python scripts/task-lifecycle.py sync-mirror` 自动刷新

- **Intent**: [从 anchor.md 复制]
- **Critical Constraints**: [从 anchor.md 复制硬约束]
- **Anchor Version**: v??

## Trace Stub

**目标**：[一句话描述当前任务目标]

**当前假设**：[正在验证的假设]

**已排除**：
- [已证伪的假设 1]

---

## 校验点日志

> **滚动压缩规则**：保留最近 N 条完整记录（默认 N=3，复杂任务可调至 5）。超过 N 条时，将旧记录压缩为一行摘要移入「历史摘要」区。

### 历史摘要

- [压缩的旧 checkpoint 摘要]

---

### [YYYY-MM-DD HH:MM] 任务启动

**anchor 摘要**: [从 anchor.md 复制的一句话意图]

---

#### [HH:MM] [操作描述]

[记录关键发现或决策]

**Re-grounding 核对**（基于 anchor v??）：

| Done-when 条目 | 状态 |
|----------------|------|
| P0: [条目] | 未开始/进行中/已完成/偏移 |
| P1: [条目] | 未开始/进行中/已完成/偏移 |

- **对齐判定**: ✅/⚠️/❌ — [一句话说明]
- **动作**: 继续/调整/暂停请示

#### [HH:MM] [操作描述] — 精简核对示例

[记录关键发现或决策]

**Re-grounding**（anchor v??, compact）: ✅ 对齐 — [一句话说明]
Done-when 变化: P0-xxx 未开始→进行中

---

## Debug 记录

| 问题 | Strike | 尝试方案 | 结果 |
|------|--------|----------|------|
| [描述] | 1 | [方案] | pass/fail |
