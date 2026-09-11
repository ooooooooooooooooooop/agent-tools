# Initial Failure Taxonomy (V0)

> Purpose: A standard classification framework for attributing task degradation, stalls, or failures during Cognitive Leverage Pilot V0 experiments.  
> Note: This document establishes the vocabulary and attribution protocol only; it draws NO empirical conclusions in Phase A.

---

## 1. Taxonomy Categories

| Category Code | Category Name | Definition & Observable Markers |
| :--- | :--- | :--- |
| `MODEL_CAPABILITY` | Core Model Reasoning Deficiency | The model fails to solve a logic puzzle, code syntax issue, or constraint satisfaction problem despite having complete context, correct tools, and accurate evidence. |
| `STATE_MISSING` | Persistent State Unavailable | Critical historical facts, project decisions, or checkpoints were not saved in durable state files, requiring discovery from scratch or resulting in amnesia. |
| `CONTEXT_CONSTRUCTION` | Context Assembly Flaw | Relevant state or instructions were available on disk, but the harness/prompt builder omitted them, truncated them, or diluted them with irrelevant noise. |
| `TOOL_LIMITATION` | Tool Capability Gap | The environment physically lacks the tool/API necessary to perform the required action (e.g., no network access, sandbox permission denied, missing CLI binary). |
| `TOOL_MISUSE` | Tool Calling Execution Error | The model attempts to call tools with invalid parameters, hallucinated schemas, malformed regexes, or invokes tools that are structurally unsuited for the task. |
| `PLANNING` | Structural Decomposition Failure | The agent fails to decompose a complex goal into viable sequential steps, creates circular dependencies, or plans steps that contradict explicit constraints. |
| `PREMATURE_COMMITMENT` | Unjustified Early Path Locking | The agent commits to an initial speculative hypothesis or buggy approach without gathering exploratory evidence, ignoring contradictory observations later. |
| `EVIDENCE_DISCIPLINE` | Freshness & Evidence Violation | Treating stale observations as current truth, failing to read modified files before editing, or conflating inference with physical fact. |
| `VERIFICATION` | Verification & Acceptance Failure | Declaring completion without running tests, relying on self-certified PASS, or failing to check regressions and invariant violations. |
| `MEMORY` | Memory Retrieval / Retention Error | Failure to retrieve relevant records from Dynamic Memory or project state, or polluting memory with invalid, contradictory assertions. |
| `HANDOFF_LOSS` | Subagent / Multi-Turn Loss | Critical constraints, context, or instructions are lost during delegation from Manager to Executor or across session compaction/resumption boundaries. |
| `ORCHESTRATION` | Multi-Agent Coordination Overhead | Deadlocks, redundant duplicate work by multiple subagents, supervisor-worker communication thrashing, or supervisor polling loops. |
| `STOP_POLICY` | Erroneous Termination Logic | Stopping prematurely due to arbitrary round limits, declaring an invalid blocker before exhausting self-serve paths, or failing to halt on runaway loops. |
| `GOAL_DRIFT` | Objective Displacement | The agent pivots to a peripheral technical issue or meta-task (e.g., refactoring tests, writing lengthy explanatory memos) while abandoning the primary user goal. |
| `OTHER` | Unclassified Anomaly | Concrete anomalies outside the scope of the above categories (requires documented rationale). |
| `UNKNOWN` | Insufficient Evidence for Attribution | The failure occurred, but available logs, traces, and artifacts are insufficient to determine the mechanism. |

---

## 2. Attribution Epistemic Tagging

Every attribution entry in post-run evaluations must be assigned an epistemic certainty tag:

- **`OBSERVED`**: Directly verified by machine evidence (e.g., tool call returned error code 1, git diff shows zero lines changed, schema validation failed).
- **`INFERRED`**: Supported by strong circumstantial evidence and temporal correlation, but cannot be proven directly from a single physical log trace.
- **`UNKNOWN`**: Insufficient data to differentiate between competing candidate root causes.
