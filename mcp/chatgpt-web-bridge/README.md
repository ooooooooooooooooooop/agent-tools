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
```

MCP 注册——**推荐 stdio**（harness 会话生灭绑定，不用时零后台进程；首个工具调用
自动拉起 Chrome，冷启动选举锁防重复拉起）：

```json
"chatgpt-web": { "command": "<venv>/Scripts/chatgpt-web2api-mcp.exe" }
```

共享 daemon 备选：`.\start.ps1`（Chrome + REST:8080 + SSE:8090），注册
`{"type":"sse","url":"http://127.0.0.1:8090/sse"}`。任一模式首次使用前在弹出的
Chrome 里登录 ChatGPT 一次（profile 持久）。

## 本补丁层要点

- conv-affinity：一会话一 tab，`/c/{id}` tab 跨进程收养，不导航不关闭
- `Target.createTarget` 全部 `background:true`——不抢前台焦点
- `request_pace.py`：账号级跨进程节流（send≥30s / read≥8s / 429→冷却300s）
- `resolve_project_id`：project_id 可传项目名，未知/歧义直接报错
- 上游修复：临时路由误采、zh 占位文本过早完成判定
- composer 多行插入：会话页 `Input.insertText` 遇 `\n` 只落首段（2026-09-14
  ChatGPT 前端更新后实证），改用 `execCommand('insertText')` 走编辑器自身的
  插入路径

排障速查：
- `Composer text verification failed`：插入文本与读回不一致——多为换行截断
  或会话残留的未发送草稿；发送失败后桥会自动清 composer 草稿（2026-09-15
  起），仍见此错先 `get_conversation` 核实尾部再重发
- `wait_reply` 只在 tail assistant 终态（非 `in_progress`）报 `replied`；
  timeout 看 `tail_status`——`in_progress` = 网页端还在生成，再 wait 别催促
- `no driver slot available` / health `degraded` + `driver_connected:false`：
  daemon 丢了 CDP 连接且不自愈，重跑 `start.ps1`（幂等，不动 Chrome/登录态）；
  若存在多个同名 daemon 进程，先清掉再起

License: MIT（见 `LICENSE`）。
