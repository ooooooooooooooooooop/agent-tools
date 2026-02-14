# Contributing

Thanks for your interest in contributing to this skills repository.

## Skill Directory Structure

Every skill must follow this layout:

```text
your-skill-name/
  SKILL.md              # Required — skill definition (read by AI agent)
  references/           # Optional — supporting docs, rubrics, templates
  scripts/              # Optional — automation scripts
  assets/               # Optional — templates, images, etc.
  examples/             # Recommended — 1-2 example outputs
```

- `SKILL.md` is the entry point. It must include YAML frontmatter with at least `name` and a trigger description.
- Use `_template/` as a starting point for new skills.

## Adding a New Skill

1. Copy `_template/` and rename it to your skill name (lowercase, hyphenated).
2. Edit `SKILL.md` with your skill definition.
3. Add at least one example output in `examples/`.
4. Update `skills.json` with your skill's metadata.
5. Open a PR.

## Pull Request Guidelines

- One skill per PR.
- Include a brief description of what the skill does and when to use it.
- Ensure `SKILL.md` frontmatter is valid YAML.
- Python scripts must pass `py_compile` checks.
- Markdown files should be well-formed (CI runs markdownlint).

## Code Style

- See `.editorconfig` for formatting rules.
- Python: 4-space indent, UTF-8.
- Markdown: 2-space indent, no trailing whitespace in code blocks.
- PowerShell: 4-space indent.

## Questions?

Open an issue if anything is unclear.
