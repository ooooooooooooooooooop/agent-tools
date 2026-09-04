# DSH Workspace Registry Integrity — Fix Design

**Audit:** `DSH_WORKSPACE_REGISTRY_INTEGRITY_FORENSICS`
**Status:** FIX DESIGN ONLY (no implementation performed)
**Evidence grade:** `SECOND_WRITER_STRONGLY_SUPPORTED_NOT_IDENTIFIED` (mechanism confirmed; no clean photograph of two concurrent host PIDs at the incident moment)
**Date:** 2026-09-04

---

## 0. Executive summary (plain language)

On 2026-09-03 ~12:40 local, a duplicate workspace (`c1e48f21…`, path `C:\Desktop\共享\wurank.top`) was created in the live DSH host and **never added to the registry order**. Its table record (with 3 real WURANK-V2 sessions) survived, but its order entry was lost — leaving `tables.workspaces` = 9 while `global.workspaceIds` = 8. The next host startup correctly refused to boot (`workspace domain is inconsistent`).

The bad state cannot be produced by a clean single-process DSH create (which is serialized, dedupes paths, appends order last, and leaves a recoverable crash marker). It is the signature of **two writers with divergent in-memory views racing on one whole-file JSON store** (`workspace.json`) that has **no cross-process lock and no revision check** — the last write wins per whole file. The 09-03 host churn (six `web` starts, restart-script port races observed three times, plus non-shortcut launches from `aic apply dsh`/sync) is the exposure.

This document is the **fix design**, not the implementation. It is deliberately layered so each piece can be adopted independently and rolled back.

---

## 1. Root-cause statement (evidence-graded)

```text
PRIMARY_ROOT_CAUSE      = CROSS_PROCESS_LOST_UPDATE on workspace.json (whole-file last-write-wins,
                          no cross-process lock, no revision/CAS), producing BOTH invariant breaks
                          (duplicate canonical path + record present in table but absent from order)
                          from one divergent-writer interleaving.
CONTRIBUTING_CAUSES     = dsh_desktop_restart.ps1 port-residual race (Wait-PortFree ignores 15s timeout,
                          proceeds anyway => old host port not released when new host starts)
                        + multiple host launch paths (desktop shortcut, aic apply dsh, sync engine restart)
                          with no single-instance guard
                        + no write-time invariant validation (validateStoredState runs only at startup)
ROOT_CAUSE_1            = CROSS_PROCESS_LOST_UPDATE (dup path + orphan record from divergent writers)
ROOT_CAUSE_2            = MISSING_SINGLE_WRITER_COORDINATION + MISSING_WRITE_TIME_VALIDATION
                          (the two "bugs" the spec warns not to force-merge — they are distinct defects:
                          R1 = the lost-update mechanism, R2 = the absence of guards that would catch it
                          at write time instead of next startup)
```

The spec's §26 warning is honored: **two distinct defects**, not one. R1 is the writer-coordination hole; R2 is the missing fail-fast validation. A fix that only adds a lock (R1) but no write-time check (R2) would still let a *different* future inconsistency reach the next startup silently.

---

## 2. Design principles

1. **Root fix at the writer-coordination layer** (R1), not a band-aid on the restart script.
2. **Fail-fast at write time** (R2): an inconsistent state must never be persisted as a "clean" file that only the next startup rejects.
3. **Never auto-merge** ambiguous state. Recovery is `REVIEW_REQUIRED` + a concrete repair plan, never unconditional `same-path → merge`.
4. **Repo-owned, layered, reversible.** Because `dsh-workspace`/`dsh-storage-json` are upstream base packages (not repo overlays), the repo cannot patch them in-tree like `context-pressure-guard`. The design therefore has an **upstream layer** (what DSH itself should change) and a **repo layer** (what Personal AI can own today without upstream).
5. **The restart race is a contributing exposure, not the root** — fixing only `dsh_desktop_restart.ps1` would leave the other launch paths and the fundamental no-lock store intact.

---

## 3. Fix layers

### Layer A — Single-writer coordination on the registry (R1 root, upstream-first)

**Goal:** make concurrent writers impossible, not merely unlikely.

