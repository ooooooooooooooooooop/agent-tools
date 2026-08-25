# subagent-prep-exec-gate

DSH 用户级插件：子代理（subagent）派发的"准备/执行平衡"守卫。

## 解决的问题

2026-08-25 实测反模式：同一会话单日派发 **19 个 subagent 全部为调查类**（Trace/Design/Analyze...）、14 个 codex 派工 **0 个 implementation**，用户两次发火（"不是收口，什么时候才能开始找问题解决问题"）。broker 层的 work-structure gate 只覆盖 `route_agent_task`/`queue_cli_request`，**原生 subagent 通道不受约束**——本插件补上这个盲区。

## 机制

| 事件 | 作用 |
|---|---|
| `tools/post-execute` | 拦截 `subagent` 工具调用，按 description 分类计数 PREP/EXEC；触发条件（连续 ≥6 PREP 且 0 EXEC）时向模型下一步输入注入可见警告（`additionalContexts`，参照 `@deepseek-ai/dsh-repeat-tool-reminder` 的模式） |
| `agent/pre-step` | 出现新的 user 消息时重置计数窗口（用户新指令 = 新一轮） |

分类：description 以 `[EXEC]` 或 `implement/build/run/repair/fix/generate/execute/apply/produce/create` 开头 → EXEC；其余（含 `[PREP]` 与裸描述）→ PREP。一次 EXEC 派发重置连续 PREP 计数。

## 为什么是"警告注入"而非"硬拒绝"

- subagent 派发发生在模型输出（tool call），post-execute 是事后钩子，无法阻止调用本身；
- 但警告作为 `additionalContexts` 注入**下一次模型输入**，模型必然看到（对比约定层文档——这是机制层可见）；
- 若连续警告仍被无视，可升级为在 `agent/pre-step` 中按计数注入强指令或阻断派生（插件扩展点已留）。

## 部署

```powershell
# 1) 复制插件到 profile
Copy-Item dsh\subagent-prep-exec-gate\subagent-prep-exec-gate.mjs ~/.dsh/profiles\web\plugins\

# 2) 注册（合并 cordis.patch.yml 条目到 profile 的 patch）
#    条目见 cordis.patch.yml（- insert: - id: subagent-prep-exec-gate ...）
```

## 配置

`warnPrep`（默认 6）：连续 PREP 触发阈值。

## 验证

- 静态检查：`node --check subagent-prep-exec-gate.mjs`
- 行为验证：派发 6 个 `description="[PREP] 调查 xxx"` 的 subagent 后，第 7 次模型输入应含 `[subagent-prep-exec-gate] 警告`；派发 1 个 `description="[EXEC] implement xxx"` 后计数重置，警告消失。
