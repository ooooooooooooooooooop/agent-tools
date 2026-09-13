# dsh-world-model

Personal AI 世界模型的**机制层**插件（非提示词）。theory V0.3.1 / schema 1.1。

插件是身体上的适配器器官——灵魂（W/V/U/L canonical 状态）harness 无关，
本插件只认 `canonicalDir` / `stateDir` / `bodyId` 三个接口；换 harness = 换适配器。

| 机制 | 实现 |
|---|---|
| `world_model` 工具 | activate/model/predict/observe/evaluate/update/probe/meta/value/input/persist/status/declassify → 真实落盘 |
| RAW_EVIDENCE 捕获 | `tools/result` 机械记录全部工具结果（L0，带 source/channel/tool provenance、event_seq、causal prev_event 链） |
| 硬门禁 | `tools.guard`：CORE/FULL 下 consequential mutation 无绑定预测不放行；已评估/superseded 预测不授权；不可逆模式需 `irreversible:true` |
| Briefing | 每会话注入 canonical 编译产物 `briefing.md`（七段、≤8KiB）；STALE 检测 |
| INPUT_SEMANTICS | `op:"input"`：EPISTEMIC_CLAIM→W证据 / NORMATIVE_DIRECTIVE→查 governance normative_authorities / AUTHORIZATION / DURABLE_VALUE_STATEMENT→value proposal / PREFERENCE→局部 |
| Body lease | 同一 entity_id 单 canonical writer；lease 不匹配 → LEASE_DENIED；lineage_head 异动 → FORK_DETECTED |
| 状态层 | L1 `~/.dsh/world-model/` 随时写；L2/L3 canonical 只收 `proposals/`（治理路径） |

配套工具（同目录）：

- `migrate_v031.py` — canonical schema 1.0→1.1 迁移/回滚/verify（不 silent drop）
- `canonical_compile.py` — 编译 `briefing.md`（GENERATED，不可手写）+ `runtime-state.json` 投影
- `lp_evaluator.py` — Learning Progress 只读评估（closed episodes → metric vector；无 canonical 写权）
- `bcc_check.py` + `simulate_body.mjs` — BCC-1 六协议测试（真实插件 vs python 参照体）

模式：`config.mode` 或 `DSH_WM_MODE` = `off|core|full|strict-off`。
OFF/CORE/FULL 是世界模型的**形式化深度**不是开关——世界模型永远在场。

部署：见 `cordis.patch.yml` 注释。回归：`python -m unittest tests.test_wm_v031 -v`。
