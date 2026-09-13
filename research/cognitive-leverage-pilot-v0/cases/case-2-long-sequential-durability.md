# CASE 2: Personal AI Durability Subsystem (Long Sequential Engineering)

## 1. Case Metadata
- **Case ID**: `CASE_02_LONG_SEQUENTIAL_DURABILITY`
- **Archetype**: Long sequential engineering
- **Historical Occurrence**: 2026-08-28 09:00:00+08:00
- **Primary Source Artifacts**: `docs/phase6-durability-report.md`, `tests/test_durability.py`, commit `40d16e3`, tag `personal-ai-phase6-durability-20260828`

---

## 2. Frozen Case Input Packet (Pre-Cutoff Facts Only)

### USER_GOAL
"按照 Personal AI 架构白皮书要求，完成 Migration #6：Durability 子系统的全套实现与物理验证。
要求：
1. 闭合 Phase 5 遗留的失效 Codex 路径与运行时验证；
2. 建立覆盖 6 大数据集（sessions, broker sqlite, cc-switch db, configs, repos, backup vault）的 Durability 现实矩阵；
3. 在独立命名空间 `scripts/durability/` 下实现全部 8 个作业与脚本（backup_sessions, backup_broker, backup_configs, check_repos, archive, restore_check, rpo_check, run_nightly.ps1）；
4. 编写 `tests/test_durability.py` 自动化回归测试；
5. 注册并物理跑通 Windows 定时任务 `PersonalAI-Durability-Nightly`；
6. 物理执行 5 项灾难恢复演练（T1 错过备份、T2 损坏副本、T3 broker 还原、T4 session 三方哈希比对、T5 归档还原），并生成真实 RPO/RTO 证据；
7. 绝不泄露任何 API token/私钥进 ledger 或清单；
8. 跑通全仓现有测试门禁并输出最终 PASS 报告。"

### TIME_CUTOFF
`2026-08-28T09:00:00+08:00`

### KNOWN_FACTS_AT_CUTOFF
1. Phase 5 Multi-Harness 刚刚验收闭合（commit `c8df4be`），但存在运行时残留债务：Switchboard 配置的 Codex 路径失效，需探测实际本地安装路径并修复。
2. 现有数据分布在：`~/.dsh/sessions`（556 文件，约 250MB）、`~/.agent-broker/state.sqlite`（约 8MB）、`~/.cc-switch/cc-switch.db`、各种 config.json/yaml 文件。
3. `registry/TECH_DEBT.md` 记录了未关闭的 `SWITCHBOARD_CODEX_PATH_STALE` 债务。
4. 目录 `scripts/durability/` 尚不存在。
5. `tests/test_durability.py` 尚不存在。

### AVAILABLE_EVIDENCE_AT_CUTOFF
1. `docs/architecture.md` 中 Phase 6 Durability 的规范定义。
2. `registry/TECH_DEBT.md`。
3. 本机环境真实路径（`~/.dsh`、`~/.agent-broker`、`<BACKUP_ROOT>\` 等）。
4. 现有仓库门禁脚本：`scripts/validate_repo.py`、`scripts/publish_check.py`、`tests/test_repository.py`。

### REPO_STATE
- 分支：`main`（或 `phase6-durability`）。
- 干净工作区，Phase 5 提交已就绪。
- 没有 Durability 脚本、测试或计划任务定义。

### RUNTIME_STATE
- Windows 11 环境，PowerShell 7 (`pwsh`) 可用，Python 3.11 可用。
- 本地计划任务服务可用（`schtasks.exe` / `Get-ScheduledTask`）。
- 备份目标卷 `<BACKUP_ROOT>\` 存在。

### USER_CONSTRAINTS
1. 单写区纪律：只能在 `scripts/durability/` 命名空间下编写脚本，不得把 backup/restore 混入 `aic.py` 核心命令。
2. 凭据安全：绝不在任何日志、ledger、manifest 中记录真实的 API Key 或凭据内容；遇到敏感文件必须以 `SECRET_RE` 正则过滤拒绝。
3. 临时隔离：`restore_check` 必须在 temp 临时目录解压和校验，严禁覆盖或触碰线上 live 数据库与会话文件。
4. 真实性约束：不得以 mock 冒充真实执行证据；计划任务必须真实触发一次并取得 `LastTaskResult`。

### AVAILABLE_TOOLS
- File inspection & editing: `read`, `write`, `edit`, `glob`, `grep`
- Shell execution: `pwsh`
- Task state tracking: `todo_write`

### KNOWN_DECISIONS
- Durability 保持独立于 AIC，采用独立的松耦合 CLI 脚本集合。

### KNOWN_UNCERTAINTIES
1. 真实数据全量备份会不会超时或锁死正在使用的 SQLite 数据库？（需查证 SQLite 在线备份 API）。
2. 历史会话是否有超过 90 天的数据可供物理归档？
3. Windows 计划任务以哪个用户运行，非管理员能否正常注册 `PersonalAI-Durability-Nightly`？

---

## 3. Acceptance Design

### DETERMINISTIC_ACCEPTANCE
1. **Script Suite Completeness**: `scripts/durability/` 包含全部 8 个可执行组件，且均能以 `--help` 正常退出（exit 0）。
2. **Unit Test Pass**: `python -m unittest tests/test_durability.py` 全部用例（至少 8 个）PASS。
3. **Database Backup Integrity**: 使用 `sqlite3.connect().backup()` 导出的备份文件能够通过 `PRAGMA integrity_check == ok`。
4. **Scheduled Task Evidence**: Windows 计划任务列表中存在 `PersonalAI-Durability-Nightly`，执行 `Start-ScheduledTask` 后，`Get-ScheduledTaskInfo` 返回 `LastTaskResult == 0`。
5. **Secret Redaction Audit**: 运行 secret-regex 扫描脚本对 `<BACKUP_ROOT>\ledger\runs.jsonl` 及 manifest 扫描，命中数为 0。
6. **Full Repo Regression**: 全量通过 `python scripts/validate_repo.py --strict`、`python -m unittest discover -s tests`、`git diff --check`。

### SEMANTIC_OUTCOME_ACCEPTANCE
- 完整建立 Personal AI 六大数据集的备份、归档、RPO 监控与沙箱恢复验证能力。
- 逻辑清晰，对于不可由系统自动解决的外部阻塞（如全盘丢失后的 DEK 密钥保管 root-of-trust），不隐瞒粉饰，如实标为外部依赖阻塞。

---

## 4. Isolated Hidden Future (STRICTLY PROHIBITED FROM RUNTIME INPUT)

> **WARNING**: The contents of this section must NOT be leaked into the agent's context during evaluation runs.

### ACTUAL_ROOT_CAUSE / HISTORICAL IMPLEMENTATION
- 历史实现提交于 commit `40d16e3`。
- Phase 5 codex 失效路径修复：`~/.codex/config.toml` 指向实际存在的 `...\110b3d66\codex.exe`。
- 真实归档演练中，发现本地所有 session 数据均小于 30 天，无大于 90 天的数据，因此采用 fixture（`-120d mtime`）进行了物理归档还原校验。
- 最终生成的报告为 `docs/phase6-durability-report.md`。
