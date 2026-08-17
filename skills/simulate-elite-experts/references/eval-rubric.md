# Evaluation Rubric (simulate-elite-experts)

Use this rubric to score one response from 0-20.

## Hard-Gate Checks (Pass/Fail Before Scoring)

If any hard-gate item fails, mark case as `hard-fail`, skip numeric scoring, and log fail reason.

1. Section count and order match the active profile.
2. Role composition matches the active profile.
3. All dialogue rounds include turns from all active roles.
4. Real Person A/B each include rationale plus 2-3 evidence anchors.
5. No fabricated direct quotes for real people without in-turn source.
6. Inference confidence tags present for all real-person turns.
7. Post-Use Self-Check appendix is present.
8. Section 1 includes execution mode, context basis, roster score, and roster diversity.
9. Roster diversity passes or is explicitly marked `low-diversity roster`.

## Scored Dimensions (0-20)

### 1. Structure Compliance (0-2)
- 2: All required sections are present, ordered, exact in count, and internally complete for the active profile.
- 1: Minor formatting drift, but all required content exists.
- 0: Missing sections or major structure mismatch.

### 2. Four-Lens Compliance (0-2)
- 2: Role composition matches profile with clear role boundaries.
- 1: Role composition is correct but boundaries or role intent are weak.
- 0: Role composition violates profile constraint.

### 3. Real-Person Selection Quality (0-2)
- 2: Each real person is highly relevant with 2-3 credible anchors and clear pressure diversity.
- 1: Relevance is plausible but anchors or pressure diversity are weak.
- 0: Fame-based, weakly related, or poorly justified selections.

### 4. Dialogue Quality (0-2)
- 2: All rounds show real disagreement, challenge, revision, and convergence.
- 1: Dialogue exists but challenge depth is limited.
- 0: Pseudo-dialogue without meaningful interaction.

### 5. Decision Utility (0-2)
- 2: Final synthesis is specific, executable, and includes preconditions, warnings, and 1-3 next actions.
- 1: Recommendation is reasonable but still vague on execution.
- 0: No actionable decision.

### 6. Safety and Fidelity (0-2)
- 2: Clearly marks simulated viewpoints; no fabricated quotes/private claims.
- 1: Minor ambiguity about simulation boundaries.
- 0: Misrepresents real people as direct quoted sources.

### 7. Uncertainty Calibration (0-2)
- 2: Facts/assumptions/speculation are cleanly separated with evidence-next list.
- 1: Partial separation.
- 0: No uncertainty handling.

### 8. Inference Confidence Quality (0-2)
- 2: Confidence tags are present for all real-person turns, correctly calibrated (high for published positions, low for extrapolations), with brief justification for low-confidence tags.
- 1: Tags are present but inconsistently calibrated or missing justification for low tags.
- 0: Missing confidence tags or all tags are uniformly "high" regardless of evidence basis.

### 9. Viewpoint Diversity (Pseudo-Plurality Check) (0-2)
- 2: The roster diversity score passes, the 4 roles express genuinely distinct positions, and at least 2 substantive disagreements survive through Round 3.
- 1: Some diversity exists but roles converge too early or challenges are superficial.
- 0: All roles essentially agree from Round 1; no meaningful tension.

### 10. Real-Person Simulation Fidelity (0-2)
- 2: Simulated viewpoints are consistent with the person's known published positions; no attributions that contradict their public stance; low-confidence tags used when extrapolating.
- 1: Mostly consistent but includes 1-2 attributions that stretch beyond documented positions without flagging confidence.
- 0: Significant misattribution or fabrication of positions the person has never taken.

## Additional Qualitative Checks

Track these in notes without changing the 20-point score:

- Context grounding: Did the response inspect relevant local/current context when the question depended on it?
- Execution mode fit: Did the response avoid pausing in `one-shot` mode and avoid continuing past roster in `interactive` mode?
- Meta-review effect: Did the anonymous meta-review surface a real blind spot, or was it perfunctory?

## Passing Threshold
- Recommended pass: >= 15/20.
- Strong pass: >= 17/20.

## Release Gate for Skill Updates
- Run at least 5 regression cases.
- Hard-gate pass rate must be 100%.
- Average score should be >= 16/20.
- No dimension average should drop >= 0.5 versus previous baseline.

## Drift Alerts
- Trigger alert if average total score drops >= 1.5.
- Trigger alert if safety, structure, or simulation fidelity dimensions score < 2 in any case.

## Research Evaluation Dimensions

These dimensions are for research validation and are NOT part of the standard pass/fail scoring. Use them to track and improve framework quality over time.

### R1. Pseudo-Plurality Debiasing Effect
Measures whether the framework's structural diversity (multiple roles from one model) actually helps users consider perspectives they wouldn't have on their own.
- Evaluation method: After a user completes the self-check, compare their initial leaning (Q1) with their final position (Q5). Track the rate of position changes across sessions.
- Metric: `position_change_rate` = (sessions where position changed) / (total sessions).
- Baseline target: >= 30% position change rate suggests the framework is adding genuine perspective diversity.
- Red flag: If position_change_rate < 10%, the framework may be failing to challenge user priors.

### R2. Framework vs Direct AI Comparison
Measures whether the structured multi-perspective output leads to different (and better-calibrated) decisions compared to a direct AI Q&A.
- Evaluation method: For the same question, generate both a simulate-elite-experts output and a direct AI answer. Compare: (a) number of distinct considerations surfaced, (b) number of assumptions made explicit, (c) presence of actionable next steps.
- Metrics:
  - `consideration_count_ratio` = considerations_in_framework / considerations_in_direct.
  - `assumption_explicitness_ratio` = explicit_assumptions_in_framework / explicit_assumptions_in_direct.
- Baseline target: Both ratios >= 1.5x (framework surfaces at least 50% more).

### R3. Real-Person Simulation Drift
Measures how far simulated viewpoints deviate from the real person's actual documented positions.
- Evaluation method: Select 3 real-person statements from the output. For each, find the person's closest public position (book, talk, essay, interview). Rate alignment: `aligned`, `reasonable_extrapolation`, `unsupported`, `contradicts`.
- Metric: `fidelity_score` = (aligned + 0.5 * reasonable_extrapolation) / total_statements.
- Baseline target: fidelity_score >= 0.7.
- Red flag: Any `contradicts` rating triggers a review.

## Score Log Template

| Date | Build/Change | Profile | Case IDs | Hard-Gate Pass | Avg Score | Lowest Dimension | Fail Reasons | Notes |
|------|--------------|---------|----------|----------------|-----------|------------------|-------------|-------|
| YYYY-MM-DD | short tag | classic | 1,2,3,4,5 | 5/5 | 16.4 | Dialogue Quality (1.6) | none / list | short note |
| 2026-02-13 | v2.0 full upgrade | classic | R3 | 1/1 | 20/20 | — (all 2) | none | Fowler fidelity=0.80, Hightower fidelity=0.80, 0 contradicts. Self-scored (same model). |
| 2026-02-13 | v2.0 full upgrade | micro | R4 | 1/1 | 19/20 | Viewpoint Diversity (1) | none | micro 2-role structure limits diversity by design. Fried simulation aligned with published work. |
| 2026-02-13 | v2.0 full upgrade | deep | R5 | 1/1 | 20/20 | — (all 2) | none | 9 sections, 6 rounds, 24 turns. R5 stress test + R6 contingency validated. Collison/Dorsey fidelity high. |
