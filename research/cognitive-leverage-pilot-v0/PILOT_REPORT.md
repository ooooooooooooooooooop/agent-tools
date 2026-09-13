# COGNITIVE_LEVERAGE_PILOT_V0_PHASE_A_REPORT

> **Research Track**: `research/cognitive-leverage-pilot-v0/`  
> **Phase**: PHASE A — PILOT PREPARATION  
> **Status**: PREPARATION COMPLETE  
> **Mode**: DIAGNOSTIC & RESEARCH ONLY (NO PRODUCTION RUNS, NO ARCHITECTURE CHANGES)

---

## 1. Physical Research Location

The pilot research track has been established in strict isolation within the Personal AI workspace:

```text
<REPO_ROOT>research\cognitive-leverage-pilot-v0\
├── outcome_contract.md
├── trace_schema.json
├── trace_contract.md
├── failure_taxonomy.md
├── PILOT_REPORT.md
├── cases/
│   ├── case-1-root-cause-workspace-integrity.md
│   ├── case-2-long-sequential-durability.md
│   ├── case-3-acceptance-risk-profile-admission.md
│   └── case-4-architecture-durable-execution-gap.md
├── configs/
│   ├── baselines_specification.md
│   ├── current_personal_ai.json
│   ├── minimal_strong_single_agent.json
│   └── current_manager_executor.json
├── runs/
│   └── README.md
└── analysis/
    └── README.md
```

**Zero Production Mutation**: No files inside production paths (`registry/`, `skills/`, `mcp/`, `dsh-config/`, or upstream DSH source) were modified or added. All materials reside strictly under `research/cognitive-leverage-pilot-v0/`.

---

## 2. Baseline Feasibility

Three distinct operational baselines were discovered and specified:

### A. CURRENT_PERSONAL_AI (Production & Controlled)
- **Model Resolution**: Primary route resolves to `cpa/gpt-5.6-sol-xhigh` (with `gpt-5.6-luna-max` governance fallback); compaction route to `cpa/gemini-3.8-flash-high`.
- **Context Construction**: DSH System Prompt + `~/.dsh/AGENTS.md` (Global preferences, continuous adoption, autonomous execution governance SSOT) + project `AGENTS.md` + injected skills list + active conversation history.
- **Tool Access**: Full native DSH tools (`read`, `write`, `edit`, `glob`, `grep`, `pwsh`, `todo_write`, `skill`, `subagent`, `subagent_fork`, `workflow`, `ralph`, `get_goal`, `create_goal`, `update_goal`, `job_*`).
- **Memory / State**: `personal-ai-state` (`identity.md`, `goals.md`, `preferences.md`), Dynamic Memory (`memory/records/`), project state (`.ai/state/state.md`).
- **Orchestration & Stopping**: Bounded autonomous goal continuation governed by `execution-discipline` and `autonomous-execution-governor`.
- **Feasibility**: **100% FEASIBLE**. Already runs in live production.

### B. MINIMAL_STRONG_SINGLE_AGENT (Controlled)
- **Definition**: Single primary Agent with complete tool access and project state, autonomous continuation toward goal, **NO forced Manager -> Executor orchestration**, and **NO new special cognitive mechanisms**.
- **Feasibility**: **NATURALLY CONSTRUCTIBLE**.
- **Implementation Mechanism**: DSH natively supports single-agent execution in a session. For controlled runs, delegation tools (`subagent`, `subagent_fork`, `workflow`, `ralph`, Switchboard MCP) and hierarchical skills (`agent-switchboard-ops`, `subagent-execution-governance`) are masked or constrained via prompt, running on identical frontier model resolution (`cpa/gpt-5.6-sol-xhigh`) with native DSH goal continuation. No production modifications required.

### C. CURRENT_MANAGER_EXECUTOR (Production & Controlled)
- **Model Resolution**: Manager on `cpa/gpt-5.6-sol-xhigh`; Executor on `cpa/claude-3.7-sonnet` (via Claude Code CLI) or `cpa/gpt-5.6-luna-max` (via Codex CLI / DSH subagents).
- **Context Construction**: Manager slices tasks into strict, self-contained contracts (`GOAL`, `OWN`, `MUST PRESERVE`, `EXIT`, `BLOCKED`). Executor receives ONLY the sliced contract, with zero access to manager session context.
- **Tool Access**: Manager has DSH tools + Switchboard MCP (`mcp__agent-switchboard__*`). Executor has CLI tools (bash/read/edit/write).
- **Orchestration**: Two-tier lifecycle: Start supervisor -> Send sliced task -> Long-poll wait (`wait_supervisor_event`) -> Inspect artifacts -> Approve/repair -> Close supervisor.
- **Feasibility**: **FEASIBLE WITH PREREQUISITES**. Requires local Switchboard broker service and authenticated CLI backends.

---

## 3. Candidate Historical Cases

