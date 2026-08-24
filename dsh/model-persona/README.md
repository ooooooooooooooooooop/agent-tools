# model-persona（DSH 用户级插件）

按当前 agent 使用的具体模型（provider/model），自动在系统提示词中注入
**官方来源的行为 steering 指引**。无需跑评测——各厂商官方文档已替你烧过。

- 目标 profile：`web` / `headless`（所有 profile 均可）
- 运行位置：`~/.dsh/profiles/<profile>/plugins/`
- 许可证：仓库根 [MIT](../../LICENSE)

## 为什么是插件而不是 persona 配置

`dsh-persona` 是**静态模板**（`{{model}}` 只插值模型名，不按模型分支）。
本插件注册一个 `system-prompt` section，并在 `system-prompt/assemble` 瀑布中
读取**当前请求实际生效的模型**（`variables.provider/model`），查画像表返回对应
steering；未匹配的模型返回空串（`renderPrompt` 自动丢弃，零 token 成本）。

## 机制

- 使用 `dsh-system-prompt` 的 `system-prompt/assemble` 瀑布：本插件在瀑布
  `next()` **之后**读取组装结果中的 `variables.provider/model`，再改写本插件的
  section 文本。
- **对话途中切换模型也能正确跟随**：`dsh-agent.installModelSelection()` 是
  `system-prompt/assemble` 瀑布上的监听器，会把 `variables.provider/model`
  覆盖为**当前请求实际生效的模型选择**（`agent.options` 只是创建时快照）。
  本插件的监听器先 `await next()` 再读值，因此拿到的是覆盖后的当前模型——
  会话中途切模型后，persona 与 steering 一起跟随，不会错配。
- 匹配顺序：`<provider>:<model>` 精确 → `model` 跨 provider 兜底。
- 同一模型每次渲染相同文本，KV cache 前缀稳定；切换模型时该 section 文本变化
  （模型切换本来就会失效前缀）。
- 不含 `{{}}` 变量引用，不与 `renderPrompt` 的插值冲突。

## 画像表来源

每条 steering 均来自对应厂商的**官方文档/官方提示词**，不依赖社区经验或猜测：

| 模型 | 来源 |
|---|---|
| `deepseek-v4-flash` / `deepseek-v4-pro` | [DeepSeek V4 发布说明](https://api-docs.deepseek.com/news/news260424/) / [思考模式](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/) |
| `k3-256k` | [Kimi K3](https://www.kimi.ai/blog/kimi-k3) / [推理强度](https://platform.kimi.com/docs/guide/use-reasoning-effort) |
| `claude-sonnet-4-6` | [Claude Code Best Practices](https://code.claude.com/docs/en/best-practices) |
| `claude-opus-4-6-thinking` | [Anthropic Prompting Best Practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices) / [Effort 文档](https://platform.claude.com/docs/en/build-with-claude/effort) |
| `gemini-3.7-flash-high` | [Gemini CLI System Prompt](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/system-prompt.md) / SRI FixedCode |
| `gpt-5.6-sol-xhigh` / `gpt-5.6-luna-max` | [Codex Base Instructions](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/prompts/base_instructions/default.md) |
| `codex-auto-review` | [Codex Auto Review](https://developers.openai.com/codex/sandboxing/auto-review) |

## 安装

### 前提

- 目标设备已安装 `@deepseek-ai/dsh`，且 profile 至少启动过一次。
- 无需修改 `settings.yaml`：插件只读 `agent.options`，不依赖配置。

### 步骤

1. 复制插件文件到 profile 的 plugins 目录：

   ```bash
   mkdir -p ~/.dsh/profiles/web/plugins
   cp model-persona.js ~/.dsh/profiles/web/plugins/
   ```

2. 注册插件。目标设备的 `~/.dsh/profiles/web/cordis.patch.yml` 若是初始模板
   （内容为 `[]`），直接替换为本包 `cordis.patch.yml` 的内容；若已有其它条目，
   只把 `insert` 列表合并进原数组：

   ```yaml
   - insert:
     - id: model-persona
       name: './plugins/model-persona.js'
   ```

3. 生效：`watchUserPatches` 会热重载 `cordis.patch.yml`，GUI 无需重启；
   保险起见重启一次 `dsh web` 也行。

### 验证

```bash
node --check ~/.dsh/profiles/web/plugins/model-persona.js
```

然后打开一个会话，用任意已配置模型发送一条消息，在系统提示词中应看到
对应的 steering 段落（双斜杠分隔的 bullet list）。未匹配的模型不显示任何内容。

## 自定义

### 新增模型

编辑插件中的 `PERSONAS` 对象，按以下格式添加条目：

```js
"your-model-id": {
  source: "https://...",
  steering: [
    "第一条指引",
    "第二条指引"
  ]
}
```

- 模型 id 是 `agent.options.model` 的值（如 `gpt-5.6-sol-xhigh`）。
- 如需限制到特定 provider 路由，用 `"provider:model"` 作为键。

### 修改现有 steering

每条 steering 是**可回滚假设**。修改后观察目标模型行为变化：
- 如果某条规则导致模型变差（如过度保守），删除或注释掉；
- 如果厂商更新了官方文档，按新版本更新来源。

## 发布边界

本目录只发布**可移植内容**：插件源码与注册片段。各设备 `cordis.patch.yml` 中的
MCP 条目、本机路径、会话与凭据属于设备运行层，**永不进入本仓库**。