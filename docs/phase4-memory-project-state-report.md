# PHASE4_MEMORY_PROJECT_STATE_REPORT（Track B：phase4-memory-project-state）

1. **Scope**：Migration #4 单写区——memory canonical schema/retrieval/lifecycle/ownership、file MemoryProvider、context builder、project state（novel-main）、personal-ai-state 初始化。未触碰 Track A 区域（models/providers/gateways/routing/trace/aic）。
2. **Changed files**：无既有文件改动（纯新增）。
3. **New files**：`scripts/memory/provider.py`（FileMemoryProvider）、`scripts/memory/context_builder.py`、`tests/test_memory_provider.py`（9 例）、本报告。仓外（Track B 拥有）：`personal-ai-state/`（state/identity+preferences+goals、memory/records ×6、projects/novel-main/overlay、README；git 初始化并推送私有远端）、`novel-main/.ai/state/state.md`（novel 仓库本地提交 aa8c39b，未推送）。
4. **Deleted files**：无。
5. **Architecture contract mapping**：
   - 冻结修正 A 落地：`record.yaml`（immutable 元数据）+ `revisions/<rev>.yaml`（独立 immutable 文件，append-only）+ `state.yaml`（mutable 生命周期，跨设备 (at,device) 合并 + 冲突标记）；**无共享 revisions[] 数组**。
   - importance 永不落盘：检索分值 = retention_prior×0.4 + recency×0.2 + 词项重叠×0.4，查询时派生（测试断言持久化文件无 importance/derived_score）。
   - forget 双模：tombstone（默认，保留文件）；hard 仅限 sensitivity=sensitive（守卫测试覆盖）。
   - export/import/merge：bundle/v1；revision 并集合并；lifecycle 分歧取 (at,device) 大者并打 conflict 标记。
   - Context Builder：scope 链 project→personal/global；预算截断 + omitted 计数；每条带 provenance。
   - Consolidation：显式 consolidate（episodic ids → semantic，supersede 链）；sweep 只报告 review 过期项。
6. **Tests**：9 单例全绿（write/read、revision 独立文件、dedupe、supersede 链、forget 守卫、scope 隔离、merge、无 importance 落盘、package scoping/provenance）。
7. **Physical acceptance evidence**：
   - 真实迁移：broker topics + 仓库事实 → 6 条记忆（novel-main ×4、personal ×1、skills ×1），全部经 provider 写入、含 provenance。
   - 检索 scoping 实证：novel-main 任务包命中 project:novel-main ×4 + personal ×1；**project:skills 零污染**；provenance 全展示 ✅
   - 项目恢复测试实证（全新子代理，仅允许读 .ai/state/state.md + package）：goal/architecture/decisions/constraints/completed/unresolved/next 七项全部正确回答；memories provenance 与 scope 清单正确 ✅
   - personal-ai-state 私有远端 `ooooooooooooooooooop/personal-ai-state` 创建并推送 ✅
   - 门禁：validate_repo PASS / evals 20/20 / publish PASS / unittest OK / diff-check clean ✅
8. **Reality conflicts**：DSH harness 当前从 AGENTS.md 注入偏好（非 state/preferences.md 读取）——运行时接入属 Harness Integration（#6）；本轨只建立 canonical 层与工具，未改 harness 行为（遵守冻结边界）。
9. **Technical debt**：① 派生索引（检索加速层）未建——当前全量扫描，小规模可用，索引属后续；② consolidation 为显式操作，自动策略（token 化做硬事实、prompt 化做 working state、可证据化进 durable memory 的自动判定）待 Governance Automation；③ novel-main 仓库提交 aa8c39b 未推送远端（避免未授权推送，待用户确认）。
10. **Git status**：worktree 干净（提交后）；分支 phase4-memory-project-state，未 merge。
11. **Commit SHA**：见分支 HEAD（`git log -1 phase4-memory-project-state`）；novel-main 侧 aa8c39b；personal-ai-state 侧 e01957d。
12. **Verdict**：**PASS**
