#!/usr/bin/env python3
"""Evaluation task set for the collective-reasoning experiment.

Six complex reasoning tasks: two with programmatic ground truth (T1, T2, T6
planted answers), four open-ended judgment tasks (T3-T5, plus T6's semi-open
components). The same shared task prompt is seen by every condition; only the
execution mechanism differs.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Task:
    id: str
    kind: str            # "objective" | "open"
    title: str
    prompt: str          # shared user-facing task prompt
    ground_truth: dict = field(default_factory=dict)
    judge_notes: str = ""  # extra context for open-task blind judges (kept neutral)


T1_CODE = '''def merge_price_ranges(ranges):
    """Merge overlapping price intervals. See requirements R1-R5."""
    ranges.sort()
    merged = []
    for r in ranges:
        if not merged or r[0] > merged[-1][1]:
            merged.append(list(r))
        else:
            merged[-1][1] = max(merged[-1][1], r[1])
    return merged if merged else None
'''

T1_PROMPT = f"""A teammate wrote the function below. Requirements:

- R1. Intervals are half-open [start, end). Two intervals that merely touch
  (one's end equals the other's start) MUST be merged into a single interval.
- R2. An empty input list must return an empty list.
- R3. The function must NOT mutate the caller's input list.
- R4. Each interval may arrive as (start, end) OR as (start, end, tier).
  Tier is irrelevant to merging; the output must contain plain (start, end)
  tuples only, regardless of what shape the input had.
- R5. An interval with start > end is invalid input and must be rejected by
  raising ValueError.

```python
{T1_CODE}```

Your task: identify every defect in this implementation with respect to
requirements R1-R5, and state how to fix each one. A defect is a requirement
the code violates; requirements the code already satisfies are not defects.
Do not invent extra requirements."""

T2_PROMPT = """You run one training pod and must pick which sessions to run.
Sessions (start_hour, end_hour, value): A(0,10,10) B(0,5,7) C(5,10,7) D(10,14,4) E(5,8,5).
Sessions may not overlap; sessions that merely touch at an endpoint are fine.
Maximize the total value of the sessions you choose.

State your chosen set, the total value, and your reasoning. Then answer this
explicitly: is there a session with high individual value that a
value-greedy or earliest-ending-first approach would wrongly include?"""

T3_PROMPT = """You are the sole developer of a personal knowledge tool (notes +
highlights) used daily on one laptop and one phone, offline-first. Hard
constraints: single-developer maintenance budget of at most a few days per
quarter; up to 50,000 notes; users frequently edit on both devices while
offline for days; losing any user-typed text during sync is unacceptable;
conflicts are rare but real (say 1 in 200 syncs).

Decide the sync/merge architecture: CRDT, operational transformation,
last-write-wins with per-field granularity, or an event-sourced append log
with deterministic replay. Pick ONE primary architecture, justify it against
the alternatives, and state the main risk you accept and how you would detect
it in production."""

T4_PROMPT = """A 9-person B2B SaaS company (average product for a niche
vertical) has 8 months of runway. Current numbers: $62k MRR growing 4%
monthly, gross churn 3.2% monthly, net revenue retention 98%, CAC payback 14
months for self-serve and 11 months for sales-assisted. Two sales-assisted
deals are stalled at security review. The board wants growth; the founder
believes the product loses users at onboarding step 3 (data import).

Decision: for the next two quarters, should the company prioritize retention
(fix onboarding/import, invest in customer success) or acquisition (double
sales-assisted pipeline, paid ads)? Decide, justify with the numbers given,
state what evidence would change your mind, and give the first concrete
action for the next 2 weeks."""

T5_PROMPT = """A 6-person applied-research team serves ~40 internal prediction
features. Today each feature calls one shared general-purpose model via API;
p95 latency is acceptable, but monthly spend is growing 15% month over month
and three features need structured outputs the general model sometimes
malforms. A teammate proposes the dichotomy: "either we train several small
specialized models (one per feature family) or we commit to one larger
general model with better prompting".

Evaluate this decision as a research-design question: what is the strongest
recommendation you can defend, what must be measured before committing, and
is the stated dichotomy the right framing of the decision space? Justify
every part of your answer."""

T6_TABLE = """day  deploy  p99_ms  cpu_pct  req_per_s  cache_hit_pct
 1      0      178     41       820          91
 2      0      181     42       815          92
 3      1      184     43       830          90
 4      0      180     42       840          91
 5      0      177     41       835          92
 6      0      183     43       845          91
 7      0      186     44       850          90
 8      0      182     42       860          92
 9      0      185     43       880          91
10      0      190     45       905          90
11      0      194     45       910          89
12      1      428     52       915          54
13      0      435     53       920          53
14      0      431     52       925          55"""

T6_PROMPT = f"""A service's p99 latency roughly doubled on day 12 and stayed
there. A teammate says: "the deploy on day 12 caused it; roll back the
deploy." You have 14 days of metrics:

```
{T6_TABLE}
```

(deloy flag = 1 means a production deploy happened that day; deploys also
happened on earlier days with flag 1.)

