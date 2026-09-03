# dsh-model-switch-controller

Target-aware model switch controller for the DSH Web UI model selector.

When the user switches models mid-session, the persisted session route and the
proposed route diverge at the `agent/request` waterfall. This plugin detects
that divergence and picks one of three honest paths instead of letting the
request die at `CONTEXT_PREFLIGHT_BLOCKED`:

| Mode | When |
| --- | --- |
| `IN_PLACE_SWITCH` | measured conservative input + safety margin + output budget fits the target effective limit |
| `COMPACT_THEN_SWITCH` | over limit, compaction is available and bounded; the request is rebuilt after each compaction pass |
| `HANDOFF_SWITCH` | clearly over, compaction unavailable / not useful / exhausted, or still over after bounded compaction; reuses `DSH_HANDOFF_V1` via `contextLifecycle` |
| `BLOCKED_WITH_REASON` | target not runtime-admitted (`TARGET_MODEL_UNAVAILABLE`) or invalid input |

## Boundaries

- Function plugin: no host-scoped Cordis Service, no provider registration, no
  durable session registry. State is a bounded in-memory op map plus JSONL
  evidence and handoff idempotence sidecars on disk.
- Context Preflight is never bypassed; the controller admits only when the
  same inequality the pressure guard enforces already holds on the measured
  base (`tokenMeter`), so an `IN_PLACE` decision can never be surprised by the
  guard's preflight.
- Capacity truth is never guessed: `llm.resolveModelInfo` is the runtime
  resolver; attested evidence wins over declared windows; unknown capacity
  degrades to the conservative admitted limit (`CONSERVATIVE_FALLBACK`,
  default 262144), never to the declared window.
- Handoff reuses the existing `contextLifecycle.createNewSession` /
  `markReadOnly` (`DSH_HANDOFF_V1`). No second migration, summary schema,
  checkpoint, or continuation protocol. The source session is never deleted or
  rewritten; the raw transcript is never re-injected.
- Compaction is driven through the existing `BasicCompactionEngine`
  (`ctx.get("compaction")`) with a bounded attempt count; a
  not-smaller summary maps to `COMPACTION_NOT_USEFUL` and goes straight to
  handoff.

## Events (JSONL, `<sidecarDir>/events.jsonl`)

`MODEL_SWITCH_REQUESTED`, `TARGET_CAPABILITY_RESOLVED`,
`CONTEXT_PREFLIGHT_RESULT`, `COMPACTION_ATTEMPTED`, `COMPACTION_RESULT`,
`HANDOFF_REQUIRED`, `HANDOFF_CREATED`, `TARGET_SESSION_CREATED`,
`TARGET_MODEL_VERIFIED`, `SOURCE_SESSION_RETAINED`.

## Tests

```sh
node --test dsh/model-switch-controller/test/
```
