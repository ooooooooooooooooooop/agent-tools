---
name: minimal-implementation
description: 以最小正确改动完成代码、Skill、规则、脚本、修复、重构和工作流修改，并提供可复核的验证证据。用于修改代码、Skill、规则文件、脚本、辅助函数或工具，或用户要求优化、改进、重构、稳定化和增强鲁棒性的场景，避免不必要的辅助代码、降级路径、抽象、依赖、整文件重写和无关重构。
---

# Minimal Implementation

Use this skill to keep changes small, correct, and grounded in the existing project.

## Core Rule

Make the smallest correct change that solves the current request. Do not expand scope for "engineering completeness" unless the user explicitly asks for architecture, long-term extensibility, or a broader redesign.

## Decision Ladder

Before adding new code, rules, files, helpers, or abstractions, check in order:

1. Does this need to exist at all?
2. Is there already an implementation, pattern, rule, helper, script, or tool in this project?
3. Can the existing structure be reused?
4. Can the standard library solve it?
5. Can a platform/native capability solve it?
6. Can an already-installed dependency solve it?
7. Only then add the smallest implementation that works.

Stop at the first rung that satisfies the request.

## Guardrails

- Do not add unnecessary helpers.
- Do not add unnecessary fallback paths.
- Do not add unnecessary abstractions.
- Do not introduce new dependencies unless required.
- Do not do unrelated refactors.
- Do not rewrite whole files unless the user explicitly asks.
- Do not broaden the task just to make it feel more engineered.
- Preserve existing naming, structure, and style when possible.
- For bug fixes, fix the root cause in the shared path rather than patching only the visible symptom.

## Change Budget and Acceptance

Before editing, write down the smallest affected surface and the acceptance check that proves it. Keep an explicit `intentionally not changed` list for nearby files that could be tempting to refactor. If a new helper or abstraction does not remove real duplication, improve correctness, or make verification deterministic, do not add it.

Do not treat a passing exit code or a stale artifact as proof. Prefer fresh, artifact-level evidence: targeted tests, validator output, hashes, rendered output, or a direct behavior check.

## Output Behavior

For small tasks:
- Make the minimal change directly.
- Keep explanation short.

For medium or large tasks:
- State what existing implementation, pattern, or rule can be reused.
- State the minimal change plan.
- State what extensions are intentionally not being done.
- State the verification method.

After the change, report:

- changed surface;
- acceptance evidence;
- intentionally unchanged surface;
- remaining risk or a true blocker.

## When Full Design Is Allowed

Use a broader design only when:
- the user explicitly asks for architecture, long-term extensibility, or a full redesign;
- the existing code shape requires a shared abstraction to avoid real duplication or inconsistent behavior;
- safety, security, data-loss prevention, accessibility, or correctness would be weakened by the smaller change.
