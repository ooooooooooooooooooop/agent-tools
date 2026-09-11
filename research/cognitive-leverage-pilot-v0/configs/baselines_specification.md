# Baselines Specification: Three Systems Discovery & Operational Profiles

This document formalizes the discovery and definition of the three comparative systems under study in Cognitive Leverage Pilot V0.

It rigorously separates:
1. **Production Baseline**: The system in its live, operational configuration as it exists today.
2. **Controlled Comparison Baseline**: The normalized, isolated experimental configuration designed to eliminate extraneous variables (such as divergent tool sandboxes, external model outages, or mismatched temperature settings).

---

## Baseline A: CURRENT_PERSONAL_AI

The production Personal AI architecture running inside DeepSeek Harness (DSH) under AIC overlays and governance contracts.

### 1. Specification Dimensions

| Dimension | Production Baseline Configuration | Controlled Comparison Configuration |
| :--- | :--- | :--- |
| **Model Resolution** | Primary: `cpa/gpt-5.6-sol-xhigh` (or `gpt-5.6-luna-max` fallback via `routing-policy.yaml`). Compaction: `cpa/gemini-3.8-flash-high`. | Fixed primary: `cpa/gpt-5.6-sol-xhigh` (or designated benchmark tier). Explicit temperature & seed if supported by provider. |
| **Context Construction** | DSH System Prompt + `~/.dsh/AGENTS.md` (Global user preferences, continuous adoption, autonomous execution governance SSOT) + project-level `AGENTS.md` + injected available skills list + active conversation history. | Same production context stack, but frozen at case cutoff; excludes any post-cutoff session history or external notes. |
| **Tool Access** | Native DSH tools: `read`, `write`, `edit`, `glob`, `grep`, `pwsh`, `todo_write`, `skill`, `subagent`, `subagent_fork`, `workflow`, `ralph`, `get_goal`, `create_goal`, `update_goal`, `job_*`, `ask_user_question`. | Same native DSH tools. (Note: `web_search` disabled; CLI router used if network search is required). |
| **Memory / State Access** | `personal-ai-state` (`state/identity.md`, `state/goals.md`, `state/preferences.md`), Dynamic Memory records (`memory/records/`), project state (`.ai/state/state.md`), and local `.taskflow/`. | Pointed to frozen test-fixture copy of state to prevent live state mutation. |
| **Orchestration** | Hybrid goal-turn autonomous driver. Autonomous continuation driven by DSH goal tool (`create_goal`, `update_goal action=resume`) bounded by `autonomous-execution-governor`. Allows opportunistic subagent delegation. | Bounded goal continuation with identical round budget (e.g., max 10 rounds per case). |
| **Stopping Behavior** | Governed by `execution-discipline` & `autonomous-execution-governor`: Stops on (1) all done-when criteria verified; (2) legitimate external blocker; (3) budget hard cap; or (4) progress delta = 0 circuit break. | Same contract. |
| **Verification Behavior** | Mixed process-level enforcement (`decision-gates` and `execution-discipline` checks before claiming done). | Evaluated against independent post-run deterministic acceptance suite. |
| **Runtime Ownership** | Deep-Managed by DSH (`0.1.1-rc.2`) + AIC composition + Cordis plugins. | Clean temporary DSH workspace/profile or containerized execution instance. |
| **Reproducible Startup** | Run launcher `scripts/aic/dsh_desktop_restart.ps1` or `pwsh -File dsh-config/profiles/web/dsh-launch-web.ps1`. Open Web GUI at `http://127.0.0.1:3080`. | Headless or programmatic CLI run using isolated test workspace with fixed arguments. |

---

## Baseline B: MINIMAL_STRONG_SINGLE_AGENT

A clean, minimalist baseline designed to answer Research Question 3: "What level of performance can a minimal strong single agent achieve without multi-agent orchestration overhead?"

