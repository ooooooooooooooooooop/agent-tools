# 在其它设备上安装 chatgpt-web 桥

桥运行时已 vendored 进本仓库：`mcp/chatgpt-web-bridge/`（Octo-Lex/ChatGPT-Web2API @497527d + 本机补丁，MIT）。同步本仓库到目标设备后：

## 一次性安装（Windows）

```powershell
cd <本仓库>/mcp/chatgpt-web-bridge
powershell -ExecutionPolicy Bypass -File install.ps1   # venv + pip install -e . + 写 ~/.chatgpt_web2api/config.json
.\start.ps1                                          # Chrome + REST:8080 + MCP:8090
# 在弹出的 Chrome 里登录 ChatGPT 一次（登录态设备本地）
```

## 注册 MCP（各 harness 指向本地 SSE 端点）

```json
"chatgpt-web": { "type": "sse", "url": "http://127.0.0.1:8090/sse" }
```

Devin CLI: `%APPDATA%\devin\mcp_config.json`。其它 harness 同理写各自的 MCP 配置。

## 验收

```powershell
curl http://127.0.0.1:8080/v1/models   # 200
curl http://127.0.0.1:8090/sse -m 3    # SSE 活着
# MCP 调 list_conversations 能列出会话即可。
```

## 注意

- conversation_id 与项目 gizmo id 是账号级资源，跨设备通用——换机后 `list_conversations` 找回原会话续用；项目名可直接传给 `project_id`。
- Chrome profile/登录态、`~/.chatgpt_web2api/`（config、tab registry、pace 文件）是设备本地的，不同步。
- 本地改动的完整说明见 `mcp/chatgpt-web-bridge/VENDORED.md`。