Four representative historical cases were selected from real Personal AI / DSH / AIC engineering records:

| Case ID | Archetype | Real Incident / Task | Date & Cutoff | Historical Evidence Artifacts |
| :--- | :--- | :--- | :--- | :--- |
| **CASE 1** | Root-Cause Investigation | `DSH_WORKSPACE_REGISTRY_INTEGRITY` | `2026-09-04T07:47:00+08:00` | Crash logs, corrupted snapshot `workspace.json.bak-20260904`, handoff doc `docs/dsh-workspace-registry-integrity-incident-handoff.md`, reproduction test scripts. |
| **CASE 2** | Long Sequential Engineering | `PHASE6_DURABILITY_SUBSYSTEM` | `2026-08-28T09:00:00+08:00` | Spec in `docs/architecture.md`, commit `40d16e3`, `tests/test_durability.py`, 8 durability scripts, scheduled task, `docs/phase6-durability-report.md`. |
| **CASE 3** | Implementation + Acceptance / False-Completion Risk | `MIGRATION_8_1_PROFILE_ADMISSION` | `2026-08-31T10:00:00+08:00` | `docs/automatic-execution-profile-admission-report.md`, `tests/test_profile_admission.py`, `scripts/autonomy/profile_admission.py`, 14-assertion adversarial suite. |
| **CASE 4** | Architecture / Ambiguous Reasoning | `PERSONAL_AI_DURABLE_EXECUTION_GAP` | `2026-09-03T11:00:00+08:00` | `docs/durable-execution-gap-report.md`, `docs/durable-execution-gap-index.json`, historical session traces (`d249ef16`, `e30ae08e`). |

---

## 4. Why Each Case Was Selected

1. **Case 1 (Root-Cause Investigation)**:
   - *Why*: A true forensic investigation. The surface symptom (startup crash on missing order entry) easily seduced naive agents into applying shallow array patches. Finding the true mechanism required tracing whole-file atomic overwrites in `@deepseek-ai/dsh-storage-json`, lack of file locks, non-atomic multi-file writes in `createCanonical`, and launcher port-race conditions.
2. **Case 2 (Long Sequential Engineering)**:
   - *Why*: High-depth, multi-phase engineering spanning 8 discrete scripts, SQLite backup API integration, Windows scheduled task registration, ledger logging with secret redaction, 8 unit tests, and 5 physical disaster drills. Tests persistence and disciplined state management over many sequential tool steps.
3. **Case 3 (Acceptance & False-Completion Risk)**:
   - *Why*: Represents the classic vulnerability where an agent writes a simple string-matching script and falsely declares "PASS 100%". The true problem required building an adversarial-resistant classifier that rejects budget escalation when 90% consumed, preserves cumulative usage on resume, and intercepts before the first expensive call.
4. **Case 4 (Architecture & Ambiguous Reasoning)**:
   - *Why*: Solved a high-ambiguity system question ("Why do tasks get lost and why can no one tell what is unfinished?") without being handed a pre-packaged recipe. Required formalizing a 4-state orthogonal domain model (Orchestration vs Job vs Worker vs Validation) under strict constraints (no heavy daemons, native CLI independence preserved).

---

## 5. Frozen-State Evidence Availability

All 4 cases have verified, physically preserved pre-cutoff artifacts:
- **Case 1**: The corrupted `workspace.json` (39,446 bytes) is preserved and reproducible; DSH 0.1.1-rc.2 packages are installed.
- **Case 2**: Git tree at commit `c8df4be` (end of Phase 5) contains all necessary pre-conditions; the 8 scripts and tests did not yet exist.
- **Case 3**: `registry/autonomous-execution-governance.yaml` pre-admission state is recoverable via Git; `checkpoint.py` pre-auto-admit state is preserved.
- **Case 4**: Historical session logs and storage formats from August–September 2026 are preserved in `~/.dsh/storages/` and `~/.dsh/sessions/`.

---

## 6. Leakage Audit

To prevent experimental contamination:
1. **Isolated Packet Files**: Each case packet in `cases/` contains strictly pre-cutoff information (`USER_GOAL`, `KNOWN_FACTS_AT_CUTOFF`, `AVAILABLE_EVIDENCE_AT_CUTOFF`, `USER_CONSTRAINTS`).
2. **Quarantined Hidden Future**: Actual root causes, post-cutoff PRs, final reports, and subsequent user corrections are sequestered in dedicated `Isolated Hidden Future` blocks with explicit warnings.
3. **Runtime Harness Safeguard**: The test runner feeding prompts to candidate baselines must only parse the `Pre-Cutoff Facts` block and strip out the `Hidden Future` section.

---

## 7. Acceptance Design

