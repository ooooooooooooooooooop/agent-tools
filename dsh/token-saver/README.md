# token-saver：DSH 省 token 配置包

把三处「省 token」改动打包，跨设备部署时复制本目录 + 按下面步骤合并配置。
本包不含任何本机路径；部署时有两处占位符需要替换。

## 包含什么

| 文件 | 对应改动 | 部署目标 |
|---|---|---|
| `dsh-subagent-context-summary.js` | 子代理上下文摘要化（fork 不再全量复制父会话，注入「摘要 + 最近 1 轮」） | `~/.dsh/profiles/<profile>/plugins/` |
| `cordis.patch.yml` | 上面插件的注册条目 | 合并进 `~/.dsh/profiles/<profile>/cordis.patch.yml` |
| 本 README 的「步骤 2/3」片段 | 子代理默认模型降档 / deepseek 重试频率策略 | `~/.dsh/.agent-presets/<preset>/agent.cordis.yml`、`~/.dsh/settings.yaml` |

## 部署步骤（按顺序）

### 步骤 1：摘要插件（省子代理上下文大头）

1. 复制 `dsh-subagent-context-summary.js` 到目标设备 `~/.dsh/profiles/web/plugins/`（profile 名按实际）；
2. 把 `cordis.patch.yml` 的条目合并进同 profile 的 `cordis.patch.yml`；
3. ⚠️ **部署时必改**：条目中 `name` 的 `<你的DSH profile目录>` 替换为目标设备实际路径（如 `C:\Users\用户名\.dsh\profiles\web`）。Windows 路径反斜杠要写成 YAML 单引号字符串。

> 依赖：DSH ≥ rc.7（`dsh-subagent-in-process-driver` 导出 `startInProcessRun`）。
> 效果：父会话超过 30k 字符后，`subagent_fork` 子代理的初始上下文从「全量历史」变为「compaction 摘要 + 最近 1 轮」，典型场景可省子代理侧约 50-70% 输入 token。

### 步骤 2：子代理默认模型降档（可选，部署时问用户）

目标：所有 `subagent` / `subagent_fork` 子代理默认用便宜模型，权威判断留给父会话。

把下面片段合并进目标设备 `~/.dsh/.agent-presets/<preset>/agent.cordis.yml` 的 delegation 组（`tool-subagent` 与 `tool-subagent-fork` 两个条目都加）：

```yaml
    - id: tool-subagent
      name: '@deepseek-ai/dsh-tool-subagent'
      config:
        provider: spawn
        toolName: subagent
        backgroundMode: continuable
        agentOptions:
          provider: <子代理provider>       # 部署时问用户
          model: <子代理模型>              # 部署时问用户，例如 gpt-5.6-luna-max

    - id: tool-subagent-fork
      name: '@deepseek-ai/dsh-tool-subagent'
      config:
        provider: fork
        toolName: subagent_fork
        backgroundMode: continuable
        agentOptions:
          provider: <子代理provider>       # 部署时问用户
          model: <子代理模型>              # 部署时问用户，例如 gpt-5.6-luna-max
```

⚠️ **部署时主动问用户**：`<子代理provider>` / `<子代理模型>` 不要写死——先确认目标设备的模型可用性（如 cpa 代理是否提供 luna-max、有没有 kimi 等），让用户选择，选完再填。填错模型会导致所有子代理启动失败。

### 步骤 3：deepseek 重试频率策略（可选）

在 `~/.dsh/settings.yaml` 的 `llm-pi-ai.providers.deepseek` 下合并：

```yaml
    deepseek:
      apiKeyEnv: <你的API key环境变量>      # 部署时按目标设备保留
      baseURL: <你的baseURL>               # 部署时按目标设备保留
      retryPolicy:
        mode: normal
        maxRetries: 10
        retryableCodes: [EMPTY_RESPONSE, RATE_LIMIT, SERVER, TIMEOUT, TRANSPORT]
        backoff:
          initialDelayMs: 3000
          maxDelayMs: 60000
          jitterRatio: 0
```

⚠️ 只合并 `retryPolicy` 段；`apiKeyEnv`/`baseURL` 等按目标设备已有配置保留，不要整文件覆盖。

> 说明：DSH 退避为 2 倍指数，实际等待序列 3s → 6s → 12s → 24s → 48s → 60s（封顶）→ 60s…，共 10 次重试，无抖动。

## 回滚

- 步骤 1：从 `cordis.patch.yml` 删除条目 + 删除插件文件；
- 步骤 2：删掉两个 `agentOptions` 段；
- 步骤 3：删掉 `retryPolicy` 段。

## 不包含（按用户决定）

- 长会话压缩阈值保持 DSH 默认（窗口 80% 触发），未改动；
- usage 记录无需配置：DSH ≥ rc.7 的 deepseek/pi-ai 通道已在请求中带 `stream_options: { include_usage: true }`，零成本。
