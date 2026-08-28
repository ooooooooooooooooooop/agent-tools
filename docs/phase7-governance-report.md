# PHASE7_GOVERNANCE_REPORT（Migration #7 · 2026-08-28）

## 1. Phase6 repo durability closure
PARTIAL（有意）：novel-main 3 个未推送 commit（aa8c39b/17516b3/d88fe25, master→origin/main, public 仓）——net tree 干净，但**历史内嵌脱敏前私有基础设施名**（逐 commit 扫描实证 8/7/1 hits）。规则禁 force/rewrite/squash → **NOVEL_REPO_DURABILITY = BLOCKED_PRIVACY**，不 push，作为 unresolved risk 登记（TECH_DEBT）。机制价值：Phase 6 RPO 检测→本次审计→阻止一次隐私泄露。

## 2. Governance reality inventory
EXISTS：validate_repo/quality/evals/publish、a六动词、durability ledger+rpo+restore、model trace、memory tests、evolution_scan/triage+inbox、capabilities.yaml、scheduled tasks×2。MISSING→本次建：统一状态、model 六态矩阵+canary、routing 深检、memory 治理+staleness、scope 回归、dup rules、dead config、capability drift、opaque 可见性、static 边界回归、project state 分类、治理 inbox 写入、治理调度。DUPLICATE/OBSOLETE：无。

## 3-4. Boundary & mutation policy
`scripts/governance/` 独立 namespace；aic 六动词不变（只加了 diff metadata 输出，无新动词）；无 daemon/DB/harness。`registry/governance-policy.yaml` 冻结 auto_allowed/auto_forbidden 清单；identity_assessment 限 consistent/suspicious/unknown。

## 5-6. Model governance + ADMISSION_GAP
model_state.py 六态独立矩阵（DISCOVERED/REACHABLE/HEALTH_CHECKED/ADMITTED/ROUTING_ENABLED/OBSERVED_IN_USE），实测 14 模型；4 个 observed-in-use-not-admitted → 4 份 inbox proposal（luna/flash/fable目标/sol，含引用方证据；proposal 幂等去重）。未自动 admit、未 routing-enable。

## 7. Health/canary
model_health.py：reachability=GET /v1/models（免费）；401/403=reachable-auth-gated；identity=unknown（不断言真实身份）；capability/quality canary 手动档，不高频消耗付费模型。

## 8. Routing governance
routing_gov.py：规则引用 admitted✓、provider 合法✓、fallback 无环✓、broker backend 合法✓、alias SSOT（私有 gateways.yaml）✓、每规则 dry-run classification 输出。findings=0。

## 9-11. Memory governance + staleness + scope isolation
memory_gov.py 按真实 schema（record.yaml/revisions/）校验：7 records findings=0（修订：type 含 decision、created.* 嵌套、fingerprint 校验位）。staleness 多因子分布 {HEALTHY:7}；proposal-only。scope 隔离回归 fixture 固化（test_governance.TestScopeIsolation：alpha 检索零 beta 泄漏）。

## 12-14. Dup rules / dead config / capability
dup_rules：3 对 INTENTIONAL_OVERLAP（switchboard 同一 managed block，by design），无 DUPLICATE/CONFLICT。dead_config：provider 可达性+引用/角色组合状态。capability_gov：初测发现 codex 本地 agent_switchboard/node_repl 未登记 → 补登 capabilities.yaml harness_local（accepted+codex scope）→ **CAPABILITY_DRIFT = 0**。

## 15. AIC_OPAQUE_PATH_VISIBILITY = 已关闭
collect_opaque_paths() + dsh.yaml opaque_paths 契约（reason+owner）；实测 2 条已登记；新增未登记 opaque → drift（红队 #12 覆盖）。

## 16-17. Static boundary & project state
static_gov：5 文件动态签名扫描 violations=0（STATIC_CONTEXT_BOUNDARY_VIOLATION 回归）。project_state_gov：novel-main = PAUSED（有停机标记，非错误），schema 完整。

## 18-19. Durability integration & inbox
durability_gov 只读消费 runs.jsonl/任务状态；FULL_DR_READINESS=PARTIAL 不提升。inbox 复用 ~/.dsh/.evolution-inbox/proposals/，schema 含 id/type/evidence/severity/affected_ssot/recommended_action/safe_to_auto_apply/created_at/status。

## 20-21. Status & scheduling & visibility
gov_status.py：11 域逐域状态+原因（无解释的总分禁止）→ 实测 OVERALL=BLOCKED，原因逐条列出（Durability DEGRADED=repos BREACHED/novel BLOCKED_PRIVACY；Models DEGRADED=ADMISSION_GAP 待评审；External=BLOCKED 两条）。GOVERNANCE_ALERTING = LOCAL_ONLY（governance.jsonl + gov_status 查询）。调度：PersonalAI-Governance-Frequent（每日 04:00，物理 Start-ScheduledTask result=0）+ Weekly（周日 04:45）；expensive canary 手动。

## 22. Red-team（17 测试全绿，覆盖规定 14 项）
generated drift✓ / routing 引用不存在模型✓ / routing-enabled 未 admitted✓ / discovered≠admitted✓ / provider unreachable✓ / identity suspicious 值域✓ / stale memory×3✓ / 跨 project 污染✓ / duplicate rule✓ / dead config 状态✓ / missing capability(superset)✓ / opaque 新增检测✓ / RPO breach✓ / secret fixture 拒绝✓。

## 23. No silent self-healing
全治理链路只有 DETECTED→PROPOSED（inbox）；无 APPLIED 自动 canonical 变更；唯一自动再生成面=deterministic generated（aic render 契约内）。证据：ADMISSION_GAP/CAPABILITY_DRIFT 均以 proposal/登记收束，无任何 canonical 自动改写。

## 24-25. Full regression & final acceptance
repo unittest 60 OK / MCP OK / validate_repo/quality 20/evals 20/publish PASS / diff-check clean / aic VALID / 五 target NO DRIFT。Harness Independence=PASS（Phase5 ANSWER.md 实物）、Continuity=PASS（BLUEFALCON-7734）、Memory Isolation=PASS（fixture 回归固化）、Durability automatable=PASS、Governance 红队=PASS、Boundary=六动词无新 daemon。

## 26-28. Blockers / changed files / commit
External blockers：BACKUP_KEY_CUSTODY（WAITING_FOR_CUSTODY_ROOT）、NOVEL_REPO_DURABILITY（BLOCKED_PRIVACY）、ADMISSION_GAP（4 proposal 待评审）。变更：scripts/governance/（10 模块+2 runner）、registry/governance-policy.yaml、capabilities.yaml（harness_local）、harnesses/dsh.yaml（opaque_paths）、aic.py（opaque 枚举+diff metadata）、tests/test_governance.py、test_durability.py（common 冲突修复）、TECH_DEBT.md、本报告；计划任务 ×2。

## 29. Final Verdicts
PHASE6_REPO_DURABILITY_CLOSURE = PARTIAL（BLOCKED_PRIVACY 有意不 push，隐私优先于 RPO）
PHASE7_GOVERNANCE_IMPLEMENTATION = PASS
PERSONAL_AI_INFRASTRUCTURE_IMPLEMENTATION = PASS
FULL_DR_READINESS = PARTIAL（external_blocker = BACKUP_KEY_CUSTODY）
