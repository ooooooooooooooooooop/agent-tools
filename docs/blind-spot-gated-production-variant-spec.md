# Blind-Spot Gated Reasoning: Minimal Production Variant Design Spec

Status: **DESIGN SPEC ONLY — DO NOT DEPLOY**
Context: Follows collective-reasoning exp1 + exp1r renderer ablation findings.
Purpose: Capture the evidenced mechanism gain (blind-spot discovery + materiality-gated
re-entry) without the 2.4x token cost, debate protocol complexity, or option-space
drift of full open collective reasoning.

---

## 1. Core Evidentiary Basis (from exp1 & exp1r)

1. **What works with evidence:**
   - Fresh-context blind-spot reviewers surfaced decision-relevant, plausible novel
     insights in 6/6 evaluations across all open tasks (T3-T5).
   - Retrospective concordance on the 6 discovery tasks: `RETROSPECTIVE_CONCORDANCE = 6/6`
     (`material=False` on complete objective tasks T1, T2, T6; `material=True` on open
     tasks T3-T5 where the group held shared blind spots).
   - Generalization to unseen tasks: `HELD_OUT_GENERALIZATION = UNVERIFIED` pending
     dedicated held-out validation on fresh datasets.
   - Gated re-entry drove verifiable stance shifts in 4-5/5 participants per open task.
2. **What does NOT work / is not evidenced:**
   - Full open-ended debate (multi-turn free rounds) added almost nothing: stopping
     evaluators halted T1/T2 after 1 round and T3-T6 after 2 rounds; extra rounds
     mostly rephrased.
   - Neutral transcript rendering systematically lost pairwise actionability ratings.
   - Full collective cost ~2.4x COUNCIL tokens with no pairwise final-quality win.
3. **Design response:** Extract ONLY the blind-spot + materiality gate pattern. Run it
   as a **one-shot, clean-context audit overlay** on any primary reasoning path.

---

## 2. Trigger Boundaries & Task-Phase Gate

The overlay does NOT rely solely on standard Gate 0. It executes a dedicated semantic
**Task-Phase Gate** before any outside review:

### Task Phases
1. **JUDGMENT Phase (Eligible for Blind-Spot Audit):**
   - Active formation, selection, or revision of: judgments, decisions, architectures/schemes,
     causal/root-cause diagnoses, research conclusions, strategic priorities, or trade-offs.
   - High reversibility cost (irreversible or expensive to undo).
   - Significant information ambiguity / competing stakeholder constraints.

2. **EXECUTION Phase (Default Skipped):**
   - Mechanical implementation of an already-decided specification (coding, deployment, file edits,
     renaming, format transforms, running established test suites, standard operations).
   - Fast path: returns `SKIP_EXECUTION_PHASE` immediately with zero reviewer calls.

3. **Execution-to-Judgment Escalation:**
   - If an execution task encounters unexpected empirical evidence that invalidates the original
     premise, goal, architecture, or acceptance criteria (e.g. fatal table lock, memory leak,
     broken invariant), it escalates with `ESCALATE_TO_JUDGMENT` to enter blind-spot review.

---

## 3. Architecture & Data Flow

```text
[User Task]
     │
     ▼
[Task-Phase Classifier] ──► EXECUTION (no blocker) ──► Fast Path: Execute & Deliver (0 calls)
     │
     ▼ (JUDGMENT or ESCALATED)
[Primary Reasoning Engine (Main Model A)]
  (Single-model classic 4-lens deliberation)
     │
     ▼
[Candidate Answer Formed]
     │
     ▼
[DecisionPacket Extractor]
  Extracts canonical bounded brief (<=150 words verdict, <=150 words uncertainties,
  <=200 words rationale, known facts, declared option space).
  *HARD BOUNDARY: Zero transcripts, zero scratchpads, zero model names leaked.*
     │
     ▼
[Heterogeneous Reviewer Resolver] ──► No admitted heterog model ──► Status: HETEROGENEOUS_REVIEW_UNAVAILABLE
     │                                                              (Safe governance fallback; no fake audit)
     ▼ (Heterogeneous Model B, different vendor family)
┌─────────────────────────────────────────────────────────┐
│ Clean-Context Blind-Spot Search (1 fresh call)          │
│ Targets 5 specific failure modes:                       │
│   (a) Hidden/unexamined assumptions                     │
│   (b) Wrong framing of decision space                   │
│   (c) Omitted viable alternatives                       │
│   (d) Neglected second-order effects                    │
│   (e) A dramatically simpler path                       │
│ *Constraint:* Bounded choices must be evaluated first;  │
│ outside choices labeled '[OUT-OF-FRAMEWORK]'.           │
└─────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────┐
│ Materiality Gate (1 fast utility call)                  │
│ Evaluates if critique raises MATERIAL decision-altering │
│ findings not already addressed by Candidate Answer.     │
└─────────────────────────────────────────────────────────┘
     │
     ├── material == FALSE ──► Clean Delivery: Candidate Answer directly (0 process noise)
     │
     └── material == TRUE  ──► [Sovereign Single-Shot Re-Entry]
                               Primary Engine A receives Candidate + Audit Critique.
                               Engine A evaluates with full agency:
                                 - ACCEPT: update recommendation & actions
                                 - PARTIALLY ACCEPT: integrate contingency
                                 - REJECT WITH REASON: defend original decision
                               Produces: Revised Final Answer + Transparent Update Note
```

