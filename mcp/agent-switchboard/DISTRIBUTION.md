# Distribution provenance

This directory is a modified source distribution of [FutureisinPast/mcp-agent-switchboard](https://github.com/FutureisinPast/mcp-agent-switchboard).

- Upstream commit: `821ef987bc7037bb18ce3a55e07b3dade88c8432`
- Upstream version: `1.0.30`
- Distribution revision: `2026.08.14.3`
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

## Publication boundary

This package contains source, installers and deterministic tests only. It intentionally excludes runtime SQLite, sessions, responses, logs, user configuration, machine paths and automation state.
