// multiprocess-lost-update.test.mjs — True multi-process adversarial tests proving
// the lost-update root cause and incident shape against the REAL upstream
// dsh-storage-json backend using independent Node OS child processes.
//
// This fixture proves the defect using two distinct operating system processes:
//   Process A reads state N
//   Process B reads state N
//   B publishes mutation -> newer state
//   A publishes stale state
//
// Result on unpatched upstream:
//   TRUE_MULTIPROCESS_LOST_UPDATE_REPRO=PASS (Process B's update is silently overwritten)
//   TRUE_MULTIPROCESS_INCIDENT_SHAPE_REPRO=PASS (Interleaved creates leave record in tables but absent from global.workspaceIds)
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { fork } from 'node:child_process';

const __dirname = dirname(fileURLToPath(import.meta.url));
const WORKER_SCRIPT = join(__dirname, 'multiprocess-worker.mjs');

const WORKSPACE_DESCRIPTOR = {
  name: 'workspace',
  version: 2,
  tables: ['workspaces'],
  hasGlobal: true,
};

function spawnWorker() {
  const child = fork(WORKER_SCRIPT, [], {
    stdio: ['ignore', 'inherit', 'inherit', 'ipc'],
  });
  let reqId = 0;
  const pending = new Map();

  child.on('message', (msg) => {
    if (msg && msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.status === 'ok') {
        resolve(msg);
      } else {
        reject(new Error(`[PID ${msg.pid}] ${msg.error}`));
      }
    }
  });

  function send(action, payload = {}) {
    const id = ++reqId;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      child.send({ id, action, ...payload });
    });
  }

  async function terminate() {
    try {
      await send('exit');
    } catch {
      // process might already be exited
    }
    child.kill();
  }

  return { child, pid: child.pid, send, terminate };
}

function readDiskJson(dir, unitName = 'workspace') {
  const filePath = join(dir, `${unitName}.json`);
  const content = readFileSync(filePath, 'utf8');
  return JSON.parse(content);
}

test('two distinct OS processes open the same storage unit (no cross-process lock)', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-mp-lock-'));
  const workerA = spawnWorker();
  const workerB = spawnWorker();

  try {
    assert.notEqual(workerA.pid, workerB.pid, 'workers must be distinct OS processes');
    assert.notEqual(workerA.pid, process.pid, 'worker A must be distinct from runner');
    assert.notEqual(workerB.pid, process.pid, 'worker B must be distinct from runner');

    const resA = await workerA.send('open', { dir, descriptor: WORKSPACE_DESCRIPTOR });
    const resB = await workerB.send('open', { dir, descriptor: WORKSPACE_DESCRIPTOR });

    assert.equal(resA.status, 'ok');
    assert.equal(resB.status, 'ok');

    await workerA.send('close');
    await workerB.send('close');
  } finally {
    await workerA.terminate();
    await workerB.terminate();
  }
});

test('true multi-process lost-update repro: B commits newer state, stale A overwrites it', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-mp-lost-'));
  const workerSeed = spawnWorker();
  const workerA = spawnWorker();
  const workerB = spawnWorker();

  try {
    // 1. Seed state N
    await workerSeed.send('open', { dir, descriptor: WORKSPACE_DESCRIPTOR });
    await workerSeed.send('putRecord', {
      table: 'workspaces',
      key: 'ws-seed',
      value: { path: 'C:/ws-seed', title: 'Seed', sessionIds: [], createdAt: 't0', updatedAt: 't0' },
    });
    await workerSeed.send('setGlobal', {
      value: { initialized: true, workspaceIds: ['ws-seed'], archivedSessionIds: [] },
    });
    await workerSeed.send('close');

    // 2. Process A and Process B both open state N into memory
    await workerA.send('open', { dir, descriptor: WORKSPACE_DESCRIPTOR });
    await workerB.send('open', { dir, descriptor: WORKSPACE_DESCRIPTOR });

    // 3. Process B publishes mutation -> newer state (adds ws-b)
    await workerB.send('putRecord', {
      table: 'workspaces',
      key: 'ws-b',
      value: { path: 'C:/ws-b', title: 'Workspace B', sessionIds: [], createdAt: 't1', updatedAt: 't1' },
    });
    await workerB.send('setGlobal', {
      value: { initialized: true, workspaceIds: ['ws-seed', 'ws-b'], archivedSessionIds: [] },
    });
    // Verify disk has ws-b right now
    const diskAfterB = readDiskJson(dir);
    assert.ok(diskAfterB.tables.workspaces['ws-b'], 'disk must have ws-b after B writes');
    assert.ok(diskAfterB.global.workspaceIds.includes('ws-b'), 'disk order must have ws-b after B writes');

    // 4. Process A (which loaded state N before B wrote) publishes stale mutation (adds ws-a)
    await workerA.send('putRecord', {
      table: 'workspaces',
      key: 'ws-a',
      value: { path: 'C:/ws-a', title: 'Workspace A', sessionIds: [], createdAt: 't2', updatedAt: 't2' },
    });
    await workerA.send('setGlobal', {
      value: { initialized: true, workspaceIds: ['ws-seed', 'ws-a'], archivedSessionIds: [] },
    });

    // 5. Inspect final on-disk state
    const diskFinal = readDiskJson(dir);

    // Process B's mutation was completely overwritten by Process A's stale whole-file publish
    const bInTable = diskFinal.tables.workspaces['ws-b'] !== undefined;
    const bInOrder = diskFinal.global.workspaceIds.includes('ws-b');
    const aInTable = diskFinal.tables.workspaces['ws-a'] !== undefined;
    const aInOrder = diskFinal.global.workspaceIds.includes('ws-a');

    assert.equal(aInTable, true, 'Process A record present on disk');
    assert.equal(aInOrder, true, 'Process A order present on disk');
    assert.equal(bInTable, false, 'Process B record was completely wiped from table by stale writer A');
    assert.equal(bInOrder, false, 'Process B was completely wiped from order by stale writer A');

    console.log(`[EVIDENCE] TRUE_MULTIPROCESS_LOST_UPDATE_REPRO=PASS (PID A=${workerA.pid}, PID B=${workerB.pid})`);

    await workerA.send('close');
    await workerB.send('close');
  } finally {
    await workerSeed.terminate();
    await workerA.terminate();
    await workerB.terminate();
  }
});

