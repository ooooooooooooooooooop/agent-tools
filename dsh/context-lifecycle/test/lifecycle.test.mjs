import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const CHECK = process.env.DSH_CHECKOUT;
if (!CHECK) throw new Error('DSH_CHECKOUT is not set');
const NM = join(CHECK, 'node_modules', '@deepseek-ai');
const cordis = await import(pathToFileURL(join(NM, 'cordis', 'lib', 'index.js')).href);
const sessionMod = await import(pathToFileURL(join(NM, 'dsh-session', 'lib', 'index.js')).href);
const llm = await import(pathToFileURL(join(NM, 'dsh-llm', 'lib', 'index.js')).href);
const PROFILE = join(CHECK, 'web');
const lifecycle = await import(pathToFileURL(join(PROFILE, 'plugins', 'dsh-context-lifecycle', 'lib', 'index.js')).href);
const { Context } = cordis;
const { Session } = sessionMod;
const { createUserMessage } = llm;
const { ContextLifecycle, READ_ONLY_CONTEXT_EXHAUSTED, READ_ONLY_ARCHIVED, CONTEXT_PREFLIGHT_BLOCKED, RESTART_REQUIRED, isRestartCommand, sessionDigest } = lifecycle;

function makeSession(id = `lifecycle-${Math.random().toString(36).slice(2)}`) {
  const session = Session.create(id, [], { version: 0, id, createdAt: Date.now(), cwd: process.cwd() });
  session.append('request/header', { header: { config: { provider: 'cpa', model: 'gpt-5.6-sol-xhigh' } }, reason: 'initial' });
  session.append('turn/start', { turn: 1 });
  session.append('step/start', { turn: 1, step: 1 });
  session.append('user/message', createUserMessage({ content: [{ type: 'text', text: 'finish the recovery task' }], source: { kind: 'user' } }), { surfaceOp: 'append' });
  return session;
}

