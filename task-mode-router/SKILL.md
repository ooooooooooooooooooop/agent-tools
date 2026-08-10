---
name: task-mode-router
description: 按任务规模与风险选择执行深度，避免流程过度或风险失控。用于判断代码、配置、文档和工作流请求应采用小型直接修改、中型计划变更、大型分阶段执行，还是高风险流程；高风险场景包括安装工具、修改全局配置、添加 MCP 服务或 hooks、删除文件、修改权限和使用浏览器登录状态。
---

# Task Mode Router

Use this skill to choose the lightest workflow that still controls risk.

## Small Task

Characteristics:
- Single file or single point change.
- Clear instruction.
- Low risk.
- Easy to verify and roll back.

Action:
- Make the smallest change directly.
- Avoid long plans, new abstractions, and new dependencies.

## Read-Only Audit

Characteristics:
- The user asks to inspect, compare, explain, review, or report status.
- No file, repository, external system, or configuration write is requested.

Action:
- Use the smallest evidence-gathering workflow.
- Do not create reports, alter strategy/configuration, sync files, or clean up artifacts as an implied next step.
- Report facts, inference, and remaining uncertainty separately.

## Medium Task

Characteristics:
- Multiple edits or moderate judgment.
- Possible behavior impact.
- Verification is needed.

Action:
- Give a short plan.
- State the affected files and risk.
- Execute after the plan is clear.
- Report verification and rollback.

## Large Task

Characteristics:
- Multi-file feature, refactor, architecture change, rule-system change, or long-term workflow.
- Requirements or acceptance criteria matter.

Action:
- Define goal, non-goals, constraints, impact, and acceptance criteria.
- Use the repository's task tracking method when one exists.
- Wait for confirmation if material ambiguity remains.

## High-Risk Task

Characteristics:
- Installing Skill, MCP, CLI, plugin, or hook.
- Changing user-level or global configuration.
- Deleting, moving, or permission-changing operations.
- Running untrusted code.
- Using real browser login state or sensitive pages.

Action:
- Audit first.
- Explain source, command, files written, local file access, command execution, upload risk, permissions, verification, and rollback.
- Do not proceed without explicit confirmation.

## Precedence and Composition

Apply the highest applicable risk class: high-risk write > read-only audit boundary > large/medium/small execution depth. A read-only request remains read-only even if the likely next action would be useful. Use `clarify-before-change` for unresolved scope, `minimal-implementation` for the selected implementation, and `unified-taskflow` only when the large-task trigger and recovery tracking are genuinely needed; dependencies guide routing and do not authorize actions.

## Decision Rule

Choose the lowest mode that covers the actual risk. Do not make small tasks ceremonial, and do not treat high-risk tasks as ordinary edits.
