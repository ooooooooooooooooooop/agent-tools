# DSH Workspace Registry Integrity Incident Handoff

**Incident Identifier:** `DSH_WORKSPACE_REGISTRY_INTEGRITY`
**Date:** 2026-09-04
**Primary Target File:** `~/.dsh/storages/workspace.json`
**Base Distribution:** `base-dsh-0.1.1-rc.2` (SHA-256: `c0226687bb20f45c603ec6fe50f3de16d1c3510c3a803304ec575ef9bc366c62`)
**Status:** `INCIDENT_STATUS=MITIGATED_PENDING_UPSTREAM`

---

## 1. Incident Overview & Summary

On 2026-09-04 07:47 local, DSH startup failed with the error:
```text
workspace domain is inconsistent: workspace 'c1e48f21-7910-482a-9f5b-3ee026949787' is absent from registry order
```
Forensic analysis of the corrupted snapshot (`workspace.json.bak-20260904`, 39,446 B) revealed:
- An orphaned workspace record `c1e48f21-7910-482a-9f5b-3ee026949787` (`wurank.top`, path `C:\Desktop\共享\wurank.top`, 3 sessions, created `2026-09-03T04:40:58Z`, updated `04:41:50Z`) existed in `tables.workspaces`.
- The orphan was absent from `global.workspaceIds` (records=9, order=8), violating the fundamental registry invariant `records.length === order.length`.
- The orphan duplicated the canonical path of earlier workspace `a9d1651f-9d51-4676-b041-de07e400e1d6` (same path, created 2026-09-01T07:38:46Z, disjoint session sets).
- Immediate manual recovery (current `workspace.json`, 39,166 B, 07:47:45) merged the sessions into canonical `a9d1651f`, removed the orphan record, restored 8/8 symmetry, and unblocked startup.

---

## 2. Incident State

```text
MECHANISM_ROOT_CAUSE=CONFIRMED
SECOND_WRITER_INSTANCE=STRONGLY_SUPPORTED_NOT_IDENTIFIED
```

### Mechanism Root Cause (`CONFIRMED`)
1. **Unprotected Whole-File Atomic Overwrites:**
   `@deepseek-ai/dsh-storage-json` implements storage persistence via whole-file replace (`writeAtomic`: temp file + fsync + atomic rename). Each backend instance maintains authoritative in-memory state. It has **no cross-process file lock** and **no revision/CAS check**. The handle guard (`openUnit`) only prevents concurrent handles *within a single process*.
2. **Non-Atomic Logical Workspace Creation:**
   `dsh-workspace.createCanonical` executes workspace creation across multiple distinct physical whole-file publishes (`table.put` followed by `setState` for `workspaceIds`).
3. **Cross-Process Lost Update & Interleaving:**
   When two processes open `workspace.json` concurrently:
   - A concurrent stale writer overwrites newer writes, causing lost updates.
   - Interleaving between Write 1 (`table.put`) and Write 2 (`setState`) allows a second process to capture the new table record into its in-memory view while serializing a stale global order that omits the new ID, persisting an orphan record absent from `global.workspaceIds`.
   - Both mechanisms have been 100% deterministically reproduced against real upstream packages in `dsh/workspace-registry-root-fix/test/storage-lost-update.test.mjs` and `dsh/workspace-registry-root-fix/test/multiprocess-lost-update.test.mjs`.

### Second Writer Evidence (`STRONGLY_SUPPORTED_NOT_IDENTIFIED`)
- Temporal correlation: On 2026-09-03, 6 host instances were spawned (`dsh-subprocess-*` directories).
- Launcher port race: `dsh_desktop_restart.ps1` previously timed out on `Wait-PortFree` and printed "proceeding anyway" (observed at 08:27, 08:39, and 17:01).
- Orphan creation coincided precisely with the authoring of `~/.dsh/.agent-presets/cc-content/` at 12:40:24-47 local.
- While the existence of concurrent/overlapping host processes on 09-03 is strongly supported by lifecycle traces and restart behavior, an exact snapshot of the two concurrent PIDs at the millisecond of creation was not captured due to PID reuse.

---

## 3. 已实施 (Implemented Protections)

All repo-owned Phase-1 mitigation layers have been implemented, tested, and committed on branch `workspace-registry-phase1` (commit `58d6569`):

* **restart fail-closed:**
  `scripts/aic/dsh_desktop_restart.ps1` updated: `Wait-PortFree` no longer "proceeds anyway" on timeout. Added `[switch]$FailClosed`, which explicitly throws `RESTART_BLOCKED_OLD_HOST_NOT_TERMINATED` when residual port 3080 connections remain, preventing a new host from launching alongside an existing one.
