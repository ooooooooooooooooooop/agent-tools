---
name: personal-ai-operations-review
description: 对已进入 OPERATIONS MODE 的 Personal AI / DSH 基础设施做只读运维复查：聚合 governance、durability、aic drift、proposal、memory/model/routing 与 personalization 行为监控（纠正率/重复纠正/选择泄漏），区分 NO ACTION、REVIEW、ACTION REQUIRED 与 EXTERNAL BLOCKER；用于日常健康检查、异常复盘、升级前检查，或用户询问“系统现在健康吗/需要管什么/AI 最近是不是又变难用了/个性化有没有退化”时。默认不修改 canonical，不为变绿而静默修复。
---

# Personal AI 运维复查

## 适用范围

用于已经完成建设、进入 `OPERATIONS MODE` 的 Personal AI Infrastructure。目标是回答“系统现在是否正常、哪些问题需要人处理、哪些只是已知外部阻塞”。

优先复用现有 Control Plane、Governance、Durability、Memory、Model/Trace、Harness Adapter 与 Personalization Calibration 的真实输出；不要重新实现底层检查。

## 不适用

- 不用于设计新的 Architecture Phase、重构 Control Plane、替换 MemoryProvider 或新增基础设施。
- 不用于直接批准 model admission、修改 routing/default model/preference、删除 provider/plugin/memory、清 Git 历史或处理其他 canonical mutation。
- 不把 `BLOCKED`/`DEGRADED` 自动视为系统失败；先判断它是否是治理系统正确暴露的真实约束。
- 用户明确要求实施某个 change 时，先转入既有 `Need → Impact → Proposal → Implementation → Verification → Regression` 流程，不在本 Skill 内静默执行。
- 不在普通任务中自动执行完整巡检。

## 工作流程

1. **定位真实入口**
   - 找到 `agent-tools` 仓库根目录和当前分支/commit。
   - 优先读取 `docs/personal-ai-operations.md`、当前 Architecture/operations handoff 与仓库已有脚本说明。
   - 命令或路径不存在时标 `UNKNOWN`，先搜索仓库实际入口；不要凭旧报告猜路径。

2. **读取 Human-facing status**
   - 优先运行现有简版入口（已含 Personalization 行）：

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
   - 默认只显示数量、严重级别、是否新增（如 `4 open proposals, no new high-severity items`）；不逐条展开内容。

6. **检查 Memory / Model / Routing 信号**
   - Memory：scope pollution、schema/provenance、stale/review candidate、conflict/supersede 异常。
   - Model：provider reachability、request success、fallback 增多、identity `suspicious`、ADMISSION_GAP 新证据。
   - Routing：非法引用、fallback chain 异常、overlay 绕过治理。
   - 只引用现有治理证据；没有证据时写 `UNKNOWN`。

7. **检查 Personalization（行为监控域）**
   - 运行：

     ```text
     python skills/personal-ai-operations-review/scripts/personalization_status.py
     ```

   - 数据链：`scripts/extract_user_msgs.py`（sessions → 仓库 `output/pref-calibration/user_messages.jsonl`，需 zstandard；缺数据时该域 UNKNOWN 并提示先运行此脚本）→ `scripts/behavior_metrics.py`（基线统计）→ 纠正分类 + 选择检查。
   - 至少读取：Correction Rate、Repeat Correction（优先于单发纠正）、Unnecessary Clarification、Over-Personalization、Scope Leakage；只能近似的指标明确标 `PARTIAL/UNKNOWN`，不得伪造精度。
   - 纠正必须分类：`NEW_MODEL_ERROR / KNOWLEDGE_ERROR / TASK_AMBIGUITY / PERSONALIZATION_FAILURE / REPEAT_PERSONALIZATION_FAILURE`；关键词只是候选触发器，判定须结合上下文。
   - Preference Selection 只检查不修改：默认最小规则集、按 task scope 注入、不相关 preference 不注入；发现 `OVER_PERSONALIZATION / IRRELEVANT_INJECTION / WRONG_SCOPE` → `Personalization: DEGRADED`；发现 project 间 scope 泄漏 → `ACTION REQUIRED`；两者都只产 Change Proposal。
   - 基线以 `output/pref-calibration/PERSONAL_AI_PREFERENCE_CALIBRATION_REPORT.md` 为 SSOT，每次重新读取，不硬编码；看趋势，不要求 Correction Rate → 0。

