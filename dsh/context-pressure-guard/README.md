# DSH Context-Pressure Guard

This overlay pins context admission for DSH `0.1.1-rc.2` routes that expose a
large context window but can reject requests at the provider boundary.

The guard estimates the fully assembled request, applies a conservative input
bound, uses the minimum of configured and provider-attested capacity, reserves
at least 16,384 tokens, and caps normal output at 65,536 tokens. The final
request configuration is frozen before `prepareCall`; the same prepared call
is then dispatched exactly once. Unsafe requests fail locally with
`CONTEXT_PREFLIGHT_BLOCKED` and do not reach the provider.

The package also carries the pressure-safe token meter and artifact-backed
tool-result pruner used by the profile deployment. The pruner keeps the durable
full result in the spill artifact and replaces only stale successful results on
the model-visible surface with a reference containing call id, tool name,
status, hash, event sequence, artifact path and a short summary; its
`restore(session, callId)` operation rehydrates one result on demand.
Compaction convergence is a separate overlay, and the context-lifecycle
package owns read-only exhaustion, handoff and restart protection.

## Replacement replay incident — frozen closure

The DSH token-meter replacement replay incident is closed. The durable status
is:

```text
INCIDENT_REMEDIATION = COMPLETE
DSH_TOKEN_METER_REPLACEMENT_REPLAY_FIX = COMPLETE
OVERALL = PASS
INCIDENT_LINE = CLOSED
```

Canonical contract: `assistant/message` with `surfaceOp=replace` is a
logical-surface replacement, not a new provider invocation or step-lifecycle
event. A replacement assistant participates in surface folding, creates no
new step lifecycle, creates no new provider-usage anchor, and its copied
usage is excluded from projection and accounting. A normal, non-replacement
assistant continues to require strict matching `step/start` lifecycle
validation.

The following are independent follow-ups and do not reopen this incident:

- `AIC_SOURCE_STATE_DRIFT` — `FOLLOW_UP`; UI source-state alignment is
  separate from token-meter remediation.
- `ARCHIVED_SESSION_ANCHOR_COVERAGE` — `FOLLOW_UP`; profile anchor coverage
  is separate from the accepted target-session runtime validation.

## Portable profile fragment

Apply this fragment through the DSH profile patch loader. Keep the upstream
`token-meter` and `agent-loop` rows disabled so each service has one owner.

```yaml
- id: token-meter
  disabled: true
- id: agent-loop
  disabled: true
- insert:
  - id: token-meter-pressure-guard
    name: './plugins/dsh-token-meter-pressure-guard/lib/index.js'
  - id: agent-loop-pressure-guard
    name: './plugins/dsh-agent-loop-pressure-guard/lib/index.js'
    config:
      contextAdmission:
        safetyMargin: 16384
        operationalMaxOutput: 65536
        inputMultiplier: 1.08
        routes:
          - provider: 'opencode-go'
            model: 'deepseek-v4-flash'
            providerAttestedLimit: 1048576
```

Install with `test/install-guard.ps1`, verify with `test/guard-pressure.ps1`,
and run the offline/runtime checks with `test/run-tests.ps1`. Restart the DSH
host explicitly after installation so the process loads the profile overlay.
