# Contributing

Thanks for your interest in contributing to this skills repository.

## Skill Directory Structure

Every skill must follow this layout:

```text
your-skill-name/
  SKILL.md              # Required — skill definition (read by AI agent)
  agents/openai.yaml    # Required for published skills
  examples/             # Required: at least one representative case
  references/           # Optional — supporting docs, rubrics, templates
  scripts/              # Optional — deterministic helpers
  assets/               # Optional — templates, images, etc.
```

- `SKILL.md` is the entry point. It must include YAML frontmatter with at least `name` and a trigger description.
- Use `_template/` as a starting point for new skills.
- Keep all package files UTF-8 and use repository-relative paths.
- Do not include local runtime state, private memory, dependency trees, or generated deliverables in a skill package.

## Adding a New Skill

1. Copy `_template/` and rename it to your skill name (lowercase, hyphenated).
2. Edit `SKILL.md` with your skill definition.
3. Add `agents/openai.yaml` and at least one example output in `examples/`.
4. Update `skills.json` with your skill's metadata.
5. Run `python3 scripts/validate_repo.py --strict` and the relevant skill smoke tests.
6. Run `python3 -m unittest discover -s tests -v`.
7. Open a PR.

## Pull Request Guidelines

- One skill per PR.
- Include a brief description of what the skill does and when to use it.
- Ensure `SKILL.md` frontmatter is valid YAML.
- Python scripts must pass `py_compile` checks.
- Markdown files should be well-formed (CI runs markdownlint).
- Manifest entries, package frontmatter, local links, agent metadata, and examples must pass the repository validator.
- `skills.json` must declare `category`, `priority`, and `depends_on`; dependencies are descriptive and do not trigger installation.
- Do not add local memory, caches, generated reports, machine-specific paths, or private project data to a published package.
- Synchronization to another device is a separate explicit operation; use `scripts/sync_skills.py --check` before `--apply` and verify afterward.

## Code Style

- See `.editorconfig` for formatting rules.
- Python: 4-space indent, UTF-8.
- Markdown: 2-space indent, no trailing whitespace in code blocks.
- PowerShell: 4-space indent.

## Questions?

Open an issue if anything is unclear.
