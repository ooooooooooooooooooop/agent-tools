# PHASE5_MULTI_HARNESS_REPORT（Migration #5 · 2026-08-28）

## 1. Backup architecture frozen state
BACKUP_GEN2_CONFIDENTIALITY=PASS；CIPHERTEXT_DURABILITY=PASS；FULL_DISASTER_RECOVERABILITY=PARTIAL；GEN3_ARCHITECTURE=READY；GEN3_IMPLEMENTATION=DEFERRED；KEY_CUSTODY=WAITING_FOR_CUSTODY_ROOT（不参与本阶段 verdict）。

## 2. Harness reality inventory（2026-08-28 重新只读实测）
- **DSH**：settings.yaml=CANONICAL-derived；AGENTS.md=用户全局偏好（2931B，独立家族）；sessions/goals/telemetry=RUNTIME；.credentials.yaml=SECRET。
- **Codex**（cli 0.149.0-alpha.4.1，bin 110b3d66）：config.toml model/mcp=GENERATED 面；plugins/desktop/windows/marketplaces/projects/notify=OVERLAY；auth.json=SECRET；*.sqlite/sessions=RUNTIME；AGENTS.md=GENERATED（switchboard cost-routing managed block，sha256 标记）。发现：broker codex_path 漂移（登记 SWITCHBOARD_CODEX_PATH_STALE）。
- **Claude Code**（2.1.239）：settings.json env alias=GENERATED（唯一 SSOT=personal-ai-state gateways.yaml）；hooks=OVERLAY（switchboard 安装物）；config.json primaryApiKey="any"=占位 OVERLAY；CLAUDE.md=GENERATED（同 managed block）；projects/sessions/history=RUNTIME。
- **Gemini**：settings.json mcpServers=GENERATED（capabilities canonical）；auth=OVERLAY；GEMINI.md=GENERATED（同 block）；.env=SECRET 容器（当前空）。
- **agent-switchboard**：routing_preferences=GENERATED（与 canonical 一致）；cli_backends/providers 的 base_url=GENERATED；models 清单=OVERLAY（ADMISSION_GAP 登记）；state.sqlite/topics/supervisors=RUNTIME。

## 3. Adapter ownership matrix
aic 单写：registry/*、harnesses/*.yaml、aic core。switchboard 拥有：harness 指令 managed block、自身 hooks。harness 专有 API 零跨 adapter 依赖（grep 实证）。

## 4. Static/runtime instruction audit = PASS
两家族均只含 hard constraints/execution policy/长期偏好：DSH 用户偏好（省 token 等五模块）；codex/claude/gemini 的 switchboard cost-routing 块。**无 active goals / project state / episodic memory / decisions 混入 static**。无迁移需要。

## 5-8. Adapter 结果
- Codex：`aic render/diff codex` = NO DRIFT（model + MCP 能力集 superset 校验）
- Claude：`aic render/diff claude` = NO DRIFT（12 个 generated 字段全部由私有 gateways canonical 投影）
- Gemini：`aic render/diff gemini` = NO DRIFT（mcpServers ⊇ canonical 能力集）
- switchboard：`aic render/diff switchboard` = NO DRIFT（routing_preferences + base_url 精确一致）

## 9. Runtime Context injection
DSH=会话首帧注入；Codex/Gemini=session-start injection（无原生动态 hook）；Claude=原生 UserPromptSubmit hook（switchboard 已装）；switchboard=prompt frame。aic 只渲染 hook 配置指针，不调 MemoryProvider/不构包（边界审计实证）。

## 10. DSH-off Harness Independence = PASS
Codex CLI 独立进程（--sandbox workspace-write，cwd=Temp）：仅凭 novel-main/.ai/state/state.md + Context Package（3428B）正确回答七项 + provenance/scope，并产出 ANSWER.md 实物。DSH 运行时不参与被测路径（编排从 DSH 会话发起已透明声明；Codex 消费面为零 DSH 制品）。tokens=27,005。

## 11. Cross-Harness Continuity = PASS
Harness A（DSH）写 memory（marker=BLUEFALCON-7734，record aa4b5f010286）→ 重建 package → Harness B（Codex 全新会话）无人工解释恢复：marker、写入方、时间、next action 全部正确。tokens=11,490。

## 12. Scope Isolation = PASS
package-b scopes = project:novel-main ×5 + personal ×1；project:skills 记录零出现。

## 13. Per-Harness drift red-team（全部真实字段、恢复后 NO DRIFT）
- codex：`model` gpt-5.6-luna→-DRIFT → diff 报 file/field/expected/actual ✅
- claude：`env.ANTHROPIC_DEFAULT_OPUS_MODEL_NAME` → 精确报告 ✅
- gemini：删除 fetch MCP → superset 校验报告缺失 ✅
- switchboard：`routing_preferences.fast` cpa→codex_cli → 检出（显示列字典键，已注记）✅

## 14. Control Plane boundary audit = PASS
六动词（discover/render/diff/validate/apply/bootstrap 桩）无第七动词（propose-admissions 为 discover 旗标）；无 MemoryProvider import / LLM 调用 / 编排 / 路由执行 / daemon（urllib 仅 discover 的网关 /v1/models 探活，非 LLM）。

## 15. Secrets audit = PASS
agent-tools / personal-ai-state / novel-main 三仓 git grep 全模式（sk-/ghp-/AKIA/AIza/PEM/api_key=长值/password=）零命中；仅存 env 名/占位符。

## 16. Full regression = PASS
repo unittest OK / MCP regression OK / validate_repo PASS / quality 20/20 / evals 20/20 / publish PASS / diff-check clean。

## 17-18. All-target diff & changed files
dsh/codex/claude/gemini/switchboard 全 NO DRIFT；DSH live 行为未变。变更：`registry/capabilities.yaml`（新）、`registry/harnesses/{codex,claude,gemini,switchboard}.yaml`（新）、`scripts/aic/aic.py`（+通用 adapter 引擎）、`registry/TECH_DEBT.md`（+3 条）、本报告。

## 19. Architecture reality conflicts
1 项记录（不阻塞、不需改架构）：Expected: switchboard models ⊆ admitted catalog；Observed: gpt-5.6-luna/gemini-3.7-flash/fable 目标/sol 在用未准入；Evidence: models.yaml vs config.json；Impact: canonical 校验不能约束暴露清单；Local Resolution: 清单归 OVERLAY + ADMISSION_GAP 技术债；Architecture change required: NO。

## 20. Technical debt
AIC_OPAQUE_PATH_VISIBILITY；ADMISSION_GAP；SWITCHBOARD_CODEX_PATH_STALE；BACKUP_KEY_CUSTODY（READY/DEFERRED/WAITING）；另：Health-checked 档、检索派生索引、consolidation 自动策略（沿用前报）。

## 21. Commit/tag/push
见 §22 前执行记录：branch phase5-multiharness → merge main → tag personal-ai-phase5-multiharness-20260828。

## 22. Final Verdict：**PASS**
