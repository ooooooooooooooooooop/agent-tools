# Blind-Spot Gated Reasoning: Minimal Production Variant Design Spec

Status: **OPT-IN READY — NOT DEFAULT — DO NOT ENABLE GLOBALLY**
Context: Derived from collective-reasoning exp1, renderer ablation exp1r, held-out acceptance H1-H6, and live heterogeneous-review acceptance.
Purpose: Capture the evidenced mechanism gain (blind-spot discovery + materiality-gated re-entry) without the cost, debate-loop complexity, or option-space drift of full open collective reasoning.

---

## 1. Core Evidentiary Basis

1. **What works with evidence:**
   - Fresh-context blind-spot reviewers surfaced decision-relevant, plausible novel insights in 6/6 judge-task evaluations across open discovery tasks T3-T5.
   - Retrospective concordance on the six discovery tasks is `RETROSPECTIVE_CONCORDANCE = 6/6` (`material=False` on T1/T2/T6; `material=True` on T3/T4/T5).
   - Held-out acceptance H1-H6 completed after the mechanism was frozen. Observed acceptance evidence includes: H3 pure execution skipped 1/1; H2 no-material fast path passed; H5 detected 2/2 planted blind-spot targets; H6 preserved the bounded option space; clean-context leakage was not observed in the held-out cases.
   - These held-out counts are acceptance evidence, not population estimates. Statistical production generalization, real-world blind-spot prevalence, precision, and recall remain **UNVERIFIED** until enough live usage accumulates.
   - Live acceptance exercised the intended path: primary model -> bounded decision packet -> heterogeneous fresh-context reviewer -> materiality decision -> sovereign single re-entry when material.
2. **What is not supported for production:**
   - Full open-ended multi-model debate remains research-only. It costs materially more, showed diminishing returns after early rounds, and did not demonstrate a stable production-quality advantage over the current baseline.
   - Renderer ablation showed a dimension trade-off, not a monotonic upgrade: decision synthesis improved actionability against COUNCIL (R1 4-2) but lost direct comparisons against R0 and CURRENT (2-4 in each matchup). Therefore decision synthesis is a task-dependent output option, not a universal superiority claim.
   - No evidence supports enabling this overlay for pure execution tasks or as a global default.
3. **Design response:**
   - Extract only the high-ROI blind-spot + materiality-gate mechanism.
   - Run it as a one-shot, clean-context audit overlay on an already-formed judgment.
   - Keep default `simulate-elite-experts` behavior unchanged unless the mode is explicitly selected.

---

## 2. Trigger Boundaries & Task-Phase Gate

The overlay does not rely solely on standard Gate 0. It executes a dedicated semantic **Task-Phase Gate** before any outside review.

### Task Phases

1. **JUDGMENT Phase (Eligible for Blind-Spot Audit):**
   - Active formation, selection, or revision of judgments, decisions, architectures/schemes, causal/root-cause diagnoses, research conclusions, strategic priorities, or trade-offs.
   - High reversibility cost or meaningful consequence if wrong.
   - Significant information ambiguity, competing constraints, or unresolved assumptions.

2. **EXECUTION Phase (Default Skipped):**
   - Mechanical implementation of an already-decided specification: coding, deployment, file edits, renaming, format transforms, running established test suites, or standard operations.
   - Fast path returns `SKIP_EXECUTION_PHASE` with zero reviewer calls.

3. **Execution-to-Judgment Escalation:**
   - If execution encounters unexpected empirical evidence that invalidates the original premise, goal, architecture, or acceptance criteria, it may escalate with `ESCALATE_TO_JUDGMENT` and enter blind-spot review.

---

## 3. Architecture & Data Flow

```text
[User Task]
     │
     ▼
[Task-Phase Classifier] ──► EXECUTION ──► Fast Path: Execute & Deliver (0 review calls)
     │
     ▼ JUDGMENT / ESCALATED
[Primary Reasoning Engine (Main Model A)]
     │
     ▼
[Candidate Answer Formed]
     │
     ▼
[DecisionPacket Extractor]
  bounded verdict + rationale + uncertainties + hard facts/constraints + option space
  HARD BOUNDARY: no transcripts, no scratchpads, no model-vote or roster leakage
     │
     ▼
[Heterogeneous Reviewer Resolver]
     ├── no admitted heterogeneous model
     │      └── HETEROGENEOUS_REVIEW_UNAVAILABLE
     │          (safe bypass; never fake an external review)
     ▼ different vendor family
[Clean-Context Blind-Spot Search]
  targets:
  (a) hidden/unexamined assumptions
  (b) wrong framing of the decision space
  (c) omitted viable or dominating alternatives
  (d) neglected second-order effects
  (e) a materially simpler path
  bounded choices are evaluated first; outside options are labeled [OUT-OF-FRAMEWORK]
     │
     ▼
[Materiality Gate]
  asks whether the critique introduces decision-altering content not already addressed
     │
     ├── material == FALSE ──► deliver candidate cleanly
     │
     └── material == TRUE ──► [Sovereign Single-Shot Re-Entry]
                               Main Model A may:
                                 - ACCEPT
                                 - PARTIALLY ACCEPT
                                 - REJECT WITH REASON
                               then produces the final answer
```

