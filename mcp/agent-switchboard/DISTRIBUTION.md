# Distribution provenance

This directory is a modified source distribution of [FutureisinPast/mcp-agent-switchboard](https://github.com/FutureisinPast/mcp-agent-switchboard).

- Upstream commit: `9d35157bf85fec6e442c3054fec3469b456869d8`
- Upstream version: `1.0.32`
- Distribution revision: `2026.08.18.1`
- License: `PolyForm Noncommercial License 1.0.0`

The upstream `LICENSE` and Required Notice are retained unchanged. This directory is not covered by the repository root MIT license. Commercial use requires a separate written license from the upstream licensor.

## Distribution changes

- Treat on-disk Claude Code session readability separately from bridge heartbeat telemetry.
- Normalize MCP STDIO and Claude JSONL Unicode at the transport boundary.
- Address an exact running `claude --resume` session through its existing MSYS/mintty terminal.
- Distinguish window targeting, input, submission, transcript marker, branch confirmation and reply.
- Support Claude-native interruption only when the active branch proves an unfinished tool call.
- Add durable, acknowledged incremental watcher cursors for token-efficient monitoring.
- Add a detached Switchboard-owned Claude stream with durable commands, replay confirmation, receipt-and-terminal-result-confirmed native interruption, explicit process-tree interruption, and no foreground-window or clipboard control.
- Add event-gated supervision: routine progress is recorded locally, while optional ephemeral Codex decisions run only for material terminal/failure events and are bounded by an autonomous-action limit.
- Require per-call foreground authorization for the legacy mintty sender; managed supervision never falls back to that route.
- Keep configured provider aliases explicit instead of changing user model settings.
- Add broker-owned supervision state for Codex Goal runs (Phase 1: observability): a deterministic capability probe (`bridge goal probe`, honest about enforcement vs observation-only), Goal contract validation (unbounded objectives rejected `goal_contract_unbounded`, budgets or explicit `unbudgeted`), a persisted criterion ledger under `~/.agent-broker/goals/`, and host-computed completion. Codex Goal state is read from `~/.codex/goals_1.sqlite` read-only; no second manager agent, no periodic model calls.
- Add broker-owned Claude concurrency control (`claude_pool.py`, `bridge claude-pool`): a machine-wide SQLite register of every Claude-owned process group, atomic `claim-slot` ceilings (machine-wide + per-project) that fail closed, orphan reaping that flags dead-owner sessions `attention_required` without silent reuse, and a cross-process `ProjectWriteLease` serializing write-class supervision per project. CLI-only; doctor reports pool schema health and enforced ceilings.
- Expose detached managed Claude supervision over the CLI (`bridge managed-claude create|send|status|list|stop`), mirroring the existing MCP tools so a headless caller without an MCP client can supervise detached Claude Code sessions.
- Add an experimental, opt-in Claude Agent SDK backend probe (`claude_sdk_backend.py`, `bridge probe sdk`): a deterministic capability report (is the SDK importable, which control surface it exposes) plus an `--run-prompt` real-model driver that is never the default. The default Claude route stays zero-dependency.
- Advance Codex Goal supervision to Phase 2 (enforcement) (`bridge goal dispatch|work-unit|verify|enforce`, addressing [issue #4](https://github.com/ooooooooooooooooooop/agent-tools/issues/4) acceptance 3/4/7): verifier-driven `verified` (failing verifiers increment attempts and block past the max; timeouts fail closed), deterministic work-unit dispatch with bounded reference-based packaging (no transcript replay), dependency-aware local blockers (a blocked criterion never stops unrelated ready criteria; global block only when the dependency graph proves every path fully blocked), repeated no-progress fingerprint routing to alternative routes or `attention_required` (never a meta-analysis loop), and fail-closed budget enforcement (total + per-criterion budgets, token/time telemetry with `enforcement_requires_telemetry` when unavailable). Still CLI-only and still zero model calls.
- Rebase onto upstream v1.0.32 (dynamic/fail-closed Gemini Flash workhorse routing), preserving all distribution features.
- Add a broker-wide loopback Claude hook event receiver (`hook_event_server.py`): managed daemons ensure a `127.0.0.1` endpoint (default port 43827, `AGENT_BROKER_HOOK_EVENT_PORT` override), publish the actual runtime base URL atomically in `hook-event-server.endpoint`, and remove it on clean exit. The receiver validates `Stop`/`SubagentStop`/`StopFailure`/`SessionEnd` payloads, maps `session_id` to durable supervisor state, appends sequenced events under the shared file lock (unknown sessions go to `hook-events-orphans.jsonl` with a 202), and surfaces `hook_stop_failure`/`hook_session_end` as material events for `wait_supervisor_event`. Includes frozen-exe entrypoint wiring and receiver/integration tests.
- Extend the standard installer to idempotently merge four static Claude Code event-hook commands (`agent_broker_entry.py hook-event`) into `~/.claude/settings.json`; the forwarding command resolves the current port at runtime from the endpoint file, while installation backs up every real edit, fails closed on invalid JSON, and precisely removes both v2 commands and v1 curl hooks during uninstall.

## Publication boundary

This package contains source, installers and deterministic tests only. It intentionally excludes runtime SQLite, sessions, responses, logs, user configuration, machine paths and automation state.
