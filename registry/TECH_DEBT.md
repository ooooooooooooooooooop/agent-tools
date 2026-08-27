# TECH_DEBT — 已登记技术债（冻结期不处理）

| ID | 登记日期 | 内容 | 归属阶段 |
|---|---|---|---|
| AIC_OPAQUE_PATH_VISIBILITY | 2026-08-28 | `aic` 的 cordis loader 把**所有** `!!js` 表达式全局替换为占位符，当前无按路径枚举 opaque/ignored 字段的机制。当前实际 opaque 字段（agent-preset-cc）：`tool-bash.disabled`、`tool-pwsh.disabled`（均不在 field_checks 范围内，故无漏检）。后续 Harness Adapter 阶段：diff 输出应列出 opaque 路径清单。 | Harness Adapter 后续阶段 |
