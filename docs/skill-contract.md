# Skill 包契约

每个已登记包必须能从干净仓库运行，不依赖第三方 Python 包。

## 必需文件

```text
<skill-name>/
  SKILL.md
  agents/openai.yaml
  examples/<至少一个 Markdown 文件>.md
```

`SKILL.md` 必须是 UTF-8 Markdown，frontmatter 至少包含非空 `name` 和 `description`，且名称必须与 manifest 一致。描述必须同时说明能力和触发场景；正文使用命令式规则，不能重复堆叠触发段落。

`agents/openai.yaml` 必须包含非空的 `interface.display_name`、`interface.short_description` 和 `interface.default_prompt`，并与当前 Skill 保持一致。

## 正文质量要求

工作流 Skill 至少覆盖以下适用项：

- 适用范围和触发边界；
- 不适用场景或不应触发的条件；
- 有顺序的工作流程；
- 输出契约；
- 安全边界或非目标；
- 验证方式或包内检查脚本。

可能造成误导的任务必须区分事实、假设、推断和用户提供的指令。只读审计不能静默变成写入、清理或报告生成；高风险写入必须说明目标并要求明确意图。

复杂 Skill 应遵循渐进披露：入口正文保持精简，变体规则、评分表、模板和长说明放入 `references/`。正文不应超过 500 行。

## Manifest 字段

| 字段 | 必需 | 含义 |
|---|---|---|
| `name` | 是 | 小写连字符包名 |
| `path` | 是 | 仓库相对路径 |
| `version` | 是 | 包版本 |
| `description` | 是 | 中文注册简介 |
| `lang` | 是 | 支持语言 |
| `category` | 是 | reasoning、workflow、writing、reporting 或 maintenance |
| `priority` | 是 | P0、P1 或 P2 维护优先级 |
| `tier` | 是 | core、conditional 或 optional |
| `depends_on` | 是 | 其他注册 Skill 名称或空列表 |

`depends_on` 只描述路由或组合关系，不会自动安装或执行依赖。`profiles` 位于 manifest 顶层，用于选择 `core` 或 `full` 恢复集合。

## 验证命令

```bash
python3 scripts/validate_repo.py --strict
python3 skill-quality-gate/scripts/quality_report.py --root . --strict
python3 -m unittest discover -s tests -v
```
