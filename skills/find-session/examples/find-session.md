# 快速定位会话示例

用户请求：

> 按对话名"升级"找到之前的会话，看看它消耗了多少 token。

执行：

1. `python scripts/find_session.py 升级 --sort tokens --max 3`
   → 列出标题含"升级"的会话，按输出 token 倒序，显示完整输入/输出/缓存写统计。
2. 若需要看内容：`python scripts/find_session.py <session-id>`。

用户请求（正文搜索）：

> 之前有个对话讨论过"上下文溢出"，帮我找到它。

执行：

`python scripts/find_session.py --grep 上下文溢出`

→ 扫描最近 30 个会话的正文，输出含该关键词的会话及匹配消息片段（时间/角色/前 300 字）；找不到再加 `--full` 全量扫描。
