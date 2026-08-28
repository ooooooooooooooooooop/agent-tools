# 对抗式对齐审计派工单（闸门 1）

> 用途：闸门 1 触发事件命中时启动独立审计（objective 变更 / 关键假设证伪 / 不可逆高风险边界 / 多 worker 集成 / 证据冲突 / 连续无进展 / 重大架构决策 / 最终验收必跑），防"自检即跑偏"。
> 硬约束：**只传 objective + 原始证据锚 + 产出物；禁止传任何摘要、work_memory、checkpoint 笔记。**
> 执行：独立 `subagent(run_in_background: true)`，用最便宜档位；`job_output(job_id, wait=true, timeout_ms=60000)` 长轮询收取。

## 派工单

```text
任务类型：对抗式对齐审计
审计对象：<objective 逐字拷贝>
审计依据（只读）：
  - 原始证据锚：<checkpoints/cp-001.json, cp-002.json, cp-003.json>
  - 产出物：<文件列表 + SHA-256>
审计要求：
  - 只依据上述原始证据与产出物，逐项核对与 objective 的一致性；
  - 不得阅读任何摘要、work_memory 或 checkpoint 笔记；
  - 输出唯一结论：✅ 对齐 / ⚠️ 需确认（列出具体不一致项）/ ❌ 不对齐（说明原因）
验收标准：审计结论必须包含至少一条可复核证据（文件路径+行号 或 命令+退出码）
禁止项：不得修改任何文件；不得扩大审计范围；不得输出"感觉还在正轨"式自由文本
```

## 审计结论处置

| 结论 | 处置 |
|---|---|
| ✅ 对齐 | 推进下一个 checkpoint |
| ⚠️ 需确认 | 先逐项 resolve 每个不一致项，更新锚后再推进 |
| ❌ 不对齐 | 暂停，回退到最近一个对齐 checkpoint，修正后重跑审计 |

## 频次配置

```text
audit_frequency: 3   # 默认每 3 checkpoint 审计一次
                     # 0 = 关闭审计（不信任场景强制开）
                     # 10 = 低风险任务降低频次
                     # 1 = 高风险任务每步都审
```

## 审计记录（写入锚的 gates_run）

```json
{
  "gate": "gate_audit",
  "verdict": "PASS",
  "audit_agent_id": "subagent-xxx",
  "audit_conclusion": "✅ 对齐",
  "unresolved": []
}
```
