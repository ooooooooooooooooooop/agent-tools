# LLM 替代评估示例

用户问题：

```text
Qwen 27B 能否替代 Gemini Flash 做小说状态抽取？
```

执行要点：

1. 将 Gemini Flash 定为 Baseline，Qwen 27B 定为 Candidate。
2. 使用版本固定、预期输出明确的小说片段测试集。
3. 至少记录任务质量、延迟和成本；质量阈值必须在执行前写入 decisionRules。
4. 两个模型使用相同输入与判分规则；需要主观评价时使用匿名输出和固定 evaluator。
5. 调用 `research_create`、`research_execute`、`research_compare`、`research_evidence`。
6. 若本机没有 Qwen 或 Gemini 能力，返回 `UNSUPPORTED` 并列出缺失能力，不生成虚构分数。
7. 结论附 Research ID、revision、Run、Evidence 与 Artifact digest，使另一台设备可以同步后继续。

合格结论示例：

```text
Decision: INCONCLUSIVE

Qwen 在 38/40 个可判定 case 上达到与 Baseline 相同的 exact-match，成本更低，
但两个失败 case 集中在跨章节状态继承，超过预设的最大质量下降阈值。
Evidence: ev-...；outputs: sha256:...；dataset: sha256:...
下一步应增加跨章节 case 后继续同一 Research，而不是新建研究。
```
