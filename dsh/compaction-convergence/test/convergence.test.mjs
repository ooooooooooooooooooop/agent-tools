// dsh-compaction-convergence — selector + pressure convergence tests.
// Runs from run-tests.ps1 with DSH_CHECKOUT set; imports the staged fork copy so
// ESM bare imports resolve from the checkout node_modules.
import test from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const CHECK = process.env.DSH_CHECKOUT;
if (!CHECK) throw new Error('DSH_CHECKOUT is not set');
const NM = path.join(CHECK, 'node_modules', '@deepseek-ai');

const dshSessionMod = await import(pathToFileURL(path.join(NM, 'dsh-session', 'lib', 'index.js')).href);
const cordisMod = await import(pathToFileURL(path.join(CHECK, 'node_modules', '@deepseek-ai', 'cordis', 'lib', 'index.js')).href);
const fork = await import(pathToFileURL(path.join(NM, 'dsh-compaction-basic-convergence', 'lib', 'index.js')).href);
const { Session } = dshSessionMod;
const { Context } = cordisMod;
const { BasicCompactionEngine, selectCompactableRange, isSummaryNotSmallerError } = fork;

function makeSession() {
  const id = 's-' + Math.random().toString(36).slice(2);
  const session = Session.create(id, [], { version: 0, id, createdAt: Date.now(), cwd: 'C:/test' });
  session.append('request/header', { header: { config: { provider: 'cpa', model: 'x' } } });
  return session;
}
function pushUser(session, text) {
  return session.append('user/message', { content: [{ type: 'text', text }] }, { surfaceOp: 'append' }).seq;
}
function pushCheckpoint(session, text, compactionId = 't') {
  return session.append(
    'user/message',
    { content: [{ type: 'text', text }], source: { kind: 'plugin', plugin: 'compact', compactionId } },
    { surfaceOp: 'append' }
  ).seq;
}
function makeMeter() {
  const prices = new Map();
  const calls = { measure: 0, estimateMessage: 0 };
  return {
    prices,
    calls,
    price(seq, tokens) { prices.set(seq, tokens); },
    priceByText(seq, text) { prices.set(seq, Math.max(8, Math.ceil(text.length / 4) + 4)); },
    measure(session) {
      calls.measure += 1;
      const nodes = session.surface.nodes.map((seq) => ({ seq, tokens: prices.get(seq) ?? 0 }));
      const surfaceTokens = nodes.reduce((a, n) => a + n.tokens, 0);
      return { nodes, surfaceTokens, totalTokens: surfaceTokens + 100, baseline: { kind: 'estimated', tokens: 100 }, surfaceDeltaTokens: 0 };
    },
    estimateMessage(message) {
      calls.estimateMessage += 1;
      const text = (message?.content ?? []).filter((b) => b.type === 'text').map((b) => b.text).join('');
      return Math.ceil(text.length / 4) + 4;
    },
  };
}
function makeCtx(meter, extra = {}) {
  const ctx = new Context();
  ctx.reflect.provide('llm', { resolveModelInfo: async () => ({ context: { contextWindow: 262144 } }) });
  ctx.reflect.provide('tokenMeter', meter);
  ctx.reflect.provide('sessions', { flush: async () => {} });
  if (extra.pruner !== undefined) ctx.reflect.provide('toolResultPruner', extra.pruner);
  return ctx;
}
class TestEngine extends BasicCompactionEngine {
  summarizeCalls = 0;
  summaryText = 'S'.repeat(120);
  summarizeResult = null;
  async summarize() {
    this.summarizeCalls += 1;
    if (this.summarizeResult !== null) return this.summarizeResult;
    return { summary: [{ type: 'text', text: this.summaryText }], llmStreamCall: false, provider: 'cpa', model: 'x' };
  }
}
const ENGINE_CFG = {
  summarizationProvider: 'cpa',
  summarizationModel: 'x',
  maxTokens: 2048,
  compactionRetries: 0,
  modelPolicies: [{ provider: 'cpa', model: 'x', thresholdRatio: 0.005, retainTokens: 500 }],
};
function makeAgent(session) {
  return { session, options: { provider: 'cpa', model: 'x' } };
}

