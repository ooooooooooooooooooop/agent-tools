# Agent Switchboard

> **This repository distribution:** this directory is a modified source distribution based on upstream commit `821ef987bc7037bb18ce3a55e07b3dade88c8432` (version `1.0.30`). It adds windowless managed Claude supervision, exact existing-session foreground control, transcript-branch confirmation, Unicode transport repair and acknowledged incremental monitoring. See [DISTRIBUTION.md](./DISTRIBUTION.md). The package remains under the PolyForm Noncommercial License; the parent repository's MIT license does not apply to this directory.

**Use Claude Code, Codex, Gemini, Antigravity, and VS Code together — without copy-pasting context between them.**

Agent Switchboard is a **local MCP bridge** that lets your AI coding agents hand tasks to each other, review each other's work, run multi-round debates, and share compact project context — a local nervous system where the tools stay separate but coordinate through one local broker.

**No API keys. No cloud broker. No extra billing.** It uses the local CLIs, IDE bridge routes, and subscriptions you already have where possible.

> **⭐ If this saves your agent workflow, please star the repo so others can find it!**

## Why this exists

Modern AI coding workflows are fragmented. You might use **Codex** or **Claude Code** as the reasoning brain, **Gemini Flash** for bounded workhorse tasks, and **Antigravity / VS Code** for workspace context — but normally they can't talk to each other, so you end up manually copying plans, files, errors, and context from one assistant to another. That's fine for small tasks; it gets messy fast on real projects. Agent Switchboard gives those agents a shared local coordination layer so they cooperate instead of working blind to each other.

## Fast Version

- **Ask one assistant to use another** - from Codex, ask the Claude frontier to audit; from Claude, ask the Codex frontier to reason; send bounded implementation, search, and preparation work to Gemini Flash/Antigravity.
- **See across chats** - pull a *compact snapshot* of what another agent's session knows; **Codex and Claude Code are read on demand from disk**, no copy-paste.
- **Run cross-model debate** - Codex vs Claude for N rounds, then synthesize a verdict.
- **Keep the selected model as the brain, route labour cheaply** - global Codex/Claude/Gemini rules, reader/workhorse roles, and a completion audit are installed and refreshed by the same exe.
- **Use Gemini Flash as an external workhorse** - Codex, Claude, and Gemini brains can proactively route bounded cheap labour through the Switchboard MCP `route_agent_task` tool, without treating Flash as a native child or authoritative brain.
- **Token compaction is built in** - compressed handoffs, compact context packs, work memory, and retrievable originals instead of dumping entire transcripts.
- **Keep it local** - SQLite state under `~/.agent-broker`; no private chat scraping, no cloud broker.
- **Use subscriptions you already pay for** - no required API keys or metered orchestration service.
- **Know the truth** - `doctor` reports which routes are full, partial, or app-only on your machine.

Everything user-facing — the `agent-switchboard.exe` binary, the command, and the MCP server key — is `agent-switchboard`. Internally, local state stays in `~/.agent-broker` and the Python entrypoint is `agent_broker_mcp.py`.

