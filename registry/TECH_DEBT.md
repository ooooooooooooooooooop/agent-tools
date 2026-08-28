# TECH_DEBT — 已登记技术债（冻结期不处理）

| ID | 登记日期 | 内容 | 归属阶段 |
|---|---|---|---|
| AIC_OPAQUE_PATH_VISIBILITY | 2026-08-28 | ~~cordis !!js opaque 无枚举机制~~ → **已关闭（Migration #7）**：`collect_opaque_paths()` 显式枚举 + `dsh.yaml opaque_paths` 契约登记（reason+owner），新增未登记 opaque 即 drift。实测 2 条：`[tool-bash].disabled`、`[tool-pwsh].disabled`。 | 已关闭 2026-08-28 |
| ADMISSION_GAP | 2026-08-28 | 实际在用但未准入：luna/flash/fable目标/sol。**Migration #7 已产出 4 份 governance proposal**（.evolution-inbox gov-20260828-*，含引用方/可达性证据），等待人工准入决策；未自动 admit、未 routing-enable。 | 治理提案待评审 |
| NOVEL_REPO_DURABILITY | 2026-08-28 | novel-main 3 个未推送 commit 的历史内容含脱敏前私有基础设施名（公仓不可 push；禁 force/rewrite/squash）。= BLOCKED_PRIVACY，RPO repos 保持 BREACHED 如实显示。解决方向：新开干净分支重放 sanitized state（待人工裁决）。 | 未解决风险 |
| SWITCHBOARD_CODEX_PATH_STALE | 2026-08-28 | ~~broker codex_path 漂移~~ → **已关闭（Phase5 Runtime Closure）**：解析链=config（失效自动 exists() 跳过）→ ~/.codex/config.toml CODEX_CLI_PATH marker → PATH；已用 switchboard 自身 resolver 结果修复 live config，并经 queue_cli_request 物理验证（req 0571670a，11s，codex_cli:gpt-5.6-luna）。 | 已关闭 2026-08-28 |
| BACKUP_KEY_CUSTODY | 2026-08-28 | Gen2 key 单副本于 Disk 0；Gen3 envelope 架构 READY、实现 DEFERRED、等待 custody root（评审文档在备份报告目录 BACKUP_KEY_ARCHITECTURE_REVIEW.md）。 | GEN3_KEY_CUSTODY_MIGRATION（未来单独启动） |