---

## 4. Canonical Contracts & Anti-Patterns

### Canonical DecisionPacket Contract (Unified)
- `verdict_summary`: Max 150 words / ~1000 characters.
- `core_rationale`: Max 200 words / ~1200 characters.
- `declared_uncertainties`: Max 150 words / ~900 characters.
- `hard_constraints_and_facts`: Max 200 words / ~1200 characters.
- `allowed_option_space`: Explicit list if constrained by prompt (e.g. `["SQS Standard", "SQS FIFO"]`).
- **Strictly Prohibited:** Full debate transcripts, internal scratchpads, persona names/rosters, model vote tallies, majority opinions.

### Option-Space Discipline (Preserving Innovation without Chaos)
- If user prompt restricts choices, reviewer MUST evaluate within bounded space first.
- If reviewer identifies a strictly dominating option outside the set, it MUST label it `[OUT-OF-FRAMEWORK]` and present it as a meta-level challenge rather than an answer substitution.
- Primary engine evaluates the meta-challenge during re-entry.

### Clean User-Facing Delivery
- **No Material Finding:** Deliver candidate answer cleanly with zero internal process clutter.
- **Material Challenge Evaluated but Defended/Rejected:** Append concise 1-sentence note explaining why the original choice stands.
- **Material Challenge Adopted:** Append clear `### Decision Update: Addressed Blind Spot` section stating what was found and why it changed the recommendation.
- Full debug audit ledger is preserved in execution metadata / trace files, never polluting default user experience.

### Anti-Patterns (Explicitly Forbidden)
1. **NO 4-model debate by default:** Does not spin up multi-agent conversational loops.
2. **NO pre-assigned personas:** Reviewers are instructed neutrally with no roleplay.
3. **NO transcript leaking:** Reviewers NEVER see internal scratchpads or dialogue turns.
4. **NO neutral-scribe rendering:** The final product is ALWAYS delivered by a committed decision synthesizer.
5. **NO silent homogeneous fallback:** If a reviewer from a different vendor family is unavailable, output `HETEROGENEOUS_REVIEW_UNAVAILABLE` rather than pretending an echo-chamber review succeeded.
   (Directly addresses the harm observed in T3 where COLLECTIVE dropped CRDTs for
   an unlisted option.)

---

## 5. Token / Cost Budget Comparison

| Configuration | Typical Calls | Est. Prompt Tokens | Est. Comp Tokens | Relative Cost |
|---|---|---|---|---|
| exp1 Full COLLECTIVE | ~25-35 | ~550k (total) | ~470k (total) | **1.0x (baseline: expensive)** |
| exp1 Traditional COUNCIL | ~11 | ~180k | ~240k | ~0.42x |
| **This Blind-Spot Variant (Pass)** | **3-4** | **~15k-25k** | **~4k-8k** | **~0.04x (~96% cheaper)** |
| **This Blind-Spot Variant (Re-entry)** | **5-6** | **~30k-45k** | **~8k-14k** | **~0.08x (~92% cheaper)** |

- When the candidate answer is already sound (expected in ~70% of production queries),
  total cost is 1 primary call + 1-2 blind-spot probes + 1 gate evaluation.
- Re-entry triggers ONLY when genuine material blind spots are surfaced.

---

## 6. Production Integration Path (When Authorized)

When the user decides to graduate this from research to production:
1. Wrap as an opt-in execution profile under `simulate-elite-experts`
   (e.g., `execution_mode: blind-spot-gated`).
2. Implement the 3 prompts (`blindspot_probe`, `materiality_evaluator`,
   `reentry_synthesis`) in a standalone helper script under the skill.
3. Add a verification harness that tests the gate against the exp1 regression
   scenarios (T1/T2/T6 must pass gate=False; T3/T4/T5 must pass gate=True).
4. Do NOT modify default classic profile until the gated profile proves parity
   on execution speed and user satisfaction.
