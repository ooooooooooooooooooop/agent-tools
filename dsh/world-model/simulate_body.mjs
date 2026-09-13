// simulate_body.mjs — BCC body simulator: load world-model.mjs with a mock ctx,
// feed a JSON scenario (tool results / session events / executions), emit the
// guard decisions + captured ledger events as JSON to stdout.
// Usage: node simulate_body.mjs <scenario.json> <stateDir> <canonicalDir> [mode]
import { readFileSync } from 'node:fs';
import { apply } from './world-model.mjs';

const [scenarioPath, stateDir, canonicalDir, mode] = process.argv.slice(2);
const scenario = JSON.parse(readFileSync(scenarioPath, 'utf8'));

const events = [];
const guardDecisions = [];
let registeredTool = null;
let lastPid = null;

const ctx = {
  on: (ev, fn) => { (ctx._handlers ||= {})[ev] = fn; },
  get: (svc) => svc === 'agents' ? { get: () => null } : null,
  tools: {
    register: (t) => { registeredTool = t; },
    guard: (fn) => { ctx._guard = fn; },
  },
};

apply(ctx, { stateDir, canonicalDir, mode: mode || scenario.mode || 'off', bodyId: scenario.bodyId || 'body-A' });

const execFor = (name, sid) => ({ name, agent: { session: { id: sid || 's1' } }, arguments: scenario.args || {} });

for (const step of scenario.steps || []) {
  if (step.type === 'tool_result') {
    ctx._handlers['tools/result']?.(execFor(step.tool, step.session), step.result ?? 'ok');
  } else if (step.type === 'session_event') {
    ctx._handlers['session/event']?.({ id: step.session || 's1' }, { type: step.event });
  } else if (step.type === 'tool_call') {
    const inp = { ...(step.input || {}) };
    if (inp.prediction_id === '__last__' && lastPid) inp.prediction_id = lastPid;
    const out = registeredTool?.execute(inp, execFor(step.tool || 'world_model', step.session));
    if (out?.prediction_id) lastPid = out.prediction_id;
    events.push({ step: 'tool_call', op: inp?.op, out });
  } else if (step.type === 'guard') {
    const r = ctx._guard?.({ name: step.tool, arguments: step.arguments || {}, agent: { session: { id: step.session || 's1' } } });
    guardDecisions.push({ tool: step.tool, args: step.arguments || {}, decision: r === undefined ? 'permit' : 'deny', msg: r || null });
  }
}

// drain ledger written to stateDir for ordering analysis
console.log(JSON.stringify({
  guardDecisions,
  toolCalls: events,
}, null, 0));
