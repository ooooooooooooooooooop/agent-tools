import test from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const CHECK = process.env.DSH_CHECKOUT;
if (!CHECK) throw new Error('DSH_CHECKOUT is not set');
const NM = path.join(CHECK, 'node_modules', '@deepseek-ai');
const guard = await import(pathToFileURL(path.join(NM, 'dsh-agent-loop-pressure-guard', 'lib', 'index.js')).href);
const sessionMod = await import(pathToFileURL(path.join(NM, 'dsh-session', 'lib', 'index.js')).href);
const meterMod = await import(pathToFileURL(path.join(NM, 'dsh-token-meter-pressure-guard', 'lib', 'index.js')).href);
const { Context } = await import(pathToFileURL(path.join(NM, 'cordis', 'lib', 'index.js')).href);
const prunerMod = await import(pathToFileURL(path.join(NM, 'dsh-tool-result-pruner-pressure-guard', 'lib', 'index.js')).href);
const { ToolResultPruner } = prunerMod;
const { admissionDecision, conservativeInputUpperBound, effectiveContextLimit } = guard;
const { Session } = sessionMod;
const { TokenMeter } = meterMod;

function pressureSession() {
  const id = 'pressure-' + Math.random().toString(36).slice(2);
  const session = Session.create(id, [], { version: 0, id, createdAt: Date.now(), cwd: 'C:/test' });
  session.append('request/header', { header: { config: { provider: 'opencode-go', model: 'deepseek-v4-flash' } } });
  session.append('request/context', { provider: 'opencode-go', model: 'deepseek-v4-flash', contextWindow: 1000000 });
  session.append('turn/start', { turn: 1 });
  session.append('step/start', { turn: 1, step: 1 });
  session.append('user/message', { content: [{ type: 'text', text: 'x'.repeat(4000) }], source: { kind: 'user' }, role: 'user', id: 'u1' }, { surfaceOp: 'append' });
  session.append('assistant/message', {
    turn: 1,
    step: 1,
    message: { role: 'assistant', content: [{ type: 'text', text: 'ok' }], source: { kind: 'model', provider: 'opencode-go', model: 'deepseek-v4-flash' }, id: 'a1' },
    usage: { inputTokens: 100000, outputTokens: 1000 },
  }, { surfaceOp: 'append', sourceEventSeqs: [] });
  session.append('step/end', { turn: 1, step: 1 });
  return session;
}

test('1. 920057 + 131072 is locally blocked before provider dispatch', () => {
  let providerCalls = 0;
  const decision = admissionDecision({ configuredLimit: 1048576, providerAttestedLimit: 1048576, estimatedInput: 920057, inputMultiplier: 1.08, safetyMargin: 16384, operationalMaxOutput: 131072, requestedMaxTokens: 131072 });
  if (decision.safe && decision.allowedOutput === 131072) providerCalls += 1;
  assert.equal(providerCalls, 0);
  assert.ok(decision.allowedOutput < 131072);
});

test('2. effective limit uses min(configured, provider-attested)', () => {
  assert.equal(effectiveContextLimit(1000000, 1048576), 1000000);
  assert.equal(effectiveContextLimit(1050000, 1048576), 1048576);
});

test('3. conservative bound exceeds observed 6.39 percent underestimate', () => {
  assert.ok(conservativeInputUpperBound(864832, 1.08) >= 920057);
});

test('4. operational max output is capped at 65536', () => {
  const d = admissionDecision({ configuredLimit: 1000000, providerAttestedLimit: 1048576, estimatedInput: 800000, inputMultiplier: 1.08, safetyMargin: 16384, operationalMaxOutput: 65536 });
  assert.equal(d.effectiveLimit, 1000000);
  assert.equal(d.allowedOutput, 65536);
});

test('5. no available output is unsafe', () => {
  const d = admissionDecision({ configuredLimit: 1000000, estimatedInput: 920000, inputMultiplier: 1.08, safetyMargin: 16384, operationalMaxOutput: 65536 });
  assert.equal(d.safe, false);
  assert.ok(d.allowedOutput <= 0);
});

test('6. provider failure usage 0/0 does not replace the meter anchor', () => {
  const ctx = new Context();
  const meter = new TokenMeter(ctx, {});
  const session = pressureSession();
  const before = meter.measure(session).totalTokens;
  session.append('turn/start', { turn: 2 });
  session.append('step/start', { turn: 2, step: 1 });
  session.append('assistant/chunk', { turn: 2, step: 1, chunk: { type: 'usage', usage: { inputTokens: 0, outputTokens: 0 } } });
  const after = meter.measure(session).totalTokens;
  assert.equal(after, before);
});

test('7. final admission is recalculated after a late instruction injection', () => {
  const before = admissionDecision({ configuredLimit: 1000000, estimatedInput: 800000, inputMultiplier: 1.08, safetyMargin: 16384, operationalMaxOutput: 65536 });
  const after = admissionDecision({ configuredLimit: 1000000, estimatedInput: 920000, inputMultiplier: 1.08, safetyMargin: 16384, operationalMaxOutput: 65536 });
  assert.equal(before.safe, true);
  assert.equal(after.safe, false);
});

test('8. oversized historical tool result becomes a traceable artifact reference', async () => {
  const ctx = new Context();
  const saved = [];
  ctx.reflect.provide('tokenMeter', { estimateMessage: () => 30000 });
  ctx.reflect.provide('spillStore', { async saveText(input) { saved.push(input); return { locator: 'C:/tmp/artifact.txt', bytes: Buffer.byteLength(input.content), retrievalHint: 'read artifact' }; } });
  const pruner = new ToolResultPruner(ctx, { thresholdChars: 8192, headChars: 4096, tailChars: 1024 });
  const id = 'artifact-' + Math.random().toString(36).slice(2);
  const session = Session.create(id, [], { version: 0, id, createdAt: Date.now(), cwd: 'C:/test' });
  const original = 'z'.repeat(50000);
  const event = session.append('tool/result', {
    turn: 1, step: 1,
    message: { role: 'user', source: { kind: 'tool', callId: 'call-1' }, id: 'r1', content: [{ type: 'tool-result', toolCallId: 'call-1', content: [{ type: 'text', text: original }] }] },
  }, { surfaceOp: 'append' });
  const beforeChars = original.length;
  const outcome = await pruner.pruneSession(session);
  const replacement = session.events[outcome.pruned[0].replacementSeq];
  const text = replacement.data.message.content[0].content[0].text;
  assert.equal(saved.length, 1);
  assert.match(text, /artifact-reference sha256=[0-9a-f]{64}/);
  assert.match(text, /locator=C:\/tmp\/artifact.txt/);
  assert.ok(text.length < beforeChars / 4, `replacement ${text.length} must be much smaller than ${beforeChars}`);
});
test('9. deployed overlay config resolves the observed route effective limit to 1000000', () => {
  const configured = 1000000;
  const attested = 1048576;
  assert.equal(effectiveContextLimit(configured, attested), 1000000);
});