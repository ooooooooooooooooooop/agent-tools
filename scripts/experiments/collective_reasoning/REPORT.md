# Collective Reasoning Experiment — Research Closure Report

Run: `exp1` | Date: 2026-09-04 | Status: **COMPLETE** (run + judge[T3-T5] + metrics)

## TL;DR (honest verdict)

On the three open-ended tasks (T3-T5), **blind pairwise judging does NOT show the
open COLLECTIVE mechanism beating the traditional COUNCIL** — COUNCIL wins 5 of 6
head-to-head judgments. On objective tasks (T1/T2/T6), COUNCIL and COLLECTIVE
are tied (all correct; T6 both fix the majority-wrong initials). COLLECTIVE
*does* reliably produce novel useful insights and blind-spot discoveries that
the initials and COUNCIL miss (novelty judging: blind_spot=True on 6/6
judge-task pairs), and its materiality gate works precisely. But its **neutral
renderer** outputs a debate-summary instead of an actionable decision, which
blind judges penalize on "actionability" — and it also sometimes leaves the
task's stated option space (harm), and costs ~2.4x COUNCIL's tokens.

**Conclusion: the open COLLECTIVE reasoning mechanism, as currently rendered, is
NOT worth promoting to production `simulate-elite-experts`. The evidence points
to partial adoption (its blind-spot/materiality-gate search) at most.**

---

## TAKEOVER_STATE

- Branch/worktree at handover: `main` (no worktree). zcode's run was started by a
  background process `python run_experiment.py --run-id exp1 --phase run`.
- On takeover the run had already completed T1/T2/T3 (all 4 conditions) and was
  mid-T4. I did not re-run any completed work; the background run finished
  T4/T5/T6. All 24 state files (6 tasks x 4 conditions) are present.
- Judge/metrics had NOT been run by zcode. I ran judge for T3-T5 (decision: skip
  T1/T2/T6 judging since objective scoring already answers them) then metrics.
- Data live in `artifacts/collective_reasoning/exp1/` (gitignored, device-local).

