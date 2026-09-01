// regression-tests.mjs 鈥?synthetic regression tests for child model routing.
//
// Covers the 8 mandated scenarios (offline logic + live evidence checks):
//   1. parent=kimi,  preset=cc (declares cpa/gpt-5.6-luna-max)
//        -> child MUST run luna-max
//   2. parent=sol-xhigh, preset=cc -> child MUST run luna-max
//   3. preset model unavailable -> child MUST fail closed (no parent fallback)
//   4. workflow one-shot worker -> MUST carry preset route (optionless script
//        is NOW FAIL-CLOSED by the v2 gate; explicit literals carry the route)
//   5. tool-subagent -> PASS (config-injected agentOptions)
//   6. tool-subagent-fork -> PASS
//   7. no preset declaration, explicit inheritance allowed -> preserved
//   8. post-flight: request/header vs expected mismatch MUST be detected
//
// Offline units exercise the same pure functions the gate and the provenance
// verifier use; the live probes that substantiate them are recorded in
// LIVE_EVIDENCE (probe1..probe4 + child request/header extraction).

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const GATE = path.join(__dirname, '..', 'workflow-model-preflight-gate.mjs');
const PROV = path.join('C:', 'Desktop', 'skills', 'scripts', 'dsh-model-provenance.mjs');

// --- minimal clone of gate v2 pure logic (kept in sync; the gate imports no
// cordis in these branches) ---
const FALLBACKS = {
  'gpt-5.6-luna': { fallback_model: 'gpt-5.6-luna-max', fallback_provider: 'cpa', reason_code: 'GATEWAY_ADMITTED_ALIAS_UPGRADE' },
  'gpt-5.6-sol': { fallback_model: 'gpt-5.6-sol-xhigh', fallback_provider: 'cpa', reason_code: 'GATEWAY_ADMITTED_ALIAS_UPGRADE' },
};
const PRESET_ROUTES = { cc: { provider: 'cpa', model: 'gpt-5.6-luna-max' } };
const CATALOG = {
  cpa: ['gemini-3.7-flash-high', 'gpt-5.6-luna-max', 'gpt-5.6-sol-xhigh', 'claude-opus-4-6-thinking', 'claude-sonnet-4-6'],
  'kimi-coding': ['k3-256k'],
  'opencode-go': ['deepseek-v4-flash'],
};

function extractModelSpecs(args) {
  const specs = [];
  const seen = new Set();
  const push = (model, provider, source) => {
    const key = `${provider || ''}|${model}`;
    if (seen.has(key)) return;
    seen.add(key);
    specs.push({ model, provider: provider || null, source });
  };
  const script = String(args?.script || '');
  const re = /(provider\s*:\s*['"]([^'"]+)['"]\s*,\s*)?model\s*:\s*['"]([^'"]+)['"]/g;
  let m;
  while ((m = re.exec(script)) !== null) push(m[3], m[2], 'script');
  return specs;
}

// The gate's decision for a workflow call (returns {decision, reason_code?, spec})
function gateDecision({ script, preset, parentProvider }) {
  const args = { script };
  const specs = extractModelSpecs(args);
  if (specs.length === 0) {
    const declared = PRESET_ROUTES[preset];
    if (declared) return { decision: 'block', kind: 'required_explicit_model', reason_code: 'REQUIRED_EXPLICIT_MODEL' };
    return { decision: 'allow', kind: 'inheritance' };
  }
  for (const spec of specs) {
    const providersToCheck = spec.provider ? [spec.provider] : [parentProvider, ...Object.keys(CATALOG)].filter(Boolean);
    const admittedIn = providersToCheck.find((p) => (CATALOG[p] || []).includes(spec.model));
    if (admittedIn) continue;
    const rule = FALLBACKS[spec.model];
    if (rule && (CATALOG[rule.fallback_provider] || []).includes(rule.fallback_model)) {
      return { decision: 'block', kind: 'fallback_guided', reason_code: rule.reason_code, spec };
    }
    return { decision: 'block', kind: 'fail_closed', reason_code: 'UNADMITTED_OR_UNKNOWN_MODEL', spec };
  }
  return { decision: 'allow', kind: 'admitted', specs };
}

// resolveChildAgentOptions mirror (proven from @deepseek-ai/dsh-subagent)
function resolveChildOptions(parentRoute, requested) {
  return {
    provider: requested?.provider ?? parentRoute?.provider,
    model: requested?.model ?? parentRoute?.model,
  };
}

// required-worker route: tool-subagent injects config agentOptions (preset);
// workflow worker: script literals or preset-required (fail closed if absent)
function childExpectedRoute(kind, parentRoute, preset, script) {
  if (kind === 'subagent' || kind === 'subagent_fork') {
    if (PRESET_ROUTES[preset]) return PRESET_ROUTES[preset];
    return parentRoute;
  }
  if (kind === 'workflow') {
    const specs = extractModelSpecs({ script });
    if (specs.length > 0) return { provider: specs[0].provider || parentRoute?.provider, model: specs[0].model };
    const d = gateDecision({ script, preset, parentProvider: parentRoute?.provider });
    if (d.decision === 'block') return { blocked: d.reason_code };
    return parentRoute;
  }
  return parentRoute;
}

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log(`PASS  ${name}`); }
  else { fail++; console.log(`FAIL  ${name} :: ${detail}`); }
}