### 1. Definition
- Single primary Agent with no subordinate agents.
- Full legitimate tool access (read, write, edit, glob, grep, execution/pwsh).
- Full permitted project state access.
- Capable of autonomous continuation toward a goal.
- **NO forced Manager -> Executor orchestration** (no switchboard supervisor, no required subagent delegation for coding or investigation).
- **NO new special cognitive mechanisms** added.

### 2. Feasibility & Construction in Current System
- **Feasibility Verdict**: **NATURALLY CONSTRUCTIBLE**.
- **Construction Method**:
  DSH natively supports a single primary agent executing tasks directly within its session. To construct this baseline cleanly without modifying production code:
  1. **Tool Masking**: Mask or disable delegation tools (`subagent`, `subagent_fork`, `workflow`, `ralph`, and switchboard MCP) or inject an immutable system prompt rule: *"Execute all investigations, modifications, and verifications directly; do NOT delegate to subagents or external supervisors."*
  2. **Skill Masking**: Do NOT load `agent-switchboard-ops` or `subagent-execution-governance`.
  3. **Model & State Parity**: Use the exact same frontier model resolution (`cpa/gpt-5.6-sol-xhigh`) and the same toolset (`read`, `write`, `edit`, `glob`, `grep`, `pwsh`, `todo_write`).
  4. **Autonomous Continuation**: Utilize native DSH goal loop (`create_goal`, `update_goal`) with identical round budgets.

---

## Baseline C: CURRENT_MANAGER_EXECUTOR

The two-tier hierarchical orchestration system operated via `agent-switchboard` and `agent-switchboard-ops`.

### 1. Specification Dimensions

| Dimension | Production Baseline Configuration | Controlled Comparison Configuration |
| :--- | :--- | :--- |
| **Model Resolution** | Manager: DSH primary model (`cpa/gpt-5.6-sol-xhigh`). Executor: `claude_cli` (Claude Code / Sonnet/Opus), `codex_cli` (Codex CLI / Luna), or DSH native subagents (`cpa/gpt-5.6-luna-max`). | Manager: `cpa/gpt-5.6-sol-xhigh`. Executor: Fixed target model (e.g. `claude-3-7-sonnet` or `gpt-5.6-luna-max`) matching historical route. |
| **Context Construction** | Manager receives top-level project context and constructs explicit sliced prompt contracts (`GOAL`, `OWN`, `MUST PRESERVE`, `EXIT`, `BLOCKED`) sent to Executor. Executor has zero view of Manager's session context. | Identical sliced prompt contract format. No historical leakage into Executor prompt. |
| **Tool Access** | Manager: Native DSH tools + Switchboard MCP tools (`start_managed_claude_supervisor`, `send_to_managed_claude_session`, `wait_supervisor_event`, `queue_cli_request`, `close_supervisor`). Executor: Local shell, read, edit, write in target subprocess. | Standard switchboard toolset with controlled execution timeouts. |
| **Memory / State Access** | Manager accesses `personal-ai-state` and project state; passes relevant extracts to Executor. Executor writes to project workspace on disk. | Isolated test workspace and copy of project state. |
| **Orchestration** | Explicit two-tier lifecycle: Start supervisor -> Send sliced task contract -> Long-poll wait (`wait_supervisor_event`) -> Inspect artifacts -> Approve or issue repair order -> Close supervisor. | Strict phase-gated execution loop. |
| **Stopping Behavior** | Executor stops when slice completes; Manager stops when overall milestone passes independent validation. | Same two-tier termination logic. |
| **Verification Behavior** | Manager executes read-only independent verification (diff, tests, hashes); explicitly rejects Executor self-certified PASS. | Independent acceptance check by Manager before reporting completion. |
| **Runtime Ownership** | Dual runtime: DSH manager session + background Switchboard broker daemon managing worker CLI processes. | Managed local broker daemon with cleaned SQLite database. |
| **Reproducible Startup** | Start DSH session with Switchboard MCP enabled; start broker background service if not auto-spawned. | Controlled startup script verifying broker socket/pipe before issuing manager prompt. |
