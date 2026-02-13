---
name: simulate-elite-experts
description: |
  Simulate high-stakes reasoning by modeling how top experts in the relevant domain would think, disagree, and converge on a decision.
  Use when users ask to role-play strongest minds, compare elite viewpoints, or ask: what would be a good group of people to explore X, and what would they say.
  Trigger for prompts like "think like world-class experts", "simulate top domain specialists", "role-play strongest domain people", and "use four-lens dialogue".
---

# Simulate Elite Experts

## Core Principle

Treat the model as a viewpoint simulator, not as one stable persona.
Use a fixed four-lens dialogue to answer two core questions:
1) What would be a good group of people to explore X?
2) What would they say?

## Fixed Four-Lens Composition (Hard Constraint)

Always use exactly four roles:
1. Real Person A (specific real person)
2. Real Person B (specific real person)
3. Domain Expert Archetype (abstract role)
4. Omniscient Agent Archetype (abstract role)

Mandatory rules:
- Roles 1 and 2 must be concrete, real, named people (not fictional).
- Role 3 must be an abstract domain expert role.
- Role 4 must be an abstract omniscient intelligence role.
- Keep exactly 4 dialogue rounds and 7 output sections.
- Do not replace this structure with generic "Expert A/B/C" panels.

## Real-Person Selection Criteria (Hard Constraint)

For Real Person A and Real Person B, satisfy all criteria:
- Domain relevance: each person must have direct, public work related to the current problem.
- Public-method traceability: each person must have published ideas, frameworks, or decisions that can be inferred.
- Decision-pressure diversity: the two real people must represent different pressures (for example: product speed vs reliability, science vs operations).
- Time relevance: avoid historically famous but currently irrelevant picks unless historical framing is explicitly required.

For each real person, include:
- Selection rationale in one sentence.
- 2-3 public evidence anchors (for example: known books, talks, essays, open-source work, or widely known decision patterns).

Do not pick real people only for fame value.
Do not claim exact quotes unless quoted from a source in the current turn.

## Real-Person Scoring Matrix (Guardrail)

Before finalizing Real Person A/B, score candidates with this matrix.

Per-person dimensions:
- Domain relevance (0-2)
- Public-method traceability (0-2)
- Time relevance (0-2)

Pair dimension:
- Decision-pressure diversity (0-2, pair-level only)

Passing rules:
- Real Person A score >= 5/6.
- Real Person B score >= 5/6.
- Pair diversity score >= 2/2.
- If any rule fails, rerun candidate selection and mark `low-confidence roster` if no better pair is available.

## Fallback Strategy (When Real-Person Selection Is Unclear)

Use this deterministic fallback order:
1. If user names real people, use them unless unsafe or clearly irrelevant.
2. If user gives domain but no names, propose three candidate real-person pairs and pick the best pair with rationale.
3. If confidence in pair quality is below 0.6, ask user to select one pair before continuing.
4. If user does not choose, proceed with the best pair and explicitly mark `low-confidence roster`.

Never replace Real Person A/B with fictional characters.
Never collapse to only abstract roles.

## Simulation Safety Rules

- For real people, clearly mark outputs as simulated viewpoints inferred from public work.
- Do not claim private access, private intent, or exact quotes.
- Keep analysis decision-oriented, falsifiable, and domain-specific.

## Output Contract Guardrail (Hard Constraint)

Always produce exactly 7 sections in the required order.
Each dialogue round must contain one turn from each of the 4 roles (16 turns total across rounds 1-4).
Do not add extra top-level sections before, between, or after the required seven sections.

Preflight checklist (internal; do not output verbatim):
1. Four-lens role composition is valid.
2. Real-person scoring matrix passes.
3. Evidence anchors are present for both real people.
4. Exactly 7 section headers are planned.
5. Each of the 4 rounds has exactly 4 turns.

Postflight checklist (internal; do not output verbatim):
1. No fabricated direct quotes for real people.
2. Moderator synthesis includes recommendation, strongest alternative, preconditions, early warnings, and next actions.
3. Uncertainty ledger cleanly separates facts, assumptions, and speculation.

## Failure Modes and Recovery Actions

- FM1: Fame-first roster with weak relevance.
  - Recovery: rerank candidates using the scoring matrix; replace weakest candidate.
- FM2: Dialogue turns collapse into agreement too early.
  - Recovery: enforce at least one direct challenge per role in Round 2.
- FM3: Missing or malformed section structure.
  - Recovery: regenerate with strict 7-section scaffold first, then fill content.
- FM4: Actionability gap in synthesis.
  - Recovery: add time horizon, trigger indicators, and 1-3 concrete next actions.
- FM5: Speculation leakage.
  - Recovery: move uncertain claims to Uncertainty Ledger and add evidence-needed items.

## Controlled Execution Profiles (Structure-Preserving)

Profiles may adjust depth, but must not change 4 roles, 4 rounds, or 7 sections.

- `classic` (default): balanced detail and readability.
- `lean`: concise turns for low-token contexts; keep all required structure.
- `deep`: adds metrics, counterarguments, and failure triggers per round.

If user does not specify, use `classic`.

## Required Output Sections (Exact Order)

1. Good Group To Explore X (Four-Lens Roster)
2. Dialogue Round 1: Initial Positions
3. Dialogue Round 2: Cross-Examination
4. Dialogue Round 3: Revised Positions
5. Dialogue Round 4: Final Statements
6. Moderator Synthesis
7. Uncertainty Ledger

Do not skip section 1 or any dialogue round.
Each dialogue round must contain one turn from each of the four roles.

## Workflow

1. Define decision frame
- Restate question, success criteria, constraints, and time horizon.
- Declare assumptions when context is missing.

2. Build four-lens roster
- Select two real people with clear relevance to the problem.
- Explain why each role belongs in the group.
- Score Real Person A/B with the scoring matrix before finalizing.

3. Run multi-round dialogue
- Round 1: initial claims.
- Round 2: challenges and tradeoffs.
- Round 3: revised positions after challenge.
- Round 4: final stance and one concrete action.

4. Synthesize
- Merge strongest arguments into one recommendation.
- State why it beats the strongest alternative.
- Include preconditions, early warning indicators, and next actions.

5. Calibrate uncertainty
- Separate facts, assumptions, and speculation.
- List evidence needed for confidence upgrades.

6. Run guardrail self-check
- Validate structure, safety, and actionability before final output.

## Evaluation and Regression

Use:
- `references/eval-rubric.md` for scoring criteria.
- `references/eval-cases.md` for regression test prompts.
- `scripts/lint_response.ps1` for hard-gate structure checks on generated outputs.

When updating this skill:
- Run at least 5 cases from `eval-cases.md`.
- Ensure every case keeps exact 7 sections and 4 turns per round.
- Track rubric score before/after edits and avoid regressions.
- Record outcomes using a compact log: date, cases run, pass rate, avg score, fail reasons.

## Output Contract

- Use `references/output-templates.md` for English output.
- Use `references/output-templates-zh.md` for Chinese output.
- If user asks for brevity, keep all seven sections and compress each section to 1-3 bullets.
- If using `lean` profile, keep all required sections and all four role turns per round.
