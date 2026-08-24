# 示例：处理一条轮询反模式 inbox 条目（完整九步问题分析）

## 场景

`evolution_scan.js` 产出 390 条 inbox 条目，其中 poll 模式 26 条。以下是按 0.3.0 流程处理的过程演示。

## 阶段 1：问题分析（完整九步）

**1.1 量化基线**：总 390 条；poll 26/390=6.7%，策略后 15 条为增量；最极端 session-d249ef16 pollCount=305。
**1.2 全量列问题**：表象=某会话轮询 305 次；真问题=监督等待阶段无"单次长轮询"硬约束。
**1.3 根因分类**：有约束不执行（模块七/八已确立，规则后仍违反）。
**1.4 结构性方案**：临时=文档约束止血；结构性=检测脚本系统层确认（+ broker 侧强制为 Phase 3 候选）。
**1.5 联网借鉴**（必做，异步队列 + 单次 request_result）：回收 LangGraph event streaming / Claude SDK streaming / CrewAI event bus / AutoGen pub-sub / AWS SQS Long Polling 等 11 机制；核心判断="任务句柄+阻塞等待/事件流+超时恢复"。
**1.6 归纳取舍**：方案 A（流程层，观察期）+ B（系统层，确认检测闭环）；C（broker 改造）列为 Phase 3；D（webhook/SSE）不做。
**1.7 优先级排序**：本轮选 poll 问题集，标记 processing。

## 阶段 2：根因深挖与补丁起草

确认**已知规则的回归**。补丁草案（约束分层标注）：A=agent-switchboard-ops 等待硬约束（流程层）；B=evolution_scan POLL_TOOLS 补漏（系统层）。

## 阶段 3：评审

本地预检 validate_repo + quality_report PASS；前沿模型评审返回 PASS。

## 阶段 4：产出提案

```markdown
# 进化提案 #1：监督型长任务中无 wait 轮询反弹（已知规则的回归）
- 问题分析摘要：poll 26/390=6.7%，根因=有约束不执行，借鉴对照 11 机制（见 analysis.md）
- 变更对象：L1 技能（agent-switchboard-ops，流程层）+ scripts/evolution_scan.js（系统层）
- 预期指标：request_status/list_agents 无 wait 调用 ≤1 次/会话；poll 占比下降
- 评审状态：PENDING（等待人工批准）
```

## 阶段 5：交接

问题分析摘要 + 提案交给用户批准；批准后固化；inbox 标 `applied`。
