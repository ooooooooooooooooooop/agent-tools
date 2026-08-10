---
name: clarify-before-change
description: Clarify ambiguous, high-risk, or multi-file changes before editing. Use when modifying skills, AGENTS.md, project rules, workflow rules, repository structure, complex code, documents with structural impact, or any change that may be hard to roll back; also use when the user asks to optimize a skill, change rules, refactor, restructure, or gives incomplete requirements.
---

# Clarify Before Change

Use this skill to prevent premature edits when the target, scope, or risk is unclear.

## Workflow

1. Restate the user's goal in one sentence.
2. Identify missing information that materially affects the change.
3. List the assumptions you would otherwise make.
4. List the main risks, including reversibility and cross-file impact.
5. Propose the smallest executable path.
6. Ask at most one key question if the answer is required before work can proceed.
7. If the task is clear and low-risk, do not over-question; proceed with the minimal path.

## Evidence-First Gate

Before asking a question, inspect the files, tests, rules, call sites, and current behavior that can answer it. Classify the request as:

- `audit`: read-only inspection; report evidence and do not edit;
- `change`: an authorized reversible edit with a concrete target;
- `high-risk`: a write outside the project, deletion, credentialed action, install, or global configuration change.

Proceed when repository evidence resolves the ambiguity. Ask one focused question only when the missing answer changes the target, business meaning, or irreversible scope. If a safe narrow path exists, use it and record the assumption in the final report.

## Decision Record

For a medium or large change, keep this compact record before editing:

```text
Facts: repository evidence already confirmed
Assumptions: unresolved but reversible choices
Risk: files, behavior, data, or external state affected
Minimal path: smallest change that satisfies the request
Non-goals: explicitly unchanged areas
Status: proceed | ask | blocked
```

## Guardrails

- Do not execute high-risk or ambiguous changes immediately.
- Do not ask questions just to create process overhead.
- Do not invent hidden fallback behavior.
- Separate facts from assumptions.
- If a later discovery invalidates the direction, stop and reassess before editing further.

## Output

Use this shape when clarification is needed:

```text
Goal:
Missing information:
Assumptions:
Risks:
Minimal path:
Question:
```

For an audit or completed change, report the status and evidence instead of emitting an empty question template.
