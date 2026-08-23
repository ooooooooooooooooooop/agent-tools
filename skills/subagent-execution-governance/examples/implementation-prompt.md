# 实现子代理 Prompt 模板（Implementation Agent）

> 实现子代理：只写自有文件，有界读取，不自由探索仓库。

## 模板

```text
目标：依据契约实现 [目标]，不扩大范围。

契约快照：
[ 此处粘贴契约快照全文，含 GOAL/OWN/MAY READ/REFERENCE/
  IN SCOPE/OUT OF SCOPE/MUST PRESERVE/MUST NOT/EXIT/BLOCKED ]

读取白名单：
- 自动允许（Level 0）：OWN 文件、MAY READ 文件、契约快照本身
- 有预算额外读取（Level 1，最多 [N] 次，需带 reason code）：
  - API_SIGNATURE_UNKNOWN     类型/签名未知
  - TEST_EXPECTATION_UNKNOWN  测试预期未知
  - TYPE_DEFINITION_REQUIRED  需要类型定义
- 禁止（Level 2）：跨模块架构探索、repo-wide grep、超过预算

预算约束：
- 首次写入前 ≤ 6 次工具调用
- 额外探索（Level 1 读取）≤ [N] 次
- 未耗尽预算前必须发生以下之一：
  A. 第一处有效代码修改（mutation）
  B. 返回 BLOCKED（结构化格式，见下）
- 禁止第三种状态："继续研究"

状态回报要求：
每阶段报告当前状态（IMPLEMENTING / MUTATED / BLOCKED / DONE / FAILED），
以及当前产出物（已建/修改的文件列表）。

结构化 BLOCKED 格式（走此路径时使用）：
  missing_fact:      [具体缺失的信息]
  why_required:      [为什么需要这个信息]
  already_checked:   [已检查的文件列表]
  requested_context: [请求的具体上下文]

完成时返回：
  DONE / FAILED
  Changed:    [文件列表]
  Validation: [测试/编译结果]
  Deviations: [与契约的偏差，无则写 none]
  BLOCKER:    [阻塞原因，无则写 none]
  Details:    [artifact 引用或摘要，禁止完整工作历史]
```

## 关键约束

- 实现子代理**不负责"理解架构"**——契约快照已经做了这件事
- 遇到契约没覆盖的 API 细节 → 走 BLOCKED，不是自己去翻源码
- OWN 文件以外的文件 **只读不改**，除非契约 MAY READ 明确允许