8. **分类行动**
   - `NO ACTION`：系统正常，或仅存在已知且无需当前处理的 blocker/proposal。
   - `REVIEW`：需要人工看证据并作裁决，但没有即时运行风险（含 personalization 趋势恶化）。
   - `ACTION REQUIRED`：出现新的真实故障、RPO breach、drift、secret/privacy 风险、scope pollution/leakage 等，应建立 Change Proposal。
   - `EXTERNAL BLOCKER`：需要当前系统外的条件（如 custody root、硬件、外部授权）才能关闭。
   - 已知外部 blocker（当前：`BACKUP_KEY_CUSTODY`、`NOVEL_REPO_DURABILITY/BLOCKED_PRIVACY`）状态无变化时只标 `KNOWN EXTERNAL BLOCKER`：不得每次巡检重新建议解决，也不得单独因此让 Overall 显示 `ACTION REQUIRED`。

9. **需要修改时只产 Change Proposal**
   - 使用：`Need → Impact → Proposal → Implementation → Verification → Regression`。
   - 明确 affected SSOT、风险、验收、回滚与是否阻塞日常使用。
   - 除非用户在当前任务中明确要求实施，否则停止在 Proposal。

## 事实纪律

- 区分 `FACT / INFERENCE / USER-PROVIDED / UNKNOWN`。
- 以本次运行的命令、ledger、registry、trace、git 状态为事实；旧报告只作线索。
- `NO DRIFT` 只证明 canonical 与 live 一致，不证明 live runtime 一定有效；存在运行时健康信号时应交叉验证。
- 不为追求 `HEALTHY` 总状态隐藏 `BLOCKED_PRIVACY`、`WAITING_FOR_CUSTODY_ROOT`、`ADMISSION_GAP` 等真实状态。

## 输出契约

默认输出必须非常短（progressive disclosure：只有用户说“展开/为什么/具体哪个/看详细”时才下钻到底层证据）：

```text
Personal AI Status

Infrastructure     HEALTHY / DEGRADED / BLOCKED / UNKNOWN
Personalization    HEALTHY / DEGRADED / BLOCKED / UNKNOWN
Durability         HEALTHY / DEGRADED / BLOCKED / UNKNOWN
Governance         HEALTHY / DEGRADED / BLOCKED / UNKNOWN
Proposals          HEALTHY / REVIEW
External Blockers  HEALTHY / BLOCKED

Overall: NO ACTION / REVIEW / ACTION REQUIRED / EXTERNAL BLOCKER
```

- 每个非 HEALTHY 项只给一句原因；禁止默认倾倒几十项底层检查结果。
- 下钻时按需展示：aic diff、model governance、memory governance、durability ledger、correction patterns（`personalization_status.py --detail`）、proposal evidence。
- 如果 `Overall = NO ACTION`，明确写“无需修改，继续正常使用”，然后停止，不主动提出新的架构优化。
- 如果 `Overall = ACTION REQUIRED`，附一个最小 Change Proposal，但不要自行实施。

## 安全边界

- 默认只读；不调用 `aic apply`、不写 canonical、不 push、不 admit model、不改 preference/memory、不删除 config。
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

实际运维复查至少要有：`personal_status/gov_status` 证据、`aic validate`、已登记 Harness 的 diff 状态、durability/RPO 证据、outstanding proposal 摘要、personalization 行为指标和 `git status`。