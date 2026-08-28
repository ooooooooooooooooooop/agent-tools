---
name: personal-ai-operations-review
description: 对已进入 OPERATIONS MODE 的 Personal AI / DSH 基础设施做只读运维复查：聚合 governance、durability、aic drift、proposal、memory/model/routing 状态，区分 NO ACTION、REVIEW、ACTION REQUIRED 与 EXTERNAL BLOCKER；用于日常健康检查、异常复盘、升级前检查或用户询问“系统现在健康吗/需要管什么”时。默认不修改 canonical，不为变绿而静默修复。
---

# Personal AI 运维复查

## 适用范围

用于已经完成建设、进入 `OPERATIONS MODE` 的 Personal AI Infrastructure。目标是回答“系统现在是否正常、哪些问题需要人处理、哪些只是已知外部阻塞”。

优先复用现有 Control Plane、Governance、Durability、Memory、Model/Trace 和 Harness Adapter 的真实输出；不要重新实现底层检查。

## 不适用

- 不用于设计新的 Architecture Phase、重构 Control Plane、替换 MemoryProvider 或新增基础设施。
- 不用于直接批准 model admission、修改 routing/default model、删除 provider/plugin/memory、清 Git 历史或处理其他 canonical mutation。
- 不把 `BLOCKED`/`DEGRADED` 自动视为系统失败；先判断它是否是治理系统正确暴露的真实约束。
- 用户明确要求实施某个 change 时，先转入既有 `Need → Impact → Proposal → Implementation → Verification → Regression` 流程，不在本 Skill 内静默执行。

## 工作流程

1. **定位真实入口**
   - 找到 `agent-tools` 仓库根目录和当前分支/commit。
   - 优先读取 `docs/personal-ai-operations.md`、当前 Architecture/operations handoff 与仓库已有脚本说明。
   - 命令或路径不存在时标 `UNKNOWN`，先搜索仓库实际入口；不要凭旧报告猜路径。

2. **读取 Human-facing status**
   - 优先运行现有简版入口，例如：

     ```text
     python scripts/governance/personal_status.py
     ```

   - 再运行详细 governance status，例如：

     ```text
     python scripts/governance/gov_status.py
     ```

   - 记录每个域的 `HEALTHY / DEGRADED / BLOCKED / UNKNOWN` 及原因，不只记录总状态。

3. **验证 Control Plane 与 Harness drift**
   - 运行已有 `aic validate`。
   - 对当前已登记 Harness 逐个执行 `aic diff <target>`；至少覆盖 dsh/codex/claude/gemini/switchboard（若 registry 现状不同，以实际登记为准）。
   - 正常目标应为 `VALID / NO DRIFT`。发现 drift 时只报告 expected/actual/owner，不自动 apply。

4. **检查 Durability**
   - 读取现有 durability run ledger、RPO checker、计划任务最近状态与 latest verified backup。
   - 区分“计划任务成功”与“最近一次已验证备份仍在 RPO 内”；以后者为准。
   - 检查 repo durability 风险：unpushed commit、remote 不可达、privacy blocker。
   - `BACKUP_KEY_CUSTODY` 等已知外部条件必须保持原状态，不能为了总览变绿而改写。

5. **检查 Governance / Proposals**
   - 读取 outstanding proposals 与高严重度 findings。
   - 对 model admission、dead config、memory review、duplicate rule 等 proposal 判断是否已有足够新证据值得人工裁决。
   - `open proposal` 本身不等于故障；只有需要用户决策或风险升级时标 `REVIEW`/`ACTION REQUIRED`。

6. **检查 Memory / Model / Routing 信号**
   - Memory：scope pollution、schema/provenance、stale/review candidate、conflict/supersede 异常。
   - Model：provider reachability、request success、fallback 增多、identity `suspicious`、ADMISSION_GAP 新证据。
   - Routing：非法引用、fallback chain 异常、overlay 绕过治理。
   - 只引用现有治理证据；没有证据时写 `UNKNOWN`。

7. **分类行动**
   - `NO ACTION`：系统正常，或仅存在已知且无需当前处理的 blocker/proposal。
   - `REVIEW`：需要人工看证据并作裁决，但没有即时运行风险。
   - `ACTION REQUIRED`：出现新的真实故障、RPO breach、drift、secret/privacy 风险、scope pollution 等，应建立 Change Proposal。
   - `EXTERNAL BLOCKER`：需要当前系统外的条件（如 custody root、硬件、外部授权）才能关闭。

8. **需要修改时只产 Change Proposal**
   - 使用：`Need → Impact → Proposal → Implementation → Verification → Regression`。
   - 明确 affected SSOT、风险、验收、回滚与是否阻塞日常使用。
   - 除非用户在当前任务中明确要求实施，否则停止在 Proposal。

## 事实纪律

- 区分 `FACT / INFERENCE / USER-PROVIDED / UNKNOWN`。
- 以本次运行的命令、ledger、registry、trace、git 状态为事实；旧报告只作线索。
- `NO DRIFT` 只证明 canonical 与 live 一致，不证明 live runtime 一定有效；存在运行时健康信号时应交叉验证。
- 不为追求 `HEALTHY` 总状态隐藏 `BLOCKED_PRIVACY`、`WAITING_FOR_CUSTODY_ROOT`、`ADMISSION_GAP` 等真实状态。

## 输出契约

输出一个紧凑的 `PERSONAL_AI_OPERATIONS_REVIEW`：

```text
Overall: HEALTHY | DEGRADED | BLOCKED | UNKNOWN
Action: NO ACTION | REVIEW | ACTION REQUIRED | EXTERNAL BLOCKER

Infrastructure: <status + reason>
Control Plane: <status + reason>
Harnesses: <status + drift summary>
Models/Routing: <status + reason>
Memory/Projects: <status + reason>
Durability: <status + RPO/restore reason>
Governance: <status + proposal summary>
External Blockers: <list or none>

New findings:
- ...

Recommended next action:
- ...
```

如果 `Action = NO ACTION`，明确写“无需修改，继续正常使用”，然后停止，不主动提出新的架构优化。

如果 `Action = ACTION REQUIRED`，附一个最小 Change Proposal，但不要自行实施。

## 安全边界

- 默认只读；不调用 `aic apply`、不写 canonical、不 push、不 admit model、不删除 memory/config。
- 不输出 secret、token、decrypt key 或完整凭据。
- 不通过自动修复把真实异常变成绿色。
- 不新增 daemon、数据库、Harness 或外部通知服务。

## 验证

Skill 包自身按仓库契约验证：

```text
python scripts/validate_repo.py --strict
python skills/skill-quality-gate/scripts/quality_report.py --root . --strict
python -m unittest discover -s tests -v
```

实际运维复查至少要有：`personal_status/gov_status` 证据、`aic validate`、已登记 Harness 的 diff 状态、durability/RPO 证据、outstanding proposal 摘要和 `git status`。