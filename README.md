# skills

Public skills repository for agent tooling.

## Included Skills

| Skill | Description | Language |
|-------|-------------|----------|
| [simulate-elite-experts](./simulate-elite-experts) | Simulate high-stakes reasoning by modeling how top domain experts think, disagree, and converge | English / 中文 |
| [unified-taskflow](./unified-taskflow) | 复杂任务管理系统，基于锚点锚定和检查点协议 | 中文 |
| [clarify-before-change](./clarify-before-change) | Clarify ambiguous or high-risk changes before editing | English / 中文 |
| [task-mode-router](./task-mode-router) | Route tasks by size and risk before choosing execution depth | English / 中文 |
| [minimal-implementation](./minimal-implementation) | Prefer the smallest correct implementation and verification scope | English / 中文 |
| [natural-rewrite](./natural-rewrite) | Rewrite Chinese or English copy naturally while preserving meaning | English / 中文 |
| [weekly-work-summary](./weekly-work-summary) | Reconstruct evidence-based weekly summaries from workspace activity | English / 中文 |
| [skill-repository-maintainer](./skill-repository-maintainer) | Audit, validate, organize, and explicitly synchronize a skill repository | English / 中文 |
| [environment-bootstrap](./environment-bootstrap) | Restore a known skill set on another device with hash verification | English / 中文 |

## Install

```bash
# Replace <skill-name> with any entry in the table above.
npx skills add https://github.com/ooooooooooooooooooop/skills --skill <skill-name>
```

## Usage

After installation, trigger skills with:

```text
/simulate-elite-experts
/unified-taskflow
/skill-repository-maintainer
/environment-bootstrap
```

## Repository Layout

```text
<skill-name>/
  SKILL.md              # Required skill definition
  agents/openai.yaml    # Published skill metadata
  examples/              # Representative inputs and outputs
  references/            # Long-form rules and evaluation material
  scripts/               # Deterministic helpers, when needed

scripts/
  validate_repo.py       # Validate manifest, packages, frontmatter, and links
  sync_skills.py         # Check or explicitly sync packages to another device
tests/                   # Repository-level fixtures and regression checks
docs/                    # Repository architecture and release notes
```

See [architecture.md](./docs/architecture.md), [skill-contract.md](./docs/skill-contract.md), and [sync-and-release.md](./docs/sync-and-release.md) for the source-of-truth boundary, package contract, and cross-device workflow.

Runtime state such as `.taskflow/`, `.grepai/`, `node_modules/`, local memory,
temporary inspection files, and generated weekly reports is intentionally kept
outside the published skill packages.

## License

[MIT](./LICENSE)
