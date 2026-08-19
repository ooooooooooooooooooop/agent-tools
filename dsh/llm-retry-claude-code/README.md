# llm-retry-claude-code（DSH 用户级插件）

把模型请求的重试策略**提升到 Claude Code 基准**：`maxRetries 10`、起步 `500ms`、
单次等待封顶 `8s`、`25%` 抖动——即 `min(0.5 * 2^n, 8s)` 的指数退避，且每次重试间隔
逐步提升。低于该基准的 provider 自动升级，已配置更强策略的 provider 不受影响。

- 目标 profile：`web`（同一插件对 `headless` 同样适用，机制相同）
- 运行位置：`~/.dsh/profiles/web/plugins/`（`$DSH_HOME` 默认为 `~/.dsh`）
- 许可证：仓库根 [MIT](../../LICENSE)

## 为什么是插件而不是 node_modules 补丁

`dsh-llm` 位于 `node_modules/@deepseek-ai/`，每次 `npm update` 或重装
`@deepseek-ai/dsh` 都会覆盖它，补丁静默丢失。插件放在 profile 目录并通过
`cordis.patch.yml` 注册，**升级/重装后自动保留**，无需重放任何补丁。
也不用手动在每个 provider 的 settings 里写 `retryPolicy`——装一次，全部 provider
（含以后新增的）都达到 Claude Code 基准。

## 机制

agent-loop 通过 `LlmRuntime.prepareCall()` 准备每次模型请求，`dsh-llm-retry` 在
`agent/request-error` 瀑布上执行 prepared call 携带的 `retryPolicy`（指数退避 +
抖动 + 限流感知）。本插件包装 `prepareCall`：

- `mode: "always"` 或 `maxRetries >= 10` 的策略**原样透传**（不强改用户配置）；
- 其余 `mode: "normal"` 策略升级为 `{ maxRetries: 10, initialDelayMs: 500,
  maxDelayMs: 8000, jitterRatio: 0.25 }`，`retryableCodes` 沿用策略自身清单；
- 仅在包装后的策略与原始不同时替换，原始 frozen 对象不被突变。

## 文件

| 文件 | 说明 |
|---|---|
| `llm-retry-claude-code.js` | 插件源码（ESM，`apply` + `inject:["llm"]`） |
| `cordis.patch.yml` | 可移植注册片段：新设备直接替换默认 `[]`，已有条目则合并 insert |
| `README.md` | 本说明 |

## 在其它设备上安装

### 前提

- 目标设备已安装 `@deepseek-ai/dsh`，且 `web` profile 至少启动过一次
  （首次使用自动初始化 `~/.dsh/profiles/web/`）。
- 无需修改 `settings.yaml`：本插件在代码层兜底，任何未显式配置 retryPolicy 的
  provider 自动获得 Claude Code 基准。

### 步骤

1. 复制插件文件：

   ```bash
   mkdir -p ~/.dsh/profiles/web/plugins
   cp llm-retry-claude-code.js ~/.dsh/profiles/web/plugins/
   ```

2. 注册插件。目标设备的 `~/.dsh/profiles/web/cordis.patch.yml` 若是初始模板
   （内容为 `[]`），直接替换为本包 `cordis.patch.yml` 的内容；若已有其它条目
   （如本机 MCP 行），只把 insert 列表合并进原数组：

   ```yaml
   - insert:
     - id: llm-retry-claude-code
       name: './plugins/llm-retry-claude-code.js'
   ```

3. 给 profile 的 `package.json` 加 `"type": "module"`（官方模板不含此字段，
   不加则 `.js` 插件按 CommonJS 解析，`import` 语句报 MODULE_TYPELESS 错）：

   ```json
   {
     "name": "dsh-profile-web",
     "private": true,
     "type": "module",
     "dependencies": {},
     "dsh": { "profile": { "bundles": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app"] } }
   }
   ```

   `bundles` 保持出厂值即可；`loadProfile` 会把出厂元组规范化回模板，同时保留
   `"type": "module"` 这个额外字段。

4. 生效：`watchUserPatches` 会热重载 `cordis.patch.yml`，GUI 无需重启；保险起见
   重启一次 `dsh web` 也行。先放插件文件、后改 patch，避免 watcher 先于文件触发。

### 验证

```bash
node --check ~/.dsh/profiles/web/plugins/llm-retry-claude-code.js
```

然后触发一次模型请求错误，观察会话 `llm/retry` 事件中的 `maxRetries: 10` 与
递增的 `delayMs`（约 `500 → 1000 → 2000 → 4000 → 8000`，封顶 8000）。未命中重试码
或 `always`/已达基准的策略保持原样（透传 / 不升级 / 原始对象不被突变）。

## 发布边界

本目录只发布**可移植内容**：插件源码与注册片段。各设备 `cordis.patch.yml` 中的
MCP 条目、本机路径、会话与凭据属于设备运行层，**永不进入本仓库**。
