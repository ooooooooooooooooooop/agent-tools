# web-search-adapter

免 Key、国内直连的 DSH Web Search Provider，基于 DuckDuckGo Lite 版实现。

## 解决什么问题

DSH 内置的 `web_search` 工具需要有效的 API Key（如 DeepSeek 官方接口）。当没有可用 Key 时，原生搜索功能失效，只能绕道 agent-switchboard 委派给外部 CLI，增加延迟与复杂度。

本插件注册一个**无需 API Key**的搜索 Provider，直接对接 DuckDuckGo Lite 版，国内网络通常可直接访问，恢复 `web_search` 工具的原生可用性。

> 端点选型（2026-08-28 实测）：`html.duckduckgo.com/html/` 会把数据中心出口 IP 拦到 anomaly 验证码页（HTTP 200 但无结果）；`lite.duckduckgo.com/lite/` 同环境返回 10 条真实结果、无验证码，解析器已对实时响应验证通过。

## 工作原理

1. 实现 `ctx.web.registerSearchProvider()` 接口，注册为 `duckduckgo-lite` Provider；
2. 收到搜索请求时，向 `https://lite.duckduckgo.com/lite/` 发送 GET 请求；
3. 解析返回的纯 HTML：从 `a.result-link` 提取标题与跳转壳 URL（`uddg` 参数还原真实地址），从随后的 `td.result-snippet` 提取摘要；
4. 返回标准化 `sources: [{url, title, snippet}]` 结构，供 DSH Web 层消费。

## 配置项

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `endpoint` | string | `https://lite.duckduckgo.com/lite/` | DuckDuckGo Lite 搜索入口 |
| `timeoutMs` | number | `15000` | 请求超时时间（毫秒） |
| `maxResults` | number | `10` | 单次搜索最多返回的结果数 |

## 部署步骤

1. 把 `web-search-adapter.js` 复制到目标 DSH Profile 的 `plugins/` 目录：
   ```text
   ~/.dsh/profiles/web/plugins/web-search-adapter.js
   ```
2. 把 `cordis.patch.yml` 中的 `insert` 片段合并到同 Profile 的 `cordis.patch.yml`：
   ```yaml
   - insert:
     - id: web-search-adapter
       name: './plugins/web-search-adapter.js'
       config:
         endpoint: 'https://lite.duckduckgo.com/lite/'
         timeoutMs: 15000
   ```
3. 重启 DSH 或重新挂载 Profile，即可在会话中使用 `web_search` 工具。

## 验证方式

在 DSH 会话中直接调用：
```javascript
web_search({ queries: ["DeepSeek Harness"] })
```
应返回结构化结果列表，无需绕道 Switchboard。

## 局限与后续

- **稳定性**：DuckDuckGo Lite 可能调整页面结构或开始拦截，导致解析失效；若返回持续为空，先检查是否被拦到 anomaly 页，再考虑切换 Bing 国内版或 SearXNG。
- **结果质量**：HTML 解析结果不如官方 API 丰富（缺少发布时间、评分等元数据），仅作兜底使用。
- **合规性**：请遵守目标网站 robots.txt 与使用条款，控制请求频率。
