# DSH Workspace Registry — Layer A Upstream Root-Storage Patch Spec

**Task:** `DSH_WORKSPACE_REGISTRY_INTEGRITY_REMEDIATION` — Layer A (root storage)
**Status:** SPEC + ADVERSARIAL TESTS (no repo deploy-pipeline change; root fix waits on upstream/base rebuild)
**Date:** 2026-09-04

---

## 1. Purpose

Phase-1 (repo-owned protections: launcher single-instance + fail-closed restart,
read-only integrity health check) reduced the *trigger* frequency. This document
is the **Layer A root fix spec** for the actual storage defect: `dsh-storage-json`
and `dsh-workspace` (deep base packages) have no cross-process coordination and
no write-time validation, so two hosts sharing `~/.dsh/storages/workspace.json`
produce a whole-file lost-update — the `DSH_WORKSPACE_REGISTRY_INTEGRITY`
incident (2026-09-04).

These packages are baked into the pinned base snapshot
(`base-dsh-0.1.1-rc.2/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/`)
and are imported via bare specifiers by `dsh-host-apiproxy`, `dsh-message-feedback`,
`dsh-session-projection-cache`, `dsh-storage`, etc. The repo's `dsh_runtime.apply`
does not swap deep base packages at deploy time (only plugin overlays into
`profiles/web/plugins/`). Therefore the root fix is an **upstream change** to
these two packages (or a base rebuild carrying the patched packages). This spec
is precise enough to become an upstream PR; the adversarial tests prove the
defect on the current upstream code.

---

## 2. Target packages & versions (pinned)

| Package | Version | File | Role |
|---|---|---|---|
| `@deepseek-ai/dsh-storage-json` | 0.1.1-rc.2 (base snapshot) | `lib/index.js` | JSON KV backend: whole-file atomic replace, per-process in-memory authoritative state |
| `@deepseek-ai/dsh-workspace` | 0.1.1-rc.2 (base snapshot) | `lib/types/index.js`, `lib/types/spec.js` | Workspace registry: createCanonical (multi-write table+order), bootstrap, validateStoredState |
| `@deepseek-ai/dsh-storage-domain` | 0.1.1-rc.2 | `lib/index.js` | Domain write chain (single per-process chain), `domain/changed` (post-commit only) |

Base entry SHA-256 (provenance anchor): `c0226687bb20f45c603ec6fe50f3de16d1c3510c3a803304ec575ef9bc366c62` (from `base-distribution.json`).

---

## 3. Root-cause mechanism (what the patch must fix)

1. **`dsh-storage-json` whole-file last-write-wins, no cross-process lock.**
   `JsonStorageBackend.kv.open` enforces "a unit has exactly one live handle"
   **per backend instance (= per process)**. Two OS processes each open the same
   unit file; each keeps an authoritative in-memory `state` and republishes the
   WHOLE file via `writeAtomic` (temp+fsync+rename) on every write. There is no
   cross-process file lock and no revision check, so the last writer's whole-file
   publish silently overwrites the other's — even if the other made a newer,
   disjoint change. **Proven by `test/storage-lost-update.test.mjs`.**

2. **`dsh-workspace.createCanonical` is not a single logical transaction.**
   It performs: `setState(pendingMutation)` → `table.put(id, record)` →
   `setState(workspaceIds:[id,...])`. Under a single process the domain write
   chain serializes these, and a crash leaves a recoverable `pendingMutation`.
   But under two processes, each has its own chain + in-memory view, so the
   table-put and order-append can land in *different* whole-file publishes from
   *different* processes → record in table, absent from order, no pending marker.
   `rebuildEntities` builds `entities` only from order ids, so a second `create`
   on the orphaned path would not dedupe → duplicate canonical path.

3. **`validateStoredState` runs only at startup.** There is no write-time
   invariant check, so a bad state is persisted silently and only the next boot
   rejects it.

---

## 4. The upstream patch (exact changes)

### 4.1 `dsh-storage-json` — revision/CAS + cross-process lock

**File:** `dsh-storage-json/lib/index.js`

**(a) Add a monotonic revision to each persisted unit.**

- In `serialize(name, state)`, include a `revision` in the document header
  (e.g. under `unit.revision`), incremented on every publish.
