# chatgpt-web-bridge（vendored）

ChatGPT 网页版桥：用专用 Chrome profile + CDP 驱动已登录的 ChatGPT Web，
对外暴露 OpenAI 兼容 REST（`:8080`）与 MCP SSE（`:8090`）双通道。
网页端只有文本——永远不拿本地工具。

- 上游：<https://github.com/Octo-Lex/ChatGPT-Web2API> @ `497527d`（MIT）
- 本机补丁与差异说明：[VENDORED.md](./VENDORED.md)
- 使用协议（四种模式/会话定位/纪律）：[`skills/chatgpt-web-bridge`](../../skills/chatgpt-web-bridge/SKILL.md)

## 安装与启动（Windows）

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1   # venv + pip install -e . + 推荐 config
.\start.ps1                                          # Chrome + REST:8080 + MCP:8090
# 在弹出的 Chrome 里登录 ChatGPT 一次
```

MCP 注册：`"chatgpt-web": {"type":"sse","url":"http://127.0.0.1:8090/sse"}`。

## 本补丁层要点

- conv-affinity：一会话一 tab，`/c/{id}` tab 跨进程收养，不导航不关闭
- `Target.createTarget` 全部 `background:true`——不抢前台焦点
- `request_pace.py`：账号级跨进程节流（send≥30s / read≥8s / 429→冷却300s）
- `resolve_project_id`：project_id 可传项目名，未知/歧义直接报错
- 上游修复：临时路由误采、zh 占位文本过早完成判定

License: MIT（见 `LICENSE`）。
