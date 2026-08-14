# 仓库架构

本仓库是 Agent 工具的源码与发布控制面，不是运行时工作区。

## 项目类型

```text
源码仓库层
  skills.json + 根目录 Skill 包
  mcp.json + mcp/ 下 MCP 包
          |
          v
质量与发布层
  scripts/ + tests/ + CI
          |
          v
设备运行层
  ~/.codex/skills + ~/.agent-broker + 各 MCP host 配置
```

- `skills.json` 只登记 Skill，不承担 MCP 安装信息。
- `mcp.json` 登记 MCP 入口、安装器、版本、平台、上游基线和许可证。
- 根目录 Skill 包保留当前布局，以兼容 Skills CLI。
- `mcp/<name>/` 是可独立验证的源码发行包；第三方修改版使用自己的子目录许可证。
- `scripts/validate_repo.py` 同时检查两类注册表、包结构、许可证、链接和运行时文件边界。
- `scripts/sync_skills.py` 仍只同步 Skills，不安装 MCP。

## 状态边界

以下内容永远属于设备运行层，不进入发布：

- `.taskflow/`、`.grepai/`、`.claude/`、`node_modules/`；
- `state.sqlite`、会话 JSONL、日志、响应文件；
- 用户路径、会话/任务 ID、模型选择和认证信息；
- 从某台设备导出的 Codex 自动化状态。

## 许可证边界

根目录 MIT 许可证覆盖仓库自有内容和 Skills。第三方 MCP 的许可证在各自子目录内生效；根许可证不得覆盖或改写它。仓库 README 和 `mcp.json` 必须明确这一点。

## 发布门禁

1. 两类注册表与目录一致。
2. 严格仓库校验、Skill 质量门禁、仓库回归通过。
3. 每个 MCP 的完整测试通过。
4. `git diff --check` 通过。
5. 最终差异不包含运行时或私有文件。

设备安装、Git 提交、推送和发布均是独立动作，不由仓库测试自动执行。
