# Minimal Trace Contract (V0)

## 1. Trace Philosophy

The Cognitive Leverage Pilot V0 trace contract records **observable behavior, concrete decisions, and verifiable state transitions**.

- **No Chain-of-Thought Requirement**: Models are neither required nor expected to reveal internal hidden reasoning traces. Epistemic quality is judged strictly on observable evidence, explicit assumptions, and physical actions.
- **Machine-Usable Format**: Traces are recorded per step/turn as structured JSON lines conforming to `trace_schema.json`.
- **Differential Attribution**: Enables post-run automated attribution of failures without subjective human reconstruction.

---

## 2. Core Trace Fields & Capture Rules

1. `observed_reality`: Captures what the agent actually saw before acting (file contents, error message, tool outputs). Each item must record freshness: `CURRENT`, `LAST_KNOWN`, or `UNKNOWN`.
2. `assumptions`: Captures working assumptions made by the agent, explicitly tagged as `REVERSIBLE` or `IRREVERSIBLE`.
3. `decisions`: The concrete action chosen and its rationale, plus the `next_best_action`.
4. `tool_calls`: Machine-level execution record (tool name, parameters, execution status, latency, error message).
5. `evidence_discovered`: Newly established factual evidence backed by physical artifact references or SHA-256 digests.
6. `plan_changes`: Structured diff of plan modifications (reason, dropped item, added item).
7. `verification_attempts`: Explicit checks executed (unit tests, git diff, hashes, command returns) and whether they passed.
8. `completion_claim`: Self-reported completion verdict and the specific `done_when` criteria verified.
9. `escalation`: Whether the agent stopped to ask the user, categorized as `GOOD_ESCALATION` (value judgment, destructive permission, unacquirable fact) vs `REPAIR_INTERVENTION`.
10. `artifacts`: Paths created, modified, or inspected, with SHA-256 digests.
11. `resource_usage`: Exact input, cached, output tokens, wall-clock time, and cost ledger entries.
