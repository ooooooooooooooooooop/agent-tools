# 示例：指挥循环

任务：让网页端设计实验，本地 harness 执行并回帖。

```
# 首轮建专线（落进项目以获得项目上下文）
r = chat_completion(project_id="g-p-...", message="设计第一轮实验，输出为可执行任务清单")
conv = r["conversation_id"]          # 存下 = 专线

# 循环：网页端输出任务 → 本地执行 → 回帖事实
本地执行网页端给的任务…
chat_completion(conversation_id=conv, message="执行结果：…\n关键证据：…\n阻塞项：…")

# 网页端基于回帖继续设计下一轮 —— 直到它说完成
```

本地侧纪律：回帖只写「结果 + 证据 + 阻塞」，不写「我建议」。判断是网页端的事。
