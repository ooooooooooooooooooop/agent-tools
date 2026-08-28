# PHASE6_DURABILITY_REPORT（Migration #6 · 2026-08-28）

## 1. Phase5 runtime closure
PHASE5_RUNTIME_CLOSURE = PASS。configured_codex_path=...\f71e347e(失效) → actual=...\110b3d66\codex.exe (0.149.0-alpha.4.1)；resolution_source=~/.codex/config.toml CODEX_CLI_PATH marker（discover_codex 对失效 config 值做 exists() 自动跳过）。ownership=DEVICE_LOCAL（switchboard setup/repair 所有，aic overlay 不管）。用 owner resolver 的值修复 live config（备份 .bak-20260828）；`aic validate`=VALID、`aic diff switchboard`=NO DRIFT。Phase5 original verdict remains PASS；runtime debt closed after checkpoint（commit 40d16e3，未重打 tag）。

## 2. Switchboard→Codex physical evidence
queue_cli_request backend=codex_cli, req 0571670a-fae9-4464-9203-64e2d42c2b41：autorun worker → codex exec → completed 11s，responder_model=codex_cli:gpt-5.6-luna，输出 "RUNTIME_OK GPT-5"，ledger/topic 已记录。SWITCHBOARD_CODEX_RUNTIME = PASS。

## 3. Durability reality matrix（2026-08-28 新测）
| dataset | source | 规模/变化率 | backup | last_success | RPO | 保留 | restore | 自动化 |
|---|---|---|---|---|---|---|---|---|
| sessions | ~/.dsh/sessions | 556 文件 256.8MB（全 <30d，高增长） | 增量复制+hash manifest | 2026-08-28 | 26h | hot90d/cold12mo | restore_check 三方哈希 | ✅ nightly |
| broker sqlite | ~/.agent-broker/state.sqlite | 8MB+wal | sqlite backup API 快照+integrity_check | 2026-08-28 | 26h | 累积快照 | temp 恢复 integrity ok | ✅ nightly |
| cc-switch db | ~/.cc-switch/cc-switch.db | 15MB | 同上 | 2026-08-28 | 26h(随 broker) | 同上 | 同上 | ✅ |
| configs（不可重建） | broker config/claude/codex/gemini settings、cpa config.yaml+auths、cc-switch settings、AGENTS.md | KB 级，低变化 | 每日拷贝+manifest 分类 generated/irreplaceable | 2026-08-28 | 168h | 每日代际 | 解析检查 | ✅ nightly |
| repos | skills/personal-ai-state/novel-main | unpushed=检测 | remote=恢复面 | 持续 | 26h | git 历史 | clone/remote | ✅ 风险扫描 |
| backup vault | D:\ai-backup\remote-package* | 797MB | gen2 加密（人工管线） | 2026-08-28 | 人工 | gen 保留 | gen1/gen2 decrypt 已验 | 人工（DEK 单副本=外部阻塞） |

## 4-8. Jobs/Archive/Scheduled/Ledger
jobs: backup_sessions（增量 index size+mtime+sha，dated 代际不覆盖）/ backup_broker（在线 backup API，integrity 验证后才记 verified）/ backup_configs（generated vs irreplaceable 分类，secret 文件 local-only 不打印内容）/ check_repos（rev-list 修正，检测不提交）/ archive（dry-run 默认，--execute 才动）/ restore_check（temp 隔离）/ rpo_check（ledger 驱动）。
Archive 策略验证：真实数据全 <30d、256.8MB 无容量压力 → 90d 阈值保留，不强行搬近期数据。
Scheduled：PersonalAI-Durability-Nightly（powershell.exe，每日 03:30，StartWhenAvailable；物理 Start-ScheduledTask LastTaskResult=0）。
Ledger：D:\ai-backup\ledger\runs.jsonl append-only，字段 job/dataset/started/finished/status/files/bytes/manifest/integrity/error；写入前 secret-regex 拒绝。

## 9. RPO evidence
sessions/broker/configs HEALTHY（age≈0h）；repos BREACHED=novel-main 3 个未推送提交+脏树（真实风险，如实报告，不代提交）。

## 10. RTO/restore evidence
restore_check 物理通过：broker-2026-08-28T0932.sqlite integrity_check=ok；session 三方哈希 match（novel-main/efeddc47… 随机样本）；configs 5 个 JSON/YAML 解析通过。临时目录恢复，未触 live。

## 11. Failure tests
T1 错过备份：--simulate-age 100h → BREACHED exit=1 ✅；T2 副本毁损：integrity 立即 DatabaseError("disk image is malformed")（恢复工具已加固捕获）✅；T3 broker restore ok ✅；T4 session 3-way ✅；T5 archive restore：真实无 >90d 样本 → PHYSICAL_OLD_DATA_ARCHIVE=NOT_APPLICABLE；fixture（-120d mtime）物理通过：zip+manifest+三方 sha 一致 ✅。

## 12. Disaster scenarios
Disk 0 loss：ciphertext survives, Gen2 key does not → FULL RESTORE = BLOCKED_BY_KEY_CUSTODY（不粉饰）。DSH deletion：canonical/personal-state/project-state 已推送远端 + local tiers ✅。agent-tools local deletion：remote clone 恢复至最近 push（RPO=最后 push，由 repos 扫描监控）。Broker corruption：最新 verified snapshot 可恢复（T3）。Session 误删：最新 verified backup 恢复（T4）。

## 13. Derived-data rebuild proof
npx cache（%LOCALAPPDATA%\npm-cache，exists）can_rebuild=npm 重装自动重建；projcache/.dsh/cache 不存在（运行时再生成）；derived index 未实现（retrieval.py 全扫，无索引文件 → trivially rebuildable）；generated config 可 aic render 重建（configs manifest 已标 generated-rebuildable）。均不备份。

## 14. Security audit
durability 脚本/ledger/清单 secret-regex 扫描 0 命中；cpa auths/config.yaml 仅本地 D:，清单只记 name+sha；ledger 写入前 SECRET_RE 拒绝。

## 15-17. Boundary / regression / diffs
aic 仍六动词无 backup/schedule/restore/monitor；durability 独立 scripts/durability/ namespace。全回归：repo unittest OK / MCP OK / validate_repo PASS / quality 20/20 / evals 20/20 / publish PASS / diff-check clean / aic VALID / 五 target NO DRIFT。

## 18-19. Changed files & task inventory
新增 `scripts/durability/`（common/backup_sessions/backup_broker/backup_configs/check_repos/archive/rpo_check/restore_check + run_nightly.ps1）、`tests/test_durability.py`（8 用例）、本报告；`registry/TECH_DEBT.md`（关闭 SWITCHBOARD_CODEX_PATH_STALE）；personal-ai-state `sync/this-device.yaml`（设备绑定）。计划任务：PersonalAI-Durability-Nightly 03:30 daily。

## 20. Unresolved external blockers
BACKUP_KEY_CUSTODY = WAITING_FOR_CUSTODY_ROOT（EXTERNAL_DURABILITY_BLOCKER，独立保留）；ADMISSION_GAP、AIC_OPAQUE_PATH_VISIBILITY、Health-checked 档、consolidation 策略（沿用）。

## 21. Commit/tag/push
main commit（见 git log）→ push → tag personal-ai-phase6-durability-20260828。

## 22. Verdicts
PHASE5_RUNTIME_CLOSURE = PASS
PHASE6_IMPLEMENTATION = PASS
FULL_DR_READINESS = PARTIAL（external_blocker = BACKUP_KEY_CUSTODY；custody root 存在前不得宣称 FULL_DISASTER_RECOVERABILITY = PASS）
