# Operations Review 示例

用户：

> 检查一下我的 Personal AI / DSH 生态现在有没有需要处理的问题。

期望行为：

1. 先运行现有 human-facing status、governance、durability、aic validate/diff 与 proposal 检查，不修改任何 canonical。
2. 如果总状态是 BLOCKED，但原因只是已知的 `BACKUP_KEY_CUSTODY`，不要把它误判成基础设施故障。
3. 如果发现新的 RPO breach、配置 drift、scope pollution 或 secret/privacy 风险，标记 `ACTION REQUIRED` 并给出 Change Proposal；不要自动修复。
4. open model admission proposal 只有在已经积累出足够新证据、需要人工裁决时才标 `REVIEW`。

示例输出：

```text
PERSONAL_AI_OPERATIONS_REVIEW

Overall: BLOCKED
Action: NO ACTION

Infrastructure: HEALTHY — Architecture V2.1 运行面无新异常。
Control Plane: HEALTHY — aic validate=VALID。
Harnesses: HEALTHY — 已登记 targets 全部 NO DRIFT。
Models/Routing: HEALTHY — 无新增 suspicious identity 或 routing 异常；4 个 admission proposals 仍为已知待评审项。
Memory/Projects: HEALTHY — 无 scope pollution / schema / provenance 新异常。
Durability: DEGRADED — 已知 novel repo privacy blocker；当前 verified backups 在 RPO 内。
Governance: HEALTHY — 定时治理最近运行成功，无新增高严重度 finding。
External Blockers: BACKUP_KEY_CUSTODY=WAITING_FOR_CUSTODY_ROOT。

New findings:
- none

Recommended next action:
- 无需修改，继续正常使用。
```

这里 `Overall: BLOCKED` 与 `Action: NO ACTION` 可以同时成立：前者描述系统真实状态，后者描述本次是否需要采取新的人工动作。

## Personalization 域示例

用户：

> 看看个性化有没有退化 / AI 最近是不是又变难用了

期望行为：

1. 运行 `personalization_status.py`；无会话消息数据时标 `UNKNOWN` 并提示生成方式，不伪造指标。
2. 纠正分类：只有命中偏好模式且跨会话重复才算 `REPEAT_PERSONALIZATION_FAILURE`；单发纠正不直接判 personalization 失败。
3. 简单问答被注入大量无关 workflow preference（over-personalization）→ `DEGRADED`，只产 Change Proposal。
4. project:A 任务注入了 project:B 的 overlay（scope leakage）→ `ACTION REQUIRED`。
5. Correction Rate 与基线（校准报告 SSOT）持平且无新增重复纠正 → `HEALTHY`，不要求降到 0。

默认短输出示例：

```text
Personal AI Status

Infrastructure     HEALTHY
Personalization    HEALTHY — Correction Rate 与基线持平，无新增重复纠正
Durability         HEALTHY
Governance         HEALTHY
Proposals          HEALTHY — 4 open, no new high-severity
External Blockers  BLOCKED — KNOWN EXTERNAL BLOCKER（BACKUP_KEY_CUSTODY，无变化）

Overall: EXTERNAL BLOCKER（仅已知外部 blocker，无新增异常，继续正常使用）
```

用户说“展开 personalization”时才追加 `--detail` 的指标明细与纠正模式清单。