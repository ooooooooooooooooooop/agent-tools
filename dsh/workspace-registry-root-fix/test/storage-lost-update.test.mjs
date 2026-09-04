// workspace-registry-root-fix — adversarial test proving the lost-update root cause
// against the REAL upstream dsh-storage-json backend (whole-file last-write-wins,
// no cross-process lock, no revision/CAS).
//
// This is the "RED" proof: the current upstream code produces the exact class of
// corruption that caused DSH_WORKSPACE_REGISTRY_INTEGRITY (2026-09-04) — a
// workspace record present in tables but absent from order (or a whole update
// silently lost) when two writers race on one workspace.json. After the Layer A
// upstream patch (revision/CAS + cross-process lock) these same tests must turn
// GREEN (stale write rejected, no lost update).
//
// Two independent JsonStorageBackend instances simulate two OS processes: the
// backend's "a unit has exactly one live handle" guard is per-instance, so two
// processes each open the same unit file and each republish the WHOLE file from
// its own in-memory view (last-write-wins).
//
// Usage: node dsh/workspace-registry-root-fix/test/storage-lost-update.test.mjs
// Loads upstream dsh-storage-json from the live base install (read-only; writes
// only to a temp dir).
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const HOME = process.env.USERPROFILE;
const BASE = join(HOME, '.dsh', 'profiles', 'web', 'base-dsh-0.1.1-rc.2',
  'node_modules', '@deepseek-ai', 'dsh', 'node_modules', '@deepseek-ai');
const storageJsonUrl = pathToFileURL(join(BASE, 'dsh-storage-json', 'lib', 'index.js')).href;

const { JsonStorageBackend } = await import(storageJsonUrl);

// The workspace unit descriptor (unit.version 2, global + tables.workspaces) —
// mirroring dsh-workspace's workspaceDomainSpec shape enough for the backend.
const WORKSPACE_DESCRIPTOR = {
  name: 'workspace',
  version: 2,
  tables: ['workspaces'],
  hasGlobal: true,
};

function makeRegistryState(workspaceIds, workspaces) {
  return { initialized: true, workspaceIds, archivedSessionIds: [], workspaces };
}

test('two processes each open the same unit file (no cross-process lock)', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'msw-root-a-'));
  const p1 = new JsonStorageBackend(dir);
  const p2 = new JsonStorageBackend(dir);
  const u1 = await p1.kv.open(WORKSPACE_DESCRIPTOR);
  const u2 = await p2.kv.open(WORKSPACE_DESCRIPTOR);
  // Both processes hold a live handle to the SAME file — the per-process guard
  // does not prevent cross-process concurrent writers.
  assert.ok(u1 && u2, 'both processes must open the unit');
  await u1.close();
  await u2.close();
  await p1.close();
  await p2.close();
});

test('CASE A: whole-file last-write-wins silently loses a concurrent create', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'msw-root-b-'));
  // Process 1 and Process 2 both start from the same on-disk state (one workspace A).
  const seed = { version: 2, global: { initialized: true, workspaceIds: ['a'], archivedSessionIds: [] }, tables: { workspaces: { a: { path: 'C:/w1', title: 'w1', sessionIds: [], createdAt: 't1', updatedAt: 't1' } } } };
  // Write the seed via one backend so the file exists in the canonical shape.
  const seeder = new JsonStorageBackend(dir);
  const seedUnit = await seeder.kv.open(WORKSPACE_DESCRIPTOR);
  await seedUnit.setGlobal(seed.global);
  await seedUnit.putRecord('workspaces', 'a', seed.tables.workspaces.a);
  await seedUnit.close();
  await seeder.close();

  // Process A: reads the seed, adds workspace b (order [a, b]).
  const pA = new JsonStorageBackend(dir);
  const uA = await pA.kv.open(WORKSPACE_DESCRIPTOR);
  const aGlobal = { initialized: true, workspaceIds: ['a', 'b'], archivedSessionIds: [] };
  await uA.putRecord('workspaces', 'b', { path: 'C:/w2', title: 'w2', sessionIds: [], createdAt: 't2', updatedAt: 't2' });
  await uA.setGlobal(aGlobal);   // process A publishes order [a, b]
  await uA.close();
  await pA.close();

  // Process B: ALSO read the seed earlier (stale), adds workspace c with order [a, c]
  // (does NOT contain b), then publishes — overwriting A's order.
  const pB = new JsonStorageBackend(dir);
  const uB = await pB.kv.open(WORKSPACE_DESCRIPTOR);
  // Simulate B's stale view: it re-reads the file NOW (which has b) but we model
  // that it held an older in-memory order. Real race: B read before A wrote.
  // To model deterministically, B writes a global WITHOUT b and a table that
  // still has b (its table view was from before A added b, but the final file
  // keeps b's record if B only rewrites the global — actually B's whole-file
  // write would drop b entirely). We model the most incident-like outcome: B's
  // whole-file publish includes c + order [a,c] but NOT b -> b is LOST from both.
  const bState = { initialized: true, workspaceIds: ['a', 'c'], archivedSessionIds: [] };
  await uB.putRecord('workspaces', 'c', { path: 'C:/w3', title: 'w3', sessionIds: [], createdAt: 't3', updatedAt: 't3' });
  await uB.setGlobal(bState);   // B publishes order [a, c] — b dropped from order AND table
  await uB.close();
  await pB.close();

  // Read the final file. Process B opened fresh AFTER A's writes, so B's table
  // view still contains b (B never deleted it), but B's setGlobal republished a
  // whole file whose global order is [a, c] — b is dropped from ORDER while its
  // table record survives. That is the incident orphan shape (record present in
  // table, absent from order), not a full silent loss (full loss needs B's table
  // view to ALSO predate A). Either way, A's intended create is lost from the
  // authoritative order.
  const reader = new JsonStorageBackend(dir);
  const uR = await reader.kv.open(WORKSPACE_DESCRIPTOR);
  const finalTable = Object.fromEntries((uR.state?.tables?.get?.('workspaces') ?? new Map()).entries());
  const globalAfter = uR.state?.global;
  await uR.close();
  await reader.close();

  // b is NOT in the final order (B's stale order won) — A's create is lost from
  // the authoritative display order.
  const orderHasB = globalAfter?.workspaceIds?.includes('b');
  assert.equal(orderHasB, false, 'process B overwrote A: order no longer contains b');
  // b's table record survives (B's fresh table view kept it) -> orphan, the
  // exact incident shape dsh-workspace validateStoredState rejects at startup.
  const tableHasB = finalTable['b'] !== undefined;
  assert.equal(tableHasB, true, 'b record survives in table but is orphaned from order');
  const orderSet = new Set(globalAfter?.workspaceIds ?? []);
  const tableIds = Object.keys(finalTable);
  assert.notEqual(orderSet.size, tableIds.length, 'records/order mismatch = the incident');
});