test('1. first compaction produces a checkpoint node', async () => {
  const session = makeSession();
  const meter = makeMeter();
  const engine = new TestEngine(makeCtx(meter), ENGINE_CFG);
  session.append('turn/start', { turn: 1 });
  const seqs = [];
  for (let i = 0; i < 8; i += 1) seqs.push(pushUser(session, `u${i}`.repeat(400)));
  for (const s of seqs) meter.priceByText(s, `u${s}`.repeat(400));
  const agent = makeAgent(session);
  const result = await engine.compactIfNeeded(agent, 'pressure', new AbortController().signal);
  assert.ok(result !== null, 'pressure should compact');
  const headSeq = session.surface.nodes[0];
  const headEvent = session.events[headSeq];
  assert.equal(headEvent.type, 'user/message');
  assert.equal(headEvent.data.source?.kind, 'plugin');
  assert.equal(headEvent.data.source?.plugin, 'compact');
  assert.equal(engine.summarizeCalls, 1);
});

test('2. second pressure event must not compact the checkpoint again', async () => {
  const session = makeSession();
  const meter = makeMeter();
  const engine = new TestEngine(makeCtx(meter), ENGINE_CFG);
  session.append('turn/start', { turn: 1 });
  const seqs = [];
  for (let i = 0; i < 8; i += 1) seqs.push(pushUser(session, `a${i}`.repeat(400)));
  for (const s of seqs) meter.priceByText(s, `a${s}`.repeat(400));
  const agent = makeAgent(session);
  await engine.compactIfNeeded(agent, 'pressure', new AbortController().signal);
  assert.equal(engine.summarizeCalls, 1);
  const checkpointSeq = session.surface.nodes[0];
  const before = session.surface.nodes.slice();
  const result = await engine.compactIfNeeded(agent, 'pressure', new AbortController().signal);
  assert.equal(engine.summarizeCalls, 1, 'checkpoint must not be re-summarized');
  assert.equal(result, null);
  assert.deepEqual(session.surface.nodes, before, 'surface must not change');
  const head = session.events[checkpointSeq];
  assert.equal(head.data.source?.plugin, 'compact');
});

test('3. checkpoint + old normal nodes: selector skips checkpoint and selects normal region', async () => {
  const session = makeSession();
  const meter = makeMeter();
  session.append('turn/start', { turn: 1 });
  const cp = pushCheckpoint(session, 'cp'.repeat(300));
  meter.priceByText(cp, 'cp'.repeat(300));
  const seqs = [];
  for (let i = 0; i < 6; i += 1) seqs.push(pushUser(session, `n${i}`.repeat(400)));
  for (const s of seqs) meter.priceByText(s, `n${s}`.repeat(400));
  const measurement = meter.measure(session);
  const range = selectCompactableRange(session, measurement, 500);
  assert.ok(range !== null);
  assert.notEqual(range.start, cp, 'checkpoint must not become start');
  assert.ok(range.start > cp, 'selection must begin after the checkpoint');
  assert.ok(seqs.includes(range.start));
});

test('4. candidate region all checkpoint: returns null and zero summarizer calls', async () => {
  const session = makeSession();
  const meter = makeMeter();
  session.append('turn/start', { turn: 1 });
  const cp1 = pushCheckpoint(session, 'c1'.repeat(300));
  const cp2 = pushCheckpoint(session, 'c2'.repeat(300));
  meter.priceByText(cp1, 'c1'.repeat(300));
  meter.priceByText(cp2, 'c2'.repeat(300));
  const seqs = [];
  for (let i = 0; i < 6; i += 1) seqs.push(pushUser(session, `t${i}`.repeat(700)));
  for (const s of seqs) meter.priceByText(s, `t${s}`.repeat(700));
  // tail retain 500 tokens → keepFromIdx 停在末尾第一个满足处；制造候选区只剩两个 checkpoint
  const measurement = meter.measure(session);
  // 手动把保留尾巴设得很大以保证候选区只有 checkpoint：用 retainTokens 覆盖全部普通节点
  const totalTail = meter.measure(session).nodes.slice(2).reduce((a, n) => a + n.tokens, 0);
  const range = selectCompactableRange(session, meter.measure(session), totalTail + 1);
  assert.equal(range, null, 'all-checkpoint candidate region must return null');
  const engine = new TestEngine(makeCtx(meter), { ...ENGINE_CFG, modelPolicies: [{ provider: 'cpa', model: 'x', thresholdRatio: 0.01, retainTokens: totalTail + 1 }] });
  // confirm the engine does not call the summarizer end to end
  session.append('turn/end', { turn: 1 });
  session.append('turn/start', { turn: 2 });
  await engine.compactIfNeeded(makeAgent(session), 'pressure', new AbortController().signal);
  assert.equal(engine.summarizeCalls, 0);
});

