# 仓库架构

本仓库是 Agent 工具的源码与发布控制面，不是运行时工作区。

## 项目类型

```text
源码仓库层
  skills.json + skills/<name>/ Skill 包
  mcp.json + mcp/ 下 MCP 包
  dsh/ 下 DSH 插件包（不设注册表）
          |
          v
质量与发布层
  scripts/ + tests/ + CI
          |
          v
设备运行层
  ~/.codex/skills + ~/.agent-broker + 各 MCP host 配置 + ~/.dsh 各 profile
```

- `skills.json` 只登记 Skill，不承担 MCP 安装信息。
- `mcp.json` 登记 MCP 入口、安装器、版本、平台、上游基线和许可证。
- Skill 包收在 `skills/<name>/` 下；`scripts/validate_repo.py::discover_skill_dirs()` 识别该目录下的 `SKILL.md`（为兼容仍认可根目录平铺的旧包）。`skills.json` 的 `path` 写 `./skills/<name>`。
- `mcp/<name>/` 是可独立验证的源码发行包；第三方修改版使用自己的子目录许可证。
- `dsh/<name>/` 是 DSH 用户级插件源码包：插件 JS + 可移植 `cordis.patch.yml` 片段 + README。不登记 manifest；校验器对 `dsh/` 只做链接与 markdown 兜底，发布内容禁止含本机路径。
- `scripts/validate_repo.py` 同时检查两类注册表、包结构、许可证、链接和运行时文件边界。
- `scripts/sync_skills.py` 仍只同步 Skills，不安装 MCP。
- `agent-switchboard` 的受管 Claude 守护进程属于设备运行层：源码随 MCP 发布，队列、事件、决策账本和 PID 状态只写入 `~/.agent-broker/supervisors/`。
- 定时 Codex 自动任务不是监督控制面。受管监督从 Claude 结构化流生成本地事件，仅在回合终态、重复失败、重试耗尽或进程异常退出时启动一次临时 Codex 判断。

## 状态边界

以下内容永远属于设备运行层，不进入发布：

- `.taskflow/`、`.grepai/`、`.claude/`、`node_modules/`；
- `state.sqlite`、会话 JSONL、日志、响应文件；
- `supervisors/` 下的命令、事件、决策账本和进程状态；
- 用户路径、会话/任务 ID、模型选择和认证信息；
- 各设备 profile 的 `cordis.patch.yml` 全量内容（含本机 MCP 条目与路径）——仓库只发布插件源码与可移植注册片段；
- 从某台设备导出的 Codex 自动化状态。

## 许可证边界

根目录 MIT 许可证覆盖仓库自有内容和 Skills。第三方 MCP 的许可证在各自子目录内生效；根许可证不得覆盖或改写它。仓库 README 和 `mcp.json` 必须明确这一点。

## 发布门禁

1. 两类注册表与目录一致（`dsh/` 插件目录不设注册表，靠链接与 markdown 检查兜底）。
2. 严格仓库校验、Skill 质量门禁、仓库回归通过。
3. 每个 MCP 的完整测试通过。
4. `git diff --check` 通过。
5. 最终差异不包含运行时或私有文件。

设备安装、Git 提交、推送和发布均是独立动作，不由仓库测试自动执行。