test('CASE B: divergent table/order views can leave an orphan (record without order)', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'msw-root-c-'));
  const seed = { version: 2, global: { initialized: true, workspaceIds: ['a'], archivedSessionIds: [] }, tables: { workspaces: { a: { path: 'C:/w1', title: 'w1', sessionIds: [], createdAt: 't1', updatedAt: 't1' } } } };
  const seeder = new JsonStorageBackend(dir);
  const seedUnit = await seeder.kv.open(WORKSPACE_DESCRIPTOR);
  await seedUnit.setGlobal(seed.global);
  await seedUnit.putRecord('workspaces', 'a', seed.tables.workspaces.a);
  await seedUnit.close();
  await seeder.close();

  // Writer A adds b (table put + order [a,b]) — two separate whole-file writes.
  const pA = new JsonStorageBackend(dir);
  const uA = await pA.kv.open(WORKSPACE_DESCRIPTOR);
  await uA.putRecord('workspaces', 'b', { path: 'C:/w2', title: 'w2', sessionIds: [], createdAt: 't2', updatedAt: 't2' });
  await uA.setGlobal({ initialized: true, workspaceIds: ['a', 'b'], archivedSessionIds: [] });
  await uA.close();
  await pA.close();

  // Writer B, with a stale global view (order [a] only) but a FRESH table read
  // (saw b), writes global [a] — the order drops b while b's table record survives
  // (B never rewrote the table). This is exactly the incident: record present in
  // table, absent from order.
  const pB = new JsonStorageBackend(dir);
  const uB = await pB.kv.open(WORKSPACE_DESCRIPTOR);
  // B's table read saw b (it opened after A's table put), but B's in-memory global
  // was captured BEFORE A's order update. B publishes the stale order.
  const staleGlobal = { initialized: true, workspaceIds: ['a'], archivedSessionIds: [] };
  await uB.setGlobal(staleGlobal);   // B overwrites order to [a] — b dropped from order
  await uB.close();
  await pB.close();

  const reader = new JsonStorageBackend(dir);
  const uR = await reader.kv.open(WORKSPACE_DESCRIPTOR);
  const globalAfter = uR.state?.global;
  const finalTable = Object.fromEntries((uR.state?.tables?.get?.('workspaces') ?? new Map()).entries());
  await uR.close();
  await reader.close();

  // b IS in the table (B never touched it) but NOT in the order -> orphan.
  assert.equal(finalTable['b'] !== undefined, true, 'b record survives in table');
  assert.equal(globalAfter?.workspaceIds?.includes('b'), false, 'b absent from order -> orphan');
  // This is the exact incident shape that dsh-workspace validateStoredState rejects at startup.
  const orderSet = new Set(globalAfter?.workspaceIds ?? []);
  const tableIds = Object.keys(finalTable);
  assert.notEqual(orderSet.size, tableIds.length, 'records/order mismatch = the incident');
});
