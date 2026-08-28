# Operations Review 示例

用户：

> 检查一下我的 Personal AI / DSH 生态现在有没有需要处理的问题。

期望行为：

1. 先运行现有 human-facing status (`personal_status.py`)、durability (`rpo_check.py`)、aic validate/diff 与 proposal 检查，不修改任何 canonical。
2. 明确输出各域的 `status` 与 `evidence_state` (CURRENT / LAST_KNOWN / UNAVAILABLE)。
3. 如果总状态是 BLOCKED / DEGRADED，但原因只是已知的 `BACKUP_KEY_CUSTODY` 或 `NOVEL_REPO_DURABILITY = BLOCKED_PRIVACY`，不要把它误判成基础设施故障或 ACTION REQUIRED。
4. 区分 backup age 超期 (`BACKUP_RPO_AGE_BREACH`) 与 repo privacy blocker (`KNOWN_PRIVACY_BLOCKER`)。
5. 只有发现新的实际 RPO 超期、Harness 配置 drift、scope leakage、secret 风险或真实治理规则违规时，才标 `ACTION REQUIRED`。

示例输出：

```text
Personal AI Status

Infrastructure     HEALTHY
  CURRENT — aic VALID, 5 targets NO DRIFT

Personalization    HEALTHY
  CURRENT — Correction Rate 6.2%（基线 6.2%），重复纠正组 5；Over-Personalization/Scope Leakage=UNKNOWN(无注入事件源)

Durability         DEGRADED
  CURRENT — novel-main remains known BLOCKED_PRIVACY; backup-age datasets healthy

Governance         HEALTHY
  CURRENT — capability_drift=0; static boundary clean

Proposals          HEALTHY
  CURRENT — 4 open, no new high severity

External Blockers  BLOCKED
  CURRENT — known, unchanged

Overall: EXTERNAL BLOCKER
```

## Personalization 域示例

用户：

> 看看个性化有没有退化 / AI 最近是不是又变难用了

期望行为：

1. 运行 `personalization_status.py`；无会话消息数据时，若存在基线校准报告，标记 `status: UNKNOWN`, `evidence_state: LAST_KNOWN` 并说明 `current check unavailable; last verified Correction Rate 6.2%`；不伪造指标，严禁说“与基线持平”。
2. 纠正分类：只有命中偏好模式且跨会话重复才算 `REPEAT_PERSONALIZATION_FAILURE`；单发纠正不直接判 personalization 失败。
3. 简单问答被注入大量无关 workflow preference（over-personalization）→ `DEGRADED`，只产 Change Proposal。
4. project:A 任务注入了 project:B 的 overlay（scope leakage）→ `ACTION REQUIRED`。
5. 当有当前测量数据时，Correction Rate 与基线持平且无新增重复纠正 → `HEALTHY`。

用户说“展开 / 为什么 / 具体哪个”时才下钻展示底层指标明细、aic diff 或 proposal 详情。
