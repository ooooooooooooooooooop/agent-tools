# repeat-tool-reminder 本地补丁（2026-08-26）

## 问题

DSH 内置插件 `@deepseek-ai/dsh-repeat-tool-reminder`（node_modules）的 `agent/pre-step` 钩子：

```js
if (messages.some((message) => message.source.kind === "user")) chains.delete(agent);
```

`messages` 在某些事件路径（快速 goal round / "继续"立即 step）为 `undefined` 时崩溃：

```
Cannot read properties of undefined (reading 'some')
```

导致**每次模型请求前**整轮失败（turn/end reason=error, code=UNKNOWN），目标会话 01:07-01:12 连续 4 轮崩溃。

## 补丁

文件：`<npx-cache>/node_modules/@deepseek-ai/dsh-repeat-tool-reminder/lib/index.js`（line 317-320）

```diff
 	ctx.on("agent/pre-step", ({ agent, messages }, next) => {
-		if (messages.some((message) => message.source.kind === "user")) chains.delete(agent);
+		// Local patch 2026-08-26: guard against messages undefined on some event paths
+		if (Array.isArray(messages) && messages.some((message) => message.source?.kind === "user")) chains.delete(agent);
 		return next();
 	});
```

同时防御了 `message.source` undefined（`?.`）。

## 重装后重打补丁

DSH 更新/重装后（npx 缓存刷新）该文件会被还原，需重新应用上面的 diff。检测方法：

```powershell
Select-String -Path "<npx-cache>\node_modules\@deepseek-ai\dsh-repeat-tool-reminder\lib\index.js" -Pattern 'Array\.isArray\(messages\)'
# 无输出 = 补丁丢失，需要重打
```

## 关联

- 我们的插件 `subagent-prep-exec-gate` 也修复过同一模式的 bug（`messages.some` → `Array.isArray` 防御），已随仓库版本部署。
- 若 DSH 上游修复此包，可移除本补丁（检测到 `Array.isArray(messages)` 或上游新版本号时）。
