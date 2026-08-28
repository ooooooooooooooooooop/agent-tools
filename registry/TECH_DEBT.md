# TECH_DEBT — 已登记技术债（冻结期不处理）

| ID | 登记日期 | 内容 | 归属阶段 |
|---|---|---|---|
| AIC_OPAQUE_PATH_VISIBILITY | 2026-08-28 | `aic` 的 cordis loader 把**所有** `!!js` 表达式全局替换为占位符，当前无按路径枚举 opaque/ignored 字段的机制。当前实际 opaque 字段（agent-preset-cc）：`tool-bash.disabled`、`tool-pwsh.disabled`（均不在 field_checks 范围内，故无漏检）。后续 Harness Adapter 阶段：diff 输出应列出 opaque 路径清单。 | Harness Adapter 后续阶段 |
| ADMISSION_GAP | 2026-08-28 | 实际在用但未准入的模型：`cpa/gpt-5.6-luna`（Codex 默认档 + cc-switch alias 实际值 + switchboard 暴露清单）、`cpa/gemini-3.7-flash`、`cpa/claude-fable-5-dd-anul-6.5-tpg`、`cpa/gpt-5.6-sol`。Migration #5 禁止扩大 catalog；下一窗口按准入流程批量评审。 | Model Governance 后续窗口 |
| SWITCHBOARD_CODEX_PATH_STALE | 2026-08-28 | `~/.agent-broker/config.json` 的 `codex_path` 指向已不存在的 bin 目录（f71e347e...），实际可用为 `110b3d66a02d864e`。属 MACHINE_LOCAL overlay，aic 不管；修复属 switchboard 自身维护。 | switchboard 维护 |
| BACKUP_KEY_CUSTODY | 2026-08-28 | Gen2 key 单副本于 Disk 0；Gen3 envelope 架构 READY、实现 DEFERRED、等待 custody root（评审文档在备份报告目录 BACKUP_KEY_ARCHITECTURE_REVIEW.md）。 | GEN3_KEY_CUSTODY_MIGRATION（未来单独启动） |
