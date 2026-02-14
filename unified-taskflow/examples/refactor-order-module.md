# 示例：重构遗留订单模块

> 触发方式：用户说"帮我重构订单模块"
> 本示例展示 unified-taskflow 的工作流程

## Phase 0: 理解快照

```
📋 意图：重构 order 模块，拆分巨型 OrderService 类（2000+ 行）为职责清晰的子模块
📌 模糊点：
  - 是否需要保持 API 向后兼容？
  - 重构范围是否包含数据库 schema 变更？
  - 测试覆盖率要求？
📎 假设：
  - 当前无其他团队依赖此模块的内部接口
  - 可以接受短期功能冻结
```

用户确认后，生成 `anchor.md`：

```markdown
# Grounding Anchor v1.0

## Intent
拆分 OrderService（2000+ 行）为 3-4 个职责单一的子服务

## Critical Constraints
- API 向后兼容（用户确认）
- 不动数据库 schema（第一期）

## Done-when
- P0: OrderService 拆分完成，所有现有测试通过
- P1: 新模块各自有独立单元测试
- P2: 文档更新
```

## Checkpoint 示例

```
┌─ Checkpoint #1 ─────────────────────────┐
│ 触发: 完成 OrderValidator 提取          │
│ 进度: 1/4 子模块完成                    │
│ 锚点校验: ✅ 未偏离                     │
│ 下一步: 提取 OrderPricingService        │
└──────────────────────────────────────────┘
```

## 3-Strike 示例

```
⚠️ Strike 1: OrderPricingService 提取后 3 个测试失败
   → 修复循环依赖
⚠️ Strike 2: 同一测试仍失败，发现隐式状态依赖
   → 调整提取策略
⚠️ Strike 3: 问题未解决
   → 🚨 升级至用户：OrderPricingService 与 OrderService 存在深度耦合，
     建议选择：(A) 合并为一个模块 (B) 先重构共享状态 (C) 跳过此模块
```
