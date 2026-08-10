# 贡献指南

感谢你维护这个 Codex Skill 仓库。

## Skill 包结构

每个已登记 Skill 使用以下布局：

```text
your-skill-name/
  SKILL.md              # 必需：Agent 读取的 Skill 定义
  agents/openai.yaml    # 必需：发布用 UI 元数据
  examples/              # 必需：至少一个真实示例
  references/            # 可选：按需加载的详细规则
  scripts/               # 可选：确定性辅助脚本
  assets/                # 可选：模板、图像等输出资源
```

Skill 包必须自包含，不能包含本地记忆、缓存、依赖树、机器路径、临时检查文件或生成报告。注册包保持在仓库根目录，以兼容 `npx skills add --skill <name>`。

## 新增或更新 Skill

1. 使用 `_template/` 或 `skill-creator` 生成最小包。
2. 用中文写清 frontmatter 的能力、触发场景和不适用边界。
3. 保持正文包含适用范围、流程、输出契约和验证方式；详细变体放入 `references/`。
4. 添加 `agents/openai.yaml` 和至少一个非空示例。
5. 在 `skills.json` 添加版本、语言、类别、优先级、`tier` 和依赖。
6. 更新对应的 `core`/`full` profile。
7. 运行以下校验：

   ```bash
   python3 scripts/validate_repo.py --strict
   python3 skill-quality-gate/scripts/quality_report.py --root . --strict
   python3 -m unittest discover -s tests -v
   ```

复杂 Skill 还必须运行自己的 smoke test 和至少 5 个代表性回归案例。不要因为结构校验通过就声称行为质量已经验证。

## 提交边界

- 一个变更应有清晰的目标和最小影响面。
- 不要为了目录对称性移动根目录 Skill 包。
- 不要把用户级同步、目的地清理或全局配置修改混入普通代码变更。
- 同步前运行 `scripts/sync_skills.py --check`；应用后对同一个目的地再次检查。
- 目的地额外文件默认保留，删除需要单独确认。

## 风格

- 使用 `.editorconfig` 规定的 LF、UTF-8 和缩进。
- Python 使用 4 个空格；Markdown 使用 2 个空格缩进。
- 命令、API、文件名和代码标识符保留原文；用户可见的 Skill 介绍和规则默认使用中文。
