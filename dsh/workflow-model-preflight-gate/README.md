# workflow-model-preflight-gate

DSH 用户级 Cordis 插件：在 `workflow` 工具 **dispatch 之前**（`ctx.tools.guard()` 单调守卫层）强制执行模型准入检查，关闭「`agent(..., {model})` 未准入模型透传 → N 个子代理全部启动后返回 null」的已知绕过路径。

## 背景（事故机理）

- `dsh-tool-workflow` → `ctx.workflowEngine.start()` → `dsh-workflow-worker-thread` 的 `startChild()` 把 `request.model` **原样**传入 `subagents.start(... agentOptions.model)`，`worker.cjs` 仅检查 `typeof model === 'string'`。
- 未在 `~/.dsh/settings.yaml` 准入的模型在子代理**首次 LLM 请求时**才被 `dsh-llm-pi-ai` 以 `UNKNOWN_MODEL` 拒绝，于是每个子代理都"启动后失败"，`agent()` 按上游 ABI 返回 `null`。
- 本插件不改上游：`ctx.tools.guard()` 回调返回拒绝原因字符串即在 dispatch 前阻断，**零子代理启动**。
- 关键实现细节（首轮教训）：守卫机制用 `ctx.tools.guard()`（与 dsh-pwsh-host-guard 同，已在本机 live 验证可阻断）；回调读 `execution.arguments`（不是 `exec.args`）；必须 `export const inject = ['tools']`。`ctx.on('tools/pre-execute')` 在该部署形态下不触达 profile 插件。

## 判定（fail-closed）

| 情况 | 决策 |
|---|---|
| 模型已在该 provider 准入 | allow |
| 未准入 + 显式 fallback 规则且目标已准入 | block + 反馈解析后模型（调用方改写重发，即自愈闭环） |
| 未准入 + 无规则 | block，`UNADMITTED_OR_UNKNOWN_MODEL` |
| 脚本无 model 字面量（继承父模型）/ 门禁内部异常 | allow（门禁不对看不见的东西发明阻断） |

## 部署

1. `workflow-model-preflight-gate.mjs` → `~/.dsh/profiles/<profile>/plugins/`
2. 合并本包 `cordis.patch.yml` 片段进 profile 的 `cordis.patch.yml`
3. 重启 DSH（host 插件在进程启动时加载，无热重载）
4. 升级 / profile rebuild 后验证：先 `node verify-deployment.mjs`（离线静态校验），再按 `SMOKE.md` 执行 live 冒烟

## 审计

每次判定追加一行 JSON 到 `~/.dsh/workflow-preflight-audit.jsonl`（可用 `config.auditPath` 覆盖）：`decision`、`requested_model`、`resolved_model`、`provider`、`reason_code`、`mapping_type`、`quality_tier_impact`、`policy_source`、`source`。

## 维护注意

- `DEFAULT_FALLBACKS` 是 `skills/subagent-execution-governance/scripts/workflow_preflight_router.py` 中 `EXPLICIT_FALLBACK_RULES` 的镜像，修改时两处同步（插件环境无 yaml 依赖，不能直接读 Python 表）。
- 准入目录的 SSOT 始终是运行时的 `~/.dsh/settings.yaml`，插件每次调用实时读取，无缓存、无进程内存状态依赖。
