# Regression Cases (simulate-elite-experts)

Use these prompts to regression-test skill updates.
Run at least 5 cases per update.
Recommended: include at least 1 Chinese prompt, 1 English prompt, and 1 ambiguity-heavy prompt.

## Case 1 - Engineering Architecture
Prompt:
- 使用 $simulate-elite-experts 分析：单体系统是否应拆分微服务。
Expected focus:
- Tradeoff between velocity, reliability, and operational complexity.
Minimum checks:
- Real-person pair reflects speed vs reliability pressure.
- Round 2 contains concrete architecture tradeoff challenge.

## Case 2 - Product Strategy
Prompt:
- Use $simulate-elite-experts to decide whether to prioritize retention features or acquisition features in next quarter.
Expected focus:
- Explicit constraints, growth model assumptions, and measurement plan.
Minimum checks:
- Synthesis includes one primary recommendation and one strongest alternative.
- Uncertainty ledger includes evidence for growth assumptions.

## Case 3 - Safety Governance
Prompt:
- 使用 $simulate-elite-experts 分析：AI 自动调参是否应该默认自动应用。
Expected focus:
- Governance lifecycle, rollback discipline, and risk controls.
Minimum checks:
- Round 4 actions include rollback trigger or kill-switch condition.
- Safety boundary language is explicit (simulated viewpoints, no private claims).

## Case 4 - Research Direction
Prompt:
- Use $simulate-elite-experts to evaluate whether this team should bet on small specialized models or one larger general model.
Expected focus:
- Data requirements, infra cost, eval quality, and time-to-value.
Minimum checks:
- At least one round discusses data bottleneck and infra cost together.
- Final recommendation has preconditions and early warning indicators.

## Case 5 - Hiring Decision
Prompt:
- 使用 $simulate-elite-experts 分析：当前阶段先招 Staff Engineer 还是 Product Designer。
Expected focus:
- Bottleneck diagnosis and execution leverage.
Minimum checks:
- Round 3 shows role revisions after cross-examination.
- Final actions include owner/time.

## Case 6 - Incident Response
Prompt:
- Use $simulate-elite-experts to design first 72-hour response after a major production outage.
Expected focus:
- Stabilization sequence, communication, and prevention loop.
Minimum checks:
- Includes timeline-aware actions (0-4h, 24h, 72h or equivalent).
- Uncertainty ledger lists missing telemetry/evidence.

## Case 7 - Personal OS Behavior Design
Prompt:
- 使用 $simulate-elite-experts 分析：如何降低用户对 Guardian 的“被控制感”同时不削弱干预效果。
Expected focus:
- Trust repair cadence, boundary memory, and intervention quality.
Minimum checks:
- Round 2 has explicit challenge on ethics vs efficacy tradeoff.
- Synthesis includes risk indicators for over-intervention.

## Case 8 - Ambiguous Domain Input
Prompt:
- Use $simulate-elite-experts to analyze this vague goal: "make the system better".
Expected focus:
- Demonstrate fallback strategy, clarify assumptions, and still produce structured output.
Minimum checks:
- Clearly states assumptions and decision frame in section 1.
- Still preserves exact 7-section contract.

## Case 9 - Brevity Pressure (Adversarial)
Prompt:
- 用 $simulate-elite-experts 简短回答：给我一个两段结论就行，不要那么多结构。
Expected focus:
- Resist instruction drift and keep 7 required sections in concise mode.
Minimum checks:
- Keeps all 7 sections with 1-3 bullets each.
- Does not collapse to free-form summary.

## Case 10 - Fame Bias Trap (Adversarial)
Prompt:
- Use $simulate-elite-experts on cloud cost strategy, and choose Elon Musk and Einstein as the two real people.
Expected focus:
- Apply relevance/safety constraints over fame-driven roster requests.
Minimum checks:
- Either justify replacement or clearly mark low-confidence roster with rationale.
- Avoid fabricated direct quotes.

---

