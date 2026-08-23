# subagent-splice-summarizer：子代理报告 splice 摘要化插件

子代理的**超长文本报告**不再全文 splice 回主上下文，而是有损压成结构化摘要（保留 status / changed / validation / blocker / artifact 等治理字段）；image/file 等非文本块原样保留。该插件以减少文本体积为目标，不承诺原文无损。

## 包含什么

| 文件 | 说明 | 部署目标 |
|---|---|---|
| `subagent-splice-summarizer-v1.mjs` | ESM 用户级 Cordis 插件，监听 `agent/pre-step` waterfall | `~/.dsh/profiles/<profile>/plugins/` |
| `cordis.patch.yml` | 插件注册条目（含 `thresholdChars` 配置） | 合并进同 profile 的 `cordis.patch.yml` |

## 部署步骤

1. 复制 `subagent-splice-summarizer-v1.mjs` 到目标设备 `~/.dsh/profiles/<profile>/plugins/`（profile 名按实际，如 `web`）；
2. 把 `cordis.patch.yml` 的 `insert` 条目合并进同 profile 的 `cordis.patch.yml`；
3. 确保后续通过该 profile 启动（如 `dsh --profile <profile>` / `dsh web`）；`watchUserPatches` 可热加载 patch，若当前事务因同名插件等原因回滚，重启该 profile 后生效。插件 `id` 在同一 profile 中必须稳定且唯一。

## 行为

- **只处理** `source.kind === 'subagent-report'` 的消息；其他消息（人类输入、工具结果、系统注入）原样不动；
- **只处理** 文本超过 `thresholdChars`（默认 8000 字符，≈5.3k token）的报告；短报告原样通过；
- 摘要策略：提取 `status / changed / validation / deviations / blocker / artifact / contract / goal / scope / must not / exit / next` 各节标题与前几行；无结构字段的报告走首尾截断（保留头 2000 + 尾 2000 字符）；image/file 等非文本块保持原样，不因文本摘要而丢失。

## 定位（重要）

这是**结构性体积保护层**，不是缓存银弹。A/B 实验（`skills/subagent-execution-governance/scripts/splice-ab-experiment.js`）实测：审计会话 117 条注入共 ~55K tokens（仅占总输入 7%），摘要化只省 2.6%——真正的大头是每步前缀重复（169 次注入 / 单步 204K 输入 / cache=0）。所以本插件的价值是"**极长报告不会一次塞爆上下文**"，缓存命中率改善仍需 A/B 重放实测。

## 治理背景

本插件是 [subagent-execution-governance](../../skills/subagent-execution-governance/) skill 的 P2 落地物：结构化结果回收（只回 status/changed/validation/blocker/artifact ref）从"prompt 要求"变成"harness 行为"。

## 回滚

从 `cordis.patch.yml` 删除该条目 + 删除插件文件即可。
