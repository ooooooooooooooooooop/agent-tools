# DSH Context Lifecycle

This profile plugin is the durable lifecycle owner for the post-compaction
guard. It never edits an existing session log. Sessions that reach the configured
pressure threshold receive a sidecar state of
`READ_ONLY_CONTEXT_EXHAUSTED` / `READ_ONLY_ARCHIVED`; `agent/request` rejects
them locally with `CONTEXT_PREFLIGHT_BLOCKED`.

Cold durable sessions can be marked with `archiveSnapshot(sessionId, details)`
before they are reopened. This writes only the sidecar, including measured
tokens and evidence hashes, so the original session bytes remain unchanged.

The service exposes executable `preview(session)`, `export(session)` and
`createNewSession(session)` operations. Handoff exports contain only the goal,
active change, incomplete tasks, decisions, files, commit, artifact hashes,
tests, blockers and current observability snapshot. Tool history and reasoning
are not copied. `requestExternalRestart()` persists a handoff and returns
`RESTART_REQUIRED`; the plugin never starts a delayed self-kill command.

`recordAdmission()` and `observability()` distinguish projected input,
reserved/effective/configured/provider-attested limits, trusted usage and sample
validity, estimate method/confidence, compaction and breaker state. The profile
must mount this package after the token meter and agent loop.
