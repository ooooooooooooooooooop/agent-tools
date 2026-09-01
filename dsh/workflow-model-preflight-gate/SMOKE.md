# SMOKE TEST — DSH 升级 / profile rebuild 后验证

每次升级 DSH、重装或重建 profile 之后，按顺序执行以下冒烟验证。
目标：确认 workflow-model-preflight-gate 仍在运行时强制路径上。全部通过才算修复存活。

## 0. 静态校验（离线，先做）

```powershell
node C:\Desktop\skills\dsh\workflow-model-preflight-gate\verify-deployment.mjs
# 期望输出 VERIFY_DEPLOYMENT: PASS（部署一致性 / patch 登记 / 模块可加载 / 判定逻辑 6 项）
```

若失败：按 `README.md` 重新部署（复制插件 + 合并 cordis.patch.yml 片段），重启 DSH 后重跑。

## 1. 插件已加载（live）

重启 DSH 后启动成功即加载成功（cordis-plugin-loader fail-loud）。
可选佐证：发起步骤 2 的阻断请求，门禁反馈文本本身即为已加载证明。

## 2. 未准入字面量模型在 agent 启动前被阻断（live）

在任意 session 请求一个 workflow，脚本含：
`agent('probe', { provider: 'cpa', model: 'gpt-5.6-luna' })`

期望：

- workflow 工具直接返回 `[workflow-model-preflight-gate] ... GATEWAY_ADMITTED_ALIAS_UPGRADE ...`
  错误（不是 NULL 结果）；
- **invalid_agents_started = 0**：该调用在会话日志中无 `tool-workflow/run-start` /
  `tool-workflow/agent-start` 事件，`~/.dsh/sessions/` 无新子会话目录。

## 3. 合法 multi-agent workflow 正常完成（live）

请求一个 ≥3 agent 的 workflow，全部使用已准入模型
（`gemini-3.7-flash-high` / `gpt-5.6-luna-max` / `claude-sonnet-4-6`）。

期望：全部 completed，`null_results = 0`，审计文件出现对应 `allow` 行。

## 4. 自愈闭环（live）

步骤 2 被拒后，agent 应按反馈把模型改写为 `gpt-5.6-luna-max` 重发同一 workflow 并完成。
**全程不得要求用户修改 JavaScript、模型字符串或任何配置。**

## 5. 审计落盘

```powershell
Get-Content "$env:USERPROFILE\.dsh\workflow-preflight-audit.jsonl" -Tail 5
```

期望：block 记录含 `requested_model / resolved_model / provider / reason_code /
mapping_type / quality_tier_impact / policy_source` 七个字段。

## 冻结边界（smoke 不覆盖、也不许为其改代码）

1. 动态拼接 model 字符串不在文本提取门禁范围内；
2. 未指定 model（继承父模型）的 workflow 按设计放行；
3. luna ↔ luna-max 档位关系保持 UNKNOWN。
