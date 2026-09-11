# 示例：只读模式

任务：把网页端「A股T1新逻辑只读测试」的最新结论拉下来当本地输入。

```
# 1. 定位会话（知道 id 就跳过这步）
list_conversations()            # 找标题/项目
list_projects()                 # 按项目归类

# 2. 拉取——不发送任何东西
get_conversation(conversation_id="6a979e16-...", limit=50, offset=0)
# has_more=true 就 offset+=limit 继续翻页

# 3. 项目资料
get_project_files(project_id="g-p-…")   # 或项目名
get_memories()
```

注意：只读模式不发任何消息、不开新会话、不写 Memory。要它继续干活请回到推进模式。
