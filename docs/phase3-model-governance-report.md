# PHASE3_MODEL_GOVERNANCE_REPORT（Track A：phase3-model-governance）

1. **Scope**：Migration #3 单写区（registry/models*|providers*|gateways*|routing-policy*、model identity trace schema、aic discovery/governance 部分）。未触碰 Track B 区域（memory/context builder/project state）。
2. **Changed files**：`registry/gateways.yaml`（cc-switch alias_map 显式化）、`scripts/aic/aic.py`（+`propose-admissions`）
3. **New files**：`registry/model-identity-trace.schema.yaml`、`scripts/trace_identity.py`、`tests/test_trace_identity.py`、本报告
4. **Deleted files**：无
5. **Architecture contract mapping**：
   - Discovered/Reachable/Health-checked/Admitted/Routing-enabled 管线：discover（生成 inventory）→ propose-admissions（提案，永不自动准入）→ 人工编辑 models.yaml（准入）→ policy 引用（routing-enabled）。Health-checked 定义为运行时 canary 证据，**明确不属于 aic**（无 LLM 调用红线）。
   - 身份链：冻结修正 B 的四段（requested→gateway_resolved→provider_reported→identity_assessment），无 verified_model/verification_confidence。
6. **Tests**：`tests/test_trace_identity.py` 5 例（consistent/suspicious/alias resolution/unknown/禁用字段断言）+ 既有 test_aic 8 例，全绿。
7. **Physical acceptance evidence**：
   - 管线分层实证：`aic propose-admissions` → admitted=9 / discovered=25 / pending=19；routing-enabled=2（gemini-3.7-flash-high、gpt-5.6-luna-max）。三者互不相等 ✅
   - 真实任务 trace（本会话 2979 事件投影）：requested=kimi-coding/k3-256k；gateway_resolved=同（直连无网关）；provider_reported=kimi-coding/k3-256k（108 条 assistant 消息一致）；identity_assessment.status=**consistent**；fallback=none-observed；policy_rule=main_default ✅
   - alias 显式化：cc-switch alias_map（claude-haiku-4-5/opus-4-8/sonnet-4-6→gpt-5.6-luna；fable-5→gpt-5.6-sol；subagent→gpt-5.6-luna）从 ~/.claude/settings.json env 提取入 canonical ✅
   - 门禁：validate_repo PASS / quality 20/20 / evals 20/20 / publish PASS / unittest OK / diff-check clean ✅
8. **Reality conflicts**：无新增（!!js opaque 已登记 AIC_OPAQUE_PATH_VISIBILITY，不属于本轨）。
9. **Technical debt**：① Health-checked 档只有契约无数据（需运行时 canary，Governance Automation 阶段）；② trace 投影目前覆盖 DSH 会话日志；Claude/Codex 侧投影属 Migration #5 adapter；③ broker routing_preferences 为描述性记录，生成化属 #5。
10. **Git status**：worktree 干净（提交后）；分支 phase3-model-governance，未 merge。
11. **Commit SHA**：见分支 HEAD（`git log -1 phase3-model-governance`）
12. **Verdict**：**PASS**
