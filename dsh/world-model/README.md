# dsh-world-model

Personal AI 世界模型的**机制层**插件（非提示词）：

| 机制 | 实现 |
|---|---|
| `world_model` 工具 | predict/model/observe/evaluate/update/probe/meta/value/persist/status → 真实落盘 ledger |
| RAW_EVIDENCE 捕获 | `tools/result` 钩子机械记录全部工具结果 |
| 硬门禁 | `tools.guard`：CORE/FULL 下 consequential mutation（edit/write/exec 等）无绑定预测不放行；不可逆模式需 `irreversible:true` 预测 |
| STATE_RESTORE | 首个会话事件时注入 canonical 摘要 |
| 状态层 | L1 `~/.dsh/world-model/`（ledger+runs+current.json）随时写；L2 `~/personal-ai-state/world-model/` 只收 `proposals/`（治理路径审批） |

模式：`config.mode` 或 `DSH_WM_MODE` = `off|core|full`（默认 off：只记账不拦截）。

配套手册：`skills/world-model-runtime`（九环执行协议细则）。

部署：见 `cordis.patch.yml` 注释。
