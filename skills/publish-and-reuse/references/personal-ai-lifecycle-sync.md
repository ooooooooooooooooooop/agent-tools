# Personal AI Lifecycle Sync 协议（reference）

本文件承载 `$publish-and-reuse` 的完整生命周期同步协议。SKILL.md 只保留触发、模式路由、高层流程、安全规则与输出契约；细节以本文件为准。

编排器实现：`scripts/personal_ai_sync.py`（薄编排器：inspect / classify / 调用既有确定性工具 / 排序 / 报告；不实现 Memory 数据库、不取代 git/aic/governance、不做 LLM 语义合并、不是 daemon）。

---

## 1. Lifecycle Modes

```text
CHECK    只读取和比较（连 fetch 之外什么都不写），输出各 Plane 状态
PULL     只允许安全的 fast-forward 拉取；禁止覆盖 dirty tree / rebase / 自动解冲突
PUSH     只允许发布已合法形成且验证通过的 canonical 变化；禁止自动 commit/force push/上传 runtime 态
SYNC     日常默认（AUTO_SYNC）：fetch → classify → determine direction → safe pull/push/merge
         → runtime refresh → verify。用户不需要选方向。
RESTORE  local canonical missing 时自动进入；复用 PULL + bootstrap + runtime regeneration，
         不维护第二套独立 restore 逻辑
```

触发路由：用户说“同步一下 / 同步我的 Personal AI / 更新一下这台电脑 / 让两台电脑一致 / 把变化同步出去”→ AUTO_SYNC；只有明确说“只检查 / 只拉远端 / 只上传 / 在新电脑恢复”才强制 CHECK / PULL / PUSH / RESTORE。

## 2. Canonical Source Matrix

| Plane | 内容 | Transport |
|---|---|---|
| **A. agent-tools** | Skills / scripts / Control Plane / model registry / routing / adapters / capabilities / governance / durability / publish-and-reuse | Git remote（public） |
| **B. personal-ai-state** | identity / preferences / goals / Dynamic Memory canonical / private overlays / project index / private gateway mappings / sync metadata | Git remote（**private**，永不并入 public agent-tools） |
| **C. Project Repositories** | 项目 code + `/.ai/state/`（goal/architecture/decisions/constraints/completed/experiments/unresolved/next_actions），与代码共同演进 | 各自 Git remote |

Project State 随项目仓库走；ACTIVE 项目由 personal-ai-state goals + project index 识别，不默认同步 archived/inactive。

## 3. SYNC_OWNERSHIP_MATRIX（实现前调查的真实状态）

