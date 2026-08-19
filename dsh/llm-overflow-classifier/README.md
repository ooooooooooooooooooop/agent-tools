# llm-overflow-classifier（DSH 用户级插件）

把 DeepSeek/OpenAI 兼容的 **"Input token exceed the limit"** 溢出措辞重新归类为
`CONTEXT_WINDOW_EXCEEDED`，让下游压缩（compaction）与重试逻辑正确触发。

- 目标 profile：`web`（同一插件对 `headless` 同样适用，机制相同）
- 运行位置：`~/.dsh/profiles/web/plugins/`（`$DSH_HOME` 默认为 `~/.dsh`）
- 许可证：仓库根 [MIT](../../LICENSE)

## 为什么是插件而不是 node_modules 补丁

`dsh-llm` 位于 `node_modules/@deepseek-ai/`，每次 `npm update` 或重装
`@deepseek-ai/dsh` 都会覆盖它，补丁静默丢失。插件放在 profile 目录并通过
`cordis.patch.yml` 注册，**升级/重装后自动保留**，无需重放任何补丁。

## 机制

`LlmRuntime.stream()` 派发 `llm/stream` waterfall；插件监听该事件并包装 chunk
流：对 `finish` + `kind:"error"` 的 chunk，若 `failure.message` 匹配
"Input token exceed the limit" 措辞且 `failure.code` 尚未归类，则改写为
`CONTEXT_WINDOW_EXCEEDED_CODE`。原始 frozen failure 对象不会被突变。

## 文件

| 文件 | 说明 |
|---|---|
| `llm-overflow-classifier.js` | 插件源码（ESM，`apply` + `inject:["llm"]`） |
| `cordis.patch.yml` | 可移植注册片段：新设备直接替换默认 `[]`，已有条目则合并 insert |
| `README.md` | 本说明 |

## 在其它设备上安装

### 前提

- 目标设备已安装 `@deepseek-ai/dsh`，且 `web` profile 至少启动过一次
  （首次使用自动初始化 `~/.dsh/profiles/web/`）。

### 步骤

1. 复制插件文件：

   ```bash
   mkdir -p ~/.dsh/profiles/web/plugins
   cp llm-overflow-classifier.js ~/.dsh/profiles/web/plugins/
   ```

2. 注册插件。目标设备的 `~/.dsh/profiles/web/cordis.patch.yml` 若是初始模板
   （内容为 `[]`），直接替换为本包 `cordis.patch.yml` 的内容；若已有其它条目
   （如本机 MCP 行），只把 insert 列表合并进原数组：

   ```yaml
   - insert:
     - id: llm-overflow-classifier
       name: './plugins/llm-overflow-classifier.js'
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
node --check ~/.dsh/profiles/web/plugins/llm-overflow-classifier.js
```

然后构造一条含 "Input token exceed the limit" 的失败消息，观察下游
`agent/request-error` 中的 `failure.code` 是否变为 `CONTEXT_WINDOW_EXCEEDED`
（命中改写 / 未命中透传 / 已归类不动 / aborted 不动 / ok 透传 / 原始对象不被突变）。

## 发布边界

本目录只发布**可移植内容**：插件源码与注册片段。各设备 `cordis.patch.yml` 中的
MCP 条目、本机路径、会话与凭据属于设备运行层，**永不进入本仓库**。
