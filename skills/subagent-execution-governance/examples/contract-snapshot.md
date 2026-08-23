# 契约快照模板（Implementation Contract）

> 每个实现子代理派发前必须有一份契约快照。以下示例基于"实现 Mission Controller 的重试状态持久化"。

```text
GOAL:
  实现 MissionController 的 retry state 持久化存储与恢复

OWN:
  src/state/retry.ts
  src/state/retry.test.ts

MAY READ:
  src/state/index.ts          # 现有状态管理入口
  src/types/retry.ts          # 重试类型定义
  src/controller/index.ts     # 调用的 controller 接口

REFERENCE:
  src/state/persistence.ts    # 参考实现：现有持久化模式

IN SCOPE:
  - 在 src/state/ 下新增 retry.ts，实现 RetryStore 类
  - 实现 save/load/clear 三个方法
  - 在 src/state/ 下新增 retry.test.ts，覆盖 save/load/clear/empty/error 路径
  - 使用现有 persistence.ts 的存储模式（接口一致）

OUT OF SCOPE:
  - 修改现有 src/state/index.ts 的导出
  - 修改 src/controller/index.ts 的入参
  - 添加新的全局配置项
  - 引入第三方存储库

MUST PRESERVE:
  - 现有 public API 签名不变
  - 现有错误处理模式（抛出特定 Error 类型）
  - 现有测试 runner（vitest）与配置文件

MUST NOT:
  - 修改 scheduler 模块
  - 修改 database schema
  - 顺手重构 src/state 里其他文件的代码
  - 添加 fallback/default 路径（除非契约明确要求）
  - 试图修复"顺便发现"的非目标问题

EXIT:
  - retry.ts 完成并 tsc 通过
  - retry.test.ts 完成并 vitest 通过

BLOCKED（允许升级阻塞的场景）:
  - RetryStore 接口与现有 persistence 模式不兼容 → 问主会话
  - 测试中出现预期之外的契约依赖 → 问主会话
  - 发现契约中 MAY READ 文件不足以完成实现 → 问主会话
```

## 要点

- **负空间（OUT OF SCOPE / MUST NOT）比正空间更具体**——这是防 scope creep 最有效的手段
- OWN 必须具体到文件路径，不是模块名称
- BLOCKED 只允许契约外信息缺失，禁止"再理解一下架构"类阻塞