# Skill 仓库

面向 Codex 工作流的可复现 Skill 仓库。仓库只发布已登记的 Skill 包；本地运行状态、私有记忆、依赖目录和生成物不属于发布内容。

## Skill 清单

| Skill | 简介 | 层级 |
|---|---|---|
| [simulate-elite-experts](./simulate-elite-experts) | 模拟顶级专家视角，交叉质询并综合复杂决策，记录不确定性 | 可选 |
| [unified-taskflow](./unified-taskflow) | 管理真正复杂的多阶段任务，追踪锚点、检查点与验收 | 条件启用 |
| [clarify-before-change](./clarify-before-change) | 在修改前澄清范围、风险、假设与验收标准，避免误改 | 核心 |
| [task-mode-router](./task-mode-router) | 按任务规模与风险选择执行深度，避免流程过度或风险失控 | 核心 |
| [natural-rewrite](./natural-rewrite) | 把中文或英文文本改得自然流畅，同时保持事实、语气和含义 | 可选 |
| [minimal-implementation](./minimal-implementation) | 以最小正确改动完成任务，并提供可复核的验证证据 | 核心 |
| [skill-repository-maintainer](./skill-repository-maintainer) | 审计、验证并安全同步 Skill 仓库，隔离私有运行时文件 | 核心 |
| [environment-bootstrap](./environment-bootstrap) | 安全恢复 Codex Skill 环境并校验哈希，不删除目标端额外文件 | 核心 |
| [skill-quality-gate](./skill-quality-gate) | 评估 Skill 的触发边界、输出契约、验证方式和回归质量 | 核心 |

安装 profile 定义在 [skills.json](./skills.json) 中：`core` 用于最小可复现环境，`full` 用于完整恢复。`weekly-work-summary` 已从本仓库移除。

## 安装

```bash
# 将 <skill-name> 替换为上表中的 Skill 名称
npx skills add https://github.com/ooooooooooooooooooop/skills --skill <skill-name>

# 或按仓库 profile 恢复；实际跨设备恢复请使用 scripts/sync_skills.py
```

## 使用

安装后可使用以下触发名：

```text
/simulate-elite-experts
/unified-taskflow
/clarify-before-change
/task-mode-router
/natural-rewrite
/minimal-implementation
/skill-repository-maintainer
/environment-bootstrap
/skill-quality-gate
```

## 仓库结构

```text
<skill-name>/
  SKILL.md              # Skill 定义与运行规则
  agents/openai.yaml    # 发布用 UI 元数据
  examples/              # 代表性输入与输出
  references/            # 按需加载的详细规则
  scripts/               # 确定性辅助脚本
  assets/                # 模板等输出资源
skills.json              # 注册表和安装 profile
scripts/                 # 仓库级校验与同步工具
tests/                   # 仓库回归测试
docs/                    # 架构、契约与发布说明
```

已登记的 Skill 包保持在仓库根目录，以兼容 `npx skills add --skill <name>`。`.taskflow/`、`.grepai/`、`node_modules/`、`.claude/`、私有记忆、临时检查文件和生成报告属于本地运行状态，不会进入发布包。

详细规则见 [架构说明](./docs/architecture.md)、[Skill 契约](./docs/skill-contract.md) 和 [同步发布流程](./docs/sync-and-release.md)。

## 许可

[MIT](./LICENSE)
