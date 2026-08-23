# subagent-usage-observer：子代理用量观测插件

把"子代理烧了多少 token、有没有空转"变成**可查询的模型工具**。安装到目标 profile 后，后续使用该 profile 启动 DSH 时跨会话、跨进程生效。

## 包含什么

| 文件 | 说明 | 部署目标 |
|---|---|---|
| `subagent-usage-observer-v1.mjs` | ESM 用户级 Cordis 插件，注册两个只读工具 | `~/.dsh/profiles/<profile>/plugins/` |
| `cordis.patch.yml` | 插件注册条目 | 合并进同 profile 的 `cordis.patch.yml` |

## 部署步骤

1. 复制 `subagent-usage-observer-v1.mjs` 到目标设备 `~/.dsh/profiles/<profile>/plugins/`（profile 名按实际，如 `web`）；
2. 把 `cordis.patch.yml` 的 `insert` 条目合并进同 profile 的 `cordis.patch.yml`；
3. 确保后续通过该 profile 启动（如 `dsh --profile <profile>` / `dsh web`）；`watchUserPatches` 可热加载 patch，若当前事务因同名工具等原因回滚，重启该 profile 后生效。插件 `id` 在同一 profile 中必须稳定且唯一。

> 依赖：DSH 的 `sessionQuery` 服务（读子代理会话日志，只读）。数据源是会话日志本身，不依赖 agent-switchboard 或任何外部通道。

## 工具

### `subagent_usage`

输入子代理 session id（如 `c8e2fc19-fdff-47d2-a05f-2e8200878890`），输出：

```json
{
  "sessionId": "...", "preset": "cc",
  "models": ["cpa/gpt-5.6-luna-max"],
  "tokens": { "uncachedInput": 603537, "output": 59501, "cacheRead": 4302848, "cacheWrite": 0, "reasoning": 0, "billedInput": 4906385 },
  "toolCalls": 82, "mutations": 16, "potentialMutationCalls": 0, "blockedReports": 0, "turns": 1, "steps": 48
}
```

### `subagent_stalled_check`

基于预算约束判定空转（替代"催 2 次判空转"的轮次拍脑袋）：

- `mutationBudget`（默认 1）：成功的 `edit`/`write` 结果达到预算 → `MUTATED`；失败调用不计 mutation。
- 检测到四字段结构化 BLOCKED（missing_fact / why_required / already_checked / requested_context）→ `BLOCKED`。
- `toolBudget`（默认 6，主行动门槛）或 `turnBudget`（默认 3，兜底上限）任一耗尽，且无已确认 mutation / BLOCKED / 可能写入的命令或委派调用 → `STALLED`。行动预算优先，不把“等到第 N 轮”当主判据。
- 出现 `pwsh` / `bash` / `run_code` / 委派 / MCP 等可能造成修改、但会话日志无法从工具名证明结果时，保守返回 `IMPLEMENTING` 并给出 indeterminate 证据，避免假阳性。

## 治理背景

本插件是 [subagent-execution-governance](../../skills/subagent-execution-governance/) skill 的 P1 落地物：把 watchdog 判定从"主会话催促观察"变成"子代理日志事实"（工具调用数与 mutation 计数），正常路径不需要 send_message / interrupt_agent。

## 回滚

从 `cordis.patch.yml` 删除该条目 + 删除插件文件即可。