- On `parse`, read the stored revision; a missing revision (legacy file)
  initializes to `0`.
- Add `unit.revision` to the `loadAll`/open snapshot so the domain can read the
  current revision.

**(b) Make `publish()` (whole-file write) a compare-and-swap.**

- `writeAtomic(path, data)` becomes `writeAtomic(path, data, expectedRevision)`:
  before the rename, atomically check the on-disk revision still equals
  `expectedRevision`; if not, **abort without writing** and throw
  `STALE_WRITE_REJECTED` (a new `StorageError` code).
- The caller (domain write chain / `putRecord`/`setGlobal`) passes the revision
  it read when it last loaded/mutated state.
- On `STALE_WRITE_REJECTED`, the caller must **re-read, re-apply its mutation,
  and retry** — never blind last-write-wins. The domain write chain already
  serializes within a process; the CAS makes it safe across processes.

**(c) Add an optional cross-process exclusive lock on unit open.**

- On `openUnit`, acquire an OS-level exclusive lock on `<unit>.lock`
  (or the unit file itself) with a **stale-PID timeout** so a crashed holder
  does not deadlock the registry forever.
- A second process that cannot acquire the lock must **fail closed at open**
  (the workspace registry refuses to start) rather than run with a stale
  in-memory view. This makes the "two writers" precondition impossible.

**Rationale:** (b) is the core fix — it turns the silent lost-update into a loud,
retryable conflict. (c) prevents the two-writer precondition. (a) is the
revision primitive (b) needs.

### 4.2 `dsh-workspace` — single atomic create + write-time validation

**File:** `dsh-workspace/lib/types/index.js` (+ `spec.js` if schema changes)

**(a) Make `createCanonical` one CAS transaction.**

- Build the complete next state (table record + order + `pendingMutation`) and
  commit it in **one** storage-domain operation that the CAS protects — not two
  separate `table.put` + `setState` whole-file writes. Under the storage-json CAS,
  a stale writer's create is rejected atomically; the winner's full next-state
  (record AND order) lands together, so a record-without-order cannot be produced.

**(b) Write-time invariant validation.**

- Run the `validateStoredState` checks **before every durable mutation commits**
  (not only at startup), using the same invariants: records/order set equality,
  no dup normalized path, no session double-account. A mutation that would
  produce an invalid state must be **refused before persist**.
- This requires a pre-write seam in `dsh-storage-domain` (today only
  `domain/changed` post-commit exists). Add a pre-write hook (e.g.
  `domain/changing`) that the workspace domain can use to validate the next
  state, OR move the invariant check into `createCanonical`/`attachSession`
  before their writes (simpler, workspace-local).

**Rationale:** (a) removes the mechanism that produced the orphan; (b) makes any
future divergence fail at write time instead of next startup. Both need the
storage-json CAS (4.1) to be truly safe across processes.

### 4.3 `dsh-storage-domain` — optional pre-write hook

If the workspace-local validation (4.2b) is insufficient (e.g. `attachSession`
and `create` both need to see the post-mutation state before any write), add a
`domain/changing` pre-write waterfall in `dsh-storage-domain` so a domain can
inspect the about-to-commit next state and veto it. This is the "write-time"
seam the repo's Phase-1 Layer B1 was blocked on.

---

## 5. Acceptance criteria (the tests that must turn GREEN after the patch)

Adversarial test fixtures currently **pass = prove the defect** (RED). After the upstream patch:

1. `dsh/workspace-registry-root-fix/test/storage-lost-update.test.mjs` (backend-instance level):
   - Two instances open the same unit file (no cross-process lock)
   - CASE A: whole-file last-write-wins silently loses a concurrent create
   - CASE B: divergent table/order views leave an orphan (record without order)

2. `dsh/workspace-registry-root-fix/test/multiprocess-lost-update.test.mjs` (true multi-process Node OS processes):
   - Two distinct OS processes open the same storage unit without mutual exclusion
   - Process A reads state N, Process B reads state N, B publishes mutation, A publishes stale mutation -> B update lost (`TRUE_MULTIPROCESS_LOST_UPDATE_REPRO=PASS`)
   - Interleaved multi-process create leaves record in tables but absent from order (`TRUE_MULTIPROCESS_INCIDENT_SHAPE_REPRO=PASS`)

