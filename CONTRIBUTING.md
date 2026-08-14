# 贡献指南

本仓库维护 Agent Skills 和 MCP servers。两类项目使用不同发布契约，不要把 MCP 包装成 Skill，也不要让 Skill 目录承担运行服务。

## Skill 包

```text
your-skill-name/
  SKILL.md
  agents/openai.yaml
  examples/
  references/
  scripts/
  assets/
```

Skill 保持在仓库根目录，以兼容 `npx skills add --skill <name>`，并登记到 `skills.json`。

## MCP 包

```text
mcp/<server-name>/
  README.md
  LICENSE
  <entrypoint>
  <installer>
  tests/
```

每个 MCP 必须登记到 `mcp.json`，声明入口、安装器、版本、上游来源、平台边界和许可证。修改第三方 MCP 时必须保留其许可证、Required Notice 和可追溯的上游提交。

MCP 包禁止包含：

- `state.sqlite`、会话 JSONL、日志、缓存或响应文件；
- 用户级 `config.json`、模型设置、认证信息；
- 真实用户名、绝对工作区路径、会话 UUID、任务或线程 ID；
- 从某台设备复制的自动化状态。

示例和测试必须使用明显虚构的路径、UUID、项目名和 provider 名称。

## 变更流程

1. 修改包并同步对应注册表。
2. 更新项目定义、架构或安装说明。
3. 运行仓库严格校验和回归测试。
4. 对 MCP 运行自己的完整测试；复杂行为应有回归夹具。
5. 检查 `git diff --check` 和隐私边界。

```bash
python scripts/validate_repo.py --strict
python skill-quality-gate/scripts/quality_report.py --root . --strict
python -m unittest discover -s tests -v
python -m unittest discover -s mcp/agent-switchboard/tests -v
```

## 提交边界

- 一个变更保持清晰目标和最小影响面。
- 不为了目录对称移动现有根目录 Skill。
- 不把用户级安装、同步或全局配置修改混入普通仓库验证。
- Git 提交、推送、发布和设备安装是独立动作，不由测试脚本自动执行。

## 风格

- 使用 `.editorconfig` 规定的 LF、UTF-8 和缩进。
- Python 使用 4 个空格；Markdown 使用 2 个空格缩进。
- 命令、API、文件名和代码标识符保留原文；用户可见说明默认使用中文。
