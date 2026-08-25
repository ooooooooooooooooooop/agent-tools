# DSH 用户级修复：LLM 输入超限分类

## 状态（2026-08-20）

**已由上游 rc.7 原生修复，用户侧不再需要任何补丁/插件。**

- `@deepseek-ai/dsh-llm` rc.7 的 `isContextWindowExceededError()` 已内置
  `INPUT_TOKEN_EXCEED_LIMIT` 正则（`lib/index.js`），可直接识别
  "Input token exceed the limit" 措辞并归类为 `CONTEXT_WINDOW_EXCEEDED`。
- rc.7 默认重试策略已提升到 Claude Code 基线
  （`maxRetries 10`、500ms 起、8s 上限、25% jitter）。
- 2026-08-20：用户级插件 `llm-overflow-classifier.js`、`llm-retry-claude-code.js`
  已从 `~/.dsh/profiles/web/plugins/` 删除，`cordis.patch.yml` 对应 insert 条目
  已移除；旧补丁 `dsh-llm-input-token-exceed-limit.patch` 已删除。
  若上游未来回退，按下方"重放/故障排查"重建即可。

## 历史记录（为什么曾有补丁/插件）

`dsh-llm` 位于 `node_modules/@deepseek-ai/`，`npm update` 或重装
`@deepseek-ai/dsh` 会覆盖它，node_modules 补丁会静默丢失——这是第一代
补丁方案被放弃的原因，也是后来用 profile 插件承载修复的原因。

## 重放/故障排查（仅在 rc.7 回退或上游回归时使用）

- 重建 `llm-overflow-classifier`：监听 `llm/stream` waterfall，对
  `finish` + `kind:"error"` 且 `failure.message` 匹配
  "Input token exceed the limit" 的 chunk 改写 `failure.code` 为
  `CONTEXT_WINDOW_EXCEEDED_CODE`；插件放
  `~/.dsh/profiles/web/plugins/`，在 `cordis.patch.yml` 加 insert 条目。
- 重建 `llm-retry-claude-code`：包装 `LlmRuntime.prepareCall`，把普通模式
  retryPolicy 提升到 `maxRetries 10 / 500ms / 8s / 25%`。
- 验证修复生效：构造含 "Input token exceed the limit" 的错误消息，观察
  下游 `agent/request-error` 中的 `failure.code`。