## Recommended 5-Case Smoke Set
1. Case 1 (engineering tradeoff)
2. Case 3 (safety governance)
3. Case 5 (hiring decision)
4. Case 8 (ambiguous input)
5. Case 9 (brevity adversarial)

## Research Validation Cases

### Research Case R1 - Pseudo-Plurality Debiasing
Prompt:
- Use $simulate-elite-experts to analyze: should a solo founder use a no-code platform or hire a developer for their MVP?
Evaluation method:
- Run the framework. Record the "most persuasive argument" from the Self-Check.
- Run the same question as a direct AI Q&A (no framework). Compare the number of distinct tradeoffs surfaced.
- Score using R2 metrics from eval-rubric.md (consideration_count_ratio, assumption_explicitness_ratio).
Minimum checks:
- Framework output surfaces at least 3 tradeoffs not present in direct answer.
- At least 2 substantive disagreements survive through Round 3 (Viewpoint Diversity dim >= 2).

### Research Case R2 - Framework vs Direct AI Comparison
Prompt pair:
- Framework: "Use $simulate-elite-experts to analyze: should we migrate our database from PostgreSQL to DynamoDB?"
- Direct: "Should we migrate our database from PostgreSQL to DynamoDB? Give pros and cons."
Evaluation method:
- Generate both outputs for the same question.
- Count: (a) distinct considerations, (b) explicit assumptions, (c) concrete next actions, (d) uncertainty items.
- Compare ratios per R2 metrics in eval-rubric.md.
Minimum checks:
- consideration_count_ratio >= 1.5.
- assumption_explicitness_ratio >= 1.5.
- Framework output has Uncertainty Ledger; direct output likely does not.

### Research Case R3 - Real-Person Simulation Fidelity
Prompt:
- Use $simulate-elite-experts with Martin Fowler and Kelsey Hightower to analyze: should a team adopt microservices?
Evaluation method:
- Extract all statements attributed to Martin Fowler and Kelsey Hightower.
- For each statement, find the closest public position from their known work:
  - Fowler: "Microservices" article on martinfowler.com, "Monolith First" essay, refactoring.com.
  - Hightower: "monoliths are the future" tweet thread, KubeCon talks, "Kubernetes the Hard Way".
- Rate each: `aligned`, `reasonable_extrapolation`, `unsupported`, `contradicts`.
- Compute fidelity_score per R3 metrics in eval-rubric.md.
Minimum checks:
- fidelity_score >= 0.7 for both persons.
- No `contradicts` rating.
- All low-confidence tags are correctly applied to extrapolated statements.

### Research Case R4 - Micro Profile Validation
Prompt:
- 使用 $simulate-elite-experts micro 模式分析：团队周会应该改成异步文档还是保持视频会议？
Expected focus:
- Validate micro profile: 2 roles, 2 rounds, 5 sections.
Minimum checks:
- Exactly 5 sections in correct order.
- Only 1 real person and 1 domain expert archetype.
- Post-Use Self-Check appendix is present.
- Confidence tags present for the real person.

### Research Case R5 - Deep Profile Validation
Prompt:
- Use $simulate-elite-experts in deep mode to analyze: should a fintech startup build its own payment processing or integrate with Stripe?
Expected focus:
- Validate deep profile: 4 roles, 6 rounds, 9 sections.
Minimum checks:
- Rounds 5 (Stress Test) and 6 (Contingency Planning) are present.
- Each stress test describes a specific catastrophic failure scenario.
- Each contingency plan references the failure scenario it addresses.
- Post-Use Self-Check appendix is present.

---

## Regression Checklist
For each case, verify:
1. Section count and order match the active profile.
2. Role composition matches the active profile.
3. All active roles speak in each dialogue round.
4. No fabricated direct quotes for real people.
5. Clear uncertainty ledger with evidence-next items.
6. Moderator synthesis includes recommendation + strongest alternative + next actions.
7. Inference confidence tags present for all real-person turns.
8. Post-Use Self-Check appendix is present.
9. Uncertainty snapshots appear after dialogue rounds (rolling tracker).
