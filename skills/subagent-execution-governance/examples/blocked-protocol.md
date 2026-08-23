# 结构化 BLOCKED 协议

> 实现子代理遇到契约外信息缺失时，必须走结构化 BLOCKED，禁止"无限问主代理"。

## 格式

```text
BLOCKED:
  missing_fact:      FooService.create() 的返回类型是什么
  why_required:      当前实现需要决定错误处理分支（成功/失败/空值）
  already_checked:   [src/foo.ts, src/types.ts, src/controller/index.ts]
  requested_context: [FooService interface 的完整定义]
```

## Orchestrator 应答四选一

收到 BLOCKED 后，禁止随手回 `send_message("继续看看")`。

```text
A. 自己回答
   把缺失信息直接补进契约，实现子代理继续

B. 派一个 discovery probe
   极窄只读查询 → 回复结果并更新契约

C. 修改契约
   任务设计有误 → 调整 GOAL/OWN/IN SCOPE 后重新派发

D. 终止 + 重设计
   阻塞不可解除 → 终止当前实现，回到设计阶段
```

## 为什么不能"继续看看"

每次 `send_message("继续看看")` 都会：
- 注入子代理的上下文，触发新轮次
- 子代理继续读更多文件，继续消耗 token
- 最终结果仍然是 BLOCKED，只是多花了 token 和延迟

## 示例

```text
# 正确：结构化 BLOCKED
BLOCKED:
  missing_fact:      PersistenceStore.save() 的参数类型
  why_required:      RetryStore.save() 需要与父类签名一致
  already_checked:   [src/state/persistence.ts, src/types/retry.ts]
  requested_context: [PersistenceStore interface, 文件:src/state/persistence.ts:45-60]

# 错误：自然语言求助
"我不确定 save 方法应该用什么参数，我看了 persistence.ts 但还是不太确定，
能不能告诉我应该怎么做，或者我再看几个文件理解一下？"
```

## 要点

- already_checked 必须有具体文件列表，不能为空
- requested_context 要精确到"文件:行号范围"，不是模糊的"这个模块"
- 同一个 missing_fact 不允许重复上报（说明契约没补齐或上报者没读契约）