test('true multi-process incident shape repro: interleaved multi-process create leaves record in tables but absent from order', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-mp-orphan-'));
  const workerSeed = spawnWorker();
  const workerA = spawnWorker();
  const workerB = spawnWorker();

  try {
    // 1. Seed state N
    await workerSeed.send('open', { dir, descriptor: WORKSPACE_DESCRIPTOR });
    await workerSeed.send('putRecord', {
      table: 'workspaces',
      key: 'ws-seed',
      value: { path: 'C:/ws-seed', title: 'Seed', sessionIds: [], createdAt: 't0', updatedAt: 't0' },
    });
    await workerSeed.send('setGlobal', {
      value: { initialized: true, workspaceIds: ['ws-seed'], archivedSessionIds: [] },
    });
    await workerSeed.send('close');

    // 2. Process B starts creating ws-orphan: performs putRecord first (Write 1 of createCanonical)
    await workerB.send('open', { dir, descriptor: WORKSPACE_DESCRIPTOR });
    await workerB.send('putRecord', {
      table: 'workspaces',
      key: 'ws-orphan',
      value: { path: 'C:/ws-orphan', title: 'Orphan Workspace', sessionIds: [], createdAt: 't1', updatedAt: 't1' },
    });

    // 3. Process A opens the unit at this exact interleaving point.
    // Process A reads the file where table has ws-orphan, but global order is still ['ws-seed'].
    await workerA.send('open', { dir, descriptor: WORKSPACE_DESCRIPTOR });

    // 4. Process B finishes its creation: updates global order to ['ws-seed', 'ws-orphan']
    await workerB.send('setGlobal', {
      value: { initialized: true, workspaceIds: ['ws-seed', 'ws-orphan'], archivedSessionIds: [] },
    });

    // 5. Process A publishes an update from its view (adds ws-a with order ['ws-seed', 'ws-a']).
    // Because Process A loaded the table containing ws-orphan, but its in-memory global order
    // only had ['ws-seed'], Process A's whole-file publish preserves ws-orphan in tables
    // while writing order ['ws-seed', 'ws-a'].
    await workerA.send('putRecord', {
      table: 'workspaces',
      key: 'ws-a',
      value: { path: 'C:/ws-a', title: 'Workspace A', sessionIds: [], createdAt: 't2', updatedAt: 't2' },
    });
    await workerA.send('setGlobal', {
      value: { initialized: true, workspaceIds: ['ws-seed', 'ws-a'], archivedSessionIds: [] },
    });

    // 6. Read final on-disk file
    const diskFinal = readDiskJson(dir);
    const tableKeys = Object.keys(diskFinal.tables.workspaces);
    const orderIds = diskFinal.global.workspaceIds;

    const orphanInTable = diskFinal.tables.workspaces['ws-orphan'] !== undefined;
    const orphanInOrder = orderIds.includes('ws-orphan');

    assert.equal(orphanInTable, true, 'ws-orphan record MUST exist in tables');
    assert.equal(orphanInOrder, false, 'ws-orphan MUST be absent from global.workspaceIds');
    assert.notEqual(tableKeys.length, orderIds.length, 'table count must mismatch order count');

    console.log(`[EVIDENCE] TRUE_MULTIPROCESS_INCIDENT_SHAPE_REPRO=PASS (PID A=${workerA.pid}, PID B=${workerB.pid})`);

    await workerA.send('close');
    await workerB.send('close');
  } finally {
    await workerSeed.terminate();
    await workerA.terminate();
    await workerB.terminate();
  }
});