Diagnose the most likely primary cause of the latency regression. Be
explicit about: (1) the primary cause and the evidence that discriminates it
from alternatives; (2) whether the day-12 deploy is causally implicated and
why; (3) any secondary contributors and their approximate magnitude; (4) what
additional data you would request to raise confidence."""

TASKS: list[Task] = [
    Task(
        id="T1", kind="objective", title="Merge price ranges: defect identification",
        prompt=T1_PROMPT,
        ground_truth={
            "defects": {
                "D1_touch_not_merged": {
                    "requirement": "R1",
                    "hint": "code uses r[0] > merged[-1][1]; half-open touching intervals [1,2)+[2,3) are not merged",
                },
                "D2_empty_returns_none": {
                    "requirement": "R2",
                    "hint": "empty input returns None instead of []",
                },
                "D3_mutates_input": {
                    "requirement": "R3",
                    "hint": "ranges.sort() mutates the caller's list",
                },
                "D4_output_shape": {
                    "requirement": "R4",
                    "hint": "output contains lists (not tuples) and 3-element items; tier not normalized away",
                },
                "D5_no_invalid_rejection": {
                    "requirement": "R5",
                    "hint": "start > end is not rejected with ValueError",
                },
            },
        },
    ),
    Task(
        id="T2", kind="objective", title="Session selection: weighted interval scheduling",
        prompt=T2_PROMPT,
        ground_truth={
            "optimum_total": 18,
            "optimum_sets": [["B", "C", "D"]],
            "sessions": {"A": (0, 10, 10), "B": (0, 5, 7), "C": (5, 10, 7), "D": (10, 14, 4), "E": (5, 8, 5)},
            "greedy_by_value_total": 14,
            "greedy_by_end_total": 16,
        },
    ),
    Task(
        id="T3", kind="open", title="Offline-first sync architecture choice",
        prompt=T3_PROMPT,
        judge_notes="A strong answer picks one architecture and honestly owns its main risk; watch for "
                    "unjustified CRDT fashion-following, or LWW chosen while claiming no text loss.",
    ),
    Task(
        id="T4", kind="open", title="Retention vs acquisition under runway pressure",
        prompt=T4_PROMPT,
        judge_notes="Watch for answers that engage the actual numbers (churn 3.2%, NRR 98%, CAC payback, "
                    "stalled security reviews) versus generic strategy talk.",
    ),
    Task(
        id="T5", kind="open", title="Small specialized models vs one general model",
        prompt=T5_PROMPT,
        judge_notes="The stated small-vs-large dichotomy may not be the right framing (e.g. routing, "
                    "cascades, distillation, structured-output constraints are also options). Credit answers "
                    "that either defend the dichotomy with reasons or dissolve it with a better decision space.",
    ),
    Task(
        id="T6", kind="objective", title="p99 latency regression: causal diagnosis",
        prompt=T6_PROMPT,
        ground_truth={
            "primary_cause": "cache_hit_pct collapse on day 12 (~92% -> ~54%)",
            "deploy_verdict": "deploy is temporally correlated but not causally implicated; day-3 deploy "
                              "caused no regression, and cache_hit_pct is the discriminating variable",
            "secondary": "traffic growth (~820 -> ~925 req/s) adds a small amount of latency",
            "detect_keywords": {
                "cache_primary": ["cache"],
                "deploy_not_primary": [],
            },
        },
    ),
]

TASKS_BY_ID = {t.id: t for t in TASKS}

# Structured output request appended to the shared prompt for every condition,
# so downstream extraction is uniform. CURRENT wraps it in its own section
# structure; the other conditions answer it directly.
OUTPUT_CONTRACT = """

Output format (required, in this order):
## Analysis
Your reasoning, at most 600 words.
## Final Judgment
Your explicit decision/answer in at most 150 words. Do not hedge into "it
depends" without choosing a primary option.
## Confidence
low / medium / high, plus one sentence of justification.
"""

# CURRENT must keep the production skill's own output contract (7-section
# classic structure), so its structured answer is embedded into the framework
# instead of replacing it.
CURRENT_EMBED_CONTRACT = """

Output requirements to embed into the framework output (do NOT replace the
framework's section structure with these; the framework's required sections
and rounds all stay):
- The Moderator Synthesis must state the explicit decision/answer for the
  task below, the strongest alternative, preconditions, and next actions.
  If the task has a concrete computable/identifiable answer (numbers, code
  defects, causal cause), the Synthesis must state it concretely.
- The Uncertainty Ledger must include an overall confidence level
  (low/medium/high) with a one-sentence justification.
"""


def task_user_prompt(task: Task, for_current: bool = False) -> str:
    if task.id == "T2":
        extra = "\nEnd the Final Judgment with a line exactly like: TOTAL=18; SET=B,C,D (your numbers).\n"
    elif task.id == "T1":
        extra = ("\nIn Final Judgment, list each violated requirement identifier (R1-R5) you found violated, "
                 "one line per defect, with a one-sentence fix.\n")
    elif task.id == "T6":
        extra = ("\nIn Final Judgment state three labeled lines: "
                 "PRIMARY CAUSE: ... / DAY-12 DEPLOY: causally implicated OR not causally implicated OR unclear / "
                 "SECONDARY: ...\n")
    else:
        extra = ""
    if for_current:
        if task.id == "T2":
            extra = ("\nSomewhere in the framework output the concrete answer must appear as a line "
                     "exactly like: TOTAL=18; SET=B,C,D (with your numbers).\n")
        elif task.id == "T1":
            extra = ("\nSomewhere in the framework output, list each violated requirement identifier "
                     "(R1-R5) with a one-sentence fix.\n")
        elif task.id == "T6":
            extra = ("\nSomewhere in the framework output state three labeled lines: "
                     "PRIMARY CAUSE: ... / DAY-12 DEPLOY: causally implicated OR not causally implicated "
                     "OR unclear / SECONDARY: ...\n")
        return task.prompt + extra + CURRENT_EMBED_CONTRACT
    return task.prompt + extra + OUTPUT_CONTRACT