test('5. same unchanged region summary-not-smaller: later pre-step performs zero duplicate summarizer call', async () => {
  const session = makeSession();
  const meter = makeMeter();
  const engine = new TestEngine(makeCtx(meter), ENGINE_CFG);
  engine.summaryText = 'X'.repeat(12000); // framed summary >> shadowed region
  session.append('turn/start', { turn: 1 });
  const seqs = [];
  for (let i = 0; i < 10; i += 1) seqs.push(pushUser(session, `g${i}`.repeat(300)));
  for (const s of seqs) meter.priceByText(s, `g${s}`.repeat(300));
  const agent = makeAgent(session);
  await assert.rejects(
    () => engine.compactIfNeeded(agent, 'pressure', new AbortController().signal),
    (err) => err.message.startsWith('summary is not smaller') || err.message.includes('still above threshold')
  );
  const afterFirst = engine.summarizeCalls;
  assert.ok(afterFirst >= 1);
  // 同一 surface，同一 region：下一个 pre-step 不得再调 summarizer
  const result2 = await engine.compactIfNeeded(agent, 'pressure', new AbortController().signal);
  assert.equal(result2, null);
  assert.equal(engine.summarizeCalls, afterFirst, 'duplicate summarizer call must be skipped');
});

test('6. surface mutation after failure: selector may reevaluate', async () => {
  const session = makeSession();
  const meter = makeMeter();
  const engine = new TestEngine(makeCtx(meter), ENGINE_CFG);
  engine.summaryText = 'Y'.repeat(12000); // fail first region
  session.append('turn/start', { turn: 1 });
  const seqs = [];
  for (let i = 0; i < 10; i += 1) seqs.push(pushUser(session, `m${i}`.repeat(300)));
  for (const s of seqs) meter.priceByText(s, `m${s}`.repeat(300));
  const agent = makeAgent(session);
  await assert.rejects(() => engine.compactIfNeeded(agent, 'pressure', new AbortController().signal));
  const afterFail = engine.summarizeCalls;
  // surface 变化：追加新节点
  const extra = pushUser(session, 'new'.repeat(2000));
  meter.priceByText(extra, 'new'.repeat(2000));
  // 新 region 足够大 → 允许重新评估（可能成功压缩，也可能仍超出阈值——两者都说明
  // surface 变化后 selector 重新进入 summarizer，而非被 FIX B 跳过）。
  engine.summaryText = 'S'.repeat(120);
  const second = await engine.compactIfNeeded(agent, 'pressure', new AbortController().signal).catch((error) => error);
  assert.ok(engine.summarizeCalls > afterFail, 'mutation must allow reevaluation');
  if (!(second instanceof Error)) assert.ok(second !== null, 'successful reevaluation returns a result');
});

test('7. ordinary compaction behavior unchanged (no checkpoint)', async () => {
  const session = makeSession();
  const meter = makeMeter();
  session.append('turn/start', { turn: 1 });
  const seqs = [];
  for (let i = 0; i < 8; i += 1) seqs.push(pushUser(session, `o${i}`.repeat(400)));
  for (const s of seqs) meter.priceByText(s, `o${s}`.repeat(400));
  const measurement = meter.measure(session);
  const range = selectCompactableRange(session, measurement, 500);
  assert.ok(range !== null);
  assert.equal(range.start, session.surface.nodes[0], 'ordinary head selection unchanged');
  assert.ok(range.end >= range.start);
  const engine = new TestEngine(makeCtx(meter), ENGINE_CFG);
  const agent = makeAgent(session);
  const beforeLen = session.surface.nodes.length;
  const result = await engine.compactIfNeeded(agent, 'pressure', new AbortController().signal);
  assert.ok(result !== null);
  assert.ok(session.surface.nodes.length < beforeLen, 'surface must shrink after ordinary compact');
});