| Test | Current (unpatched) | After CAS + lock |
|---|---|---|
| Two processes open the same unit file | PASS (no lock) | second open FAILS CLOSED (lock) |
| CASE A: concurrent create dropped from order | PASS (lost update) | **`STALE_WRITE_REJECTED`**; create retries on fresh state; no loss |
| CASE B: record orphaned from order | PASS (orphan) | **impossible** (single atomic create + write-time validation) |
| Multi-process lost-update repro | PASS (lost update proven) | **`STALE_WRITE_REJECTED`**; second write fails or retries |
| Multi-process orphan incident shape | PASS (orphan proven) | **impossible** (atomic write-time validation) |

New tests to add upstream:
- **Stale-write rejection**: process B based on revision N attempts a write after
  A committed N+1 → B must get `STALE_WRITE_REJECTED`, re-read, re-apply.
- **Concurrent same-path create** → exactly one canonical workspace survives.
- **Crash between table-put and order-append** → no domain-invalid committed state
  (pendingMutation recovery or CAS rejects).
- **Cross-process lock**: second open while first holds the lock → fail closed.

---

## 6. Deployment boundary (why this is upstream, not repo-injected)

- `dsh-storage-json`/`dsh-workspace`/`dsh-storage-domain` are baked into the
  pinned base snapshot; the repo's `dsh_runtime.apply` deploys only plugin
  overlays (`profiles/web/plugins/`) and does not swap deep base packages.
- The precedent `dsh-compaction-basic-convergence` fork is **pre-installed in the
  pinned base**, not apply-injected.
- Therefore the root fix requires an **upstream release / base rebuild** carrying
  the patched packages. This spec + the adversarial tests are the contract that
  base must satisfy. The repo should NOT extend `dsh_runtime` to hot-patch deep
  base packages (high risk, new deploy capability, contradicts the "managed base"
  immutability that was just stabilized).

---

## 7. Machine key-value output

```text
LAYER_A_STATUS = SPEC_AND_TESTS_COMPLETE (root fix pending upstream/base rebuild)
ROOT_CAUSE_1 = CROSS_PROCESS_LOST_UPDATE (whole-file last-write-wins, no lock/CAS)
ROOT_CAUSE_2 = MISSING_WRITE_TIME_VALIDATION (validateStoredState only at startup)
TARGET_PACKAGES = @deepseek-ai/dsh-storage-json, @deepseek-ai/dsh-workspace, @deepseek-ai/dsh-storage-domain
TARGET_VERSION = 0.1.1-rc.2
BASE_ENTRY_SHA256 = c0226687bb20f45c603ec6fe50f3de16d1c3510c3a803304ec575ef9bc366c62
PATCH_1 = dsh-storage-json: monotonic revision + compare-and-swap on whole-file publish + optional cross-process lock on open
PATCH_2 = dsh-workspace: single atomic create (record+order) under CAS + write-time invariant validation
PATCH_3 = dsh-storage-domain: optional domain/changing pre-write hook
ADVERSARIAL_TESTS = dsh/workspace-registry-root-fix/test/storage-lost-update.test.mjs (3/3 pass), dsh/workspace-registry-root-fix/test/multiprocess-lost-update.test.mjs (3/3 pass)
TRUE_MULTIPROCESS_LOST_UPDATE_REPRO = PASS
TRUE_MULTIPROCESS_INCIDENT_SHAPE_REPRO = PASS
UPSTREAM_PATCH_SPEC = PASS
ROOT_STORAGE_FIX = PENDING_UPSTREAM_BASE_REBUILD
DEPLOY_BOUNDARY = upstream release / base rebuild required; repo does NOT hot-patch deep base packages
FILES_ADDED = dsh/workspace-registry-root-fix/test/storage-lost-update.test.mjs, dsh/workspace-registry-root-fix/test/multiprocess-lost-update.test.mjs, dsh/workspace-registry-root-fix/test/multiprocess-worker.mjs, docs/dsh-workspace-registry-root-fix-spec.md
FILES_MODIFIED = NONE (spec + tests only)
OVERALL = LAYER_A_SPEC_DELIVERED
```
