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