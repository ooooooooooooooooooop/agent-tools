# Collective Reasoning Experiment (isolated research branch)

实验支线，**不影响生产**：不改 `skills/simulate-elite-experts` 的任何行为、不改 routing、
不注册新模型 registry / 密钥系统 / fallback。本目录只是实验代码；运行数据全部落在
`artifacts/collective_reasoning/`（已被仓库 `.gitignore` 的 `artifacts/` 规则排除，设备本地）。

## Research question

真实异构模型之间怎样交互，能够使最终判断稳定优于最佳独立初始答案，并产生
initial answer set 中不存在的高价值新认知？

## Conditions

| 条件 | 机制 | 每任务调用数（约） |
|---|---|---|
| CURRENT | 生产 Skill 真实契约（SKILL.md 全文为 system prompt，单模型 classic 档） | 1 |
| INDEPENDENT | 5 个异构模型独立作答，隔离上下文，无角色分配 | 5 |
| COUNCIL | 同批 initial -> 互评 -> 传统 moderator synthesis | 11 |
| COLLECTIVE | initial -> 全面交叉阅读 -> 可弃权自由轮（状态停机）-> weakest-belief -> blind-spot（干净上下文）-> materiality gate -> finals -> 中立 renderer | 28-42 |

模型池（复用 `registry/providers.yaml` 已声明的 provider；密钥运行时从设备既有凭据加载，
绝不落盘）：claude-sonnet-4-6 (cpa)、gemini-3.8-flash-high (cpa)、glm-5.3-flash (bai)、
qwen3.8-flash (bai)、k3-256k (kimi)。Judge：claude-opus-4-6-thinking、gemini-3.1-pro-low
（均不参赛）。Utility：gemini-3.7-flash-high（停机评估/renderer/盲区搜索之一）。

约束记录：GPT 系模型因额度不可用；deepseek 全通道不可用（CPA 上游 408、bai 余额 0）。

## Tasks

T1 代码缺陷识别（客观，5 个预置缺陷）；T2 加权区间调度（客观，最优 18={B,C,D}，
双贪心陷阱）；T3 离线同步架构；T4 留存 vs 获客；T5 小模型 vs 大模型（可消解二分法）；
T6 p99 回归因果诊断（客观，植入混淆：deploy 与 cache hit-rate 塌陷同日发生）。

## Reproduce

```bash
cd scripts/experiments/collective_reasoning
python run_experiment.py --run-id exp1 --phase run
python run_experiment.py --run-id exp1 --phase judge
python run_experiment.py --run-id exp1 --phase metrics
```

调用幂等缓存于 `<run>/calls/*.json`，重跑自动跳过已完成调用。

## Delete

删除本目录与 `artifacts/collective_reasoning/` 即完全清除，无其他仓库状态。
