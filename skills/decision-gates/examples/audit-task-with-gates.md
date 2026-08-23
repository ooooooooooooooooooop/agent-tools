# 示例：跨会话审计任务中的五道闸门

> 场景：200+ 文件仓库权限过滤审计，4 个 worker 并行，跨两个会话。

## 阶段 1：初始化

```text
create_goal 锁定 objective：
"检查所有数据库查询是否绑定用户权限过滤"

objective 逐字拷贝进 checkpoints/raw_evidence_anchors.json 的 objective 字段。
```

## 阶段 2：checkpoint cp-001（4 个 worker 并行）

```bash
# 先跑闸门 2（跨包一致性，0 token）
python scripts/gate_consistency.py checkpoints/workers-p001.json
```

输入 `workers-p001.json`：

```json
{
  "phase_id": "p-001",
  "target_files": ["src/query_a.py", "src/query_b.py", "src/legacy/v2_query.py"],
  "workers": [
    {"worker_id": "w1", "files": [
      {"path": "src/query_a.py", "conclusion": "PASS", "sha256_after": "aa11"}]},
    {"worker_id": "w2", "files": [
      {"path": "src/query_b.py", "conclusion": "PASS", "sha256_after": "bb22"},
      {"path": "src/legacy/v2_query.py", "conclusion": "SKIP", "reason": "ambiguous pattern", "sha256_after": null}]}
  ]
}
```

输出：

```json
{
  "verdict": "WARN",
  "warnings": [
    {"type": "unresolved_marker", "worker_id": "w2",
     "path": "src/legacy/v2_query.py", "conclusion": "SKIP",
     "reason": "ambiguous pattern"}
  ]
}
```

**关键动作**：WARN 中的 SKIP 必须登记进锚的 `worker_skip_register`，且
`alignment_decision` 必须写 **PARTIAL**（不能写 PASS）。

## 阶段 3：会话 B 恢复

```text
恢复顺序（硬约束）：
1. 先读 checkpoints/raw_evidence_anchors.json（锚）
2. 后读 work_memory（摘要）

锚中 alignment_decision = PARTIAL，存在未决 SKIP
→ 摘要里的"4/4 通过"按 PARTIAL 处理
→ 先 resolve src/legacy/v2_query.py 的 SKIP，再推进
```

## 阶段 4：防御自检（第 5 个 checkpoint 后）

```bash
python scripts/gate_selfcheck.py checkpoints/
```

若输出 `DEFENSE_DRIFT`：暂停推进，回退到最近无 bias 的 checkpoint。

## 产物落点

```text
checkpoints/
  raw_evidence_anchors.json   # 锚（含全部 evidence + gates_run）
  cp-001.json ... cp-005.json # 每个 checkpoint 的锚
  workers-p001.json           # 闸门 2 输入
  audit-cp003-brief.md        # 闸门 1 派工单副本
```
