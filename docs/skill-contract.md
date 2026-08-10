# Skill Package Contract

Every registered package must be usable from a fresh checkout with no optional Python dependencies.

## Required files

```text
<skill-name>/
  SKILL.md
  agents/openai.yaml
  examples/<at-least-one-markdown-file>.md
```

`SKILL.md` must be UTF-8 Markdown with YAML frontmatter containing non-empty `name` and `description`. The frontmatter name must equal the manifest name. The description must state both capability and trigger context; instructions in the body should be imperative and should not repeat the trigger paragraph unnecessarily.

`agents/openai.yaml` must expose non-empty `interface.display_name`, `interface.short_description`, and `interface.default_prompt`. Keep this file deterministic and aligned with the current `SKILL.md`; do not add opaque runtime state.

At least one example must be a non-empty Markdown file. Examples should demonstrate the package's real contract, not just repeat its name.

## Recommended behavior sections

For any workflow skill, include the smallest applicable set of:

- scope and trigger boundary;
- ordered workflow;
- output contract;
- safety or non-goals;
- verification step or bundled validator.

A skill must distinguish facts, assumptions, inference, and user-provided instructions whenever the task can produce misleading certainty. A read-only audit must not silently become a mutation or report-generation workflow. High-risk writes must state their target and require explicit intent.

## Manifest fields

The registry entry uses:

| Field | Required | Meaning |
|-------|----------|---------|
| `name` | yes | Lowercase hyphenated package name |
| `path` | yes | Repository-relative package path |
| `version` | yes | Package version string |
| `description` | yes | Short registry description |
| `lang` | yes | Supported language labels |
| `category` | yes | `reasoning`, `workflow`, `writing`, `reporting`, or `maintenance` |
| `priority` | yes | `P0`, `P1`, or `P2` maintenance priority |
| `depends_on` | yes | Other registered skill names, or an empty list |

Dependencies describe recommended routing or composition. They do not cause automatic installation or execution.

## Validation

Run:

```bash
python3 scripts/validate_repo.py --strict
python3 -m unittest discover -s tests -v
```

The validator checks manifest/package parity, frontmatter, metadata, examples, and local Markdown links. Package-specific scripts remain responsible for deeper output-shape checks.