**A1. Cross-process ownership lock (recommended, upstream `dsh-workspace`/`dsh-storage-domain`):**
- Before the workspace domain opens `workspace.json`, acquire an **OS-level exclusive lock** (file lock / lock-file with PID + heartbeat + stale-timeout) on the storage root.
- A second host that cannot acquire the lock must **fail closed at startup** (refuse to serve the workspace registry), not proceed with a stale in-memory view.
- Lock must be **released on clean close** and **recoverable on crash** (stale-PID timeout), mirroring the existing `pendingMutation` recovery philosophy.

**A2. Revision / CAS on the JSON backend (`dsh-storage-json`):**
- Add a monotonic `revision` (or content hash) to each persisted unit.
- Every write carries the revision it was based on; `writeAtomic` becomes **compare-and-swap**: if the on-disk revision differs from the writer's base, the write is **rejected** (`STALE_WRITE_REJECTED`), the writer re-reads, re-applies its mutation, and retries — **never last-write-wins**.
- This is the single most important upstream change: it converts the silent lost-update into a loud, retryable conflict.

**A3. Single logical transaction for create (upstream `dsh-workspace`):**
- `createCanonical`'s table-put + order-append + session-attach must be **one atomic next-state** under the CAS, so a crash or a rejected stale write cannot leave a record-without-order.
- The existing `pendingMutation` marker is good; extend it so the **order append is part of the same CAS commit** as the table put (today a crash between them leaves the marker, but a *stale-writer* CAS reject between them is what produced this incident).

**Rationale / why:** A lock (A1) alone prevents two hosts but not a direct/bypass writer or a future single-process bug; CAS (A2) + atomic transaction (A3) make the write itself fail-closed on any divergence. All three are upstream changes; the repo cannot ship them today, but they are the correct end state.

### Layer B — Write-time invariant validation (R2, repo-owned, non-invasive)

**Goal:** never let an inconsistent state be persisted as a clean file; detect it the moment a write would create it.

**B1. Repo overlay guard (recommended, new `dsh/` overlay or extend a repo-owned plugin):**
- Because the repo already ships overlays that mount into the DSH profile (context-pressure-guard, context-lifecycle, …), add a **thin workspace-integrity validator** that subscribes to `domain/changed` on the workspace domain and, **before** a mutation commits, re-checks the invariants:
  - `workspace record id set == order id set`
  - order has no repeats; every order id resolves
  - normalized workspace path unique (`fs.realpath`)
  - session ownership not duplicated across workspaces
- On violation: **refuse the write** (fail closed) and emit a structured diagnostic — never persist the bad state.
- This overlay holds **no generation-bound state** and registers **no Host Service** (same constraint as the model-switch controller).

**B2. Startup diagnostic hardening (upstream + repo):**
- Keep startup fail-closed (it correctly caught this incident), but make the error a **structured diagnostic** listing: missing order ids, duplicate normalized paths, conflicting session ownership, stale revision / pending mutation — so a human or an automated repair tool gets an actionable plan instead of a bare string.

**Rationale:** B1 catches the class at write time (R2); B2 makes the unavoidable startup failure actionable. B1 is repo-ownable today as an overlay; B2's diagnostic is upstream text but the repo can pre-parse it in its health tooling.

### Layer C — Launcher / restart single-instance + no-race (repo-owned, CONTRIBUTING_EXPOSURE_FIX)

**Goal:** remove the concrete restart race and the multiple-launch-path ambiguity.

**C1. `dsh_desktop_restart.ps1` (repo `scripts/aic/`):**
- Replace the "proceed anyway after 15s on residual port connections" with **fail closed**: if port 3080 is not confirmed free after the timeout, abort with a clear error (do not start a second host over a possibly-live one). This directly removes the observed 08:27/08:39/17:01 race.
- Add a **single-instance guard** at the top: if a DSH host is already running and healthy, either reuse it or refuse, instead of kill-and-restart blindly on a double-click.

**C2. `dsh-launch-web.ps1` (repo `dsh-config/profiles/web/`):**
- Add the same single-instance guard so **any** launch path (shortcut, sync-engine restart, manual) refuses to start a second host when one is already bound to the registry.