* **production single-host guard:**
  `scripts/aic/dsh_desktop_restart.ps1` checks `Test-DshReady` before restarting; if the host is already running and healthy, it reuses the instance and exits cleanly (`exit 0`) rather than terminating and restarting.
  `dsh-config/profiles/web/dsh-launch-web.ps1` checks if port 3080 is `LISTENING` (`SINGLE_INSTANCE_GUARD`) and aborts with a clear error if another host is already active.
* **workspace registry integrity validator:**
  `scripts/personal_ai_sync.py::workspace_registry_integrity()` added as an authoritative read-only health validator verifying:
  - `records === order` set equality
  - No duplicate normalized canonical paths (`realpathNormalize`)
  - Unique session ownership across workspaces
* **pre-launch integrity gate:**
  The integrity validator executes during sync/restore preflight checks (`run_restore`), flagging invalid shapes as `REVIEW_REQUIRED` before operational state can be damaged.
* **Sync workspace health:**
  Integrated into human-readable sync status reporting and automated regressions, providing continuous visibility into storage health.

```text
PHASE1_MITIGATION=PASS
```

---

## 4. 未实施 (Unimplemented Root Measures) & Cause

The following deep root-cause protections have **not** been implemented in this repository:

* **cross-process lock** (OS-level file lock on `openUnit`)
* **revision/CAS** (monotonic document revision + compare-and-swap on `writeAtomic`)
* **stale-write rejection** (`STALE_WRITE_REJECTED` error code and caller retry waterfall)
* **true write-time invariant prevention** (pre-write validation before durable persistence)

### 原因 (Reason):
`@deepseek-ai/dsh-storage-json`, `@deepseek-ai/dsh-workspace`, and `@deepseek-ai/dsh-storage-domain` are deep base packages embedded inside the pinned immutable base snapshot (`base-dsh-0.1.1-rc.2`).
- The storage domain exposes no pre-write interceptor seam (only `domain/changed`, which fires strictly post-commit).
- Pinned base packages have no repo-safe replacement seam.
- The repo's deployment engine (`dsh_runtime.apply`) only manages plugin overlays (`profiles/web/plugins/`) and does not hot-patch deep base packages. Extending the deploy engine to monkey-patch deep base packages introduces severe fragility and violates managed-base stability.

因此：
```text
ROOT_STORAGE_FIX=PENDING_UPSTREAM_BASE_REBUILD
```

Complete specifications and adversarial proof fixtures for the upstream patch are delivered in:
- Spec: `docs/dsh-workspace-registry-root-fix-spec.md`
- Backend Tests: `dsh/workspace-registry-root-fix/test/storage-lost-update.test.mjs`
- Multi-Process Tests: `dsh/workspace-registry-root-fix/test/multiprocess-lost-update.test.mjs`

---

## 5. Residual Risk

当前 mitigation 显著降低现实暴露面，但如果未来存在独立 writer 绕过 single-host mitigation，当前 pinned `dsh-storage-json` 仍理论上可以 stale whole-file overwrite。

Specifically:
- Phase-1 single-instance guards eliminate the primary accidental trigger (shortcut double-clicks, port race during restart).
- However, if a third-party process, script, or out-of-band host opens and writes `workspace.json` concurrently without going through the protected launch scripts, `dsh-storage-json` will still perform an uncoordinated whole-file overwrite without CAS rejection.

---

## 6. Reopen Conditions

This incident ticket and remediation tracking shall remain in `MITIGATED_PENDING_UPSTREAM`.

Reopening is permitted **ONLY** under either of the following two conditions:
1. **DSH upstream/base 提供相关 CAS/lock/root fix:**
   Upstream releases a version with revision/CAS, cross-process locking, and write-time invariant validation, enabling baseline verification and adoption.
2. **当前 mitigation 下事故再次发生:**
   A registry invariant violation occurs despite the Phase-1 single-instance and fail-closed guards being active.

---

## 7. Machine Key-Value Summary

```text
INCIDENT_STATUS=MITIGATED_PENDING_UPSTREAM
MECHANISM_ROOT_CAUSE=CONFIRMED
SECOND_WRITER_INSTANCE=STRONGLY_SUPPORTED_NOT_IDENTIFIED
PHASE1_MITIGATION=PASS
ROOT_STORAGE_FIX=PENDING_UPSTREAM_BASE_REBUILD
TRUE_MULTIPROCESS_LOST_UPDATE_REPRO=PASS
TRUE_MULTIPROCESS_INCIDENT_SHAPE_REPRO=PASS
UPSTREAM_PATCH_SPEC=PASS
```
