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

根目录按四类组织。前三类随仓库发布；第四类**永远仅存在于本地，不进入发布**。

```text
# ① Skill 包（根目录平铺，受 skills.json + validate_repo.py 契约约束）
<skill-name>/            # 每个含 SKILL.md 的目录即一个 Skill 包，必须登记在 skills.json
                         # 布局统一：SKILL.md + agents/openai.yaml + examples/*.md

# ② MCP 源码发行包
mcp/<server-name>/       # 可独立验证的第三方/修改版 MCP 包，自持子目录许可证
                         # 登记在 mcp.json（入口、版本、平台、上游基线）

# ③ 质量与发布脚手架
scripts/                 # 仓库级校验（validate_repo.py）与 Skill 同步（sync_skills.py）
tests/                   # 仓库级回归测试
skill-quality-gate/      # Skill 质量门禁（也是 Skill 包）
docs/                    # 架构与发布规则（architecture.md / skill-contract.md / sync-and-release.md）
.github/workflows/       # CI：严格校验 + Skill 门禁 + markdown/py 语法 + 回归测试
_template/               # 新建包的模板种子（validate_repo 显式排除，非 Skill）

# ④ 设备运行层（本地，永不发布）
.taskflow/  .grepai/  .claude/  node_modules/  state.sqlite  会话 JSONL  日志  用户配置  生成报告
```

- **为何 Skill 平铺在根目录**：`scripts/validate_repo.py` 的 `discover_skill_dirs()` 只扫描根目录下含 `SKILL.md` 的目录，这是 Skills CLI 的既定契约。把 Skill 收入 `skills/*` 子目录会导致校验器判 FAIL。真正的"干净"体现在每类内部统一，而非强制改目录层级。
- 详尽的层级、状态边界、许可证边界与发布门禁见 [`docs/architecture.md`](./docs/architecture.md)。

`agent-switchboard` 是上游项目的修改发行版，许可证不受仓库根 MIT 许可证覆盖；详见子项目内的 `LICENSE` 与 `DISTRIBUTION.md`。

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