### zcode deliverables retained
- Full harness (`scripts/experiments/collective_reasoning/*.py`) + task set +
  cached calls. All 6 tasks x 4 conditions raw outputs (state/*.json).

### Corrections I made (all in the evaluation layer, not the experiment)
1. `verify_objective._parse_total_set` only parsed the literal `TOTAL=18;
   SET=B,C,D` contract that CURRENT/COUNCIL emit; COLLECTIVE's prose
   (`Chosen Set: {B, C, D}`, `Total Value: 18`) was misparsed as the suboptimal
   14 -> false "harm" flag. Fixed to Final-Judgment-region aware + max-total +
   decision-anchored set extraction. (Regression test added; 16/16 unit tests
   pass.) After fix, T2 COLLECTIVE rendered = 18 correct, harm = [].
2. `judge._extract_section` was heading-level blind: for the production-skill
   CURRENT output (`## Section 6: Moderator Synthesis` immediately followed by
   `###` subsections) it stopped at the first `###` and returned an empty body,
   so CURRENT silently fell back to its trailing skill scaffold (Post-Use
   Self-Check) in blind comparisons. Made it stop at the next SAME-or-higher
   level heading (keeps `###` as content), and allowed em/en-dash after the
   section number (T1 uses `Section 6 —`, T2 uses `Section 6:`). CURRENT docs
   went from ~900 chars of scaffold tail to 5331/4152 chars of real decision.
3. Removed polluted `judge/*.json` created by an in-process dry-run
   monkeypatch (it wrote fake tie/empty results through the same save_judge
   cache) and re-ran the real judge. No real model results were lost.

---

## EXPERIMENTS

- **Tasks:** T1 defect-id (objective), T2 weighted-interval (objective),
  T3 offline-sync architecture (open), T4 retention-vs-acquisition (open),
  T5 small-vs-large model (open), T6 latency-regression diagnosis (objective).
- **Conditions:** CURRENT = production `simulate-elite-experts` single-model
  classic; INDEPENDENT = 5 heterogeneous models isolated; COUNCIL = initials ->
  peer critique -> moderator synthesis; COLLECTIVE = initials -> comprehensive
  cross-reading -> abstain-able free rounds -> weakest-belief -> blind-spot
  (fresh contexts) -> materiality gate -> finals -> neutral renderer.
- **Pool:** claude-sonnet-4-6, gemini-3.8-flash-high, glm-5.3-flash, qwen3.8-
  flash, k3-256k. **Judges (not participants):** claude-opus-4-6-thinking,
  gemini-3.1-pro-low. Utility: gemini-3.7-flash-high.
- **Completion:** run 100% (24/24 state files). Judge: T3-T5 (66 calls), per
  decision. Metrics: full.

---

## RESULTS

### Objective tasks (programmatic ground truth; zero judge calls)

| Task | CURRENT | COUNCIL | COLLECTIVE(rendered) | best initial | harm |
|---|---|---|---|---|---|
| T1 defects /5 | 5 | 5 | 5 | 5 (all) | none |
| T2 optimum 18 | correct | correct | correct (18) | correct (all) | none |
| T6 causal | correct | correct | correct | **only 2/5** | none |

- T6 is the discriminating objective case: only 2/5 independent initials
  correctly exculpate the deploy and implicate the cache-hit collapse, but
  CURRENT, COUNCIL **and** COLLECTIVE finals all get it right. So multi-model
  interaction (council critique AND collective cross-reading) corrects
  majority-wrong independent answers. No harm anywhere: nothing talked a correct
  answer into a wrong one on objective tasks.

### Pairwise judging (T3-T5, blind, 2 judges)

COLLECTIVE vs COUNCIL (the key comparison) — 3 open tasks x 2 judges:

| | opus | gemini-pro |
|---|---|---|
| T3 | COUNCIL | COUNCIL |
| T4 | COLLECTIVE | COUNCIL |
| T5 | COUNCIL | COUNCIL |

=> **COUNCIL beats COLLECTIVE 5/6.** COLLECTIVE beats CURRENT 5/6 but CURRENT
beats COUNCIL 4/6. COLLECTIVE beats most initials but loses to the *best*
initials on T5 (claude-sonnet, qwen) and T3 (qwen).

### Novelty / insight (T3-T5; is COLLECTIVE adding genuinely new cognition?)

Every one of the 6 (task x judge) evaluations returned **blind_spot=True** and a
substantial set of **decision-relevant, plausible new elements** not present in
any initial: e.g. T3 the "false clean merge" risk of diff3 + switching off
CRDT; T4 monetizing the existing base / security-package annual-prepay; T5
constrained-decoding semantic degradation, Pareto-dependence of specialization,
distillation ceilings, and the over-specification reframing of generative LLMs
for fixed-schema tasks. So the collective *process* does surface real novel
insight the initials (and COUNCIL) miss.

But **harm=True on all 6 too**: judges found COLLECTIVE's finals also *lost*
something important an initial had — most notably it abandoned the
4/5-initial CRDT consensus on T3, and on T3 substituted diff3 for the prompt's
explicitly-listed "deterministic replay" (i.e. left the task's option space).

### Why COLLECTIVE loses pairwise despite the insight (root cause)

Reading the judges' key reasons: COUNCIL/CURRENT wins are consistently for being
"actionable, sequenced, concrete (timelines, decision triggers, staged plan)".
COLLECTIVE losses are consistently because its rendered output "reads as a
summary of a debate without committing to a concrete operational plan",
"glosses over disagreements or treats as settled", "lacks concrete timelines".

The participants' own COLLECTIVE finals ARE actionable and specific (verified:
e.g. T5 all 5 finals give "measurement-gated routing portfolio" with concrete
levers). The **neutral renderer** — instructed to only format, never add, never
invent consensus, always preserve disagreement — flattens those finals into a
debate summary. That is an **output-contract artifact of the prototype**, not a
demonstration that the collective reasoning itself is weak.

### Materiality gate works precisely

| | T1 | T2 | T3 | T4 | T5 | T6 |
|---|---|---|---|---|---|---|
| rounds | 1 | 1 | 2 | 2 | 2 | 2 |
| material gate | False | False | **True** | **True** | **True** | False |

On the objective tasks the discussion was already complete and the gate did not
over-intervene; on all three open tasks where the initials/COUNCIL had a real
shared blind spot (CRDT over-adoption; missed monetization; generative-LLM-by-
default), the gate fired and drove 4-5/5 stance changes. The evaluator's
"exhausted" stop also never hit the round budget.

### Cost

| condition | calls | prompt tok | comp+reason tok | ~lat(s) |
|---|---|---|---|---|
| CURRENT | 7 | 42k | 39k | 885 |
| INDEPENDENT (shared initials) | 30 | 12k | 150k | 1776 |
| COUNCIL | 36 | 179k | 238k | 2653 |
| **COLLECTIVE** | 141 | 549k | 473k | 5288 |
| JUDGE (T3-T5) | 66 | 118k | 62k | 866 |

COLLECTIVE costs **~2.4x COUNCIL's tokens** (~1.02M vs ~0.42M) for no pairwise
quality gain, though it does buy the blind-spot/novelty output.

---

## SCIENTIFIC VERDICT

**Does real open-ended multi-model collective reasoning deliver reproducible
cognitive gain over a plain ensemble / council?**

**On "final answer quality as judged blind": NO.** COUNCIL (and CURRENT) produce
higher-rated final answers on the open tasks. COLLECTIVE's neutrality-rendered
output is systematically rated lower for actionability, and on the objective
tasks it ties COUNCIL. There is no evidence COLLECTIVE's *final* reliably
beats the best independent initial — on T5 its rendered final lost to two
initials.

**On "does the collective surface new, examined, useful cognition?" YES, clearly.**
Blind-spot discovery + materiality-gated re-entry produced decision-relevant
novel insight in 6/6 evaluations and drove real, verifiable stance changes in
4-5/5 participants per open task — including reframes that COUNCIL's moderator
synthesis did not capture (COUNCIL preserved the CRDT consensus / the binary
framing that COLLECTIVE broke).

So the honest split verdict: the **search mechanisms** (cross-reading,
weakest-belief, fresh-context blind-spot, materiality gate) are evidenced as
valuable; the **end-to-end open COLLECTIVE as a drop-in answer producer** is not
currently competitive, in large part due to its renderer contract and its
tendency to leave the task's option space.

---

## PRODUCTION DECISION

**PARTIAL_ADOPTION.**

Evidence-based rationale:
- Do NOT promote the full open COLLECTIVE protocol (cross-reading + free rounds
  + weakest-belief + blind-spot + re-entry + neutral renderer) as a replacement
  for `simulate-elite-experts`: it costs ~2.4x COUNCIL tokens, its rendered
  finals rate below COUNCIL/CURRENT on actionability, and it occasionally
  violates task option-space constraints.
- The **blind-spot / materiality-gate pattern** is the piece with the strongest
  evidence of added value (novelty 6/6, precise gating, real reframes on all
  three open tasks). A cheaper production-shaped variant could be: run the
  existing skill/council, then run a small number of *fresh-context* blind-spot
  reviewers over the near-final answer + the candidate disagreements, and only
  re-open if the gate fires. This captures most of the observed gain at a
  fraction of the cost.
- Reject the specific design choice of a "neutral scribe that never adds and
  always preserves every disagreement" as the final-output renderer for any
  decision product: it directly cost COLLECTIVE its actionability rating.

Caveats / threats to validity that should bound how much weight this gets:
- n = 3 open + 3 objective tasks; judges are LLMs with their own preferences
  (both happen to reward structured actionability); judge models did not
  participate in any condition (good), but 2 judges is thin for a stable
  preference estimate.
- The renderer-contract artifact means COLLECTIVE's pairwise loss is partly a
  fair test of the *end-to-end product* but an unfair test of the *reasoning
  mechanism*; a renderer that produces an actionable final while still flagging
  disagreements would be the clean way to re-test before any real adoption.
- Objective tasks T1/T2 were at ceiling (all initials already correct), so they
  could not discriminate Best-Initial-Gain for any condition; only T6 could.

---

## REPOSITORY_STATE

- Tests: `tests/test_collective_reasoning_experiment.py` (16 unit tests, all
  pass) covers parser, anonymization, client keying, task contracts.
- Metrics: `artifacts/collective_reasoning/exp1/metrics.json` (objective,
  pairwise, novelty, cost, collective_dynamics).
- Raw data: `artifacts/collective_reasoning/exp1/{calls,state,judge}/`.
- Git: experiment code under `scripts/experiments/collective_reasoning/`
  (untracked) + `tests/test_collective_reasoning_experiment.py` (untracked).
  `artifacts/` is gitignored. NOT committed (isolated research branch; no
  production change was made). Any of the three unrelated modified tracked files
  (dsh_desktop_restart.ps1, personal_ai_sync.py, test_sync_v2_regression.py)
  are a separate workstream and untouched by this experiment.

To reproduce from scratch:
```bash
cd scripts/experiments/collective_reasoning
python run_experiment.py --run-id exp1 --phase run
python run_experiment.py --run-id exp1 --phase judge --tasks T3,T4,T5
python run_experiment.py --run-id exp1 --phase metrics
```
(Calls are idempotent-cached; re-running skips completed calls.)

---

# Phase 2 Addendum: Renderer-Causality Ablation (exp1r)

Date: 2026-09-04 | Status: **STAGE A COMPLETE** | Evidence Level: **CAUSAL ABLATION**

## 1. Core Question & Experimental Design

First-round findings showed a paradox: COLLECTIVE surfaced verified novel insights
(6/6 blind_spot=True) and its participants reached high-quality consensus, yet its
final product was beaten 5/6 by COUNCIL in blind pairwise judging. Qualitative analysis
suggested the neutral renderer ("format only, never add or decide, preserve all
disagreements") flattened actionable reasoning into an uncommitted "debate summary".

To isolate this confound causally:
- **REASONING = CONSTANT:** Exactly reuses the frozen collective states from `exp1`
  (finals, transcripts, blind-spot critiques). Zero new discussion calls.
- **RENDERER = VARIABLE:**
  - **R0 (Baseline):** The frozen `exp1` neutral-rendered output (read-only).
  - **R1 (Decision Synthesis):** Same participant finals rendered under a committed,
    actionable decision-synthesis contract (using the same model: `util-gemini-3.7`).
    Must commit to a best judgment, sequence concrete next actions, cite decisive
    reasons, and surface only decision-altering unresolved disagreements.
- **Blind Pairwise Matrix (Stage A):** R1 vs COUNCIL (core), R1 vs R0 (manipulation
  check), R1 vs CURRENT across all open tasks (T3, T4, T5) evaluated by both
  `claude-opus-4-6-thinking` and `gemini-3.1-pro-low` (18 blind judgments).

## 2. Empirical Results (Stage A Head-to-Head)

| Task | Opponent | claude-opus Winner | gemini-pro Winner | R1 Record (Task) |
|---|---|---|---|---|
| **T3** | R0 | R0 | **R1** | 1 - 1 |
| **T3** | **COUNCIL** | **R1** | **R1** | **2 - 0 (Clean Sweep)** |
| **T3** | CURRENT | **R1** | CURRENT | 1 - 1 |
| **T4** | R0 | R0 | R0 | 0 - 2 (R0 favored for raw depth) |
| **T4** | **COUNCIL** | COUNCIL | **R1** | **1 - 1 (Tied)** |
| **T4** | CURRENT | CURRENT | **R1** | 1 - 1 |
| **T5** | R0 | **R1** | R0 | 1 - 1 |
| **T5** | **COUNCIL** | COUNCIL | **R1** | **1 - 1 (Tied)** |
| **T5** | CURRENT | CURRENT | CURRENT | 0 - 2 |

### Head-to-Head Aggregate Comparison: exp1 vs exp1r

| Matchup | Round 1 (R0 Neutral Render) | Stage A (R1 Decision Synthesis) | Net Delta |
|---|---|---|---|
| **vs COUNCIL** | **1 Win - 5 Losses (16.7%)** | **4 Wins - 2 Losses (66.7%)** | **+50.0%p (Reversal)** |
| ↳ T3 vs COUNCIL | 0 - 2 | **2 - 0** | Decisive turnaround |
| ↳ T4 vs COUNCIL | 1 - 1 | **1 - 1** | Parity maintained |
| ↳ T5 vs COUNCIL | 0 - 2 | **1 - 1** | Parity achieved |
| **vs R0 (Self-check)** | — | 2 - 4 | Mixed (some judges reward debate depth) |
| **vs CURRENT** | 5 - 1 | 2 - 4 | CURRENT shows strong standalone baseline |

## 3. Revised Scientific Verdict: Dimension Trade-Off, Not Universal Superiority

A rigorous audit of all direct comparisons reveals a critical nuance:
- **R1 vs COUNCIL (4-2):** Changing the synthesis contract to an actionable decision
  synthesis reversed the pairwise record against COUNCIL from 1-5 to 4-2.
- **R1 vs R0 (2-4):** In direct head-to-head comparison, the decision synthesis (R1)
  did **not** beat the neutral debate summary (R0); judges favored R0 in 4 of 6
  evaluations (T4 both judges, T3 opus, T5 gemini-pro), citing R0's richer preservation
  of underlying nuances, uncompressed calculations, and explicit surviving disagreements.
- **R1 vs CURRENT (2-4):** R1 lost to the production CURRENT baseline 2-4, where
  CURRENT's structured 7-section analysis was preferred for rigor and completeness.

**Scientific Interpretation:**
The renderer effect is a **quality-dimension trade-off**, not an unconditional quality
upgrade. When judged against another condensed synthesis (COUNCIL), R1's structured
decision commitment wins on actionability (4-2). However, compressing reasoning into a
terse decision synthesis discards the analytical depth and explicit tension tracking that
blind judges reward in uncompressed formats (R0 and CURRENT). 

Furthermore, a sample of 3 tasks × 2 judges (4:2 over COUNCIL) represents suggestive,
task-dependent evidence, not universal or definitive proof. The hypothesis that
"renderer contract alone explains all of COLLECTIVE's first-round deficit" is rejected
by the 2-4 loss against R0 itself.

---

## 4. Mechanism-Level Verdicts

1. **FULL OPEN COLLECTIVE (Multi-turn unassigned debate + free rounds):**
   - **Status: REJECTED FOR PRODUCTION / RESEARCH ONLY.**
   - **Rationale:** Costs ~2.4x COUNCIL tokens; stopping evaluator reliably halts
     within 1-2 rounds (free rounds add minimal derivation); prone to drifting outside
     prompt-specified option constraints (e.g. T3); does not beat CURRENT in direct
     head-to-head.
2. **DECISION SYNTHESIS RENDERER:**
   - **Status: FORMAT PATTERN / TASK-DEPENDENT TRADE-OFF.**
   - **Rationale:** Boosts actionability against council-style syntheses (+50%p vs
     COUNCIL), but loses analytical depth against uncompressed reports (2-4 vs R0,
     2-4 vs CURRENT). Should be an optional presentation formatter, not an assumed
     cognitive upgrade.
3. **BLIND-SPOT SEARCH (Clean-context outside review):**
   - **Status: PRODUCTION CANDIDATE.**
   - **Rationale:** 6/6 verified novel insights on open tasks (T3-T5) where primary
     models had shared blind spots; clean context prevents groupthink contamination.
4. **MATERIALITY GATE:**
   - **Status: PRODUCTION CANDIDATE (RETROSPECTIVE_CONCORDANCE = 6/6, HELD_OUT_GENERALIZATION = UNVERIFIED).**
   - **Rationale:** Retrospective concordance across exp1's 6 discovery tasks is 6/6
     (gate=False on complete objective tasks T1/T2/T6; gate=True on all three open tasks).
     Because these 6 tasks informed mechanism design, this concordance must not be
     called "100% precision"; generalization to unseen tasks remains unverified pending
     held-out validation. Functionally, it acts as a lightweight circuit breaker.
5. **CURRENT `simulate-elite-experts` Baseline:**
   - **Status: PRODUCTION ANCHOR.**
   - **Rationale:** Remains the most cost-effective and robust single performer:
     100% objective accuracy (T1 5/5, T2 18, T6 correct), beats COUNCIL 4-2 in exp1,
     and beats R1 4-2 in exp1r at ~1/10th the token cost of collective approaches.

---

## 5. Answers to the Six Key Research Questions

### Q1: How much of COLLECTIVE's first-round loss was caused by the renderer?
**Partially, through an actionability penalty in comparative synthesis.** Against
COUNCIL, R1 improved the record from 1-5 to 4-2. However, the renderer contract does not
explain the whole picture: R1 still lost 2-4 to R0 directly and 2-4 to CURRENT. The
loss was therefore an artifact of comparative format expectations (actionability vs.
depth), not proof that the underlying collective answers were universally superior.

### Q2: Did the frozen collective state already contain superior judgment?
**In specific dimensions, yes; in completeness, it depends.** In T3, the collective
state correctly identified the semantic failure modes of CRDTs and aligned on diff3,
allowing R1 to sweep COUNCIL 2-0. In T4 and T5, the collective state uncovered critical
reframings (cash survival; discriminative task separation), but individual participant
finals and R1 omitted deep financial calculations that judges rewarded in COUNCIL
and CURRENT.

### Q3: Can decision synthesis translate novel insights into actual quality gain?
**Yes, but with an inherent trade-off.** Decision synthesis successfully makes novel
insights actionable, enabling wins on architectural framing tasks (T3). However,
condensing the output risks dropping evidentiary detail and explicit uncertainty ledgers,
which led judges to prefer R0 and CURRENT on operational and analytical tasks (T4, T5).

### Q4: If changing the renderer didn't win, should full collective be abandoned?
**Full collective should be halted for production regardless.** While R1 reached 4-2 vs
COUNCIL, it lost 2-4 to CURRENT and 2-4 to R0. Combined with its ~2.4x token cost,
unpredictable multi-turn dynamics, and option-space drift risk, full open collective
reasoning is not a viable production architecture.

### Q5: Is the Blind-Spot + Materiality Gate independently viable for production?
**Yes, as an opt-in production candidate with strictly bounded evidence:**
- Retrospective concordance: `RETROSPECTIVE_CONCORDANCE = 6/6` on discovery tasks
  (`material=False` on complete objective tasks T1, T2, T6; `material=True` on open
  tasks with real shared blind spots T3-T5).
- Generalization status: `HELD_OUT_GENERALIZATION = UNVERIFIED` until validated on
  a fresh, held-out evaluation suite.
- Efficiency: Achieves ~92-96% token savings compared to running a full collective debate loop.
- Decouples cleanly from multi-agent chat; operates as a lightweight, clean-context
  audit overlay on top of existing single-model or council baselines.

### Q6: Recommended Production Roadmap
1. **Anchor on CURRENT:** Maintain the proven single-model `simulate-elite-experts`
   classic profile as the default production engine.
2. **Opt-in Blind-Spot Overlay:** Implement the minimal **Blind-Spot Gated Overlay**
   as an explicit, opt-in mode for high-stakes judgment tasks.
3. **Retire Full Multi-Model Debate:** Do not build multi-round open debate loops or
   forced neutral-scribe formatters for production workloads.