---

## 4. Canonical Contracts & Anti-Patterns

### Canonical DecisionPacket Contract

- `verdict_summary`: max 150 words / approximately 1000 characters.
- `core_rationale`: max 200 words / approximately 1200 characters.
- `declared_uncertainties`: max 150 words / approximately 900 characters.
- `hard_constraints_and_facts`: max 200 words / approximately 1200 characters.
- `allowed_option_space`: explicit list when the prompt constrains the answer set.
- Strictly prohibited: full debate transcripts, internal scratchpads, persona names/rosters, model-vote tallies, majority opinions.

### Option-Space Discipline

- If the user restricts choices, the reviewer must evaluate within that bounded space first.
- A potentially dominating outside option may still be surfaced, but only as `[OUT-OF-FRAMEWORK]` meta-level challenge; it must not silently replace the requested answer set.
- The primary engine decides whether the meta-challenge should change the recommendation during re-entry.

### Clean User-Facing Delivery

- **No material finding:** deliver the candidate answer without process clutter.
- **Material challenge evaluated but rejected:** if useful, append one concise sentence explaining why the original judgment stands.
- **Material challenge changes the decision:** clearly state the newly identified blind spot and why it changed the recommendation.
- Full audit detail remains in provenance/debug traces rather than the default user-facing response.

### Anti-Patterns

1. **No multi-model debate loop by default.**
2. **No pre-assigned personas for the external reviewer.**
3. **No transcript/scratchpad leakage to the reviewer.**
4. **No neutral-scribe final output as the only user product.**
5. **No silent homogeneous fallback.** If a different vendor family is unavailable, return `HETEROGENEOUS_REVIEW_UNAVAILABLE` rather than presenting self-review as external review.
6. **No automatic global enablement.** This remains an opt-in mode.

---

## 5. Token / Cost Budget Comparison

The numbers below describe observed or experimentally grounded execution-path magnitudes relative to the expensive exp1 full-collective baseline. They are **not** production-wide savings estimates.

| Configuration | Typical Calls | Est. Prompt Tokens | Est. Completion Tokens | Relative Experimental Cost |
|---|---:|---:|---:|---:|
| exp1 Full COLLECTIVE | ~25-35 | ~550k total | ~470k total | 1.0x |
| exp1 Traditional COUNCIL | ~11 | ~180k | ~240k | ~0.42x |
| Blind-Spot Variant — no material finding | ~3-4 including primary path | ~15k-25k | ~4k-8k | ~0.04x |
| Blind-Spot Variant — re-entry | ~5-6 including primary path | ~30k-45k | ~8k-14k | ~0.08x |

Evidence classification:
- Experimental/acceptance call and token magnitudes: observed or grounded in exp1 and held-out traces.
- Production prevalence of fast-path vs re-entry: **UNVERIFIED**.
- Production-wide percentage savings: **UNVERIFIED** and must not be inferred from the table without live telemetry.

---

## 6. Current Integration & Release State

The production candidate has been implemented and physically validated as an opt-in mode under `simulate-elite-experts`.

Implemented components include:
1. `execution_mode: blind-spot-gated` in the existing skill contract; default classic/one-shot behavior remains unchanged.
2. The task-phase gate, clean DecisionPacket extraction, blind-spot prompt construction, materiality handling, option-space guard, and sovereign re-entry helper in `skills/simulate-elite-experts/scripts/blind_spot_gate.py`.
3. Heterogeneous reviewer selection through the existing Personal AI model registry/routing ownership; no second provider, secret, registry, or fallback stack was added.
4. Unit/regression coverage for the overlay and the collective-reasoning experiment harness.
5. Held-out acceptance H1-H6 and a live end-to-end acceptance drill.

Release posture:
- `DURABLE_OPT_IN_READY = PASS` on the validated feature branch.
- The mode remains **disabled by default**.
- It may be invoked explicitly for high-value judgment-bearing work.
- Full open collective reasoning remains **research-only**.
- Integration into `origin/main` is performed through the repository PR/audit path; no force-push or silent default enablement is permitted.

---

## 7. Ongoing Evidence Discipline

After integration, do not tune the mechanism repeatedly against T1-T6 or H1-H6. Treat those sets as historical evidence/acceptance fixtures.

Future evidence should come from real opt-in usage and new held-out cases, with emphasis on:
- whether the reviewer surfaced a genuinely material blind spot;
- whether the primary model changed its judgment for defensible reasons;
- whether the resulting decision was later judged better;
- incremental calls/tokens and latency;
- false triggers on execution tasks;
- cases where the reviewer was correctly rejected.

Do not promote this mode to an automatic/default path solely from the current small acceptance set.