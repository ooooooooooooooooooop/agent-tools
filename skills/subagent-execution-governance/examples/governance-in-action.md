# 示例：契约型治理 vs 监督型治理（反面案例 → 正确姿势）

> 场景：本 skill 来源会话（session-33b69ec9-1abf-454d-9555-f2d4031a9453），3 小时、17 个子代理、子代理消耗 309 万输入 token、主会话 28 次催促 + 8 次中断。

## 反面场景一：实现子代理空转

```text
❌ 当时：
  派发 prompt 包括"可以探查"，b21b9211 2 轮读 65 文件、grep 30 次、
  输入 941K tokens 后仍被主会话催"直接实现"
  → 主会话 send_message + 子代理继续读 = 双方烧 token

✅ 现在：
  契约快照写入 OWN/MAY READ/负空间 + 三级读取 + 行动门槛：
  "首次写入前 ≤ 6 次工具调用，无 mutation 即 STALLED"
  → 实现子代理不自由探索，读完 OWN 和契约就写代码
```

## 反面场景二：并行写冲突

```text
❌ 当时：
  多个实现子代理同时派发，OWN 文件重叠，
  b21b9211 报告"目标文件已被父代理并行更新"
  → 子代理写的内容被覆盖，返工，重复消耗 token

✅ 现在：
  派发前检查所有实现子代理的 OWN 文件集合：
  OWN_A ∩ OWN_B = ∅ 才并行
  冲突文件由主会话或 integration agent 最后统一合并
```

## 反面场景三：催收式管理

```text
❌ 当时：
  主会话 28 次 send_message 催"立即收敛/不要轮询/直接写文件/不要再探查"
  + 8 次 interrupt_agent
  → 每次 send_message 注入上下文 → 上下文膨胀 → 缓存命中率恶化

✅ 现在：
  实现子代理返回状态机（IMPLEMENTING/MUTATED/BLOCKED/DONE/FAILED）
  + 结构化 BLOCKED（missing_fact/why_required/already_checked）
  → orchestrator 四选一应答，不靠"你做好了吗"判断进展
  → 正常路径上不存在"催促"
```

## 反面场景四：完整报告注入上下文

```text
❌ 当时：
  169 次 agent/inbox/spliced 事件把子代理完整工作过程 splice 回主上下文
  → 单步 204,869 输入 token 且 cache=0（缓存全未命中）

✅ 现在：
  子代理完成只回结构化结果：
  DONE | Changed: [文件列表] | Validation: 17/17 passed | Deviations: none
  → 工作历史不进 orchestrator 活动上下文
  → 缓存前缀稳定
```

## 反面场景五：直接调顶层

```text
❌ 当时：
  第三个目标未 complete，会话停在 open step
  → 主会话重启需从零开始

✅ 现在：
  checkpoint 存盘（task-state.json + implementation-contract.md）
  + repair contract 恢复（只处理失败点，不重派全新 agent）
  → 中断后新 orchestrator 也能继续
```

## 正确姿势速查

```text
派发 → 契约快照 + 三级读取 + 单写者 + 行动门槛 + 状态机 + 预算 + 结构化结果
等待 → 观察状态机（IMPLEMENTING/MUTATED/BLOCKED/DONE/FAILED），不催促
阻塞 → 四选一应答（自己回答 / probe / 改契约 / 终止）
完成 → 回收结构化结果（status/changed/validation/deviations/blocker）
失败 → repair contract，不重派全新 agent
```

## 产物落点

```text
implementation-contract.md    # 契约快照（含负空间）
task-state.json               # 当前阶段、状态机、预算消耗
completed-work.json           # 已完成文件与验证
validation-report.json        # 验证结果
checkpoints/                  # 长任务里程碑
```
