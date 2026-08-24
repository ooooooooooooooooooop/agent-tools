# 示例：CLI worker 联网搜索（web_search 未配置时的标准通道）

来源：2026-08-24 实测。内置 `web_search` 工具（`web-search-deepseek` provider）需要 DeepSeek 官方 API key（`DEEPSEEK_API_KEY` 凭据），本机未配置时报 `WEB_PROVIDER_CREDENTIAL_MISSING`。**不阻塞**：用已登录的 CLI worker 联网搜索，零新增成本。

## 前置探测（一次）

```text
list_cli_backends()   # 确认 codex_cli / claude_code / antigravity_cli / gemini_cli 可用
```

本机实测：4 个 backend 全部 `available: true`；`codex_cli` 的 gpt-5.6-luna 实测跑通联网调研。

## 调用序列（极窄探针）

```text
queue_cli_request(
  backend="codex_cli",
  target_model="gpt-5.6-luna",   # 廉价工作马；不省略档位
  effort="medium",
  project=<项目名>,
  topic=<topic>,
  prompt="联网搜索（只用官方文档/权威来源，给出 URL）：<问题>。找 3~5 个具体答案，每个输出：<字段> | 来源 URL。≤15 行结构化清单，中文。"
)
→ 拿到 request_id
request_result(request_id=..., wait_seconds=120)   # 单次长轮询收取，禁止重复派发
```

## 关键参数

| 参数 | 建议 | 说明 |
|---|---|---|
| `backend` | `codex_cli`（实测首选） | 备选 `antigravity_cli`（gemini flash）、`claude_code` |
| `target_model` | `gpt-5.6-luna` | 必须显式传档位，省略会路由到最贵档 |
| `effort` | `medium` | 调研类足够 |
| `prompt` | 结构化 + 限条数 | 必须要求"给出 URL + 来源"，防止 worker 编造 |
| `request_result` | `wait_seconds=120` | 超时后只查一次在途队列接管原 ID，严禁重发 |

## 验收

- 结果含真实来源 URL（抽查 1-2 个可打开）；
- 引用时标注"来自 CLI worker 搜索（backend/model）"，与内置 web_search 结果区分；
- 若 worker 连续失败 2 次：换 backend（如 antigravity_cli），不无限重试同一通道。

## 与内置 web_search 的取舍

| 通道 | 成本 | 延迟 | 何时用 |
|---|---|---|---|
| 内置 `web_search` | 需 DeepSeek 官方 key（约 $0.001/次） | 秒级 | 已配 key 且需要快速多次搜索 |
| CLI worker 搜索 | 0（复用已有订阅） | 1~3 分钟 | 未配 key / 低频 / 大调研（worker 可边搜边提炼） |
