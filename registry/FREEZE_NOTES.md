# FREEZE NOTES — Architecture V2.1 冻结修正登记

本文件登记实施前冻结的两条修正（2026-08-28）。它们不改变 V2.1 架构，只消除文本与物理实现歧义。

## 修正 A：Memory revision 物理布局

Memory Contract（V2.1 §5）不变，但未来 file-per-record 实现的物理语义冻结为：

- memory record metadata = immutable；
- revision = **独立的 immutable object/file**（不允许把 `revisions[]` 数组放在同一文件里让多设备共同追加）；
- supersede = 新记录引用旧记录 id；
- 目的：真正 append-friendly，避免两个设备同时追加同一 YAML/Markdown 文件造成 Git 文本冲突。

（Memory Plane 属 Migration #4，本轮未实现，仅登记。）

## 修正 B：Model Identity 字段（最终契约）

模型身份链**只有四段**：

```
requested_model → gateway_resolved_model → provider_reported_model → identity_assessment
```

`identity_assessment.status ∈ {consistent, suspicious, unknown}`；`signals` 字段可携带 canary/latency/behavior/cost 等异常检测结果。

**不得重新引入** `verified_model` / `verification_confidence`。除非存在可信 attestation，任何代码、文档、报告不得宣称已"验证"真实底层模型。

## Thin Control Plane 边界（aic 六动词）

`aic` 只允许：discover / render / diff / validate / apply / bootstrap。
严禁：daemon、database、agent 编排、LLM 调用、Memory 查询、运行时路由决策、调度器。
本阶段（Migration #2）实现 discover/render/diff/validate；apply/bootstrap 仅 CLI 契约桩。

## 过渡说明

- `registry/gateways.yaml` 按 V2.1 SSOT 矩阵终态属于 personal-ai-state（私有仓库，Migration #4 建立）。当前为 Phase-2 过渡位置：内容**不含任何 secret 与设备绝对路径**，Migration #4 时迁移。