| data_class | canonical_owner | local_path | remote | pull_policy | push_policy | auto_merge | conflict_policy | device_local | derived | backup_only | rebuild_after_sync |
|---|---|---|---|---|---|---|---|---|---|---|---|
| agent-tools 代码 | agent-tools git | `Desktop\skills` | public GitHub | FF | 需门禁 PASS + privacy scan | 否 | REVIEW | 否 | 否 | 否 | 受影响 runtime refresh |
| identity | personal-ai-state | `state/identity.md` | private git | FF | committed ahead | 否 | CONFLICT_REVIEW（禁 last-write-wins） | 否 | 否 | 否 | Context Builder 输入刷新 |
| preferences | personal-ai-state | `state/preferences.md` | private git | FF | committed ahead | 否 | CONFLICT_REVIEW | 否 | 否 | 否 | 同上 |
| goals | personal-ai-state | `state/goals.md` | private git | FF | committed ahead | 否 | CONFLICT_REVIEW | 否 | 否 | 否 | project discovery 重读 |
| Dynamic Memory records | personal-ai-state | `memory/records/*/record.yaml` | private git | FF | committed ahead | **是**（不同 record 不同设备） | 同 ID 元数据不一致 → CONFLICT | 否 | 否 | 否 | derived index rebuild |
| Memory revisions | personal-ai-state | `memory/records/*/revisions/*.yaml` | private git | FF | committed ahead | **是**（不同 immutable revision） | concurrent revision → 保留双方 + 标记 `concurrent-revisions`，supersede 消解 | 否 | 否 | 否 | derived index rebuild |
| Memory lifecycle state | personal-ai-state | `memory/records/*/state.yaml` | private git | FF | committed ahead | 否（provider 契约：lifecycle 分歧按 (at, device_id) 取胜者 + 标记） | 标记冲突 | 否 | 否 | 否 | 同上 |
| project code | 各自项目 git | `Desktop\<project>` | 各自 remote | FF | privacy audit PASS 且 policy 允许 | 否 | REVIEW | 否 | 否 | 否 | 项目自身验证 |
| Project State (`/.ai/state/`) | 各自项目 git | 随项目 | 随项目 | 随项目 | 随项目 | 否 | REVIEW | 否 | 否 | 否 | project context refresh |
| private project overlays | personal-ai-state | `projects/<name>/overlay.md` | private git | FF | committed ahead | 否 | CONFLICT_REVIEW | 否 | 否 | 否 | Context Builder |
| model registry | agent-tools | `registry/models.yaml` | public git | FF | 门禁 PASS | 否 | REVIEW | 否 | 否 | 否 | render 受影响 Harness model 段 |
| routing policy | agent-tools | `routing-policy.yaml` | public git | FF | 门禁 PASS | 否 | REVIEW | 否 | 否 | 否 | refresh routing consumers |
| gateway mappings | personal-ai-state | `registry/gateways.yaml` | private git | FF | committed ahead | 否 | CONFLICT_REVIEW | 否 | 否 | 否 | 受影响 adapter refresh |
| Harness generated configs | aic render 产物 | 各 Harness 目录 | **无** | 不传输 | 不上传 | n/a | n/a | **是** | **是** | 否 | `aic discover→render→diff` |
| Skills installed copy | agent-tools 的派生 | `~/.dsh/skills` | 无 | `sync_skills.py --apply` | 无（repo 才是 SSOT） | n/a | n/a | **是** | **是** | 否 | 增量 sync 受影响 skill |
| Plugins installed copy | agent-tools `dsh/*` 派生 | `~/.dsh/profiles/web/plugins` | 无 | `sync_skills.py --plugins-destination` | 无 | n/a | n/a | **是** | **是** | 否 | 受影响插件 |
| MCP runtime | agent-tools `mcp/*` | 仓库就地 | 随 agent-tools | git | git | 否 | REVIEW | 部分 | 否 | 否 | cordis.patch 校验 |
| derived Memory index | 无（本地重建） | 本地索引 | 无 | 不传输 | 不上传 | n/a | n/a | **是** | **是** | 否 | memory 变化后 rebuild |
| projcache | 无 | `~/.dsh/storages` | 无 | 不传输 | 不上传 | n/a | n/a | **是** | **是** | 否 | 自动再生 |
| sessions | 无（Durability） | `~/.dsh/sessions` | 无 | 不走 git sync | 不走 git sync | n/a | n/a | **是** | 否 | **是** | 无 |
| traces / broker sqlite | 无（Durability） | `~/.agent-broker` | 无 | 不走 git sync | 不走 git sync | n/a | n/a | **是** | 否 | **是** | 无 |
| durability ledger | 本机 | backup root | 无 | 不传输 | 不上传 | n/a | n/a | **是** | 否 | **是** | 重新 discover |
| device inventory | aic discover 产物 | 本地 | 无 | 不传输 | 不上传 | n/a | n/a | **是** | **是** | 否 | `aic discover` |
| executable paths | 无 | 各设备 | 无 | 不传输 | 不上传 | n/a | n/a | **是** | 否 | 否 | 每设备 discover |
| secrets | 无（独立通道） | 环境变量/credentials | **绝不 git** | 不传输 | 不传输 | n/a | n/a | **是** | 否 | 部分（加密 vault） | 只检测引用 AVAILABLE/MISSING |

**未经上表分类的数据，不得进入自动同步。**

## 4. Auto Sync Direction Resolver

每个 repo/state source 同步前必须：