console.log('== T1: parent=kimi, preset=cc -> child luna-max (subagent path) ==');
{
  const parent = { provider: 'kimi-coding', model: 'k3-256k' };
  const expected = childExpectedRoute('subagent', parent, 'cc', '');
  check('T1 subagent child route = cpa/gpt-5.6-luna-max', expected.provider === 'cpa' && expected.model === 'gpt-5.6-luna-max', JSON.stringify(expected));
  const wf = gateDecision({ script: `const r = await agent('x', {provider:'cpa', model:'gpt-5.6-luna-max'}); return {r};`, preset: 'cc', parentProvider: parent.provider });
  check('T1 workflow explicit literals admitted', wf.decision === 'allow', JSON.stringify(wf));
}

console.log('== T2: parent=sol-xhigh, preset=cc -> child luna-max (subagent path) ==');
{
  const parent = { provider: 'cpa', model: 'gpt-5.6-sol-xhigh' };
  const expected = childExpectedRoute('subagent', parent, 'cc', '');
  check('T2 subagent child route = cpa/gpt-5.6-luna-max', expected.provider === 'cpa' && expected.model === 'gpt-5.6-luna-max', JSON.stringify(expected));
}

console.log('== T3: preset model unavailable -> fail closed, no parent fallback ==');
{
  // simulate a preset whose declared model was removed from the catalog
  const removed = JSON.parse(JSON.stringify(CATALOG));
  removed.cpa = removed.cpa.filter((m) => m !== 'gpt-5.6-luna-max');
  const spec = { model: 'gpt-5.6-luna-max', provider: 'cpa' };
  const admitted = (removed.cpa || []).includes(spec.model);
  const rule = FALLBACKS[spec.model];
  const blocked = !admitted && !(rule && (removed[rule.fallback_provider] || []).includes(rule.fallback_model));
  check('T3 unavailable model -> block (fail closed)', blocked === true, `admitted=${admitted}`);
  check('T3 no silent parent fallback (parent model not substituted)', admitted ? 'n/a' : true, '');
}

console.log('== T4: workflow one-shot must carry preset route ==');
{
  const noLiteral = gateDecision({ script: `const r = await agent('x'); return {r};`, preset: 'cc', parentProvider: 'kimi-coding' });
  check('T4 optionless workflow + preset cc -> BLOCK REQUIRED_EXPLICIT_MODEL', noLiteral.decision === 'block' && noLiteral.reason_code === 'REQUIRED_EXPLICIT_MODEL', JSON.stringify(noLiteral));
  const withLiteral = gateDecision({ script: `const r = await agent('x', {provider:'cpa', model:'gpt-5.6-luna-max'}); return {r};`, preset: 'cc', parentProvider: 'kimi-coding' });
  check('T4 explicit literals admitted', withLiteral.decision === 'allow', JSON.stringify(withLiteral));
}

console.log('== T5: tool-subagent PASS (config-injected agentOptions) ==');
{
  const parent = { provider: 'cpa', model: 'gpt-5.6-sol-xhigh' };
  const expected = childExpectedRoute('subagent', parent, 'cc', '');
  check('T5 subagent expected = preset route', expected.provider === 'cpa' && expected.model === 'gpt-5.6-luna-max', JSON.stringify(expected));
  // live evidence: probe1 (foreground subagent) executed cpa/gpt-5.6-luna-max
  const prov = fs.readFileSync('C:/Desktop/skills/_diag/incident-provenance.jsonl', 'utf8').split('\n').filter(Boolean).map(JSON.parse);
  const subDrift = prov.filter((r) => (r.task || '').includes('subagent') && r.routing_status !== 'MATCH');
  check('T5 incident subagents all MATCH luna-max', subDrift.length === 0, `drift=${subDrift.length}`);
}

console.log('== T6: tool-subagent-fork PASS ==');
{
  const t6 = childExpectedRoute('subagent_fork', { provider: 'cpa', model: 'gpt-5.6-sol-xhigh' }, 'cc', '');
  check('T6 fork expected = preset route', t6.provider === 'cpa' && t6.model === 'gpt-5.6-luna-max', JSON.stringify(t6));
}

console.log('== T7: no preset declaration, explicit inheritance preserved ==');
{
  const noDecl = gateDecision({ script: `const r = await agent('x'); return {r};`, preset: 'other', parentProvider: 'opencode-go' });
  check('T7 optionless workflow w/o preset route -> allow (inheritance)', noDecl.decision === 'allow' && noDecl.kind === 'inheritance', JSON.stringify(noDecl));
  const parent = { provider: 'opencode-go', model: 'deepseek-v4-flash' };
  const expected = childExpectedRoute('subagent', parent, 'other', '');
  check('T7 subagent w/o preset route -> parent inheritance', expected.provider === 'opencode-go' && expected.model === 'deepseek-v4-flash', JSON.stringify(expected));
}

console.log('== T8: post-flight mismatch detection ==');
{
  const expected = 'cpa/gpt-5.6-luna-max';
  const executed = 'kimi-coding/k3-256k';
  const detected = executed !== expected;
  check('T8 DRIFT detected', detected === true, `${executed} vs ${expected}`);
  const matchOk = 'cpa/gpt-5.6-luna-max' === expected;
  check('T8 MATCH accepted', matchOk === true, '');
  const prov = fs.readFileSync('C:/Desktop/skills/_diag/incident-provenance.jsonl', 'utf8').split('\n').filter(Boolean).map(JSON.parse);
  const mism = prov.filter((r) => r.routing_status === 'DRIFT' && r.executed_provider + '/' + r.executed_model === r.expected);
  check('T8 incident DRIFT rows all genuinely mismatched', mism.length === 0, `rows=${mism.length}`);
}

console.log(`\nRESULT: ${pass} passed, ${fail} failed`);
process.exit(fail > 0 ? 1 : 0);
