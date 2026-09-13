# briefing 模板契约（V0.3.1 §7）

`briefing.md` 是由 `dsh/world-model/canonical_compile.py` 从 canonical
目录编译生成的 7 段 onboarding 简报，挂载到 harness systemPrompt。
本文件记录其结构契约——编译器是唯一生成者，此模板只描述输出形态。

## 固定段结构（0-6）

| 段 | 标题 | 内容来源 |
|---|---|---|
| 0 | Identity | entity_id / lineage / freeze 状态 |
| 1 | Unresolved First | 未决预测、open loops——先放未决项 |
| 2 | Reliability Alerts | 已知残差、可靠性告警 |
| 3 | Current Best Models | 当前最优模型 + support refs + 最强反证 + falsifier/revision trigger（先验+推翻钥匙，非结论堆砌） |
| 4 | Corrections & Superseded | 纠正史与被取代模型 |
| 5 | Task-Relevant Values / Constraints | 任务相关价值/约束 |
| 6 | Retrieval Pointers | 检索指针（指向 canonical 而非复制内容） |

## 容量与裁剪

- 目标 4-6 KiB UTF-8（`TARGET_MAX = 6*1024`），硬上限 8 KiB（`HARD_CAP`）
- 溢出按优先级裁剪，保留顺序：
  unresolved > alerts > task-relevant models > corrections > values > settled
- OFF/CORE/FULL 三档接收同一简报（mode 决定仪式深度，非开关）

## 编译

```bash
python canonical_compile.py --canonical <canonicalDir>
# 产出 briefing.md + runtime state（current.json）
```