test('8. overflow recovery behavior unchanged', async () => {
  const session = makeSession();
  const meter = makeMeter();
  session.append('turn/start', { turn: 1 });
  const seqs = [];
  for (let i = 0; i < 6; i += 1) seqs.push(pushUser(session, `v${i}`.repeat(300)));
  for (const s of seqs) meter.priceByText(s, `v${s}`.repeat(300));
  const engine = new TestEngine(makeCtx(meter), ENGINE_CFG);
  const agent = makeAgent(session);
  const result = await engine.compactIfNeeded(agent, 'context-overflow', new AbortController().signal);
  assert.ok(result !== null, 'overflow must still compact');
  assert.equal(engine.summarizeCalls, 1);
});

test('9. toolResultPruner behavior unchanged', async () => {
  const session = makeSession();
  const meter = makeMeter();
  let prunerCalls = 0;
  const pruner = { pruneSession() { prunerCalls += 1; } };
  session.append('turn/start', { turn: 1 });
  const seqs = [];
  for (let i = 0; i < 8; i += 1) seqs.push(pushUser(session, `p${i}`.repeat(400)));
  for (const s of seqs) meter.priceByText(s, `p${s}`.repeat(400));
  const engine = new TestEngine(makeCtx(meter, { pruner }), ENGINE_CFG);
  const agent = makeAgent(session);
  const result = await engine.compactIfNeeded(agent, 'pressure', new AbortController().signal);
  assert.ok(result !== null);
  assert.equal(prunerCalls, 1, 'pruner must run exactly once before summarization');
  assert.equal(engine.summarizeCalls, 1);
});

test('10. surface derivation after replace unchanged', async () => {
  const session = makeSession();
  const meter = makeMeter();
  const engine = new TestEngine(makeCtx(meter), ENGINE_CFG);
  session.append('turn/start', { turn: 1 });
  const seqs = [];
  for (let i = 0; i < 8; i += 1) seqs.push(pushUser(session, `d${i}`.repeat(400)));
  for (const s of seqs) meter.priceByText(s, `d${s}`.repeat(400));
  const before = session.deriveMessages();
  const agent = makeAgent(session);
  await engine.compactIfNeeded(agent, 'pressure', new AbortController().signal);
  const after = session.deriveMessages();
  const checkpointIndex = after.findIndex((m) => JSON.stringify(m.content).includes('S'.repeat(120)));
  assert.ok(checkpointIndex >= 0, 'checkpoint message appears in derived history');
  const beforeTexts = new Set(before.map((m) => JSON.stringify(m.content)));
  const afterTexts = after.map((m) => JSON.stringify(m.content));
  const survivors = afterTexts.filter((t) => beforeTexts.has(t));
  assert.ok(survivors.length <= 3, 'only the retained tail may survive verbatim');
  assert.ok(after.length < before.length, 'surface shrinks after replace');
});

test('12. continue after checkpoint: new normal history compacts while checkpoint preserved', async () => {
  const session = makeSession();
  const meter = makeMeter();
  const engine = new TestEngine(makeCtx(meter), ENGINE_CFG);
  session.append('turn/start', { turn: 1 });
  const seqs = [];
  for (let i = 0; i < 8; i += 1) seqs.push(pushUser(session, `k${i}`.repeat(400)));
  for (const s of seqs) meter.priceByText(s, `k${s}`.repeat(400));
  const agent = makeAgent(session);
  assert.ok((await engine.compactIfNeeded(agent, 'pressure', new AbortController().signal)) !== null);
  const checkpointSeq = session.surface.nodes[0];
  // 后续步骤继续：新增普通历史
  const more = [];
  for (let i = 0; i < 8; i += 1) more.push(pushUser(session, `j${i}`.repeat(400)));
  for (const s of more) meter.priceByText(s, `j${s}`.repeat(400));
  const result2 = await engine.compactIfNeeded(agent, 'pressure', new AbortController().signal);
  assert.ok(result2 !== null, 'new normal history must still compact');
  assert.equal(session.surface.nodes[0], checkpointSeq, 'checkpoint must remain at head');
  const headAfter = session.events[session.surface.nodes[0]];
  assert.equal(headAfter.data.source?.plugin, 'compact');
  assert.ok(session.surface.nodes.length < 10, 'surface keeps shrinking');
});