1. `git fetch origin`（先 fetch 再决策，§21 防数据丢失）
2. local HEAD / remote HEAD / merge-base
3. ahead / behind 计数（ancestry，不是时间）
4. working tree dirty 检查
5. ownership 检查（本矩阵）
6. privacy/security policy 检查
7. Memory 用 record/revision identity 判断（MemoryProvider 契约）

**禁止**用 mtime、“哪个文件更新”、last-write-wins、timestamp 覆盖做方向判定。

统一状态（§6）：

```text
IN_SYNC / REMOTE_AHEAD / LOCAL_AHEAD / LOCAL_DIRTY / DIVERGED / CONFLICT
BLOCKED_AUTH / BLOCKED_PRIVACY / OPTIONAL_NOT_INSTALLED / UNKNOWN
```

方向规则（§7）：

| 状态 | 动作 |
|---|---|
| IN_SYNC | NO ACTION |
| REMOTE_AHEAD + clean | FAST-FORWARD PULL → validate → 受影响 derived rebuild → 受影响 runtime refresh → post-check |
| REMOTE_AHEAD + dirty | UNTOUCHED（报告，不动） |
| LOCAL_AHEAD | 已 commit + owner 正确 + validation PASS + privacy PASS + 非 device-local → PUSH；否则 REVIEW |
| LOCAL_DIRTY | UNTOUCHED；禁止自动 add/commit/stash/reset/checkout/overwrite |
| DIVERGED | REVIEW_REQUIRED；禁止自动 merge infra canonical / rebase / force push；Memory 除外（见下） |

## 5. agent-tools 策略（最保守，§8）

- remote ahead + clean → FF pull → `validate_repo --strict` + 相关 tests + 受影响 runtime refresh
- local ahead → commit 已存在 + 门禁通过 + remote 无新历史 → PUSH（public：先 privacy scan）
- dirty → 不碰
- diverged → REVIEW，不得自动合并 Control Plane / registry / Skills infra code

## 6. personal-ai-state 分两类（§9）

**A. Curated State**（identity/preferences/goals/overlays/gateways/sync metadata）：
remote ahead + clean → PULL；local committed ahead → PUSH_CANDIDATE；双端修改 → CONFLICT_REVIEW。禁止 last-write-wins，禁止自动语义融合。

**B. Dynamic Memory**：复用 MemoryProvider 冻结契约（`scripts/memory/provider.py` 的 `export` / `import_bundle`）：

- 两设备不同 record → AUTO MERGE（`added`）
- 两设备不同 immutable revision → AUTO MERGE（`merged_revisions`，保留全部）
- 同 record concurrent revisions → 保留双方 revision，标记 `conflict: concurrent-revisions`，不得覆盖/丢弃/正文自动融合，后续 supersede/consolidation 消解
- 同 ID + 不可变元数据（record.yaml）不一致 → CONFLICT，不得自动修正

**git 只是 transport/versioning**（§11）：diverged 时若双端改动路径不相交且全在 `memory/records/`，允许确定性 `git merge`（disjoint 文件添加），随后用 provider 契约做语义校验与冲突标记；出现 curated 重叠或同 record.yaml 冲突立即 `merge --abort` 转 REVIEW。不写第二套 memory_git_merge.py。

Memory 变化后（§12）：只做受影响 derived index rebuild，验证 search/scope isolation/provenance/project boundaries；不重建无关 Harness 配置。

## 7. Project Repository Sync（§13）

- remote ahead + clean → FF pull
- local committed ahead + remote unchanged + privacy audit PASS + 项目 policy 允许 → PUSH
- dirty → 不碰；diverged → REVIEW_REQUIRED
- public 仓库 push 前扫描：secret / private infra names / personal overlay / machine-local path / private memory / credential / hidden provider → 命中即 BLOCKED_PRIVACY
- 当前 `NOVEL_REPO_DURABILITY = BLOCKED_PRIVACY` 继续保持，不得为 sync 自动绕过
- 禁止一般化 `git add . && git commit && git push`

