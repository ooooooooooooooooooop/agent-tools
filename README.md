# skills

Public skills repository for agent tooling.

## Included Skills

| Skill | Description | Language |
|-------|-------------|----------|
| [simulate-elite-experts](./simulate-elite-experts) | Simulate high-stakes reasoning by modeling how top domain experts think, disagree, and converge | English / 中文 |
| [unified-taskflow](./unified-taskflow) | 复杂任务管理系统，基于锚点锚定和检查点协议 | 中文 |

## Install

```bash
# simulate-elite-experts
npx skills add https://github.com/ooooooooooooooooooop/skills --skill simulate-elite-experts

# unified-taskflow
npx skills add https://github.com/ooooooooooooooooooop/skills --skill unified-taskflow
```

## Usage

After installation, trigger skills with:

```text
/simulate-elite-experts
/unified-taskflow
```

## Repository Layout

```text
simulate-elite-experts/
  SKILL.md              # Skill definition (read by AI agent)
  agents/openai.yaml    # OpenAI agent config
  references/           # Eval rubric, cases, output templates
  scripts/              # Lint script (PowerShell)

unified-taskflow/
  SKILL.md              # 技能定义（AI agent 读取）
  assets/templates/     # anchor / checkpoint / design 模板
  references/           # 治理规则、交互设计、重锚协议
  scripts/              # 任务生命周期管理 (Python)
```

## License

[MIT](./LICENSE)
