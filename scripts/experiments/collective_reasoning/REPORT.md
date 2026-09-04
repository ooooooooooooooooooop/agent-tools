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