test('archive sidecar is enforced without changing session bytes', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'dsh-lifecycle-'));
  try {
    const ctx = new Context();
    const service = new ContextLifecycle(ctx, { sidecarDir: dir, archiveThresholdTokens: 1 });
    const session = makeSession();
    const before = sessionDigest(session);
    const state = await service.markReadOnly(session, 'fixture pressure', { totalTokens: 920000 });
    assert.equal(state.status, READ_ONLY_CONTEXT_EXHAUSTED);
    assert.equal(state.operationalLabel, READ_ONLY_ARCHIVED);
    assert.throws(() => service.assertAdmissible(session), (error) => error.code === CONTEXT_PREFLIGHT_BLOCKED);
    assert.equal(sessionDigest(session), before);
    const sidecar = JSON.parse(await readFile(service.stateFile(session.id), 'utf8'));
    assert.equal(sidecar.sessionId, session.id);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test('archived agent input is blocked before inbox append', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'dsh-input-guard-'));
  try {
    const ctx = new Context();
    const service = new ContextLifecycle(ctx, { sidecarDir: dir });
    const session = makeSession('archived-input-session');
    await service.archiveSnapshot(session.id, { measuredTokens: 920057 });
    const calls = [];
    const agent = {
      session,
      send(...args) { calls.push(args); }
    };
    service.installAgentInputGuard(agent);
    assert.throws(() => agent.send(createUserMessage({ content: [{ type: 'text', text: 'must not append' }], source: { kind: 'user' } }), 'next-turn', true), (error) => error.code === CONTEXT_PREFLIGHT_BLOCKED);
    assert.equal(calls.length, 0);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test('idle API archive guard returns READ_ONLY_CONTEXT_EXHAUSTED before the provider pipeline', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'dsh-idle-api-guard-'));
  try {
    const ctx = new Context();
    const service = new ContextLifecycle(ctx, { sidecarDir: dir });
    const session = makeSession('idle-archived-session');
    await service.archiveSnapshot(session.id, { measuredTokens: 920057 });
    const calls = { provider: 0, prepareCall: 0, compaction: 0 };
    const apiProxy = { sessions: { prompt: async () => {
      calls.prepareCall += 1;
      calls.compaction += 1;
      calls.provider += 1;
      return { result: { ok: true, value: { accepted: true } } };
    } } };
    assert.equal(service.installApiPromptGuard(apiProxy), true);
    const response = await apiProxy.sessions.prompt({
      rpcId: 'idle-archive',
      payload: { sessionId: session.id, mode: 'followup', content: [{ type: 'text', text: 'must be rejected locally' }] }
    });
    assert.equal(response.result.ok, false);
    assert.equal(response.result.error.code, READ_ONLY_CONTEXT_EXHAUSTED);
    assert.equal(response.result.error.details.admissionCode, CONTEXT_PREFLIGHT_BLOCKED);
    assert.notEqual(response.result.error.code, 'agent-busy');
    assert.deepEqual(calls, { provider: 0, prepareCall: 0, compaction: 0 });
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test('handoff preview/export keeps only resumable state and can create a new session', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'dsh-handoff-'));
  try {
    const ctx = new Context();
    const created = [];
    ctx.reflect.provide('agents', { create: async ({ sessionId }) => {
      const agent = { session: makeSession(sessionId), inject(message) { created.push(message); } };
      return { agent };
    } });
    const service = new ContextLifecycle(ctx, { sidecarDir: dir });
    const session = makeSession();
    session.append('todo/write', { todos: [{ content: 'continue validation', status: 'in_progress' }] });
    const preview = service.preview(session);
    assert.equal(preview.kind, 'preview');
    assert.equal(preview.incompleteTasks[0].content, 'continue validation');
    assert.equal(preview.toolHistory, undefined);
    assert.equal(preview.reasoning, undefined);
    const exported = await service.export(session);
    assert.equal(exported.kind, 'export');
    assert.ok(exported.artifactSha256.length === 64);
    const before = sessionDigest(session);
    const next = await service.createNewSession(session, { sessionId: 'handoff-child' });
    assert.equal(next.sessionId, 'handoff-child');
    assert.equal(created.length, 1);
    assert.equal(sessionDigest(session), before);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test('restart requests are external-supervisor only', async () => {
  assert.equal(isRestartCommand({ name: 'shell', arguments: 'Start-Sleep 2; Stop-Process -Id $PID' }), true);
  assert.equal(isRestartCommand({ name: 'read', arguments: { path: 'README.md' } }), false);
  assert.equal(RESTART_REQUIRED, 'RESTART_REQUIRED');
});

test('cold durable session archive and observability are sidecar-only', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'dsh-cold-archive-'));
  try {
    const ctx = new Context();
    const service = new ContextLifecycle(ctx, { sidecarDir: dir });
    const session = makeSession('old-920k-session');
    const state = await service.archiveSnapshot(session.id, {
      measuredTokens: 920057,
      eventCount: 74036,
      lastSeq: 74035,
      sessionSha256: 'a'.repeat(64),
      evidence: { source: 'durable-session-snapshot', contentUnchanged: true }
    });
    assert.equal(state.status, READ_ONLY_CONTEXT_EXHAUSTED);
    assert.equal(state.operationalLabel, READ_ONLY_ARCHIVED);
    assert.equal(state.measuredTokens, 920057);
    service.recordAdmission(session, {
      provider: 'cpa',
      model: 'gpt-5.6-sol-xhigh',
      contextWindow: 1000000,
      projectedInput: 920057,
      reservedOutput: 131072,
      combinedContext: 1051129,
      configuredLimit: 1000000,
      effectiveLimit: 1000000,
      providerAttestedLimit: 1048576,
      trustedUsage: 920057,
      sampleValidity: 'trusted'
    });
    const view = service.observability(session);
    assert.equal(view.projectedInput, 920057);
    assert.equal(view.effectiveLimit, 1000000);
    assert.equal(view.status, READ_ONLY_CONTEXT_EXHAUSTED);
    assert.equal(session.requestContext().effectiveLimit, 1000000);
    assert.equal(JSON.parse(await readFile(service.stateFile(session.id), 'utf8')).evidence.contentUnchanged, true);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test('estimated usage never occupies the trustedUsage field', () => {
  const ctx = new Context();
  const service = new ContextLifecycle(ctx, { sidecarDir: join(tmpdir(), `dsh-usage-${Math.random().toString(36).slice(2)}`) });
  const session = makeSession('usage-semantics');
  const recorded = service.recordAdmission(session, {
    provider: 'cpa', model: 'gpt-5.6-sol-xhigh', sampleValidity: 'estimated', trustedUsage: 2031,
    usageEstimate: 2031, sampleSource: 'estimated', sampleStatus: 'no-trusted-anchor'
  });
  assert.equal(recorded.trustedUsage, undefined);
  assert.equal(recorded.usageEstimate, 2031);
  assert.equal(service.observability(session).trustedUsage, undefined);
});
