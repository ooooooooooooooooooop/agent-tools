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
   - The materiality gate discriminated cleanly: `material=False` on complete
     objective tasks (T1, T2, T6; no over-intervention), `material=True` on all three
     open tasks where the group held shared blind spots.
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

## 2. Trigger Boundaries (When This Runs)

### MUST Trigger On
- Explicit high-stakes decision requests ("decide", "recommend architecture",
  "trade-off analysis", "strategic priority", "diagnose root cause").
- When invoked under `simulate-elite-experts` (or future replacement) in `deep`
  or `thorough` profile.

### MUST NOT Trigger On
- Pure execution tasks (code generation, file edits, git operations, lint fixes,
  mechanical refactoring, script execution).
- Fact lookup, syntax questions, direct queries with unambiguous answers.
- Mid-turn interactive conversational clarifications.
- Any task where the primary answer confidence is marked HIGH and uncertainty
  ledger has no stated empirical dependencies.

---

## 3. Architecture & Data Flow

```text
[User Task]
     │
     ▼
[Primary Reasoning Engine]
  (Can be single-model classic, existing skill, or light ensemble)
     │
     ▼
[Candidate Answer Formed]
  - Decision / Recommendation
  - Stated Key Reasons
  - Declared Assumptions & Remaining Uncertainties
     │
     ▼
┌─────────────────────────────────────────────────────────┐
│ Clean-Context Blind-Spot Search (1 or 2 fresh calls)    │
│                                                         │
│ Input (STRICTLY LIMITED — NO TRANSCRIPT):               │
│   1. Original user prompt                               │
│   2. Candidate decision (terse summary, <=150 words)    │
│   3. Declared assumptions & uncertainties               │
│                                                         │
│ Search prompt targets 5 specific failure modes:         │
│   (a) Hidden / unexamined assumption                    │
│   (b) Wrong framing of the decision space               │
│   (c) Omitted viable alternative                        │
│   (d) Neglected second-order effect                     │
│   (e) A dramatically simpler path                       │
└─────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────┐
│ Materiality Gate (1 cheap utility call)                 │
│                                                         │
│ Evaluates blind-spot outputs against Candidate Answer:  │
│   "Does this raise a MATERIAL consideration that would  │
│    change the decision or its primary risk profile if   │
│    taken seriously? (material = true/false + reason)"   │
└─────────────────────────────────────────────────────────┘
     │
     ├── material == FALSE ──► Deliver Candidate Answer immediately
     │                         (Zero overhead beyond 2-3 audit calls)
     │
     └── material == TRUE  ──► [Single-Shot Re-Entry]
                               Primary engine receives:
                                 Candidate Answer + Blind-Spot Reviewer Critique
                               Produces: Revised Final Answer
                               (Maximum 1 re-entry; no looping)
```

---

## 4. Anti-Patterns (Explicitly Forbidden)

1. **NO 4-model debate by default:** Does not spin up multi-agent conversational
   loops. The baseline is a single primary engine + 1-2 blind reviewers.
2. **NO pre-assigned personas:** Reviewers are NOT assigned thought-role personas
   ("Devil's Advocate", "Skeptical CFO"). They are instructed neutrally:
   `"You are a fresh outside reviewer with no stake in the prior analysis."`
3. **NO transcript leaking:** Reviewers NEVER see the debate/scratchpad that
   produced the candidate answer. They see ONLY the prompt + terse candidate
   verdict + stated assumptions. This preserves the clean-context property that
   made exp1's blind-spot search effective.
4. **NO neutral-scribe rendering:** The final product is ALWAYS delivered by a
   decision synthesizer (actionable, sequenced, committed judgment) with an
   explicit "Unresolved Uncertainties" ledger — never a passive debate summary.
5. **NO option-space drift:** Reviewer prompts explicitly instruct:
   `"If the user prompt restricts the decision to specific alternatives, evaluate `
   `within that space first; if you propose an alternative outside the stated space, `
   `explicitly label it as an OUT-OF-FRAMEWORK option and state why the stated space fails."`
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
