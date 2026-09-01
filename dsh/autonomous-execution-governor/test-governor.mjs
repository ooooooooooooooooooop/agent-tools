// test-governor.mjs — autonomous-execution-governor 纯逻辑单测（node:test）
// 关键验收：counterexample（534 turns）在 LONG_RUNNING_CAMPAIGN 下不可能通过 gate。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { emptyState, evaluateGuards, actionKey } from './autonomous-execution-governor.mjs';

const LONG_RUNNING_CAMPAIGN = {
  agent_turns: 64,
  provider_calls: 200,
  runtime_min: 720,
  loop_breaker: { soft_window: 6, hard_window: 12 },
  checkpoint_cadence_turns: 16,
};

function run(n, makeEvt) {
  let state = emptyState({ taskId: 't', projectId: 'p', profile: 'LONG_RUNNING_CAMPAIGN', harness: 'dsh' });
  const stops = [];
  for (let i = 0; i < n; i++) {
    const evt = makeEvt ? makeEvt(i) : { key: actionKey('read', { f: i % 3 }), isMutating: false };
    const { stop, nextState } = evaluateGuards(state, evt, LONG_RUNNING_CAMPAIGN);
    state = nextState;
    if (stop) { stops.push({ at: i + 1, ...stop }); break; }
  }
  return { state, stops };
}

test('agent-turn hard limit: 534-turn incident is impossible (stops at 64)', () => {
  const { stops } = run(534, (i) => ({ key: actionKey('edit', { file: `a${i}` }), isMutating: true }));
  assert.equal(stops.length, 1);
  assert.deepEqual(stops[0], { at: 64, kind: 'session', hit: 'agent_turns' });
});

test('provider-call budget: 674 calls impossible (stops at 200)', () => {
  // turn cap 64 < call cap 200, so we must raise turn cap to isolate call budget
  const profile = { ...LONG_RUNNING_CAMPAIGN, agent_turns: 10000 };
  let state = emptyState({ taskId: 't', projectId: 'p', profile: 'X', harness: 'dsh' });
  let stop = null;
  for (let i = 0; i < 674; i++) {
    const r = evaluateGuards(state, { key: actionKey('read', { i }), isMutating: false }, profile);
    state = r.nextState;
    if (r.stop) { stop = r.stop; break; }
  }
  assert.equal(stop?.hit, 'provider_calls');
  assert.equal(state.providerCalls, 200);
});

test('runtime budget: wall-clock limit enforced', () => {
  let state = emptyState({ taskId: 't', projectId: 'p', profile: 'X', harness: 'dsh' });
  state.startTs = new Date(Date.now() - 721 * 60000).toISOString(); // 721 min ago
  const profile = { ...LONG_RUNNING_CAMPAIGN, agent_turns: 10000, provider_calls: 10000 };
  const r = evaluateGuards(state, { key: actionKey('read', {}), isMutating: false }, profile);
  assert.equal(r.stop?.hit, 'runtime_min');
});

test('loop breaker: repeated identical tool call circuit-breaks', () => {
  const key = actionKey('fetch', { url: 'same-url' });
  let state = emptyState({ taskId: 't', projectId: 'p', profile: 'X', harness: 'dsh' });
  let stop = null;
  for (let i = 0; i < 30; i++) {
    const r = evaluateGuards(state, { key, isMutating: false }, LONG_RUNNING_CAMPAIGN);
    state = r.nextState;
    if (r.stop) { stop = r.stop; break; }
  }
  assert.equal(stop?.hit, 'loop_breaker');
  assert.match(state.repeatedKeys[key] !== undefined ? 'repeat' : '', /repeat/);
});

test('loop breaker: no-progress window circuit-breaks', () => {
  let state = emptyState({ taskId: 't', projectId: 'p', profile: 'X', harness: 'dsh' });
  let stop = null;
  const profile = { ...LONG_RUNNING_CAMPAIGN, provider_calls: 10000 };
  for (let i = 0; i < 40; i++) {
    const r = evaluateGuards(state, { key: actionKey('read', { i }), isMutating: false }, profile);
    state = r.nextState;
    if (r.stop) { stop = r.stop; break; }
  }
  assert.equal(stop?.hit, 'loop_breaker');
  assert.ok(state.consecutiveNoProgress >= profile.loop_breaker.hard_window);
});

test('progress resets no-progress counter (write-like = PROGRESS_DELTA)', () => {
  let state = emptyState({ taskId: 't', projectId: 'p', profile: 'X', harness: 'dsh' });
  for (let i = 0; i < 5; i++) {
    state = evaluateGuards(state, { key: actionKey('read', { i }), isMutating: false }, LONG_RUNNING_CAMPAIGN).nextState;
  }
  assert.equal(state.consecutiveNoProgress, 5);
  state = evaluateGuards(state, { key: actionKey('edit', { f: 1 }), isMutating: true }, LONG_RUNNING_CAMPAIGN).nextState;
  assert.equal(state.consecutiveNoProgress, 0);
});

test('circuit broken persists: subsequent calls denied', () => {
  let state = emptyState({ taskId: 't', projectId: 'p', profile: 'X', harness: 'dsh' });
  let stop = null;
  for (let i = 0; i < 30; i++) {
    const r = evaluateGuards(state, { key: actionKey('read', { i }), isMutating: false }, LONG_RUNNING_CAMPAIGN);
    state = r.nextState;
    if (r.stop) { stop = r.stop; break; }
  }
  assert.equal(stop?.hit, 'loop_breaker');
  const after = evaluateGuards(state, { key: actionKey('edit', { f: 9 }), isMutating: true }, LONG_RUNNING_CAMPAIGN);
  assert.equal(after.stop?.hit, 'loop_breaker');
});