# autonomous-execution-governor：DSH 侧预算/回路断路器运行时 adapter

`AUTONOMOUS_EXECUTION_GOVERNANCE`（Personal AI 生态级能力，canonical 见 `registry/autonomous-execution-governance.yaml`）在 DSH 的运行时接线。**项目不需要也不允许自带实现**；本插件只是 Personal AI 的 DSH 适配层。

## 装什么

| 文件 | 说明 | 部署目标 |
|---|---|---|
| `autonomous-execution-governor.mjs` | ESM 用户级 Cordis 插件（`ctx.tools.guard` 守卫层） | `~/.dsh/profiles/<profile>/plugins/` |
| `cordis.patch.yml` | 插件注册条目（含启用配置） | 合并进同 profile 的 `cordis.patch.yml` |
| `test-governor.mjs` | `node:test` 纯逻辑单测（预算/回路断路器/进度检测） | 仓库内运行 |

## 前置条件

1. AIC 生成预算投影（generated state）：`python scripts/aic/aic.py apply dsh`
   → 产出 `~/.dsh/governance/execution-profiles.generated.json`（来自 canonical `registry/execution-profiles.yaml`）。
2. Durable checkpoint 工具：`scripts/autonomy/checkpoint.py`（不含本机路径，部署时在 patch `config.checkpointScript` 填绝对路径或设 `AE_GOV_CHECKPOINT_SCRIPT`）。

## 行为

- **缺省 = observation mode**：patch 里 `taskId: null` → 插件只把工具调用写入 `governor-audit.jsonl`，**不拒绝任何调用**（安全态，不会破坏既有会话）。
- **启用硬门禁**：patch 配置 `taskId` + `profile`（或环境变量 `AE_GOV_TASK_ID` / `AE_GOV_PROFILE`）后，guard 层在 dispatch 前强制执行：
  - agent_turns（以 tool action 数为 turn 代理，偏保守）、provider_calls、runtime_min → `FAIL CLOSED`（返回拒绝原因给模型）+ 写 durable checkpoint + audit。
  - loop breaker：repeated identical tool call（同 tool+args hash 窗口内重复）、no-progress（无 write-like 动作的连续轮）→ 先 soft（窗口阈值内继续观察），达 hard 阈值 → `circuit_broken`，停止一切工具派发，checkpoint `stop_reason=loop_breaker`。
  - checkpoint cadence：每 `checkpoint_cadence_turns` 轮落盘一次（`checkpoint.py save`，写入 `personal-ai-state/checkpoints/`）。
- 内部错误永远 fail-open（记录 audit，不阻断）。

## 验证

```bash
node --test dsh/autonomous-execution-governor/test-governor.mjs   # 纯逻辑单测
python scripts/autonomy/acceptance.py --live                       # 12 项对接收（含本插件机制对应项）
python scripts/aic/aic.py diff dsh                                 # 预算投影 + 治理块 NO DRIFT
```

## 回滚

从 `cordis.patch.yml` 删除该条目 + 删除插件文件即可；guard 层无残留状态（状态文件在 `~/.dsh/governance/state/`，可一并删除）。

## REMAINING_LIMITATIONS（诚实边界）

- token/成本预算的**硬**门禁在本插件 v1 不做（需要在 guard 拿不到每调用 token 计量）；由 `checkpoint.py`（python 侧按 usage 快照）记账、`usage_ledger.py runaway` 检测，cached-input 计量复用 DSH 原生 token-meter / subagent-usage-observer（cacheRead 可读，已实测）。
- 纯文本推理轮（无工具调用）无法在 guard 层观测；此类逻辑依赖 governor 注入的 AGENTS.md 硬规则 + model 层提示 + 会话级原生 turn/compaction 兜底。
- 本插件按任务粒度强制；语义上的"task 边界"由调用方在 patch/环境变量中声明。