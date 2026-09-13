# 在其它设备上安装 chatgpt-web 桥

桥运行时已 vendored 进本仓库：`mcp/chatgpt-web-bridge/`（Octo-Lex/ChatGPT-Web2API @497527d + 本机补丁，MIT）。同步本仓库到目标设备后：

## 一次性安装（Windows）

```powershell
cd <本仓库>/mcp/chatgpt-web-bridge
powershell -ExecutionPolicy Bypass -File install.ps1   # venv + pip install -e . + 写 ~/.chatgpt_web2api/config.json
# venv 默认在包内 .venv，设 W2A_VENV 可放仓库外
```

## 注册 MCP（推荐 stdio，harness-bound）

```json
"chatgpt-web": { "command": "<venv>/Scripts/chatgpt-web2api-mcp.exe" }
```

stdio 模式：MCP 进程随 harness 会话生灭，首个工具调用自动拉起 Chrome——不用时
零后台进程，无需开机自启。Devin: `%APPDATA%\devin\mcp_config.json`；其它 harness 同理。

共享 daemon 备选（多客户端共享/REST 消费方）：`.\start.ps1` 起 Chrome + REST:8080 +
SSE:8090，注册 `{ "type": "sse", "url": "http://127.0.0.1:8090/sse" }`；
挂掉后跑 `chatgpt-web2api ensure` 自愈（幂等带锁）。

任一模式首次使用前：在桥启动的 Chrome 里登录 ChatGPT 一次（登录态设备本地）。

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