> Built for [Antigravity](https://antigravity.google) and VS Code users. Antigravity is a VS Code fork, so the same bridge extension installs in both.

> **Honest scope:** only **Antigravity** has a true programmatic in-app send *and* a structured reply back to the broker. Claude/Codex are reached through a CLI round-trip or an auto-opened inbox file - see [Delivery, honestly](#delivery-honestly). This is a power-user tool for people who already run these assistants; it drives logged-in subscription UIs, so read [Terms & risk](#terms--risk) first.

---

## Requirements

Two supported install paths — pick one:

- **Self-contained `agent-switchboard.exe`** (no Python needed). Upstream release binaries are available from the [upstream Releases page](https://github.com/FutureisinPast/mcp-agent-switchboard/releases), but they do not include this distribution's changes unless upstream adopts them.
- **Python 3.10+** (run from source). The broker is one dependency-free Python file; agents launch it as `python agent_broker_mcp.py`.

Other notes:
- Windows 10/11 for the installer, bridge auto-select, and shortcut patching (the broker itself is cross-platform; the installer/CDP layer is Windows-first today).
- **Antigravity CLI (`agy`)** is required for the default headless Antigravity route. Install it from the [official Antigravity CLI docs](https://antigravity.google/docs/cli/installation); without it, Switchboard falls back to the existing in-app bridge/inbox.
- **Node.js on PATH** is needed only for the CDP helpers (Antigravity model auto-select, Codex/Claude webview submit).
- Optional: `pip install tiktoken` for exact token accounting (a `chars/4` estimate is used if it's absent; the exe bundles it).

---

## Quick Start (Windows)

1. **Close Antigravity and VS Code.** The installer refuses to run while either IDE is open, so extensions and debug flags can't be left half-updated.
2. **Install** one of two ways:

   **A — Self-contained exe (no Python):** download `agent-switchboard.exe` from the Releases page and run it. Pick **Install** from the menu (or `agent-switchboard.exe install`).

   **B — From source (Python 3.10+):**

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\install-agent-broker.ps1
   ```

   Either way the installer detects which assistants you have (Codex, Claude Code, Antigravity, VS Code), **registers the MCP server with each**, installs the bridge extension (VSIX embedded in the exe; auto-built/located from source), and writes config. If **Antigravity is installed**, it then **offers (default Yes) to enable automated in-app model selection** — press Enter to accept (it patches the Antigravity launcher to open a local debug port) or decline to skip. Every config it edits is backed up first.
3. **Open Antigravity / VS Code again** so the `Agent Switchboard Bridge` extension activates.
4. **Try it.** In any registered assistant: *"Use Agent Switchboard to ask Claude Opus to audit this function."*
5. **Check what actually works on your machine:** run `agent-switchboard.exe doctor` (or `python agent_broker_mcp.py bridge doctor`). It's read-only and tells you, per assistant, whether a CLI/extension is present, which delivery route you'll get, and whether a headless debate can run — see [Diagnostics: `doctor`](#diagnostics-doctor).

**Uninstall / rollback (both paths):** run `agent-switchboard.exe uninstall` (or `python setup.py uninstall`), or pick **Uninstall** from the menu. It reverses MCP registration in all four hosts, **removes the bridge extension**, and removes the installed broker exe. Add `--remove-data` to also delete `~/.agent-broker`. The broker uses whatever subscriptions your assistants are already logged into.

---

## What It Does

| Goal | ✅ |
|---|---|
| Let Codex, Claude Code, and Antigravity consult each other | ✅ |
| Use existing **subscriptions** — no API keys, no extra billing | ✅ |
| Keep all shared state **local** (SQLite), never scrape private chat history | ✅ |
| **Token compaction** so cross-agent calls don't burn context | ✅ compressed handoffs + compact context packs with a locally-stored, retrievable original (Headroom-style *retrieval*, not a reversible codec) |
| Keep a short per-topic **work memory** so the next model sees what changed, where, why, checks, risks, and next step | ✅ |
| **Peek at another open chat** — fetch a *compact snapshot* of what another agent's session knows, on request (opt-in, local, never silent scraping) | ✅ active context snapshots; **Codex & Claude Code read on disk**, with **Antigravity local task/log/activity fallback** (`request_context_snapshot` → `get_latest_context_snapshot`) |
| **Cross-model debate** — two assistants debate N rounds headless on your subscriptions, then a synthesis judge writes a verdict | ✅ (`agent-switchboard debate`) |
| Route Codex, Claude, and Antigravity to their **headless CLIs** by default; use the **in-app chat/inbox** only when asked or as fallback | ✅ |
| Select Antigravity CLI models naturally (`gemini flash` for the moving workhorse, or an exact version pin) and discover future models from live `agy models` output | ✅ |
| Let Codex/Claude proactively use newest live Flash High as a cheap external workhorse, with native fallback and independent-package concurrency rules | ✅ |
| Fall back to the in-app extension / app automatically when a CLI isn't installed | ✅ (see caveats in the docs) |
| Send a prompt straight into Antigravity's chat panel + get a structured reply back | ✅ (`antigravity.sendPromptToAgentPanel`, the only full in-app round-trip) |
| Pick the Antigravity model automatically | ✅ **Offered at install** (default on) when Antigravity is detected — patches the launcher to open a local CDP debug port so the broker auto-selects the model in-app; just decline at the prompt to skip |

---

## How It Works

The broker is a dependency-free Python MCP server. Each assistant talks to it over stdio JSON-RPC; the broker keeps shared state in one local SQLite file (WAL mode, so multiple hosts can poll it concurrently) and routes work to the right place.

**Routing priority:**

1. **Surface** — Codex, Claude, and Antigravity default to the **headless CLI** (reliable, model-switchable, answer returned inline). For Antigravity, that means the standalone `agy` executable, not the IDE's `antigravity chat` launcher. Say **"in app"** / **"inbox"**, pass `surface: extension` / `surface: inbox`, or set `use_inbox: true` to force the bridge panel. If `agy` is absent, an automatic Antigravity route falls back to that bridge/inbox.
2. **Model** — vague Codex requests resolve from live `codex debug models` metadata; Claude frontier consults use the moving `fable` alias and fall back to the moving `opus` alias only on an explicit availability/entitlement error. Bare Antigravity and **"Gemini Flash"** select the newest exact stable `gemini-<numeric>-flash-high` slug advertised by live `agy models`, using numeric version order; the bundled static slug is offline fallback only. Explicit version pins remain exact, and preview/nonconforming SKUs are never auto-promoted. Gemini Flash is a useful non-authoritative workhorse-level adviser: a higher version does not promote it above Sol/Fable or make it automatically authoritative.
3. **External workhorse lane** — Codex, Claude, and Gemini brains should proactively consider newest live Flash High through Agent Switchboard for bounded search/read/extraction/summaries/drafting, low-risk implementation/tests from an approved plan, and independent parallel packages. Flash is not a native child agent. Each call carries exactly one package and schema-enforced output; implementation requires a package id, a 1-5-file allowlist, and explicit acceptance criteria. Flash cannot receive danger-full-access or live production work. If `agy`/Flash is missing, quota-limited, times out, mismatches, returns malformed/contradictory output, or fails, use the host's native cheap reader/workhorse and record the fallback. The brain independently verifies cited lines, the actual diff, and checks before accepting or sending another package.
4. **Token budget** — every routed task carries a task contract (`implementation_plan`, `co_audit`, `debate`, `review`, …) with a word budget, and a compressed context pack instead of raw history. If a caller inlines a bloated `prompt` (over a soft token limit), the broker stashes the full text as a retrievable `context_ref` and returns a `prompt_notice` nudging it to send a short instruction + ref next time — so token discipline is enforced by the system, not left to each agent.

Use the Switchboard MCP tool `route_agent_task` with `target_agent: "antigravity"`, `surface: "cli"`, `target_model: "gemini flash"`, and `effort: "high"`. The sender brain must not run `agy` directly; `surface: "cli"` tells Switchboard to invoke its internal CLI backend. Set `mode: "plan"` for read-only work. For implementation, set `mode: "accept-edits"` and include `work_package_id`, 1-5 exact `allowed_files`, and `acceptance_criteria`; Switchboard rejects an incomplete or whole-plan handoff.

---

## See what another chat knows (active context snapshots)

Working in one assistant but need the *current* state another open chat is holding? Ask for a **context snapshot** — a COMPACT continuation state (objective, plan, touched files, checks, risks, next step), **not** a full transcript, and **never** silent scraping (it's opt-in and local).

- **`request_context_snapshot(project, topic, target_agent)`** asks the best available open surface for that compact state.
- **Codex & Claude Code fast path (on disk):** the broker reads the live session transcript on disk — **Codex** from `~/.codex`, **Claude Code** from `~/.claude/projects` — redacted + truncated and **scoped to the session whose `cwd` matches the project** (no cross-project leak), returning **immediately** with no agent cooperation or CDP needed. The two CLIs are symmetric.
- **Antigravity fallback:** when no live bridge snapshot is available, the broker can read local Antigravity task/log/activity files (`~/.gemini/antigravity-ide/brain` plus workspace/file history) and return a bounded, clearly low/medium-confidence continuation snapshot. This is not guaranteed to be the visible chat transcript, but it prevents dead-end "cannot find context" failures.
- **Other surfaces:** the request is queued for a capable bridge host (`claim_context_snapshot_request` / `complete_context_snapshot_request`, race-safe + idempotent, with a stale-claim reaper), or picked up from a `.agent-broker/context-snapshots/` fallback file. If **no live surface is heartbeating** and no local fallback exists, the request reports `no_live_surface` (with guidance) instead of queuing forever with no claimer.
- **Honest limit:** a surface feeds the nerve system only if it's readable on disk (Codex/Claude Code), a live heartbeating bridge (Antigravity/VS Code), **or** it proactively records. A disconnected helper — e.g. the **Claude desktop app** (Electron + server-side history, not on disk) — can be *registered to push* context, but cannot be read on demand. `doctor` shows exactly which surfaces can contribute, so blind spots are visible, not surprising.
- Read it back with **`get_latest_context_snapshot`** — it also folds into `get_context_pack` ("Latest Context Snapshots") and `get_topic_status`, so the next model picks it up automatically. Live-host routing uses `record_surface_heartbeat` / `list_live_surfaces`.

This is the cross-chat "peek" layer: agents and IDEs can see what another agent's session currently knows and fetch it on request, without copy-pasting transcripts.

---

## Delivery, honestly

There is a real difference between **delivered** (a file/prompt reached the surface), **auto-opened** (the bridge opened it for you), **submitted** (it was actually sent into a chat), and **completed back to broker state** (a model-tagged reply landed in the broker). Antigravity is still the only true structured in-app round-trip; queued Codex requests also get a broker-owned CLI worker when the Codex CLI is available.

| Target | Mechanism | How far it gets |
|---|---|---|
| **Antigravity CLI** (default) | `agy --print <prompt> --model … --effort … --output-format json --json-schema …` (direct argument-array invocation, no shell) | **reported, pending brain verification** — schema-enforced headless round-trip; `plan` is sandboxed, while bounded implementation uses `accept-edits` |
| **Antigravity** (in-app Gemini/Claude) | `antigravity.sendPromptToAgentPanel` (+ optional CDP model select) | delivered → submitted → **completed back to broker** (`complete_antigravity_request`) — the only structured round-trip |
| **Claude extension** | `claude-inbox` markdown, **auto-opened** + best-effort CDP auto-submit | delivered → auto-opened → (often) submitted → **recorded back to broker**: the request now has a durable `claude_requests` row, so a reply via `respond_to_request` lands on it, or a file written to `claude-responses/` is ingested by `bridge claude-responses` |
| **Claude CLI** | `claude -p` headless (prompt via stdin) | **completed** — full headless round-trip |
| **Managed Claude supervisor** | detached `claude -p --input-format stream-json --output-format stream-json` owned by Switchboard | **persistent and windowless** — durable message queue, replay confirmation, structured events, Claude-native interrupt receipts, explicit hard interrupt/resume, and optional event-gated Codex decisions |
| **Codex extension / inbox** | `codex-inbox` markdown, **auto-opened**, plus bounded Codex CLI worker when available | delivered → auto-opened for visibility; the worker records **completed/failed** back to broker state so polling does not hang forever. Extension-only/no-CLI installs remain manual via `respond_to_request` |
| **Codex CLI** | `codex exec` headless | **completed** — full headless round-trip |
| **Gemini** | `gemini` CLI (`-m <model>` honored) or `GEMINI_API_KEY` | **completed** via CLI; the API path is an off-by-default escape hatch |

> **Answer return-path:** any surface without a native completion API closes the loop by calling **`respond_to_request(request_id, response)`** — the broker records the answer + timing on the request and refreshes a per-topic **`ledger.md`** (`get_request_ledger`). Codex inbox requests now also start a bounded CLI worker by default, so `request_result` returns an answer or a terminal error instead of staying `delivered` forever.

> **Model enforcement, honestly:** the broker can only *switch the answering model programmatically* on **Antigravity** (CDP UI automation, best-effort) and the **CLIs** (`--model`/`-m` flag). Codex/Claude CLI responses are attested from runtime metadata and fail closed when metadata is missing or mismatched. `agy` receives the exact dynamically resolved Flash slug and returns a conversation id, but currently does not expose independent actual-model attestation, so the result labels the requested route without claiming stronger proof. Answer prose and usage summaries are never accepted as model proof. The broker **cannot** drive the Codex- or Claude-*extension* model picker, so those surfaces still receive a strict guard plus a notification to select the requested model.

> **Model + effort on the CLIs:** model and reasoning effort are **separate inputs**, never folded together. Pass **`effort`** and the broker sets the CLI's own effort flag. A bare family request defaults to the live Codex frontier at `max`, Claude's moving `fable` alias at `max` (then `opus` only when unavailable), or Antigravity's dynamically selected latest stable Gemini Flash High workhorse at `high`. Explicit `cheap_read` and `balanced` policies select dynamically discovered Codex reader/workhorse models or Claude `haiku`/`sonnet`; prompt keywords never guess. Flash remains a non-authoritative external workhorse regardless of version. If Claude's frontier is unavailable, Flash supplies a degraded advisory second opinion; for routine bounded labour, both Codex and Claude may proactively use it with the failure/concurrency safeguards above. The selected main-session model is never rewritten. Flash permission bypass is prohibited; production deployment remains with the brain.

> The broker is **target-driven** when a target is named. If a Codex/Claude caller leaves the target completely empty, Switchboard uses the caller only as a fallback: Codex defaults to Claude, and Claude defaults to Codex. A named target or prompt phrase like "consult with Claude" still wins.

### Background Claude supervision

Use `start_managed_claude_supervisor` for work that Switchboard must discuss, redirect, and supervise without disturbing the desktop. Startup creates a detached daemon and Claude stream but sends no prompt, so startup and idle time produce no model call. `send_to_managed_claude_session` reports `confirmed` only after Claude's `--replay-user-messages` stream echoes the unique request marker.

When `interrupt_current=true`, Switchboard uses Claude's bidirectional streaming `interrupt` control request, waits for its matching receipt **and** the interrupted turn's terminal `result`, and only then sends the new message. A missing/error receipt or missing terminal result fails the command; it never silently kills the process instead. Process-tree interruption remains available only as the explicit `interrupt_mode=hard` choice and resumes the same managed session before delivery.

`decision_mode=record_only` never starts Codex. `decision_mode=codex` starts one ephemeral, read-only Codex decision only for a material event: a completed Claude turn, two tool failures, exhausted API retries, unexpected Claude process exit, or a configurable period of silence while a command is busy. The silence timer is local and creates at most one event per command; it does not call a model on each interval. Assistant progress, tool starts, file activity, and idle time remain local records and consume no Codex tokens. The durable action limit stops autonomous SEND/INTERRUPT chains and exposes `attention_required` instead of looping.

The older `claim_claude_change` cursor remains available for manual/on-demand inspection, but it is not a scheduler and should not be wrapped in a periodic model automation. The legacy `send_to_claude_session` mintty route necessarily focuses a window and uses the clipboard; every real call now requires `foreground_control=true`, and managed supervision never falls back to it.

Startup-only smoke test (no Claude or Codex prompt is sent):

```powershell
python smoke-managed-claude.py --project C:\path\to\project
```

### Supervisor signals, task receipts, and close/archive

Three MCP tools give the **controlling side** a zero-poll signal path off a
detached managed supervisor — the executor never has to reverse-request the
controller, and the controller never has to spin a polling loop.

- **`wait_supervisor_event(supervisor_id, since_seq=0, event_types=None, wait_seconds<=180)`**
  blocks until the supervisor records a *material* event with `seq > since_seq`
  (`turn_completed`, `api_retry_exhausted`, `stall_timeout`, or an attention-class
  event: tool-failure threshold, action-limit reached, codex-decision failure,
  interrupted turn) and returns its summary — or `status: "timeout"` when the
  bounded wait passes with nothing new. `event_types` optionally narrows the set.
  This mirrors the existing `request_status`/`request_result` long-poll pattern.
- **`wait_task_receipt(receipt_path, terminal_statuses=["ready_for_review","blocked","pushed"], wait_seconds<=180)`**
  blocks watching a JSON task-receipt file (receipt protocol v1) and returns a
  summary once its `status` field enters `terminal_statuses`. A file that is
  absent, invalid JSON, or missing the `status` field is tolerated and re-checked
  until the deadline instead of failing.
- **`close_supervisor(supervisor_id, archive_summary, receipt_path=None)`**
  idempotently stops the supervisor (only when its daemon is still alive) and
  archives `archive_summary` — plus the receipt summary when `receipt_path` is
  supplied — into that supervisor's topic work-memory / timeline, returning the
  archive record id. It only stops the supervisor it owns and never pushes
  anything itself.

The receipt protocol v1 schema is `{protocol_version, status, completed_items[],
current_item, test_summary{command,collected,passed,failed,skipped}, commit,
pushed, blocker, updated_at}`; a valid receipt object is one that parses as JSON
and carries a `status`.

#### Dual-channel receipt watcher

A receipt-only watcher can wait for the full window when the executor dies before writing a receipt. `scripts/receipt_watch_v2.ps1` closes that blind spot by watching two independent local signals; either signal prints a line and exits with code `0`.

- **Channel A — receipt freshness:** watches `ReceiptPath` for a terminal `status` (`ready_for_review`, `blocked`, or `pushed` by default) and requires `updated_at` to be newer than `AfterUpdatedAt` when a baseline is supplied. This prevents an old receipt from satisfying a new run.
- **Channel B — supervisor anomaly:** when `SupervisorId` is supplied, watches `%USERPROFILE%\.agent-broker\supervisors\<id>\state.json` for `failed`/`stopped`, known attention events (`api_retry_exhausted`, `stall_timeout`, `tool_failure_threshold`, `turn_interrupted`, `autonomous_action_limit_reached`), or a dead `daemon_pid`. `IgnoreAttentionSeq` suppresses an already-known attention event so the watcher does not retrigger on it.

| Parameter | Default | Purpose |
|---|---:|---|
| `-ReceiptPath` | required | JSON task-receipt file to watch. |
| `-SupervisorId` | empty | Supervisor directory name used for Channel B; empty disables it. |
| `-WindowMinutes` | `30` | Maximum watch window before `window-elapsed-no-receipt`. |
| `-AfterUpdatedAt` | empty | Optional ISO 8601 baseline; Channel A accepts only a lexically newer `updated_at`. |
| `-Terminal` | `ready_for_review, blocked, pushed` | Terminal receipt statuses to accept. |
| `-IgnoreAttentionSeq` | `0` | Highest already-handled attention sequence; only larger sequences trigger. |

Run it in a detached, windowless PowerShell process from the repository directory:

```powershell
Start-Process pwsh -WindowStyle Hidden -ArgumentList @('-NoProfile','-File','.\scripts\receipt_watch_v2.ps1','-ReceiptPath','C:\path\output\skills_watcher_receipt.json','-SupervisorId','<supervisor-id>','-WindowMinutes','30','-AfterUpdatedAt','2026-08-18T12:00:00Z','-IgnoreAttentionSeq','12')
```

The output line is `RECEIPT-SIGNAL: ...` for Channel A, `SUPERVISOR-ANOMALY: ...` for Channel B, and `window-elapsed-no-receipt` when neither channel fires before the deadline.

### Codex Goal supervision (Phase 1: observability)

A durable Codex Goal objective is not the same as governed long-horizon
execution — persistence alone does not prevent drift, repeated low-value work,
local artifacts being treated as overall progress, or unbounded budget use. The
broker owns a deterministic, local supervision layer around an existing Goal run
(**Codex remains the worker; the broker owns control state**):

```powershell
python agent_broker_mcp.py bridge goal probe                 # honest capability report
python agent_broker_mcp.py bridge goal contract --objective "<text>" --criteria "<json>" [--budgets "<json>"] [--unbudgeted]
python agent_broker_mcp.py bridge goal create   --objective "<text>" --criteria "<json>" [--budgets "<json>"] [--unbudgeted] [--thread <id>]
python agent_broker_mcp.py bridge goal list
python agent_broker_mcp.py bridge goal status  <goal_ref>
python agent_broker_mcp.py bridge goal evidence <goal_ref> <criterion> <evidence...> [--status blocked|inconclusive|...]
python agent_broker_mcp.py bridge goal complete <goal_ref>
```

- **Honest capability probe** (`goal probe` / `doctor`): reads Codex's own Goal
  DB (`~/.codex/goals_1.sqlite`, read-only) to report whether goal state and
  usage telemetry are readable, whether `codex exec --resume` dispatch is
  available, and whether completion is enforceable. When the installed surface
  only exposes observation, it reports `observability_only` and never claims
  enforcement.
- **Contract validation** (`goal contract`): requires one immutable objective,
  bounded mandatory criteria with required evidence + a verifier + a stopping
  test, protected boundaries, and a real token/time/action budget — or an
  explicit user-approved `--unbudgeted` mode. Unbounded objectives ("find the
  best X until a winner exists") are rejected as `goal_contract_unbounded`.
- **Broker-owned criterion ledger** (`goal create` / `status` / `evidence`):
  persisted under `~/.agent-broker/goals/<ref>/`, survives restart, and is the
  single source of truth for completion — a worker's prose claim is never
  accepted. Live Goal usage folds into `goal status` when readable.
- **Host-computed completion** (`goal complete`): completion requires the
  original objective hash unchanged, every mandatory criterion verified,
  resolvable evidence, protected boundaries intact, and no unresolved blocker.

Phase 1 is **observability only** — no work-unit dispatch, verifier advancement,
action fingerprinting, or budget enforcement yet. It adds **no MCP tools**
(CLI-only), so ordinary one-turn and bounded requests are unaffected, and idle
time consumes zero supervision tokens.

**Phase 2 (enforcement)** turns those reserved ledger fields into real controls:

```powershell
python agent_broker_mcp.py bridge goal dispatch  <goal_ref> [--route <r>]                    # pick the next advanceable criterion, mark it running
python agent_broker_mcp.py bridge goal work-unit <goal_ref> [<criterion>]                    # build one bounded, reference-based Codex work unit
python agent_broker_mcp.py bridge goal verify    <goal_ref> <criterion> [--timeout <seconds>] # run the configured verifier
python agent_broker_mcp.py bridge goal enforce   <goal_ref> [--require-telemetry]            # compare usage/attempts against every budget
```

- **Verifier execution** — `verify` runs the criterion's configured command. A
  successful exit marks it `verified` (the ONLY way); a failing exit increments
  `attempts`, and past the max (`GOAL_MAX_VERIFIER_ATTEMPTS`, default 3) the
  criterion is `blocked`. Timeouts/spawn failures fail closed — a criterion is
  never marked verified on an unresolved command.
- **Work-unit dispatch (bounded, no transcript replay)** — `dispatch` picks the
  next advanceable criterion and marks it `running`. `work-unit` packages the
  bounded continuation: the exact original objective, the current criterion, the
  protected boundaries, required evidence refs, the verifier, and the work-unit
  budget — never the whole Goal transcript (`transcript_replay: false`).
- **Local blockers, not global stops** — a blocked criterion does NOT stop
  unrelated ready criteria: the Goal becomes `attention_required` and other
  criteria keep dispatching. It is a global `blocked` only when the dependency
  graph proves every mandatory path is fully blocked (criterion `dependencies`).
- **Blocking is gated and recoverable** — an operator-requested `blocked`
  (`evidence --status blocked`) is rejected unless the criterion was actually
  dispatched/failed a verifier AND every declared `alternative_routes` entry was
  tried (`blocked_without_attempt` / `blocked_with_untried_route`); a block is
  the last rung of RETRY -> alternative route -> block, never the first reaction.
  Verifier-driven blocks (max attempts) and budget breaches carry their own hard
  evidence and bypass the gate. A blocked criterion with an untried alternative
  route is re-dispatched on it (`via: blocked_recovery`), and recording new
  evidence reopens a blocked criterion as `inconclusive` — recoverable pause,
  not a tombstone.
- **Repeated no-progress fingerprint** — `dispatch` never re-runs the same route
  on an unchanged `last_fingerprint`: it advances to a declared
  `alternative_routes` entry, or emits `attention_required` with the exact
  unresolved condition. No "analyze why it loops" meta-agent is ever started.
- **Budget enforcement** — `enforce` checks the total Goal budget, per-criterion
  budgets (`criterion.budget.max_actions`), and runtime-observed token/time
  telemetry from Codex's Goal DB (read-only). `max_actions`/`criterion_max_actions`
  exhaustion blocks; token/time breaches raise `attention_required`; a terminal
  `receipt` explains exactly where budget went. **Fail closed**: if the contract
  budgets token/time but telemetry is unavailable, `enforce --require-telemetry`
  returns `enforcement_requires_telemetry` instead of silently passing. Unbudgeted
  goals are never silently enforced.

Phase 2 adds **no MCP tools** and still calls **no model**: verifiers are local
commands, dispatch/fingerprint/enforce are deterministic code, and idle time
consumes zero supervision tokens. The 10 acceptance criteria of
[issue #4](https://github.com/ooooooooooooooooooop/agent-tools/issues/4) (bounded
objective, visible budgets, no repeated no-progress model calls, local blockers,
host-computed completion, hash integrity, restart resume, honest `doctor`,
unaffected ordinary requests, zero idle supervision tokens) are each covered by
focused tests.

### Managed Claude supervision over the CLI

The same detached supervisors you drive over MCP tools (`start_managed_claude_supervisor`,
`send_to_managed_claude_session`, `get_managed_claude_supervisor`, ...) are also exposed as
deterministic `bridge` verbs, so a headless caller without an MCP client can supervise too:

```powershell
python agent_broker_mcp.py bridge managed-claude create <supervisor_id> [--project <dir>] [--objective "<text>"] [--permission-mode acceptEdits] [--decision-mode record_only]
python agent_broker_mcp.py bridge managed-claude send    <supervisor_id> "<prompt>" [--interrupt-current] [--confirm-timeout <seconds>]
python agent_broker_mcp.py bridge managed-claude status  <supervisor_id> [--recent <n>]
python agent_broker_mcp.py bridge managed-claude list
python agent_broker_mcp.py bridge managed-claude stop    <supervisor_id> [--timeout <seconds>]
```

`create` launches the daemon without sending a prompt (zero tokens until `send`).
`send` queues the message and reports `confirmed` only after the daemon echoes the
unique request marker in Claude's stream; `--interrupt-current` uses Claude's native
interrupt control request (receipt + terminal result) and never silently kills the
process. `status` renders the same state/events ledger the MCP path returns, and
`list` shows every supervisor on the machine.

### Claude pool (broker-owned multi-session concurrency)

Multiple Claude Code sessions can legitimately run at once — direct `claude -p`
consults, detached supervisors, async CLI workers, and existing mintty terminals.
Each path used to guard only its own supervisor_id or request row. The broker now
owns a **machine-wide orchestration layer** (`claude_pool.py`) shared by every
Claude control path:

```powershell
python agent_broker_mcp.py bridge claude-pool status                       # machine view + enforced ceilings
python agent_broker_mcp.py bridge claude-pool list    [--status running]    # registered sessions
python agent_broker_mcp.py bridge claude-pool register <session_id> <kind> <owner_pid> [--claude-pid <pid>] [--project <dir>]
python agent_broker_mcp.py bridge claude-pool unregister <session_id>
python agent_broker_mcp.py bridge claude-pool claim-slot --owner-kind <k> --owner-pid <pid> --session <id> [--project <dir>]
python agent_broker_mcp.py bridge claude-pool reap    [--skip <session-csv>]
```

- **Machine-wide register** — a SQLite pool (`~/.agent-broker/claude_pool.db`)
  records every Claude-owned process group (owner kind, owner/claude pids, project
  scope, status), so concurrent controls are visible as one pool, not isolated dirs.
- **Bounded ceilings, fail closed** — `claim-slot` enforces a machine-wide
  `AGENT_BROKER_CLAUDE_POOL_MAX` (default 8) and a per-project
  `AGENT_BROKER_CLAUDE_POOL_MAX_PER_PROJECT` (default 3). Exceeding either returns
  `claude_pool_full` / `claude_pool_project_full` instead of silently degrading.
- **Orphan reaping** — `reap` finds a live `claude` process whose owning pid (daemon
  or worker) died, flags the session `attention_required` with a durable record, and
  releases its slot. It never silently reuses a dead-owner session.
- **Workspace write lease** — the `ProjectWriteLease` serializes write-class
  supervision on ONE project (cross-process, crash-safe FileLock), matching the
  routing gate's "parallel reads / serial writes" rule.
- **Doctor integration** — `doctor` reports pool schema health, enforced ceilings,
  and the live session summary.

Pool state is derived from real process state and the existing
`~/.agent-broker/supervisors/` ledger — it never claims to own the command queues
daemons already own, and idle supervision consumes zero model tokens.

### Claude Agent SDK backend (experimental, opt-in)

The default Claude Code path stays the **zero-dependency** raw stream-json CLI. The
official `claude-agent-sdk` (Python) is an optional backend with first-party
controls the CLI path doesn't expose uniformly — live `set_model`/interrupt/`stop_task`
on a client, `fork_session`/`resume`/session-store, and typed usage introspection.
`claude_sdk_backend.py` is a deterministic **feasibility probe**, never the default:

```powershell
pip install --target ./_sdk_probe_deps claude-agent-sdk        # opt-in vendored deps (gitignored)
python agent_broker_mcp.py bridge probe sdk                     # free capability report, no model call
python agent_broker_mcp.py bridge probe sdk --run-prompt "..." [--model <name>]   # OPT-IN real driver
```

The probe reports whether the SDK imports, which surface it exposes, and how it
compares with the CLI (runtime model switch, native interrupt, resume, but not
zero-dependency). It spends zero tokens unless `--run-prompt` is passed. `doctor`
shows a `claude-agent-sdk` block with the same honesty: "available" only when it
actually imports, and the default route unchanged.

---

## Diagnostics: `doctor`

Because "what works" depends on **what you have installed**, the broker ships a
read-only `doctor` that probes this machine and tells you the truth — no state is
changed.

```powershell
agent-switchboard.exe doctor          # rendered report
agent-switchboard.exe doctor --json   # machine-readable
# from source:  python agent_broker_mcp.py bridge doctor
```

For each assistant it reports: whether the **CLI** is found (and a live
`--version` smoke test), whether the **extension** is installed, the **CDP port**,
the **delivery route** you'll actually get, the **reply path**, and whether a
**headless debate** can run. It also prints a **nerve-system** view — which
surfaces can feed `request_context_snapshot` (on-disk fast-path vs live bridge vs
push-only), so a blind spot like a disconnected desktop app is visible. It flags
broker/bridge version drift and prints actionable next steps.

**What each install combination gets you** (this is what `doctor` checks):

| You have… | Codex / Claude result |
|---|---|
| **CLI on PATH** | full headless round-trip (best); answer returns inline |
| **Extension only, no CLI** | the broker still *delivers* into the extension (auto-opened inbox + best-effort CDP auto-submit), but it's **semi-manual** and not a silent headless round-trip. `doctor` reports this as `partial` / `delivery-only` |
| **Desktop app only** | clipboard hand-off only — no programmatic return path |
| **Neither** | `doctor` tells you exactly what to install |

> **Headless debate** (running both sides automatically) needs **both** the Codex
> **and** Claude CLIs present — `doctor` reports `headless autonomous debate
> runnable: YES/no` before you try. Extension-only setups can still get a one-shot
> second opinion, just not an autonomous multi-round run.

---

## Changelog

### Unreleased (broker-owned Claude concurrency pool + managed-claude CLI + SDK probe)
- Added a broker-owned **Claude pool** (`claude_pool.py`, `bridge claude-pool`): a machine-wide SQLite register of every Claude-owned process group, atomic `claim-slot` ceilings (machine-wide `AGENT_BROKER_CLAUDE_POOL_MAX` default 8, per-project `AGENT_BROKER_CLAUDE_POOL_MAX_PER_PROJECT` default 3) that fail closed, orphan reaping that flags dead-owner sessions `attention_required` without silent reuse, and a cross-process `ProjectWriteLease` serializing write-class supervision per project (matching "parallel reads / serial writes"). `doctor` now reports pool schema health, ceilings, and the live session summary. CLI-only, no MCP tools, zero idle tokens.
- Exposed the detached managed Claude supervisor over the CLI: `bridge managed-claude create|send|status|list|stop` mirrors the existing MCP tools, so a headless caller without an MCP client can supervise detached Claude Code sessions.
- Added an **experimental, opt-in Claude Agent SDK backend probe**: `claude_sdk_backend.py` reports whether `claude-agent-sdk` is importable and which control surface it exposes (`bridge probe sdk`), plus an `--run-prompt` real-model driver that is never the default. `doctor` shows a `claude-agent-sdk` block. The default Claude route stays zero-dependency.
- **Codex Goal supervision Phase 2 (enforcement)**: `bridge goal dispatch|work-unit|verify|enforce` — verifier-driven `verified`, bounded reference-based work-unit packaging (no transcript replay), dependency-aware local blockers (a blocked criterion never stops unrelated ready criteria), repeated no-progress fingerprint routing to alternative routes or `attention_required`, and fail-closed budget enforcement (total + per-criterion `max_actions`, token/time telemetry with `enforcement_requires_telemetry` when unavailable). Addresses [issue #4](https://github.com/ooooooooooooooooooop/agent-tools/issues/4) acceptance criteria 3/4/7. Still CLI-only, still zero model calls.
- New focused tests: `tests/test_claude_pool.py` (13), `tests/test_claude_sdk_backend.py` (6), `tests/test_goal_supervisor_phase2.py` (21).

### v1.1.0 (windowless event-driven Claude supervision)
- Added a detached stream-json daemon with durable commands, explicit replay confirmation, compact event/state ledgers, Claude-native interrupt receipts, and explicit process-tree interrupt/resume. It never focuses a desktop window or touches the clipboard.
- Added optional event-gated ephemeral Codex decisions. Idle time and ordinary progress produce zero Codex calls; autonomous control chains have a durable hard limit.
- Reclassified mintty injection as explicit legacy foreground control and removed it from all managed fallback behavior.

### v1.0.33 (managed Gemini hierarchy + MCP-only sender boundary)
- Install/repair now manages the same checksum-protected hierarchy in `~/.gemini/GEMINI.md`. It narrowly replaces the known obsolete global Pine-v6 persona, while preserving unrelated user-authored Gemini content and refusing tampered managed blocks.
- Sender brains must enter cross-vendor and Flash labour through Switchboard MCP `route_agent_task`. "Through CLI" means `surface: "cli"` on that tool; only Switchboard may start `agy`, and the installed Codex/Claude pre-tool hooks deny sender-side direct invocation.
- A Switchboard-launched Flash session is explicitly the non-authoritative worker for one schema-enforced package, never the brain/router and never an autonomous whole-plan executor.

### v1.0.32 (fail-closed Gemini Flash work packages)
- Every Antigravity Flash CLI call now uses `--output-format json --json-schema ...`. Switchboard validates the structured result locally and rejects missing fields, malformed output, contradictory completion, out-of-scope file changes, and unsupported claims that a defect is intentional/by design.
- Flash implementation is limited to one package per call and requires `work_package_id`, 1-5 exact `allowed_files`, and explicit `acceptance_criteria`. Whole-plan execution, continuation to another package, `danger-full-access`, production SSH, live credentials, destructive operations, migrations, and live deployment are prohibited.
- A valid Flash result returns `brain_verification: pending` and `accepted: false`. The Codex or Claude sender must independently inspect cited primary lines, the actual diff, and check output before accepting the package or dispatching the next one; ambiguity and failure fall back to the native reader/workhorse.

### v1.0.31 (dynamic Gemini Flash external workhorse routing)
- Bare Antigravity and `gemini flash` requests now select the newest exact stable `gemini-<numeric>-flash-high` slug advertised by live `agy models`, including its current tabular output. Numeric version ordering handles future releases automatically, while preview/nonconforming models are excluded, the bundled 3.6 slug remains an offline fallback, and explicit version pins remain exact.
- Gemini Flash High is classified as a proactive external workhorse for bounded search, reading, extraction, summaries, drafting, and approved low-risk implementation/tests. It remains non-authoritative regardless of version and cannot replace the Sol/Fable frontier brain; when Claude's Fable-to-Opus chain is unavailable, Flash is only a degraded advisory second opinion and the Codex brain retains judgment.
- Missing, quota-limited, timed-out, mismatched, or failed `agy`/Flash work falls back to the host's native cheap reader/workhorse and records the fallback. Flash and native workers may run concurrently only on independent packages: reads may be parallel, while writes remain serial unless demonstrably isolated, with the brain reviewing evidence and actual diffs.

### v1.0.30 (Windows-safe Claude hook execution)
- Claude Code routing hooks now use executable-plus-argument-array form, preventing `/usr/bin/bash` from stripping backslashes out of Windows executable paths. Install/repair migrates legacy string-form Switchboard hooks without changing other user hooks; Codex hooks keep their existing command-string format.
- Completion now requires every Claude-managed background Bash, PowerShell, or Monitor job started by a package to reach a terminal result or be stopped. Launching or detaching background work does not count as verification.

### v1.0.29 (strict native pre-labour enforcement)
- The installed `PreToolUse` gate now atomically allows one bounded block of direct brain labour, then denies the next eligible read, search, evidence, test, documentation, or mechanical call until a same-vendor native reader/workhorse starts or a package-specific brain override is registered.
- Native-agent relief is bounded rather than permanent: each cheap-role start opens only the next block, while completed planning work cannot disable later implementation enforcement. Switchboard consultation controls remain available, but ordinary research MCP calls count as evidence labour.
- Direct-labour counts are captured before tool execution, resist parallel-call and retry bypasses, and set a floor that the final routing audit cannot under-report. Managed native roles are also instructed to cap their return at 8,000 characters so cheap-agent transcripts do not flood the brain context.

### v1.0.28 (bounded context ingress and complete routing audits)
- Oversized MCP verification payloads are quarantined outside the brain context by the installed `PostToolUse` hook. The brain receives a compact evidence reference and must request an explicit field projection or output cap instead of ingesting the raw response.
- Decision premises are explicit: readers locate candidate evidence and distinguish fact from interpretation; the brain adjudicates only the minimum primary evidence whose truth could change a decision.
- Completion audits now cover unplanned as well as planned work and include a direct-brain labour census for reads, searches, evidence queries, tests, documentation, and other routine work.
- `agent-switchboard.exe --version` (plus `version` and `-v`) reports the packaged release, so an installed binary can be verified directly.

### v1.0.27 (native-first labour routing)
- Same-vendor labour now uses native subagents first: Codex `explorer`/Luna-low and `worker`/Terra-medium, or Claude `Explore`/Haiku and `economy-worker`/Sonnet-medium. That same-vendor restriction remains; the current policy also permits the distinct external Antigravity Flash workhorse lane documented above.
- Plans now carry a portable semantic lane plus execution mechanism and executor-resolved exact model/effort. A Claude-authored Sonnet/Haiku package is re-resolved to Codex's current native worker/reader when Codex executes it, and vice versa.
- The completion gate records host-issued `SubagentStart`/`SubagentStop` ids and accepts mixed `native:<agent-id>` and `broker:<uuid>` receipts. Bare brain overrides no longer bypass the whole audit; retained work uses a package-specific `override: brain - <WP-ID>: <specific reason>`.
- A one-shot native-first checkpoint fires after ten mutating operations without a completed cheap native agent. Dirty-worktree or deployment ownership no longer excuses read-only, test, evidence, documentation, or isolated mechanical labour.
- Dynamic Codex role selection excludes the frontier brain from cheaper roles when alternatives exist. A transient catalog failure keeps the last-known managed native roles instead of installing stale hard-coded model ids.

### v1.0.26 (future-proof brain/labour hierarchy)
- The install/repair flow now owns checksum-marked global Codex and Claude hierarchy blocks, cheap reader/workhorse role files, and merge-safe prompt/tool/stop hooks. It preserves existing hooks and main model/effort settings; the same refresh runs whenever the installed MCP server starts.
- Codex brain/worker/reader roles are selected from live `codex debug models` priority/visibility/description metadata. Claude uses moving family aliases: Fable/max for the peer brain, Opus/max only on an explicit Fable availability failure, Sonnet/medium for workhorse implementation, and Haiku for read-only labour.
- Queued Claude jobs now preserve their requested permission mode instead of hardcoding `plan`, so approved routine implementation can execute on the workhorse. Direct and async results report requested, attempted, and runtime-attested actual models.
- A bounded completion gate observes mutating tool use and requests a broker-verified routing audit (or an explicit brain override) before an implementation can claim completion. It fails open when the broker ledger is unavailable and blocks at most once per turn.

### v1.0.25 (exact Haiku pin + tighter delegation contracts + codex discovery order)
- `CLAUDE_CHEAP_MODEL` now pins the exact `claude-haiku-4-5-20251001` model id instead of the floating `haiku` alias; the static Claude catalog entry was updated to match while keeping all existing Haiku aliases (`haiku`, `claude haiku`, `haiku 4.5`) resolvable.
- Implementation-plan and implementation task contracts, and the cost-aware routing rules, now require each work package to state `Route | exact model/effort | deliverable | verification | escalation`, require workers to record an `override: brain - <reason>` line when deviating from the assigned route, reclassify risk/difficulty at each work-package boundary, return the first ambiguity or failed fix to the brain before delegating the deterministic remainder, default to parallel reads / serial writes, and require the final routing audit to cross-check the broker's actual-model ledger rather than a worker's self-report.
- `discover_codex` (broker) and `setup.py`'s config writer/repair now resolve Codex CLI path in the same order: a valid configured `codex_path`, then a valid `CODEX_CLI_PATH` marker from `~/.codex/config.toml`, then `PATH`.
- Direct and asynchronous Codex/Claude CLI calls now record the runtime-reported model (and Codex effort), label missing evidence `unverified`, and fail closed on a requested-model mismatch. Claude trusts only the main assistant event; Codex trusts the persisted `turn_context` tied to the emitted thread id.
- Codex request rows now preserve `read-only`, `workspace-write`, or `danger-full-access` through the detached worker instead of silently forcing every worker to read-only. Native Windows sandbox failures still escalate to the brain; the broker never weakens the requested sandbox automatically.

### v1.0.24 (cost-aware frontier brain + worker routing)
- Bare serious consultations now use the current frontier brain at maximum effort: Codex Sol/max and Claude Fable/max.
- Explicit `model_policy="cheap_read"` routes read/extract/summarize labor to Luna/low or Haiku (without an unsupported Haiku effort flag).
- Explicit `model_policy="balanced"` routes bounded implementation/testing from an approved plan to Terra/medium or Sonnet/medium. Prompt keywords never silently downshift a serious request.
- The routing guide now documents both families and the evidence/escalation contract remains in the shared ground rules.

### v1.0.23 (cross-agent output discipline)
- Shared task contracts now lead with the result, describe failures concretely, avoid invented estimates, and prefer plain language.
- Review/audit/bug-hunt contracts report every substantiated in-scope finding, keep unrelated observations separate, and identify residual verification gaps when no finding is confirmed.

### v1.0.22 (Antigravity CLI-first routing)
- **Antigravity now defaults to the standalone `agy` CLI**, matching Codex and Claude's CLI-first behavior. Calls return stdout directly; if `agy` is missing, automatic routing falls back to the existing in-app bridge/inbox.
- **Explicit surface intent always wins.** `surface="extension"` / `"inbox"` or `use_inbox=true` forces the in-app bridge; `surface="cli"` requires the headless CLI.
- **Model selection uses stable, live CLI slugs.** `"flash high 3.6"` resolves to `gemini-3.6-flash-high`, and `list_agent_models` merges `agy models` output so newly released models become available without hardcoding another broker release.
- **Execution mode follows task intent.** Consult/review defaults to sandboxed `plan`; `task_kind="implementation"` defaults to `accept-edits`; bypassing permission prompts remains an explicit `danger-full-access` choice.
- Added `consult_antigravity`, `antigravity_cli_path`, doctor reporting for `agy`, installer detection, and CLI/inbox fallback guidance.

### v1.0.21 (limits are advisory — stop force-shrinking data between sessions)
- **Inline consult responses: default 5k → 20k chars, hard ceiling 40k → 200k** (`AGENT_BROKER_CONSULT_RESPONSE_CHARS` / `_MAX`). Full responses were always preserved (history + request row + `response_ref`), but the small inline cap force-shrank what the calling session actually saw.
- **Truncation no longer mangles structure.** The old path collapsed all newlines/indentation (destroying code blocks and diffs). The rare over-ceiling cut is now a clean tail-cut at a line boundary with an explicit `[... truncated inline; FULL response preserved — see response_ref]` marker.
- **Word budgets are now explicitly ADVISORY** in the task contract, ground-rules file, and the prompt-size notice: aim lean, avoid *redundant* content (re-pasted files the receiver can read itself), but **never omit unique data** needed for a correct/complete answer. The prompt notice now states the prompt was delivered in full.
- Audited the full transfer path: prompts (MCP → DB → stdin → CLI) and responses (CLI pipe → DB → `request_result`) move **untruncated**; only display excerpts (history, event log) are shortened.

### v1.0.20 (no stray Claude tabs — worker requests skip the UI inbox)
- **Worker-handled Claude requests no longer open a new Claude tab.** v1.0.19 wrote the inbox `.md` *and* started the CLI worker, so the bridge also delivered the prompt into the IDE — a stray tab popped up while the worker answered headless. Inbox files are now written **only when no CLI worker took the request** (UI fallback path), and the worker deletes any leftover inbox copies when it finalizes (covers rows queued by older servers).

### v1.0.19 (Claude/Fable requests get the CLI worker too)
- **Queued Claude consults no longer sit "queued" forever.** A Claude inbox request (e.g. Opus consulting Fable) used to depend entirely on an interactive session or the bridge picking the inbox file up — in a headless environment nothing ever did. Queueing now also starts a detached **Claude CLI worker** (`claude -p --model fable/opus/sonnet/haiku --effort …`, same machinery as the Codex worker: atomic claim, 1800s cap, rowcount-gated side effects, no console window) that records the answer; collect it with `request_result(request_id, wait_seconds=180)`. The inbox file stays as the UI fallback. Disable via `AGENT_BROKER_CLAUDE_QUEUE_AUTORUN=0`.
- **Targets that aren't CLI-runnable** (e.g. Antigravity panel models) keep the UI delivery path, and now **expire with a clear error** after ~35 min instead of hanging forever; stale-expiry covers `claude_requests` like it covers `codex_requests`.
- Schema: `claude_requests` gained `effort`, `cli_model`, `worker_pid`, `worker_started_at`, `worker_completed_at` (auto-migrated).

### v1.0.18 (max-effort headroom + honest wait expectations)
- **Worker cap raised 900s → 1800s.** A max-effort Sol consult on a real design prompt commonly runs 5-15 minutes (live-measured: 8m21s); the old cap risked killing legitimate long runs. Override via `AGENT_BROKER_CODEX_ASYNC_TIMEOUT_SECONDS`.
- **Pending/running responses now state the real ETA.** The pending payload carries `typical_wait: "5-15 minutes at max/xhigh effort"` and `retry_after_seconds: 120` (was a hammer-inducing 20), and `request_result` reports `elapsed_seconds` plus a "this is normal, not a hang" note for max/xhigh — so callers stop reading a 8-minute run as stuck.

### v1.0.17 (consult is always Sol/max — no silent downgrade)
- **Removed the prompt-keyword "cheap read" guesser.** It was silently routing real consults to `gpt-5.6-luna` at low effort whenever the prompt mentioned reading/lines/deleting — producing hedged, untrustworthy answers. Luna now runs **only** when the caller explicitly sets `model_policy='cheap_read'` or names a Luna model.
- **A serious consult on Sol is forced to `max`.** Even if the caller passes `high`/`medium`, a consult/plan/audit/review/debate is clamped up to `max` (unless it explicitly opted into a cheaper tier). Safe now that max routes async instead of hanging — so the earlier hang fix no longer costs you effort.

### v1.0.16 (no more stray cmd windows)
- **Consults no longer pop an empty `cmd.exe` window on Windows.** The detached async worker runs without a console of its own, so the Codex/Claude CLI (and git/powershell helpers) it spawned were getting a fresh console window that lingered on screen. Every child process now spawns with `CREATE_NO_WINDOW`, so all consultation work happens silently in the background.

### v1.0.15 (highest-effort default + effort-based async routing)
- **Consults/plans default to `gpt-5.6-sol` at `max` again** (v1.0.14 had dropped this to `high`). Quality is the default; latency is handled by routing, not by lowering effort.
- **`max`/`xhigh` consults route async up front.** Instead of blocking the 240s sync window and then reporting pending, an effort that doesn't fit the window returns a pending `request_id` immediately while the detached worker finishes it — collect with `request_result(request_id, wait_seconds=180)`. Efforts that fit (`high`/`medium`/`low`) still return **inline**.
- **Reading/labour stays on Luna.** `model_policy='cheap_read'` (and cheap/reader-shaped requests) run `gpt-5.6-luna` at `low` and return inline.
- **Defaults only — the caller always overrides.** An explicit `effort`, `target_model`, or `model_policy` wins: request Luna for a consult, or Sol/max for anything, as the task needs.

### v1.0.14 (Codex consult no longer hangs / times out)
- **Direct `consult_codex` no longer times out and discards the work.** A consult now runs through the same ledger+worker path as queued requests: it returns the answer inline when it finishes inside the sync window, otherwise it returns a `status: "pending"` payload with a `request_id` — the detached worker keeps running to its own cap and records the answer, so nothing is lost. Collect a pending answer with `request_result(request_id, wait_seconds=120)`.
- **Consults default to `high` effort, not `max`.** At `max`, `gpt-5.6-sol` routinely overran the 240s sync window and the timeout threw the work away. `high` finishes inline for typical consults; the sync path no longer clamps serious consults *up* to `max` (the async routing paths still do). Pass `effort: "max"` explicitly when you want it — that request just returns a pending id.
- **`request_result` / `request_status` gained `wait_seconds` long-poll.** One call blocks (bounded to the MCP window) until the request reaches a terminal state, the reliable way for a turn-based caller to collect a pending consult.
- **Worker hardening.** Atomic single-writer claim (no duplicate workers on a simultaneous start); post-completion history/events are skipped when a worker loses the finalize race; lone UTF-16 surrogates in CLI output are scrubbed before the DB write (previously crashed `store_consultation` *after* a successful consult, discarding the answer).

### v1.0.13 (Codex inbox async worker)
- **Claude -> Codex inbox requests no longer stay `delivered` forever.** Queued Codex requests now start a bounded headless Codex CLI worker that writes the answer/error back to the same request row.
- **Old stuck Codex inbox requests now fail cleanly when polled.** `request_status` / `request_result` turns stale pre-fix Codex rows into terminal errors with a requeue note.
- **Async Codex requests preserve model policy.** Task kind, token budget, target model, and effort are stored on the queued request, so serious Sol consults still run at `max`.

### v1.0.12 (Codex consult effort guard)
- **Serious Codex Sol consults no longer silently run at medium.** Accidental lower efforts on `gpt-5.6-sol` consult/audit/review/debate routes are upgraded to `max`.
- **Deliberate downshifts are still allowed.** Use `model_policy: "balanced"`, `"efficient"`, or `"lower_effort"` when medium/lower effort is intentionally enough, or `model_policy: "cheap_read"` for Luna reader/sample-prep work.

### v1.0.11 (Codex 5.6 routing guide)
- **Codex defaults now target GPT-5.6 Sol at max reasoning.** Bare `codex`/`gpt` consults, audits, reviews, debates, and co-op routes resolve to `gpt-5.6-sol` with `max` effort.
- **Cheap reader/sample-prep requests now downshift automatically.** Explicit cheap/fast reading, extraction, summarizing, drafting, or sample-prep requests can use `model_policy: "cheap_read"` and resolve to `gpt-5.6-luna` with `low` effort.
- **Agents can ask the broker which model policy to use.** New `get_model_routing_guide` returns the policy, examples, and available model catalog so Claude/Codex do not have to rediscover the rules every session.

### v1.0.10 (consultation response redaction fix)
- **Successful local consultation answers are no longer line-redacted before return or storage.** Long Fable/Claude/Codex/Gemini responses now keep security-audit wording intact in both the inline excerpt and `retrieve_shared_context(response_ref, query)`.
- **Generic shared context redaction remains enabled by default.** User-provided context, prompts, logs, and error responses still use the existing safety redaction path.
- **Retrieved shared context now flags stored redaction placeholders.** Older refs that already contain `[redacted possible secret line]` report `contains_redaction_placeholders` so callers know those lines were removed before storage.

### v1.0.9 (Fable backend routing fix)
- **Fable max no longer gets caught by the Opus async-inbox rule.** The automatic async queue is now limited to `opus` at max effort; `fable`, `sonnet`, and other Claude aliases stay on the direct Claude CLI backend unless async is explicitly requested.
- **`route_agent_task` now honors the requested Claude effort.** A routed request with `effort: low` now resolves and runs as low instead of silently falling back to Claude's max-effort default.
- **Stale Fable inbox requests can be safely cancelled.** The failed queued Fable requests from the regression are terminal once cancelled and will not be re-delivered by the bridge.

### v1.0.8 (async Opus Max consults)
- **Heavy Codex -> Claude Opus/max consults no longer block inside the MCP timeout.** Reviews, audits, debates, bug hunts, implementations, and other large max-effort Claude requests from Codex now queue through the Claude inbox and return a request id immediately.
- **Opus/max quality is preserved instead of downgraded.** The queued Claude request carries a strict model guard plus an explicit effort guard, so the receiver is told to use Claude Opus at max effort or report a mismatch instead of silently answering with a lesser/default model.
- **Codex can track the queued Claude answer.** `queue_claude_request`, `get_claude_requests`, `request_status`, and `request_result` are now exposed as MCP tools, so a caller can queue a long Opus pass, keep working, then retrieve the recorded answer.
- **Claude inbox prompts now include the return path in the injected body.** Claude sees the exact `respond_to_request(...)` instruction, plus the `.agent-broker/claude-responses/<request-id>.md` fallback, after the bridge strips the metadata header.

### v1.0.7 (direct consult timeout hardening)
- **Direct Claude/Codex consults now finish before Codex's MCP tool-call timeout.** Synchronous CLI consults are capped below the client timeout, so Codex gets a controlled broker response instead of a red `timed out awaiting tools/call` failure.
- **Codex -> Claude CLI consults are isolated from Claude extension state.** Switchboard starts Claude consults with safe mode, an empty MCP config, no Chrome bridge, and no session persistence so a Claude extension task using Switchboard does not bleed into a Codex extension consult.
- **Claude consults now use stream-json parsing.** If Claude starts answering but does not finish before the safe timeout, the broker can return any partial answer it received instead of losing everything.
- **Tool descriptions now warn that direct consults are bounded.** Full-site reviews should be split into batches or routed asynchronously; a single synchronous MCP tool call is not a safe place for a many-minute Opus pass.

### v1.0.6 (Codex/Claude peer-consult fallback)
- **Codex can now ask for a peer consult without naming every routing field.** If a Codex-origin Switchboard call leaves `target_agent` and `target_model` empty, the broker now defaults to Claude Code CLI with the flagship Claude model (`opus`, max effort) instead of falling into Antigravity model selection.
- **Claude gets the symmetric fallback.** Ambiguous Claude-origin consult/co-op/debate requests now default to Codex CLI with the flagship Codex model (`gpt-5.6-luna`, max effort).
- **Prompt wording still wins.** Natural phrases such as "consult with Claude" or "ask Codex for a second opinion" are detected before the peer fallback, and explicit structured targets continue to take priority.

### v1.0.5 (Antigravity local context + Claude tool permissions)
- **Antigravity context pickup no longer dead-ends when no live bridge answers.** `request_context_snapshot(target_agent="antigravity")` now falls back to bounded local Antigravity task/log/activity state (`~/.gemini/antigravity-ide/brain`), workspace state, file history, and recent project file mtimes. It clearly labels the result as low/medium-confidence instead of pretending it is a guaranteed visible-chat transcript.
- **Claude's default MCP catalog now includes the tools Switchboard asks it to use.** The lite profile exposes Codex queue/status, work-memory recording, context-event recording, and request-ledger tools, so Claude no longer gets instructions to call hidden tools such as `record_work_memory`.
- **Docs now describe the real Antigravity fallback and Claude tool profile.** README context-snapshot and tool-profile sections now match the shipped behavior.

### v1.0.4 (Antigravity bridge claim isolation)
- **Antigravity bridge claims are now workspace-scoped and fresh-only by default.** The bridge passes its current workspace root when claiming queued Antigravity work, and ignores queued work older than 10 minutes unless configured otherwise. This prevents an unrelated Antigravity window/chat from waking up for stale or cross-project broker tasks.
- **Context snapshot claims use the same isolation.** Live bridge hosts now claim snapshot requests only for the active workspace and within the freshness window, so snapshot polling cannot route another project’s request into the visible Antigravity panel.
- **Antigravity broker handoffs are one-shot by default.** The bridge prompt now tells the in-app agent not to create scheduled tasks, background timers, wait loops, or delayed follow-up chat turns after a broker request is delivered. If a deploy/test/tool is still pending, the agent should report current status, complete the broker request, and stop.
- **Bridge settings added:** `claimCurrentWorkspaceOnly`, `antigravityClaimMaxAgeMs`, `snapshotClaimMaxAgeMs`, and `preventAntigravityBackgroundTimers`. Bridge extension version is now `1.0.1`.

### v1.0.3 (Claude/MCP context budget reduction)
- **Claude gets a lite MCP catalog by default.** When the MCP client identifies as Claude, `tools/list` now returns 18 compact user-facing tools instead of the full bridge/internal catalog. It includes Codex routing, request return, and work-memory recording so broker instructions never ask Claude to call hidden tools. Set `AGENT_BROKER_TOOL_PROFILE=full` or `mcp_tool_profile: "full"` if a client needs every internal bridge tool.
- **Tool results are summary-first.** MCP JSON results are compact by default, `get_consultation_history` now returns bounded summaries unless `include_raw=true`, and long consult responses return an excerpt plus `response_ref` for explicit retrieval.
- **Smaller default context reads.** Default context packs are 2.4k tokens, work memory is 5 entries / ~2.6k chars, and snapshot fast paths read 4 turns / ~300 tokens unless a caller asks for more.

### v1.0.2 (straightforward CLI model + reasoning-effort selection; smallest-sufficient build rung)
- **Pick the model and reasoning effort the obvious way.** `consult_codex` / `consult_claude` / `route_agent_task` now take a first-class **`effort`** field (`minimal|low|medium|high|xhigh`, plus phrases — *"extra high" → xhigh*, *"ultra"/"max" → family top*) that is passed to the CLI as **its own flag** (Codex `-c model_reasoning_effort=`, Claude `--effort`) and **never** smuggled into the model name. A bare family request — **"codex"**, **"claude"** — now resolves to the **flagship model at the highest available effort** (Codex `gpt-5.6-luna`/`max`, Claude `opus`/`max`) instead of stalling on a model-selection prompt; a specific model is honored verbatim (**"sonnet 4.6 for implementation"**, **"gpt-5.4-mini"**). Effort phrases are split out of the model text before matching, so a request like *"5.6 luna extra high"* resolves cleanly to model `gpt-5.6-luna` + effort `xhigh` — fixing a class of failures where the effort phrase produced an invalid `--model "gpt-5.6-luna-codex xhigh"` (rejected by Codex). Per-request auto-pinning of a topic default is now **opt-in** (`remember_model`). New shared helper `resolve_cli_model_and_effort()`; **Fable** added to the Claude catalog. *(Tagged `v1.0.1` in source; first shipped as a binary in v1.0.2.)*
- **Smallest-sufficient-implementation rung in the build contracts.** The `implementation` and `implementation_plan` task contracts now tell the receiving agent to prefer the **standard library / a native platform feature / an already-installed dependency over new code or new dependencies** — explicitly **without** dropping required validation, error handling, security checks, or tests, and without disputing an approved plan (stop and report instead). Scoped to code-writing task kinds only; `consult`/`co_audit`/`debate`/`review` are unchanged, so second-opinion reasoning quality is untouched.
- **Installer: more reliable Claude desktop detection.** Recognizes the Microsoft Store / MSIX "Cowork" build (registered AppX package) in addition to the `%APPDATA%/Claude` data dir and the legacy standalone installer, so Store users aren't false-negatived.

### v1.0.0 (diagnostics + Claude reply path + CLI-default routing + debate + Claude Code nerve-system)
- **Claude Code joins the nerve system (on-disk fast path).** `request_context_snapshot` now reads live **Claude Code** sessions on disk (`~/.claude/projects`, scoped to the session whose `cwd` matches the project) and completes **immediately** — symmetric with the existing Codex `~/.codex` reader, so the most common "recent chat" surface is finally peekable without any agent cooperation. When **no surface is heartbeating** it returns `no_live_surface` (with guidance) instead of queuing forever; `doctor` gained a **nerve-system** report of which surfaces can contribute (on-disk vs live bridge vs push-only); and the installer now also registers the **Claude desktop app** (push-only — it stores chat in Electron/server-side and can't be read on disk, surfaced honestly in `doctor`). Installer hardening: Antigravity debug-port helper scripts are copied to a **durable** `~/.agent-broker` path (the frozen-exe build previously baked a PyInstaller temp path into the launcher shortcut, breaking Antigravity launch after install), uninstall **restores the patched launcher shortcuts** so the opt-in is fully reversible, and the setup menu leads with **Install** (Status moved last).
- **Headless CLI is now the default route for Codex/Claude.** `route_agent_task` sends Codex/Claude work to the headless CLI by default (reliable, model-switchable via `-m`, answer returned inline). Say **"in app"** / `surface=extension` for the in-app IDE chat panel, or `surface=app` for a visible desktop-app handoff — both honored. **Exceptions:** **Gemini** defaults to Antigravity in-app automation unless you explicitly request `surface=cli`; and **Antigravity-hosted models** (e.g. Antigravity's Opus/Gemini) **always** use Antigravity automation, never a CLI. If the CLI is missing, auto-routing degrades to the in-app extension, then the app handoff.
- **Antigravity automation is a true round-trip (verified).** From any driver (e.g. the Claude app) you can route to a *named* Antigravity model — the bridge **auto-selects that model** (switching away from whatever was active) over CDP, sends the prompt into the live Antigravity agent panel, and the structured reply returns to the broker (`complete_antigravity_request`). Confirmed working end-to-end: "send to Antigravity Gemini 3.5 (High) and reply" auto-switched the model and returned the answer. This remains the **only** surface with a fully programmatic in-app send *and* structured reply.
- **`doctor` — read-only capability report.** `agent-switchboard.exe doctor` (or `bridge doctor [--json]`) probes this machine per assistant: CLI present + live `--version` smoke test, extension installed, CDP port, the delivery route you'll actually get, the reply path, and whether a headless debate can run. Flags broker/bridge version drift and prints next steps. **No new MCP tool** (CLI-only — keeps the 36-tool context budget unchanged). Also probes for a CLI binary bundled inside an installed extension as a *detected-and-smoke-tested* fallback, never an assumed one.
- **Claude-extension replies are now first-class.** Added a durable `claude_requests` table (mirrors `codex_requests`): `queue_claude_request` records a row, `respond_to_request` and `ledger.md` now recognize Claude requests, and a new `bridge claude-responses [project]` verb ingests answer files written under `.agent-broker/claude-responses/` (idempotent; archives to `processed/`). Previously a Claude-extension reply had no row to attach to. Still no MCP tool added (36 unchanged).
- **Internal request-lifecycle adapter.** One canonical state map + `is_terminal_state()` so terminal-state logic lives in a single place for new code (reply ingestion, `doctor`, `status`/`result`/`cancel`). Existing per-table status values are unchanged on the wire — no migration.
- **Request inspection + maintenance verbs.** `bridge status <id>` and `result <id>` (read-only, normalized to the canonical lifecycle across codex/antigravity/claude requests), `cancel <id> [reason]` (terminal-guarded, idempotent), and `reap [max_age_hours]` (marks abandoned non-terminal requests `expired` — never re-queues, so no double-delivery; never touches terminal or `awaiting_model_selection` rows). CLI-only; 36 MCP tools unchanged.
- **Cross-model debate engine** (`bridge debate <project> <topic> "<proposition>" [rounds] [sideA[:model[:effort]]] [sideB[:model[:effort]]]`). Two assistants debate **headless on your subscriptions** (no API key) for N rounds, then a synthesis judge writes a verdict; the transcript + verdict are saved under `.agent-broker/debates/`. Each debater keeps real memory across rounds via its CLI's own resume primitive (`codex exec resume`, `claude -p --resume`) — **no daemon, no app-server, no network port**; every turn is a clean bounded subprocess. Defaults: Codex latest + `xhigh` reasoning vs Claude `opus` + `xhigh`, synthesis at `high`; the transcript labels each side as e.g. `codex/latest (xhigh)` so you always see which model+effort argued. Token discipline (no file/command exploration, ~500-word cap, only the opponent's last message per turn) keeps cost down without lowering reasoning. CLI-only; 36 MCP tools unchanged.

### v0.6.0 (active context snapshots)
- **Peek at what another open chat knows.** New `request_context_snapshot` asks the best available surface for a COMPACT continuation state (objective, plan, files, checks, risks, next step) - not a full transcript. Read it back with `get_latest_context_snapshot`; it also lands in `get_context_pack` under "Latest Context Snapshots" and in `get_topic_status`. Opt-in and local - no silent chat scraping.
- **Codex fast path:** for Codex the broker reads the live `~/.codex` session transcript on disk (redacted + truncated) and returns immediately - no agent cooperation or CDP needed. Strictly scoped to the session whose `cwd` matches the project (no cross-project leak).
- **Cooperative delivery for other surfaces:** `claim_context_snapshot_request` (capability-gated, stale-claim reaper), `complete_context_snapshot_request` (race-safe, idempotent), `snapshot-release` for undeliverable claims, plus `record_surface_heartbeat`/`list_live_surfaces` so the bridge can route to a live host. Bridge polls snapshots first and scans a `.agent-broker/context-snapshots/` fallback dir. 36 MCP tools.

### v0.5.0 (model enforcement + one-file install)
- **Strict model guard on non-switchable surfaces.** When a specific model is requested for the Codex/Claude *extension* (or app) — surfaces the broker can't switch — the delivered prompt now leads with a self-check: state your model; if you're not the requested one, **STOP and tell the user to switch**. The bridge also shows a "select `<model>`" notification. A lesser/default model can no longer silently answer in the requested model's place. Codex requests carry `target_model` + `strict_model`.
- **Conservative prompt-model detection.** "Get Opus's opinion" with no explicit model arg resolves to Opus (so the topic's Sonnet default doesn't win), as a one-off that doesn't rewrite the stored default. Tightly anchored so ordinary prose ("the *user*…", "*budget*…", "magnum *opus*") never misfires.
- **Self-contained `agent-switchboard.exe`.** One dual-mode binary (PyInstaller) that installs everything (the bridge **VSIX is embedded**) and runs the MCP server via `agent-switchboard.exe serve` — no Python required. Both the exe and `python setup.py` expose a built-in **uninstall** that now also **removes the bridge extension** and the installed exe.
- **Installer fixes:** `latest_vsix()` is recursive + version-aware (a fresh clone could previously ship no usable VSIX); frozen self-install uses an atomic replace and **aborts** instead of silently keeping a stale exe.

### v0.4.22 (request ledger + answer return-path)
- **`respond_to_request`** (new): any receiving agent returns its answer to the broker, which records the response + timing + responder on the queued request — the symmetric reply Codex/Claude extensions lacked. No more copy-pasting from the chat panel.
- **`get_request_ledger`** (new): a per-topic, human-readable `ledger.md` (request → answer → timing) generated from SQLite (broker is the single writer; SQLite stays the source of truth). Auto-refreshes on queue/complete/respond.
- Task contracts now tell the receiver to **return via `respond_to_request`** with the Request ID. 30 MCP tools.

### v0.4.21 (review follow-ups)
- Bridge `hasAntigravitySendCommand` caches **positive only** (re-checks negatives on a TTL) so a late-registering Antigravity command isn't refused until reload; `complete_antigravity_request` race branch returns the **actual** terminal status; removed dead bridge callback code.
- **Compact task contract:** the per-message ground-rules block is no longer re-pasted into chat — the full rules live once in `AGENT_GROUND_RULES.md` and the message references it (~183→72 tokens/message). Plus a token-economy guard that flags oversized handoff prompts (`prompt_notice`).

### v0.4.20 (audit-hardening)
- **Stop stranding Antigravity requests:** the bridge only claims them in a host that actually exposes `antigravity.sendPromptToAgentPanel`, and wraps the send in try/catch → requeue.
- **No double/stale completion:** `complete_antigravity_request` is now idempotent (status guard + rowcount → `already_completed`), the Codex callback is single-sourced through the broker, and the bridge archives the fallback response file after completing so a stale file can't re-complete a requeued request.
- **Correctness:** `consult_gemini` now passes `-m <model>` on the CLI path (was silently running the CLI default); SQLite uses WAL + a 30s busy timeout; env-int parsing can't crash the server on import.
- **Security default:** CDP model auto-selection (`useCdpModelSelection`) now ships **off**; the unauthenticated debug port is opt-in only.
- **Honesty:** versioned model aliases carry a version-collapse `note`; MCP `serverInfo` reports the real version; docs reconciled to code (28 MCP tools; bridge 0.4.20).

### v0.6 (work memory)
- **Topic Work Memory**: context packs and compacted handoffs include a short continuation log before broad history (`record_work_memory` / `get_work_memory`).

### v0.5 (router + surface selector)
- **Surface routing** (`extension` default · `app` when named) with app fallback; fixed model misrouting; Antigravity "which model?" gate; Claude inbox route; Claude CLI hardened (stdin).

### v0.4 (routing / token discipline)
- `route_agent_task` with task kinds, model aliases, strict-model handling, and token budgets.

### v0.3 (context efficiency)
- Reversible-*retrieval* context compression (`store/retrieve_shared_context`), per-topic context packs, and new-chat bootstrap.

### v0.1–0.2 (foundation)
- MCP broker with shared SQLite state; Antigravity bridge using `antigravity.sendPromptToAgentPanel`; Codex inbox + callbacks.

---

## Advanced: Run from Source

The broker is a single dependency-free Python file (Python 3.10+):

```bash
python agent_broker_mcp.py            # start the MCP stdio server
python agent_broker_mcp.py bridge ... # CLI helpers used by the bridge extension
```

**Build the release artifacts** (the bridge VSIX + the self-contained exe):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build-release.ps1
# -> extensions/antigravity-agent-broker-bridge/antigravity-agent-broker-bridge-<ver>.vsix
# -> dist/agent-switchboard.exe   (embeds the VSIX; dual-mode install + `serve`)
```

Needs Node.js (for `vsce`) and Python (PyInstaller is installed automatically if missing). Upload `dist/agent-switchboard.exe` to the GitHub Release.

Register it with an MCP client by pointing the client's MCP config at:

```json
{
  "command": "python",
  "args": ["C:\\Users\\<you>\\.agent-broker\\agent_broker_mcp.py"]
}
```

**MCP tools exposed:**

- Full profile: 50 tools.
- Public profile: 43 compact tools.
- Claude/default lite profile: 24 compact cross-agent and context tools.
- Compact profile: all 50 tools with shortened descriptions.
- Override with `AGENT_BROKER_TOOL_PROFILE=full|public|lite|compact` or `mcp_tool_profile` in `~/.agent-broker/config.json`.

The MCP `tools/list` response is the canonical name/schema catalog for the selected profile.

**Antigravity model auto-selection (experimental, off by default)** requires launching Antigravity with a debug port so the bridge can drive the model picker over Chrome DevTools Protocol, then enabling `agentBrokerBridge.useCdpModelSelection`:

```powershell
antigravity --remote-debugging-address=127.0.0.1 --remote-debugging-port=9000
```

Without it, the bridge uses whatever model is currently selected and asks you to pick the target model first.

---

## Claude Code Hook 事件接收端点

标准安装（`install-agent-broker.ps1` / `python setup.py install`）会自动完成 Claude Code hook 接线；Managed Claude supervisor daemon 会启动一个 broker-wide 的本地接收端点。
端口可用环境变量 `AGENT_BROKER_HOOK_EVENT_PORT` 配置。端点只监听 `127.0.0.1`，不会暴露到局域网或公网；启动时把实际地址写入
`~/.agent-broker/hook-event-server.endpoint`，退出时删除该文件。
请求按 `session_id` 查找 `~/.agent-broker/supervisors/<supervisor_id>/state.json`，命中后追加到该 supervisor 的
`events.jsonl`；找不到时追加到 `~/.agent-broker/hook-events-orphans.jsonl`，仍返回 `202`。

### Claude Code `settings.json` hook 片段

Claude Code hook 的 stdin 是 JSON；其中包含 `session_id`、`transcript_path`、`cwd`，并会携带事件相关字段。
安装器写入的是不含端口的静态命令；下面用占位符表示安装时解析的绝对 Python 路径和 broker 源码路径，端口由转发器运行时读取 endpoint 文件：

```json
{
  "hooks": {
    "Stop": [{"hooks": [{"type": "command", "command": "<python-absolute-path> <broker-source-dir>/agent_broker_entry.py hook-event >/dev/null 2>&1 || true"}]}],
    "SubagentStop": [{"hooks": [{"type": "command", "command": "<python-absolute-path> <broker-source-dir>/agent_broker_entry.py hook-event >/dev/null 2>&1 || true"}]}],
    "StopFailure": [{"hooks": [{"type": "command", "command": "<python-absolute-path> <broker-source-dir>/agent_broker_entry.py hook-event >/dev/null 2>&1 || true"}]}],
    "SessionEnd": [{"hooks": [{"type": "command", "command": "<python-absolute-path> <broker-source-dir>/agent_broker_entry.py hook-event >/dev/null 2>&1 || true"}]}]
  }
}
```

转发 JSON 至少应有 `session_id` 和 `event`；若直接转发 Claude hook stdin，也可用其中的 `hook_event_name` 代替 `event`（值为
`Stop`、`SubagentStop`、`StopFailure` 或 `SessionEnd`）。接收端还接受 `cwd`、`transcript_path`、`source`、`reason`、`error`、
`message`、`last_assistant_message`、`prompt`、`permission_mode` 和 `stop_hook_active`。未知字段、无效 JSON、
超过 64 KiB 的请求体都会被拒绝。`hook_stop_failure` 与 `hook_session_end` 是 material event；普通的
`hook_stop` 与 `hook_subagent_stop` 仅记录生命周期事实，不会让默认的 `wait_supervisor_event` 提前唤醒。

### Kimi Code 接入说明（仅文档片段）

不要把 Kimi 配置写入本仓库或用户配置。接入方可采用如下逻辑：

```yaml
# illustrative fragment only; not an actual Kimi configuration
SessionHeartbeat:
  action: poll_switchboard
  tool: wait_supervisor_event
  args:
    supervisor_id: <managed-supervisor-id>
    since_seq: <last-seq>
    wait_seconds: 0

UserPromptSubmit:
  action: prepend_pending_switchboard_events
  source: wait_supervisor_event
  include_types: [hook_stop_failure, hook_session_end]
```

`SessionHeartbeat` 轮询时保存返回的 `seq`，`UserPromptSubmit` 将尚未提醒的 material event 摘要注入下一次用户提示；
不应重复消费同一 `seq`，也不应把完整 transcript 注入提示。

### 运维注意事项

- 端口被占用时，daemon 不会换用未记录的端口；启动会失败并在 supervisor 状态中暴露错误。检查
  `AGENT_BROKER_HOOK_EVENT_PORT`、`127.0.0.1:43827` 的占用情况后再处理。
- 端点由常驻 supervisor daemon 启动为 broker-wide 的 detached receiver，并写入
  `~/.agent-broker/hook-event-server.pid`。daemon 重启会复用健康的 receiver；若 receiver 已退出，下一次
  daemon 启动会重新创建。MCP stdio 客户端会话结束不会直接杀掉该端点。
- session 尚未写入 `state.json`、session id 过期或 supervisor 已归档时，事件会进入
  `hook-events-orphans.jsonl`。排查时对照该文件中的 `session_id` 与各 supervisor 的 `state.json`，确认 hook
  是否连接到了同一个 broker 根目录。

---

## Terms & risk

- ⚠️ **Subscription automation, not API.** The broker drives the assistants you're already logged into — including, optionally, keystroke/CDP UI automation. Automating prompts against a logged-in subscription UI may violate a provider's terms and carries account risk. Review your providers' terms before using it, and keep automation opt-in.
- ⚠️ **No chat-history scraping.** The broker only uses authenticated IDE surfaces and shared state you create. It does not read private conversation databases.
- ⚠️ **Local debug port is unauthenticated.** CDP model auto-selection opens an unauthenticated DevTools port on `127.0.0.1:9000` (`9010` for VS Code). It ships **off** (`useCdpModelSelection: false`); only enable it when you've deliberately launched the IDE with the debug flag, and close the port when you're done.
- ⚠️ **The bridge can open files and drive UI.** It polls a local queue and can open inbox files / send prompts into the active panel / (optionally) press Enter. Read the extension source before installing.
- ⚠️ **Your data stays yours.** Everything lives under `%USERPROFILE%\.agent-broker`. The uninstaller keeps it unless you pass `-RemoveData`.

---

## FAQ

**Q: Do I need an API key?**
A: No. It uses the subscriptions your installed assistants are logged into. (A `GEMINI_API_KEY` path exists only as an off-by-default escape hatch when no Gemini CLI is present.)

**Q: Can it force Antigravity to use a specific model?**
A: Not reliably. Antigravity exposes no stable "set model" API. The experimental CDP path clicks the picker for you (needs the debug port and `useCdpModelSelection: true`); otherwise you select the model and the broker confirms which one answered.

**Q: I asked for "Opus 4.8" but it ran something else?**
A: The Claude CLI `opus` alias runs whichever Opus the installed CLI maps it to — there's no `opus 4.8` CLI alias. The broker still resolves it but attaches a `note` warning that the running version may differ. Confirm the running model if the exact version matters.

**Q: Does the Claude extension get prompts automatically like Antigravity?**
A: Closer than it used to. The bridge **auto-opens** the Claude inbox file and best-effort auto-submits it, and Claude can write a reply under `claude-responses/`. But there's no symmetric send/complete API, so it's not the structured round-trip Antigravity has. The Claude CLI route is fully headless.

**Q: Is Gemini supported?**
A: Through Antigravity's in-app Gemini, yes. A standalone `gemini` CLI is also honored (the requested model is passed with `-m`). It is optional and not bundled.

**Q: Do I need Python, or can I just run the `.exe`?**
A: Either works. The **self-contained `agent-switchboard.exe`** from Releases needs no Python — it installs everything (the bridge VSIX is embedded) and is itself the MCP server (`agent-switchboard.exe serve`). Or run from source with Python 3.10+. Both have a built-in uninstall.

**Q: I asked Codex/Claude for a specific model — does it switch automatically?**
A: On Antigravity (CDP) and the CLIs (`--model`/`-m`), yes. The broker **cannot** switch the Codex/Claude *extension* pickers, so instead it tells the receiving agent to **state its model and STOP if it isn't the requested one**, and the bridge notifies you to select it — so a lesser/default model never silently answers. A model named only in the prompt ("get Opus's opinion") is detected as a one-off and doesn't change your topic default.

**Q: Mac / Linux?**
A: The broker is plain Python and cross-platform; the installer, bridge model-selection, and shortcut patching are Windows-first today. Contributions welcome.

---

## License

PolyForm Noncommercial 1.0.0. Noncommercial use is allowed with the required copyright notice. Commercial use requires a separate written license from [FutureisinPast / ChartTrades](https://chartrades.com/). See [LICENSE](LICENSE).

---

**⭐ If this saves your agent workflow, please star the repo so others can find it!**
