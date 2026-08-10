# Skill 仓库

面向智能体工具的公开 Skill 仓库。

## 已包含的 Skill

| Skill | 简介 | 支持语言 |
|-------|-------------|----------|
| [simulate-elite-experts](./simulate-elite-experts) | 模拟顶级专家视角，交叉质询并综合复杂决策，记录不确定性 | 英文 / 中文 |
| [unified-taskflow](./unified-taskflow) | 管理真正复杂的多阶段任务，追踪锚点、检查点与验收 | 中文 |
| [clarify-before-change](./clarify-before-change) | 在修改前澄清范围、风险、假设与验收标准，避免误改 | 英文 / 中文 |
| [task-mode-router](./task-mode-router) | 按任务规模与风险选择执行深度，避免流程过度或风险失控 | 英文 / 中文 |
| [minimal-implementation](./minimal-implementation) | 以最小正确改动完成任务，并提供可复核的验证证据 | 英文 / 中文 |
| [natural-rewrite](./natural-rewrite) | 把中文或英文文本改得自然流畅，同时保持事实、语气和含义 | 英文 / 中文 |
| [weekly-work-summary](./weekly-work-summary) | 基于工作区证据重建周报、状态总结和按日回顾，区分事实与推断 | 英文 / 中文 |
| [skill-repository-maintainer](./skill-repository-maintainer) | 审计、验证并安全同步 Skill 仓库，隔离私有运行时文件 | 英文 / 中文 |
| [environment-bootstrap](./environment-bootstrap) | 安全恢复 Codex Skill 环境并校验哈希，不删除目标端额外文件 | 英文 / 中文 |

## 安装

```bash
# 将 <skill-name> 替换为上表中的任意 Skill 名称。
npx skills add https://github.com/ooooooooooooooooooop/skills --skill <skill-name>
```

## 使用

安装后，可使用以下方式触发 Skill：

```text
/simulate-elite-experts
/unified-taskflow
/clarify-before-change
/task-mode-router
/minimal-implementation
/natural-rewrite
/weekly-work-summary
/skill-repository-maintainer
/environment-bootstrap
```

## 仓库结构

```text
<skill-name>/
  SKILL.md              # Skill 定义
  agents/openai.yaml    # 发布用元数据
  examples/              # 代表性输入与输出
  references/            # 详细规则与评估材料
  scripts/               # 按需提供的确定性辅助脚本

scripts/
  validate_repo.py       # 校验 manifest、Skill 包、frontmatter 和链接
  sync_skills.py         # 检查或显式同步 Skill 包到其他设备
tests/                   # 仓库级 fixture 与回归测试
docs/                    # 仓库架构与发布说明
```

关于源仓库边界、Skill 包契约和跨设备流程，请参阅 [架构说明](./docs/architecture.md)、[Skill 契约](./docs/skill-contract.md) 和 [同步发布流程](./docs/sync-and-release.md)。

`.taskflow/`、`.grepai/`、`node_modules/`、本地记忆、临时检查文件和生成的周报等运行时状态，不会进入已发布的 Skill 包。

## 许可证

[MIT](./LICENSE)
