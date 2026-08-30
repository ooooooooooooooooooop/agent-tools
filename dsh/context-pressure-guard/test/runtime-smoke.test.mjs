import test from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const CHECK = process.env.DSH_CHECKOUT;
if (!CHECK) throw new Error('DSH_CHECKOUT is not set');
const NM = path.join(CHECK, 'node_modules', '@deepseek-ai');
const loopMod = await import(pathToFileURL(path.join(NM, 'dsh-agent-loop-pressure-guard', 'lib', 'index.js')).href);
const { Context } = await import(pathToFileURL(path.join(NM, 'cordis', 'lib', 'index.js')).href);
const { SessionId } = await import(pathToFileURL(path.join(NM, 'dsh-session', 'lib', 'index.js')).href);
const { SessionStore } = await import(pathToFileURL(path.join(NM, 'dsh-session', 'lib', 'index.js')).href);
const { AgentRegistry } = await import(pathToFileURL(path.join(NM, 'dsh-agent', 'lib', 'index.js')).href);
const { ToolRuntime } = await import(pathToFileURL(path.join(NM, 'dsh-tools', 'lib', 'index.js')).href);
const { SystemPrompt } = await import(pathToFileURL(path.join(NM, 'dsh-system-prompt', 'lib', 'index.js')).href);
const { LlmAdapter, LlmRuntime } = await import(pathToFileURL(path.join(NM, 'dsh-llm', 'lib', 'index.js')).href);
const { AgentLoop } = loopMod;

class StrictAdapter extends LlmAdapter {
  constructor(contextWindow, onDispatch) {
    super();
    this.contextWindow = contextWindow;
    this.onDispatch = onDispatch;
  }

  async resolveModel(provider, model) {
    return { provider, id: model, name: model, context: { contextWindow: this.contextWindow } };
  }

  async *stream(request) {
    assert.equal(request.maxTokens, 65536, 'adapter receives the admitted operational cap');
    this.onDispatch(request);
    yield { type: 'block-start', index: 0, blockType: 'text' };
    yield { type: 'text-delta', index: 0, delta: 'done' };
    yield { type: 'block-end', index: 0, block: { type: 'text', text: 'done' } };
    yield { type: 'usage', usage: { inputTokens: 10, outputTokens: 1 } };
    yield { type: 'finish', reason: { kind: 'stop' } };
  }
}

async function runtime(options = {}) {
  const ctx = new Context();
  new SessionStore(ctx);
  const agents = new AgentRegistry(ctx);
  new SystemPrompt(ctx, { persona: '' });
  new ToolRuntime(ctx, {});
  let providerCalls = 0;
  const dispatchedCaps = [];
  await ctx.plugin(LlmRuntime);
  ctx.llm.registerAdapter(['opencode-go'], new StrictAdapter(
    options.contextWindow ?? 1000000,
    (request) => { providerCalls += 1; dispatchedCaps.push(request.maxTokens); },
  ));
  const loop = new AgentLoop(ctx, {
    maxParallelToolCalls: 10,
    contextAdmission: {
      safetyMargin: 16384,
      operationalMaxOutput: 65536,
      inputMultiplier: 1.08,
      routes: [{ provider: 'opencode-go', model: 'deepseek-v4-flash', providerAttestedLimit: options.providerAttestedLimit ?? 1048576 }],
    },
    agents: [],
  });
  return { ctx, agents, loop, calls: () => providerCalls, caps: () => dispatchedCaps };
}

test('isolated high-pressure session blocks locally, preserves work, then completes after surface is reduced', async () => {
  const rt = await runtime();
  const handle = await rt.agents.create({
    sessionId: SessionId('pressure-smoke-' + Math.random().toString(36).slice(2)),
    meta: { cwd: 'C:/test' },
    agentOptions: { provider: 'opencode-go', model: 'deepseek-v4-flash', maxTokens: 131072 },
  });
  const huge = { role: 'user', id: 'huge', source: { kind: 'user' }, content: [{ type: 'text', text: 'x'.repeat(4_000_000) }] };
  handle.agent.followup(huge);
  await handle.agent.whenIdle();
  assert.equal(rt.calls(), 0, 'provider must not receive the unsafe request');
  assert.equal(handle.agent.session.events.findLast((e) => e.type === 'turn/end')?.data.reason.error.code, 'CONTEXT_PREFLIGHT_BLOCKED');
  assert.ok(handle.agent.session.surface.nodes.length > 0, 'blocked work remains durable in the session');

  const hugeSeq = handle.agent.session.surface.nodes.find((seq) => handle.agent.session.events[seq].type === 'user/message');
  handle.agent.session.append('user/message', { role: 'user', id: 'reduced', source: { kind: 'user' }, content: [{ type: 'text', text: 'resume from preserved task after artifact reduction' }] }, {
    surfaceOp: { op: 'replace', start: hugeSeq, end: hugeSeq },
    sourceEventSeqs: [hugeSeq],
  });
  handle.agent.followup({ role: 'user', id: 'continue', source: { kind: 'user' }, content: [{ type: 'text', text: 'continue' }] });
  await handle.agent.whenIdle();
  assert.equal(rt.calls(), 1, 'provider receives the first safe request after local admission');
  assert.deepEqual(rt.caps(), [65536], 'provider receives the operational completion cap');
  assert.equal(handle.agent.session.events.findLast((e) => e.type === 'turn/end')?.data.reason.kind, 'completed');
  await handle.dispose();
});
