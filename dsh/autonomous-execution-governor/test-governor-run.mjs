// test-governor-run.mjs — 单进程断言版（无子进程 spawn，可在受限沙箱内运行）。
// 与 test-governor.mjs（node:test 版，供 CI/门禁）共享同一纯逻辑。
import { emptyState, evaluateGuards, actionKey } from './autonomous-execution-governor.mjs';

const LONG_RUNNING_CAMPAIGN = {
  agent_turns: 64,
  provider_calls: 200,
  runtime_min: 720,
  loop_breaker: { soft_window: 6, hard_window: 12 },
  checkpoint_cadence_turns: 16,
};

let failed = 0;
const check = (label, cond, detail = '') => {
  if (cond) { console.log(`  ok   ${label}`); }
  else { failed += 1; console.error(`  FAIL ${label} ${detail}`); }
};

function run(n, makeEvt, profile = LONG_RUNNING_CAMPAIGN) {
  let state = emptyState({ taskId: 't', projectId: 'p', profile: 'LONG_RUNNING_CAMPAIGN', harness: 'dsh' });
  let stop = null;
  for (let i = 0; i < n; i++) {
    const evt = makeEvt ? makeEvt(i) : { key: actionKey('read', { f: i % 3 }), isMutating: false };
    const r = evaluateGuards(state, evt, profile);
    state = r.nextState;
    if (r.stop) { stop = { at: i + 1, ...r.stop }; break; }
  }
  return { state, stop };
}

console.log('autonomous-execution-governor pure-logic assertions');
{
  const { stop } = run(534, (i) => ({ key: actionKey('edit', { file: `a${i}` }), isMutating: true }));
  check('turn hard limit: 534-turn incident stops at 64', stop?.at === 64 && stop.hit === 'agent_turns', JSON.stringify(stop));
}
{
  const profile = { ...LONG_RUNNING_CAMPAIGN, agent_turns: 10000 };
  // 每次都是不同文件的 edit（mutating + 唯一 key）→ 隔离 call budget，避免 no-progress 先触发
  const { state, stop } = run(674, (i) => ({ key: actionKey('edit', { file: `f${i}` }), isMutating: true }), profile);
  check('provider-call budget: 674 calls stops at 200', stop?.hit === 'provider_calls' && state.providerCalls === 200, JSON.stringify(stop));
}
{
  let state = emptyState({ taskId: 't', projectId: 'p', profile: 'X', harness: 'dsh' });
  state.startTs = new Date(Date.now() - 721 * 60000).toISOString();
  const r = evaluateGuards(state, { key: actionKey('read', {}), isMutating: false },
    { ...LONG_RUNNING_CAMPAIGN, agent_turns: 10000, provider_calls: 10000 });
  check('runtime budget enforced', r.stop?.hit === 'runtime_min', JSON.stringify(r.stop));
}
{
  const key = actionKey('fetch', { url: 'same-url' });
  const { state, stop } = run(30, () => ({ key, isMutating: false }));
  check('loop breaker: repeated identical call circuit-breaks', stop?.hit === 'loop_breaker', JSON.stringify(stop));
}
{
  const profile = { ...LONG_RUNNING_CAMPAIGN, provider_calls: 10000 };
  const { state, stop } = run(40, (i) => ({ key: actionKey('read', { i }), isMutating: false }), profile);
  check('loop breaker: no-progress circuit-breaks', stop?.hit === 'loop_breaker' && state.consecutiveNoProgress >= profile.loop_breaker.hard_window, JSON.stringify(stop));
}
{
  let state = emptyState({ taskId: 't', projectId: 'p', profile: 'X', harness: 'dsh' });
  for (let i = 0; i < 5; i++) state = evaluateGuards(state, { key: actionKey('read', { i }), isMutating: false }, LONG_RUNNING_CAMPAIGN).nextState;
  const before = state.consecutiveNoProgress;
  state = evaluateGuards(state, { key: actionKey('edit', { f: 1 }), isMutating: true }, LONG_RUNNING_CAMPAIGN).nextState;
  check('write-like action resets no-progress counter', before === 5 && state.consecutiveNoProgress === 0, `before=${before} after=${state.consecutiveNoProgress}`);
}
{
  const { state, stop } = run(30, (i) => ({ key: actionKey('read', { i }), isMutating: false }));
  const after = evaluateGuards(state, { key: actionKey('edit', { f: 9 }), isMutating: true }, LONG_RUNNING_CAMPAIGN);
  check('circuit-broken persists', stop?.hit === 'loop_breaker' && after.stop?.hit === 'loop_breaker', JSON.stringify(after.stop));
}

console.log(failed === 0 ? 'ALL PASS' : `${failed} FAILED`);
process.exit(failed === 0 ? 0 : 1);