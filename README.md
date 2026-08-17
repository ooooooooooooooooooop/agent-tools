# Agent 工具仓库

面向 Codex、Claude Code 等本地 Agent 工作流的可复现工具仓库。仓库同时发布两类项目：

- **Skills**：Agent 读取的流程、规则与确定性辅助脚本。
- **MCP servers**：提供实际工具调用能力的本地服务。

本地运行状态、私有记忆、会话记录、机器路径、配置文件和生成物不属于发布内容。

## MCP servers

| MCP | 简介 | 平台 | 许可证 |
|---|---|---|---|
| [agent-switchboard](./mcp/agent-switchboard) | 读取本地会话、后台托管 Claude Code、池化多会话并发控制（并发上限/孤儿回收/项目写锁）、Codex Goal 有界监督（验证器驱动的完成判定、依赖图局部阻塞、预算强制），且仅在实质事件发生时启动监督判断 | broker 跨平台；安装、后台进程控制与旧 mintty 兼容路径以 Windows 10/11 为主 | PolyForm Noncommercial 1.0.0 |

MCP 的机器可读登记信息位于 [mcp.json](./mcp.json)。`agent-switchboard` 是上游项目的修改发行版，许可证不受仓库根 MIT 许可证覆盖；详见子项目内的 `LICENSE` 与 `DISTRIBUTION.md`。

### 安装 MCP

```powershell
git clone https://github.com/ooooooooooooooooooop/skills.git
Set-Location .\skills\mcp\agent-switchboard
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-agent-broker.ps1
```

安装器会登记本机 MCP host，因此应在确认目标设备后显式运行。它不会复制当前设备的 `state.sqlite`、会话或模型设置。

## Skills

| Skill | 简介 | 层级 |
|---|---|---|
| [simulate-elite-experts](./simulate-elite-experts) | 模拟顶级专家视角，交叉质询并综合复杂决策 | 可选 |
| [unified-taskflow](./unified-taskflow) | 管理真正复杂的多阶段任务，追踪锚点、检查点与验收 | 条件启用 |
| [clarify-before-change](./clarify-before-change) | 在修改前澄清范围、风险、假设与验收标准 | 核心 |
| [task-mode-router](./task-mode-router) | 按任务规模与风险选择执行深度 | 核心 |
| [natural-rewrite](./natural-rewrite) | 在保持事实、语气和含义的前提下自然改写文本 | 可选 |
| [minimal-implementation](./minimal-implementation) | 以最小正确改动完成任务并提供可复核证据 | 核心 |
| [skill-repository-maintainer](./skill-repository-maintainer) | 审计、验证并安全同步 Skill 包 | 核心 |
| [environment-bootstrap](./environment-bootstrap) | 恢复 Codex Skill 环境并校验文件哈希 | 核心 |
| [skill-quality-gate](./skill-quality-gate) | 评估 Skill 的触发边界、输出契约与回归质量 | 核心 |

Skill 安装 profile 定义在 [skills.json](./skills.json)。

### 安装 Skill

```bash
npx skills add https://github.com/ooooooooooooooooooop/skills --skill <skill-name>
```

## 仓库结构

```text
<skill-name>/              # 已登记 Skill 包，保持根目录布局
mcp/<server-name>/         # MCP 源码发行包
skills.json                # Skill 注册表与安装 profile
mcp.json                   # MCP 注册表、入口、版本与许可证
scripts/                   # 仓库级校验与 Skill 同步工具
tests/                     # 仓库级回归测试
docs/                      # 架构与发布规则
```

`.taskflow/`、`.grepai/`、`node_modules/`、私有记忆、会话 JSONL、SQLite、用户配置和生成报告属于本地运行状态，不进入发布包。

## 验证

```bash
python scripts/validate_repo.py --strict
python skill-quality-gate/scripts/quality_report.py --root . --strict
python -m unittest discover -s tests -v
python -m unittest discover -s mcp/agent-switchboard/tests -v
```

## 许可证

- 仓库自有内容与 Skills：根目录 [MIT](./LICENSE)。
- `mcp/agent-switchboard`：子目录 [PolyForm Noncommercial 1.0.0](./mcp/agent-switchboard/LICENSE)，商业用途需要上游另行书面许可。
