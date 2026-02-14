# unified-taskflow 治理规范 v4.1

## 一、目录隔离（强制）

### 1.1 目录结构

```text
.taskflow/
├── index.json           # 任务索引
├── active/              # 当前活跃/暂停任务
│   └── [task-name]/
│       ├── anchor.md        # 北极星文件（必须，含版本号）
│       ├── checkpoint.md    # 校验点记录（必须，含 Anchor Mirror）
│       └── design.md        # 技术设计（按需）
└── archive/             # 归档任务
    └── [YYYY-MM-DD]_[task-name]_v[N]/
```

### 1.2 规则

| 规则 | 描述 | 类型 |
|------|------|------|
| 单活跃限制 | `active/` 下最多一个 active 任务 + 一个 suspended 任务 | 硬性 |
| 隔离检查 | 新任务开始前必须检查 `active/` | 硬性 |
| 归档命名 | `日期_任务名_版本` | 软性 |

### 1.3 生命周期状态

```text
NEW → ACTIVE → COMPLETED / ABANDONED
        ↑↓          ↓
     SUSPENDED  RESUMED →──↑
```

- `ACTIVE ↔ SUSPENDED`：任务可在 active 和 suspended 之间切换
- suspended 任务保留在 `active/` 下，仅在 index.json 中标记状态为 `suspended`
- resume 时检查是否已有 active 任务，若有则拒绝

---

## 二、anchor.md 更新协议

anchor.md 是任务的唯一真相来源（含版本号），修改需遵守：

| 场景 | 动作 |
|------|------|
| Phase 0 完成时 | 首次创建（v1），由用户确认内容，通过完备性门禁 |
| 用户修改需求时 | 更新对应字段，Version +1，记录到 Change Log |
| Agent 发现歧义时 | 暂停执行，提议修改，用户确认后更新 Version 和 Change Log |
| 禁止 | Agent 单方面修改 anchor.md 而不告知用户 |

### anchor.md 版本规则

- 每次修改 anchor.md 内容时，Version 递增（v1 → v2 → v3...）
- checkpoint.md 中的 Re-grounding 核对必须记录"基于 anchor v??"
- Change Log 保留最近 3 条变更记录

---

## 三、统一 Checkpoint 协议触发规则

| 触发事件 | 动作 |
|----------|------|
| 每 2 次文件操作 | 更新 checkpoint + 内置 re-grounding 逐项核对 |
| 子任务完成 | 同上 |
| 用户新指令 | 先更新 anchor.md（如需要），再更新 checkpoint |
| 不确定性 | 同上，若偏移则暂停请示用户 |
| 意图漂移 | 主动确认用户意图，必要时更新 anchor.md |

### 滚动压缩规则

- checkpoint.md 保留最近 N 条完整记录（默认 N=3，复杂任务可调至 5）
- 超过 N 条时，将最早的记录压缩为一行摘要，移入「历史摘要」区
- Anchor Mirror 区块始终保持最新（每次 checkpoint 更新时刷新）

---

## 四、门禁例外

### Debug Window
- 统一 Checkpoint 协议放宽为 4 次文件操作触发一次
- 保留 3-Strike Protocol
- 连续失败时触发额外 Re-grounding 核对

---

## 五、最小记录

| 文件 | 要求 |
|------|------|
| anchor.md | 必须，Phase 0 完成后创建，含版本号和分层约束 |
| checkpoint.md | 必须，执行期间持续更新，含 Anchor Mirror 和滚动压缩 |
| design.md | 按需，复杂任务时生成 |

---

## 六、规则优先级

当规则之间发生冲突时，按以下优先级裁决：

**Safety > Correctness > Efficiency > Completeness**

| 优先级 | 含义 | 示例 |
|--------|------|------|
| Safety | 不引入安全漏洞、不破坏现有功能 | Critical Constraint 违反 = 立即暂停 |
| Correctness | 实现必须正确符合需求 | Done-when 逐项核对不可跳过 |
| Efficiency | 减少不必要的 token 消耗和操作 | 精简模式 re-grounding |
| Completeness | 覆盖所有 P0/P1/P2 条目 | P2 可在时间压力下降级 |

### 现有规则的类型标注

| 规则 | 类型 | 说明 |
|------|------|------|
| 单活跃限制 | 硬性 | active/ 下最多一个 active + 一个 suspended |
| anchor.md 修改需用户确认 | 硬性 | Agent 不得单方面修改 |
| Re-grounding 逐项核对 | 硬性 | 不可用自由文本替代 |
| 3-Strike 升级 | 硬性 | 3 次失败必须停止 |
| 滚动压缩阈值（N=3） | 软性 | 可根据任务复杂度调整为 5 |
| 归档命名格式 | 软性 | 建议遵守但不阻断流程 |
| 交互设计原则 | 软性 | 可选参考 |