**C3. Centralize launch:** document/enforce that the **only** launchers are `dsh-launch-web.ps1` and `dsh_desktop_restart.ps1`, both single-instance-guarded. The sync engine's `run_sync(request_restart=True)` already calls `dsh_desktop_restart.ps1` (good); it must inherit the guard.

**Marking:** this is explicitly a **CONTRIBUTING_EXPOSURE_FIX**, not the storage root fix. It removes the trigger frequency; it does not make the store safe (Layer A does).

### Layer D — Recovery policy (no unconditional auto-merge)

**Goal:** never silently merge ambiguous state.

- **Only** recover automatically when the state is unambiguous (e.g., a lone orphan whose path is unclaimed and whose sessions are a strict superset of nothing — the exact manual-merge preconditions: same canonical path, disjoint non-conflicting session sets, one clearly-canonical older record).
- Otherwise: `REVIEW_REQUIRED` + a concrete repair plan (which record is canonical, which sessions to fold, what to archive), surfaced to a human/automated reviewer.
- **Never** auto-merge on `same-path` alone (workspace identity may carry meaning; sessions may conflict; a path may be legitimately reused).

### Layer E — Health check in Sync (repo-owned, future)

- Extend `personal_ai_sync.py::session_history_status` (which already computes workspace coverage and returns REVIEW on unattached sessions) with an explicit `WORKSPACE_REGISTRY_INTEGRITY` plane checking: records/order set equality, normalized-path uniqueness, unique session ownership, reference existence. This makes the class of this incident visible on every `personal-ai sync check` without waiting for a host restart.
- Marked future/optional — does not block the core fix.

---

## 4. Adversarial concurrency test plan