Active discovery（§14）：goals.md `## active` + sync/this-device.yaml repos → ACTIVE 完整检查；PAUSED 只查 remote state、可选 pull；ARCHIVED 不主动 clone。

## 8. Runtime / Device-local / Derived（§15-§17）

- Generated Harness config 不是跨设备 canonical：`canonical changed → aic discover → device inventory → render → diff → (apply) → diff`；禁止 Device A settings 直接覆盖 Device B
- 永不同步：executable absolute path / USERPROFILE / HOME / Desktop / GPU / WSL / Docker / 本地推理 runtime / 端口 / gateway 进程路径 / device inventory / 本地已装 Harness / 本地备份盘——每设备 `aic discover` 重新生成
- 不同步派生态：Memory FTS/index、vector cache、projcache、node_modules、npx cache、generated config cache——本地 rebuild
- `aic apply <target>`（Runtime Closure 已实现）：前置 validate→discover→render→diff→ownership classify；只写 adapter 声明的 GENERATED 字段（DSH settings 限 `llm-pi-ai`/`agent-default-model` 段；cordis 走外科手术式单值改写，`!!js` opaque 不碰）；snapshot + atomic write + post-diff（post 仍有 generated drift → 自动恢复 before snapshot，`FAIL_ROLLED_BACK`）；OVERLAY/UNKNOWN/SECRET/未登记 opaque → `REVIEW_REQUIRED` 不写；secret/managed-block 缺失只警告不阻塞无关 generated；未安装 Harness = `OPTIONAL_NOT_INSTALLED`

## 9. Raw History 与 Secrets（§18/§19）

- sessions / raw traces / broker sqlite 不做日常 git 双向同步，走 Durability/Archive；稳定经验进入 Dynamic Memory / Project State；不把 250MB+ sessions 塞进 git lifecycle
- secrets 绝不 git sync：`.credentials.yaml` / API keys / tokens / passwords / backup decrypt key
- SYNC 只检测引用：`AVAILABLE / MISSING / NOT_REQUIRED`；optional Provider/Harness 的缺失 secret 不阻塞整体 Sync

## 10. AUTO_SYNC 算法（§20）

```text
Preflight → Detect repositories/Harnesses → aic discover → Fetch canonical remotes
→ Classify each source → Determine safe direction → safe PULL → safe Memory merge
→ safe PUSH →  unsafe conflicts 停下报告 → Determine affected targets（依赖映射）
→ Render affected → Apply safe generated drift（aic apply）→ Post-apply diff
→ Runtime smoke → Rebuild affected derived → Sync checkpoint → Human-facing summary
```

