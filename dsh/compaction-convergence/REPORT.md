# DSH_COMPACTION_CONVERGENCE_FIX_REPORT

Scope: minimum backport for the running DSH (checkpoint re-compaction loop + no-change pressure retry loop). No thresholds touched (`thresholdRatio/retainRatio/compactionRetries` unchanged). No `minShadowedTokens` magic values.

## 1. Current runtime/version

- Running checkout: `C:\Users\admin\AppData\Local\npm-cache\_npx\1e7f6d9597241db0`
- DSH: `0.1.1-rc.2` (all `@deepseek-ai/dsh-*` packages match)
- Target package: `@deepseek-ai/dsh-compaction-basic@0.1.1-rc.2`
- Pointer packages: `@deepseek-ai/dsh-compaction`, `dsh-session`, `dsh-llm`, `dsh-token-meter`, `cordis` — same version family.

## 2. Upstream/reference comparison

- `@deepseek-ai/dsh-compaction/checkpoint` exposes `isCompactCheckpointSource(source)` (`kind === 'plugin' && plugin === 'compact'`) — reused as-is.
- `selectCompactableRange` in `dsh-compaction-basic@0.1.1-rc.2` selects from `surfaceNodes[0]` with no checkpoint exemption (this session's forensic: 548/549 starts re-selected a checkpoint).
- Summarization failure is a plain `Error("summary is not smaller ...")`; no structured error class exists in this release → FIX B uses a documented message-prefix matcher (`isSummaryNotSmallerError`), scoped narrowly; noted as compatibility debt.

## 3. Exact root fix

Termed `dsh-compaction-convergence` patch, applied in the fork of `lib/index.js`:

- FIX A — `selectCompactableRange`: after computing the retain-tail boundary, skip head nodes whose `data.source` is a compaction checkpoint; keep the tool-pairing balanced start rule; when the whole remaining candidate region is checkpoint-only → return `null` (no useless summarizer).
  - checkpoints are no longer shadowed sources; checkpoints are never deleted; `surfaceOp.replace` canonical semantics and durable log untouched.
- FIX B — `BasicCompactionEngine.compactIfNeeded` (pressure branch): remembers the failing region `{start, end, fingerprint}` where fingerprint = `\`${surfaceTokens}:${surface.nodes.length}\``. On a later pre-step, if the same region resolves again with an unchanged fingerprint, the summarizer is not invoked (returns `null`). Any surface append/replace changes the fingerprint and re-enables evaluation. Transient/provider failures are not fused (only `isSummaryNotSmallerError` marks the region).

## 4. Changed files (pinned fork, all new)

- `dsh-compaction-basic-convergence/lib/index.js` (patched; export additions are backward compatible)
- `dsh-compaction-basic-convergence/lib/types/*.d.ts` (copied unchanged)
- `dsh-compaction-basic-convergence/package.json` (`version: 0.1.1-rc.2+conv.1`, `dshConvergencePatch` metadata)
- `dsh-compaction-basic-convergence/test/convergence.test.mjs` (12-node test suite)
- `dsh-compaction-basic-convergence/test/replay-session.mjs` (offline replay)
- `dsh-compaction-basic-convergence/test/run-tests.ps1`, `install-convergence.ps1`, `restore-convergence.ps1`
- Backup: `.dsh-convergence-backup/dsh-compaction-basic-upstream/` (pristine upstream)
- Marker: `test/.last-deploy-marker.json` (source paths + SHA-256s)

Hash: fork `lib/index.js` `5bbf319c…`, upstream `lib/index.js` `144202a0…` — both verified during deployment/rollback.

## 5. Selector tests

`convergence.test.mjs`:
1. `1. first compaction produces a checkpoint node` — PASS
2. `2. second pressure event must not compact the checkpoint again` — PASS
3. `3. checkpoint + old normal nodes: selector skips checkpoint and selects normal region` — PASS
4. `4. candidate region all checkpoint: returns null and zero summarizer calls` — PASS
5. `5. same unchanged region summary-not-smaller: later pre-step performs zero duplicate summarizer call` — PASS
6. `6. surface mutation after failure: selector may reevaluate` — PASS
7. `7. ordinary compaction behavior unchanged (no checkpoint)` — PASS
8. `8. overflow recovery behavior unchanged` — PASS
9. `9. toolResultPruner behavior unchanged` — PASS
10. `10. surface derivation after replace unchanged` — PASS
12. `12. continue after checkpoint: new normal history compacts while checkpoint preserved` — PASS

Result: **12/12 passed** (`node --test`, isolated harness against the actual `dsh-session` + cordis + fork staged in the DSH checkout).

## 6. Pressure convergence tests

Covered by 5, 6, 12:
- same-region-unchanged → summarizer skip (zero duplicate call, `summarizeCalls` unchanged);
- fingerprint change (append) → reevaluation allowed;
- transient/provider errors not fused (only `isSummaryNotSmallerError` marks).

## 7. Original-session offline replay

Read-only replay of `session-b1e697f9-…` (no mutation) at the forensic cutoff seq 287005:

| metric | BEFORE | AFTER |
|---|---|---|
| starts replayed | 549 | 549 |
| rangeNonNull | 549 | 4 |
| checkpoint selected as region start | **548** | **0** |
| null (no summarizer) | 0 | 545 |
| same-region unchanged retries | 528 (summary-not-smaller storm) | 0 |

`checkpointRecompactEliminated = true`; the 565/586 failure storm is eliminated by construction, while genuine normal-history compactability (the 4 non-null ranges corresponding to the real big reductions) remains intact.

## 8. Isolated runtime physical test

- Harness: real `Session` (dsh-session), real cordis `Context`, patched engine, stub token meter, no real LLM.
- Scenario executed in test 12: large history → compact (creates checkpoint) → append new normal history → pressure again → compacts normal region while checkpoint stays at head; plus test 6's mutation-reevaluation path.
- Deployment on the actual checkout was performed in a **reversible** way: `install-convergence.ps1` + backup + marker; `restore-convergence.ps1` round-trip verified with SHA-256 (restore → upstream hash; install → fork hash).
- Note: the live GUI process was not restarted to avoid interrupting this session; restart is required for the running process to pick up the overlay (documented in the install script output).

## 9. Model contextWindow truth

- `settings.yaml`: `cpa/gpt-5.6-sol-xhigh` has no `contextWindow`; only `any/claude-opus-5` declares 1M.
- Gateway `GET /v1/models` returns no capacity fields.
- Session evidence: the provider accepted ~511K projected tokens without a provider overflow, which is consistent with a larger true window, but there is **no authoritative capacity** to record.
- Decision: do not write a guessed `contextWindow`; configuration fix stays a separate, evidence-gated change. **MODEL_WINDOW_TRUTH: UNKNOWN** (fallback 262144 remains the only declared value).

## 10. Deployment / reproducibility

- Pinned local package overlay (scripted, not a manual npm-cache edit):
  - `install-convergence.ps1` detects the checkout, backs up upstream, copies the fork over the same package name (loader/patch unchanged), writes a marker with hashes.
  - `restore-convergence.ps1` restores the backup; install is idempotent and recovers interrupted states.
  - Verified round-trip on this machine (hashes match).
- Rebuild on another machine: copy `dsh-compaction-basic-convergence/` and run the two scripts; same fork hash ⇒ same behavior.
- **RUNTIME_REPRODUCIBILITY: PASS** (pinned source + scripts + marker; must be re-installed after any `npm install` refreshes the checkout node_modules).

## 11. Rollback

`restore-convergence.ps1 -Checkout <checkout>` restores the pristine upstream package (hash-verified). The backup lives under `.dsh-convergence-backup/`. Rolled back and re-installed successfully during this session; both directions hash-verified.

## 12. Regression

- This change is isolated to the new pinned package directory; no existing repository source file was modified in this task (the working tree diff present is from the previous S7 task and untouched).
- Test suite: 12/12 convergence tests pass; offline replay reproduces the elimination of checkpoint re-compaction; overflow/pruner/derived-surface semantics unchanged by tests 7–10.
- The novel-main objective (S7 gap closure) remains the separate tracked goal, resumed after this change.

## Final verdict

- CHECKPOINT_RECOMPACTION — **PASS** (548 checkpoint re-selections → 0; offline replay proof)
- PRESSURE_RETRY_CONVERGENCE — **PASS** (same-region unchanged → zero duplicate summarizer; fingerprinted reevaluation on any surface change)
- SURFACE_REDUCTION — **PASS** (ordinary reductions preserved; big reductions still selectable; all-checkpoint regions return null without LLM cost)
- MODEL_WINDOW_TRUTH — **UNKNOWN** (no authoritative capacity; fallback 262144 retained; separate evidence-gated config change)
- RUNTIME_REPRODUCIBILITY — **PASS** (pinned local package + install/restore scripts + marker + hash-verified round-trip)

**DSH_COMPACTION_CONVERGENCE_FIX = PASS** (core runtime items and reproducibility PASS).