Real multi-process tests (the repo's node test harness stages into the DSH checkout; the python tests run under `unittest`):

| Case | Scenario | Must hold |
|---|---|---|
| **A** | Process A and B both read revision N; A writes N+1; B attempts N-based write | B **rejected** (`STALE_WRITE_REJECTED`), never silent overwrite |
| **B** | Two processes concurrently `create` the same normalized path | exactly one canonical workspace survives; the loser either reuses it or fails, never creates a duplicate |
| **C** | Crash between table-put and order-append in `createCanonical` | disk never holds a domain-invalid committed state (pendingMutation recovery removes it, or CAS rejects) |
| **D** | Restart overlap: old host not fully exited, new host starts | new host must **not** acquire production writer ownership (single-instance guard / lock) |
| **E** | Direct/bypass writer (a process writing `workspace.json` without the registry API) | rejected by the stale-write guard / write-time validation, or the startup diagnostic names it |
| **F** | Session ownership conflict (two workspaces claim one session) | persist is refused before commit |

---

## 5. Proposed implementation order

1. **Upstream Layer A1+A2** (cross-process lock + revision/CAS on `dsh-storage-json`/`dsh-workspace`) — the root fix. Highest risk, highest value; requires upstream or a repo-managed patched overlay.
2. **Layer B1** (repo write-time invariant overlay) — independent, catches the class regardless of upstream timing.
3. **Layer C** (launcher single-instance + no-race) — low-risk, removes the observed trigger; do first if upstream is slow.
4. **Layer D** (recovery policy doc + `REVIEW_REQUIRED` diagnostics) — safe, no code risk.
5. **Layer E** (Sync health plane) — additive observability.
6. **Layer B2** (startup diagnostic text) — upstream polish.

Recommended first concrete deliverable: **Layer C + Layer B1**, both fully repo-owned and independently revertible, giving immediate protection against the observed trigger while the upstream CAS (A2) is negotiated.

---

## 6. Risk of change / rollback

- **Layer A** (upstream): highest risk (core storage semantics). Rollback = revert to the pre-patch base install; must be tested against the existing overlay set. The CAS changes last-write-wins to fail-on-stale, so any latent single-process write bug will surface loudly — desirable, but needs a migration window.
- **Layer B1** (write-time guard): low risk (additive, read-only until a violation). Rollback = remove the overlay row from `cordis.patch.yml` + redeploy.
- **Layer C** (launcher): low risk. Rollback = restore the previous `dsh_desktop_restart.ps1`/`dsh-launch-web.ps1`.
- **Layer D/E**: negligible risk.

All layers are independent; none requires another to be safe to ship.

---

## 7. Why this fix (one-paragraph)

The incident is a **lost-update between writers with no coordination and no write-time check**: one process's table-write (a real WURANK-V2 workspace + sessions) survived while another's order-write (without that workspace) won the whole-file race. The durable fix is to make the store **single-writer (lock) and stale-write-proof (CAS)**, to make create **one atomic next-state**, and to **validate invariants at write time** so a bad state can never be persisted silently. The restart-script port race is a real contributor that should be fixed (fail-closed, single-instance) but is **not** the root — the root is that the store itself has no cross-process coordination and no write-time guard. Repo-owned overlays (Layer B1) and launcher guards (Layer C) can ship immediately; the upstream CAS (Layer A) is the correct long-term root fix.

---

## 8. Standard key-value output

```text
FIX_DESIGN = COMPLETE
MECHANISM_ROOT_CAUSE = CONFIRMED
SECOND_WRITER_INSTANCE = STRONGLY_SUPPORTED_NOT_IDENTIFIED
ROOT_CAUSE_1 = CROSS_PROCESS_LOST_UPDATE (whole-file last-write-wins on workspace.json, no cross-process lock/CAS)
ROOT_CAUSE_2 = MISSING_WRITE_TIME_VALIDATION (validateStoredState only at startup)
TARGET_WRITER_MODEL = single canonical owner + stale-write-rejected CAS + one atomic create transaction
CROSS_PROCESS_COORDINATION = Layer A1: OS-level exclusive lock, fail-closed on second owner
REVISION_CAS = Layer A2: monotonic revision + compare-and-swap on the JSON backend
LOGICAL_TRANSACTION_MODEL = Layer A3: table-put + order-append + attach as one CAS next-state
WRITE_TIME_VALIDATION = Layer B1: repo write-time invariant overlay (fail-closed on violation)
ATOMIC_PERSISTENCE = keep writeAtomic (temp+fsync+rename) but add CAS precondition
DUPLICATE_PATH_POLICY = reuse existing canonical workspace or fail; never silent duplicate create
BYPASS_WRITER_POLICY = stale-write guard rejects direct/bypass writes; startup diagnostic names them
RESTART_RACE_POLICY = Layer C: fail-closed port-free wait + single-instance guard; CONTRIBUTING_EXPOSURE_FIX
STARTUP_DIAGNOSTIC = Layer B2: structured (missing order ids / dup paths / session conflicts / stale revision)
RECOVERY_POLICY = REVIEW_REQUIRED + plan unless unambiguous; never auto-merge on same-path alone
UPSTREAM_COMPONENTS_TO_CHANGE = dsh-storage-json (CAS), dsh-workspace (lock + atomic create + write-time validate)
PERSONAL_AI_COMPONENTS_TO_CHANGE = dsh_desktop_restart.ps1, dsh-launch-web.ps1, new write-time integrity overlay, personal_ai_sync health plane
ADVERSARIAL_TEST_PLAN = Cases A-F (stale-write reject, concurrent same-path create, crash atomicity, restart overlap, bypass writer, session conflict)
PROPOSED_IMPLEMENTATION_ORDER = C+B1 first (repo-owned, revertible) → A (upstream CAS) → D → E → B2
RISK_OF_CHANGE = A highest (core storage); B1/C low (additive/revertible); D/E negligible
ROLLBACK_PLAN = per-layer revert (see §6); none requires another to ship
FILES_MODIFIED = NONE (design only)
CONFIG_MODIFIED = NONE
RUNTIME_MODIFIED = NONE
SESSION_DATA_MODIFIED = NONE
COMMIT = NONE
PUSH = NONE
OVERALL = FIX_DESIGN_COMPLETE
```

---

## 9. Current data-integrity confirmation (unchanged, read-only)

The manual recovery performed earlier is verified sound and untouched by this design:
- `workspace.json` (current): order == tables == 8, no duplicate path, no orphan, A holds the exact disjoint union (6 sessions), no session leaked.
- Backups (`bak-20260904`, `pre-attachment-fix-bak`) preserved untouched for evidence.
- This document changes no file, config, runtime, session data, commit, or push.