test('13. three same-surface non-shrinking failures open a circuit and stop further summarizer calls', async () => {
  const session = makeSession();
  const meter = makeMeter();
  const engine = new TestEngine(makeCtx(meter), { ...ENGINE_CFG, maxConsecutiveFailures: 3 });
  engine.summaryText = 'Q'.repeat(12000);
  session.append('turn/start', { turn: 1 });
  const seqs = [];
  for (let i = 0; i < 10; i += 1) seqs.push(pushUser(session, `q${i}`.repeat(300)));
  for (const s of seqs) meter.priceByText(s, `q${s}`.repeat(300));
  const agent = makeAgent(session);
  for (let i = 0; i < 3; i += 1) {
    // Material but zero-token nodes change the surface fingerprint without changing the selected priced range.
    if (i > 0) {
      const seq = pushUser(session, '');
      meter.price(seq, 0);
    }
    await assert.rejects(() => engine.compactIfNeeded(agent, 'pressure', new AbortController().signal));
  }
  const calls = engine.summarizeCalls;
  const result = await engine.compactIfNeeded(agent, 'pressure', new AbortController().signal);
  assert.equal(result, null);
  assert.equal(engine.summarizeCalls, calls, 'open circuit must stop further summarizer calls');
  assert.equal(calls, 3, 'cross-step breaker must bound the failed summarizer calls');
});

test('14. selector prefers a large stale successful tool result and keeps a valid range', () => {
  const session = makeSession();
  const meter = makeMeter();
  session.append('turn/start', { turn: 1 });
  session.append('step/start', { turn: 1, step: 1 });
  const before = pushUser(session, 'before'.repeat(500));
  meter.priceByText(before, 'before'.repeat(500));
  const assistant = session.append('assistant/message', {
    turn: 1, step: 1,
    message: { role: 'assistant', content: [{ type: 'tool-call', id: 'tool-1', name: 'search', arguments: '{}' }] }
  }, { surfaceOp: 'append' });
  const call = session.append('tool/call', { turn: 1, step: 1, callId: 'tool-1', name: 'search' });
  const result = session.append('tool/result', {
    turn: 1, step: 1,
    message: { role: 'user', source: { kind: 'tool', callId: 'tool-1' }, id: 'result-1', content: [{ type: 'tool-result', toolCallId: 'tool-1', content: [{ type: 'text', text: 'large stale result'.repeat(2000) }] }] }
  }, { surfaceOp: 'append' });
  const after = pushUser(session, 'after'.repeat(500));
  meter.price(assistant.seq, 20);
  meter.price(call.seq, 4);
  meter.price(result.seq, 5000);
  meter.priceByText(after, 'after'.repeat(500));
  const range = selectCompactableRange(session, meter.measure(session), 600);
  assert.ok(range !== null);
  assert.ok(range.start <= range.end, 'selected range must be ordered');
  assert.equal(range.end, result.seq, 'largest stale successful tool result is selected before retained tail');
});

test('isSummaryNotSmallerError recognizes only the documented prefix', () => {
  assert.equal(isSummaryNotSmallerError(new Error('summary is not smaller than the shadowed content (10 >= 10)')), true);
  assert.equal(isSummaryNotSmallerError(new Error('provider timeout')), false);
  assert.equal(isSummaryNotSmallerError('not an error'), false);
  assert.equal(isSummaryNotSmallerError(new TypeError('summary is not smaller than the shadowed content')), true, 'TypeError is an Error subclass');
});