Rigorous separation of deterministic vs semantic acceptance:
- **DETERMINISTIC_ACCEPTANCE**:
  - Automated unit test suites (`unittest discover`, pytest).
  - Machine invariants (JSON schema validation, `records === order`, `git diff --check`).
  - Physical execution artifacts (scheduled task info `LastTaskResult == 0`, SQLite `integrity_check == ok`, secret-regex scan 0 hits).
- **SEMANTIC_OUTCOME_ACCEPTANCE**:
  - Evaluates whether the user's objective was truly resolved.
  - **No Path Replication Mandate**: Candidates are NOT penalized for taking an implementation path different from history (e.g., in Case 1, an agent implementing an OS file lock is judged on effectiveness, not on whether it matched the historical commit).

---

## 8. Trace Contract

- **Format**: Machine-readable JSONL stream defined in `trace_schema.json` and `trace_contract.md`.
- **Observable Focus**: Captures `observed_reality`, `assumptions` (tagged REVERSIBLE/IRREVERSIBLE), `decisions`, `tool_calls`, `evidence_discovered`, `plan_changes`, `verification_attempts`, `completion_claim`, `escalation`, `artifacts`, and `resource_usage`.
- **No CoT Exposure**: Does not require models to output chain-of-thought tokens.

---

## 9. Initial Failure Taxonomy

Standardized attribution schema (`failure_taxonomy.md`) with 16 orthogonal failure codes:
`MODEL_CAPABILITY`, `STATE_MISSING`, `CONTEXT_CONSTRUCTION`, `TOOL_LIMITATION`, `TOOL_MISUSE`, `PLANNING`, `PREMATURE_COMMITMENT`, `EVIDENCE_DISCIPLINE`, `VERIFICATION`, `MEMORY`, `HANDOFF_LOSS`, `ORCHESTRATION`, `STOP_POLICY`, `GOAL_DRIFT`, `OTHER`, `UNKNOWN`.

Every attribution is tagged with epistemic certainty: `OBSERVED`, `INFERRED`, or `UNKNOWN`.

---

## 10. Outcome Contract

Top-level metric: **`PROJECT COGNITIVE LEVERAGE`** (not "maximize autonomy").
- Strict distinction between:
  - `GOOD_ESCALATION`: Authentic value judgments, destructive action authorization, truly unacquirable facts.
  - `REPAIR_INTERVENTION`: Human forced to step in due to AI premature stop, unnecessary clarification, judgment offloading, or unverified completion.
- Multi-dimensional scorecard across: (1) Outcome Quality, (2) Cognitive Leverage, (3) Autonomy Truth, (4) Resource Bounds, (5) Operational Discipline.

---

## 11. Evidence Gaps

1. **Sub-second Concurrency Evidence in Case 1**: Exact millisecond-level PIDs of overlapping DSH processes at the moment of corruption were not captured due to OS PID recycling.
2. **Real >90d Data in Case 2**: Local historical session data at test time was all <30 days old; archive testing required a synthetic mtime fixture.
3. **Gateway Token Metering in Case 3**: CPA relay does not expose hardware-attested cost/token hooks, relying on client-side estimation.
4. **Historical Session Query Timings in Case 4**: Memory-level read latency was not logged in historical zstd session frames.

---

## 12. Replay Risks

1. **Port & Process Collisions**: Replaying Case 1 could clash with the active DSH Web GUI on port 3080 if not isolated to a distinct test port or mock environment.
2. **Live Database Contention**: Replaying Case 2 could lock live SQLite databases (`state.sqlite`) if test scripts fail to redirect to temporary directory fixtures.
3. **Model Weights Drift**: Future evaluation runs using newer model snapshots or altered temperature settings may exhibit non-deterministic drift compared to August 2026 baselines.
4. **Absolute Path Coupling**: Hardcoded Windows paths (`C:\Users\<user>\...`) in historical scripts must be parameterized in test fixtures.

---

## 13. Cases Rejected and Why

1. **`session-e40dbab0` (巨型会话 3011 万 token / 1738 steps)**: Rejected because it is an open-ended, sprawling multi-topic session lacking a single deterministic cutoff and reproducible test suite.
2. **`novel-main` S7 Chapter Generation**: Rejected because execution was paused by an external upstream Gemini 503 HTTP outage. Including it would test gateway stability rather than agent cognitive leverage.
3. **`session-67e77472` (Opus Dispatch Deception)**: Rejected as a candidate case because it was an interactive chat dispute rather than a structured engineering project with pre-existing deterministic unit tests.

---

## 14. Feasibility of V0 Execution

### PILOT_READY = PARTIAL

### Concrete Blockers Before Execution
1. **Isolated Test-Bed Runner**: An automated sandbox environment is required to instantiate isolated temporary Git worktrees and mock storage directories so that replaying Case 1 and Case 2 cannot mutate or corrupt the live `~/.dsh` or live production databases.
2. **Automated Trace Recorder**: A lightweight execution wrapper that intercepts tool calls and agent responses to stream records directly into `trace_schema.json` format without modifying DSH production code.