只有真正受 canonical change 影响的 Harness 才 apply；memory-only 变化绝不触发 Harness apply；skills/* 变化只触发 Skill sync。

顺序纪律（§21）：先 fetch all → classify all → build action plan → execute safe actions；禁止先 push 再发现 remote 有变化。

安全自动范围（§22）：clean FF pull / validated FF push / distinct immutable memory addition / distinct immutable revision merge / generated runtime render / derived rebuild / idempotent schedule registration。其他一律 REVIEW。**AUTO_SYNC = 自动处理所有确定安全的问题，不是所有问题。**

## 11. Incremental 与 Dependency Mapping（§23/§24）

禁止每次全量：重装全部 Skills / render 所有 Harness / rebuild 全部 index / 重跑所有 expensive checks。按 git diff / changed files / changed scopes 只处理受影响组件。映射表（简单代码映射，非 graph database）：

```text
skills/* changed            → sync 受影响 skills
dsh/* changed               → refresh DSH plugin
registry/models.yaml        → render 受影响 Harness model 段
routing-policy changed      → refresh routing consumers
memory/* changed            → Memory derived index refresh
preferences changed         → Context Builder 输入刷新；AIC static policy projection diff/apply
project /.ai/state changed  → project context refresh
gateway mapping changed     → 受影响 adapter refresh
governance script changed   → governance task validation
```

## 12. Machine-local Checkpoint（§25）

`~/.dsh/.personal-ai-sync/status.json`：device_id / last_successful_sync / per_repo_last_seen_commit / last_memory_merge / runtime_render_revision / last_result。不进 canonical、不进 personal-ai-state git、可删、删除后可重新 discover。

## 13. Scheduled CHECK-only（§27）

`PersonalAI-Sync-Check`（Windows Task Scheduler，登录后或每日一次）：只 fetch + classify + report；禁止自动 pull/push/merge canonical。不给 aic 加 scheduler。

## 14. RESTORE（§28-§32）

`agent-tools` 或 `personal-ai-state` missing → RESTORE = empty local 的特殊 SYNC，复用 PULL + bootstrap：

```text
Preflight → Auth check → Clone agent-tools → Clone personal-ai-state
→ aic discover → Discover active projects → Clone eligible projects
→ Validate canonical → Restore Skills/Plugins/MCP → Render installed Harnesses
→ Rebuild Memory index → Verify DSH session history（backup/live 计数 + 已知锚点 + schema 探针；非 PASS/NOT_APPLICABLE 则总体不得 PASS）
→ Register Governance/Durability/Sync-check tasks (idempotent)
→ Verify CONTINUOUS_CAPABILITY_ADOPTION generated block on installed Harnesses
→ Physical smoke test → Status
```

- Secrets（§29）：先自动完成所有不需要 secret 的部分；只列当前 active route/Harness 真正缺失的 secret；不因 optional Harness 未登录阻塞核心恢复
- Multi-Harness（§30）：按实际 installed 的 render→diff→apply→diff；未安装 = OPTIONAL_NOT_INSTALLED，不自动装齐
- Governance（§31）：复用 `scripts/governance/register_governance_tasks.ps1` 注册/回读 scheduled tasks；必须 idempotent，重复 bootstrap 不产生 duplicate
- Durability（§32）：不硬复制旧 `D:\ai-backup`，重新 discover disks/target/failure domains；无等价 target → DURABILITY=DEGRADED 但 PERSONAL_AI_CANONICAL_RESTORE 仍可 PASS；BACKUP_KEY_CUSTODY 继续 WAITING_FOR_CUSTODY_ROOT

## 15. 输出契约（§33-§35）

默认短输出（展开才下钻）：

```text
Personal AI Sync

agent-tools          IN_SYNC / PULLED / PUSHED
personal-ai-state    IN_SYNC / PULLED / PUSHED / MERGED
projects             <简要汇总>
memory               <新增/合并/冲突数量>
runtime              NO DRIFT / refreshed
secrets              READY / PARTIAL
external blockers    known external blocker, unchanged

Result: PASS / REVIEW / BLOCKED
```

RESTORE 模式追加 `dsh-session-history` plane 行（backup / live / missing 计数）。状态语义：`PASS`（备份与 live 会话匹配、已知锚点与 schema 探针通过）/ `NOT_APPLICABLE`（该设备从未配置备份，fresh restore 不阻塞）/ `PARTIAL` / `FAIL`。**历史缺失时总体 `Result` 不得写完整 PASS**（2026-09-01 事故：配置恢复 PASS 而会话历史丢失，根因 = restore 未把 DSH conversation history 纳入契约）。

冲突时先把确定安全的部分做完，只把真正冲突留下（§35）；不问“要不要同步其他没冲突的”。Known blockers 状态未变只显示 `known external blocker, unchanged`（§34）。

## 16. Failure Recovery / Idempotency（§36）

每个动作幂等可重跑：fetch / FF pull / immutable memory import 去重 / render / derived rebuild / scheduled task registration。禁止依赖“上一步应该已成功”的不可验证状态。中途失败后重跑必须安全收敛。

## 17. Personalization Restore（§40）

新设备恢复后运行一个低风险普通任务，验证 Personal State + Memory + Preference Selection inputs 已存在且 Personalization context 正常工作；不得用旧聊天 history 作为恢复依据。

## 18. Post-sync Validation（§41）

最小充分验证随 changed components 走；完整验收至少：`aic validate = VALID` + 所有已安装 Harness `aic diff = NO DRIFT` + Memory scope isolation + Context Package smoke + governance status + personal status。
