#!/usr/bin/env node
// dsh-model-provenance.mjs 鈥?post-flight worker model provenance verifier.
//
// For a DSH parent session log (session.jsonl.zstd or plain JSONL) that used
// the `workflow` and/or `subagent` tools, build one machine-readable
// provenance record per worker from REAL request headers of the worker's own
// session, compare against the EXPECTED route (script literals / preset
// declaration / tool config), and emit:
//   <out>.jsonl    鈥?one JSON record per worker (worker_id, task, preset,
//                     expected_provider/model, executed_provider/model,
//                     header_count, timestamps, result artifact hints,
//                     routing_status)
//   stdout table   鈥?compact human table
//
// Usage:
//   node scripts/dsh-model-provenance.mjs \
//     --parent <session.jsonl[.zstd]> \
//     --bucket <~/.dsh/sessions/<cwd-bucket>> \
//     --preset-route cc=cpa/gpt-5.6-luna-max[,name=provider/model...] \
//     --out <provenance.jsonl> [--workers-dir <dir>]
//
// Requires zstd.exe on PATH (or pass --zstd <path>).
// Expected-route resolution per worker:
//   - workflow agent: provider/model literals captured from the run script at
//     the matching seq (run-start/agent-start windows); when the script is
//     optionless, expected = preset-route declaration when present, else
//     'inherited' (and status records that the drift channel applies).
//   - subagent: expected = tool config agentOptions when the preset declares
//     one (same preset-route table), else 'inherited'.
// Executed = the worker session's real request/header config (its own log).
// routing_status: MATCH | DRIFT | UNADMITTED_FAIL | NO_HEADER | NO_SESSION

import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import os from 'node:os';
import { homedir } from 'node:os';

const args = process.argv.slice(2);
function opt(name, dflt) {
  const i = args.indexOf(name);
  return i >= 0 && i + 1 < args.length ? args[i + 1] : dflt;
}
function flag(name) { return args.includes(name); }

const parentPath = opt('--parent', null);
const bucket = opt('--bucket', null);
const outPath = opt('--out', null);
const zstd = opt('--zstd', 'C:\\Users\\yexue\\anaconda3\\Library\\bin\\zstd.exe');
const workersDir = opt('--workers-dir', null);
const presetRouteArg = opt('--preset-route', 'cc=cpa/gpt-5.6-luna-max');
const PRESET_ROUTES = {};
for (const part of presetRouteArg.split(',')) {
  const [p, r] = part.split('=');
  if (p && r) { const [provider, model] = r.split('/'); PRESET_ROUTES[p.trim()] = { provider, model }; }
}

if (!parentPath || !bucket || !outPath) {
  console.error('usage: dsh-model-provenance.mjs --parent <log> --bucket <sessions-bucket> --out <out.jsonl> [--preset-route ...] [--workers-dir <dir>] [--zstd <exe>]');
  process.exit(2);
}

// ---------- helpers ----------
function readLog(pathIn) {
  if (pathIn.endsWith('.zstd')) {
    const plain = path.join(os.tmpdir(), `dsmprov-${Date.now()}-${path.basename(pathIn, '.zstd')}`);
    execFileSync(zstd, ['-d', '-f', pathIn, '-o', plain], { stdio: 'ignore', timeout: 300000 });
    const text = fs.readFileSync(plain, 'utf8');
    try { fs.unlinkSync(plain); } catch { }
    return text.split('\n');
  }
  return fs.readFileSync(pathIn, 'utf8').split('\n');
}

function parseAll(lines) {
  const ev = [];
  for (const l of lines) { if (!l.trim()) continue; try { ev.push(JSON.parse(l)); } catch { } }
  return ev;
}

function wfChildren(logPath, bucketPath, workersDir) {
  const ev = parseAll(readLog(logPath));
  const runs = new Map(); // runId -> { name, meta, script, agents: [], startTime, endSeq }
  const subCalls = []; // tool subagent calls with childId + time
  let curRun = null;
  // capture script text from tool/call workflow args
  const seqScripts = new Map();
  for (const e of ev) {
    if (e.type === 'tool/call' && e.data?.name === 'workflow') {
      try {
        const a = JSON.parse(e.data.arguments);
        seqScripts.set(e.seq, { meta: a.meta || {}, script: String(a.script || '') });
      } catch { }
    }
    if (e.type === 'tool-workflow/run-start') {
      curRun = { runId: e.data.runId, name: e.data.name, agents: [], startSeq: e.seq, script: '', meta: {} };
      runs.set(e.data.runId, curRun);
    }
    if (e.type === 'tool-workflow/agent-start') {
      if (!curRun) curRun = { runId: e.data.runId, name: '?', agents: [], startSeq: e.seq, script: '', meta: {} };
      const scriptInfo = seqScripts.get(curRun.startSeq - 0) || seqScripts.get(e.seq - 1) || {};
      curRun.agents.push({ seq: e.data.seq, label: e.data.label, phase: e.data.phase, childId: e.data.childId });
      if (!curRun.script) curRun.script = scriptInfo.script || '';
      if (!curRun.meta) curRun.meta = scriptInfo.meta || {};
    }
    if (e.type === 'tool/result') {
      const s = JSON.stringify(e.data.message || '');
      const m = s.match(/started subagent ([0-9a-f-]{36})/);
      if (m) subCalls.push({ childId: m[1], time: e.time, seq: e.seq });
    }
  }
  // children logs location: workers dir if given, else bucket/<childId>/session.jsonl.zstd
  const childRoutes = new Map();
  const children = [...runs.values()].flatMap((r) => r.agents.map((a) => ({ ...a, runId: r.runId, runName: r.name, script: r.script, meta: r.meta })));
  children.push(...subCalls.map((s) => ({ childId: s.childId, label: 'subagent', runId: null, runName: 'subagent', script: '', meta: {}, time: s.time })));
  for (const c of children) {
    const plain = workersDir ? path.join(workersDir, c.childId + '.jsonl') : null;
    const zst = path.join(bucketPath, c.childId, 'session.jsonl.zstd');
    let lines = null;
    if (plain && fs.existsSync(plain)) lines = readLog(plain);
    else if (fs.existsSync(zst)) lines = readLog(zst);
    if (!lines) { childRoutes.set(c.childId, { found: false }); continue; }
    const ev2 = parseAll(lines);
    let header = null, reqHeader = null, headerCount = 0, last = 0, created = null, agentPreset = null;
    let executed = null, execCount = 0;
    for (const e of ev2) {
      if (e.type === 'session') { header = e; created = e.createdAt; agentPreset = e.agentPreset || null; }
      if (e.type === 'request/header') {
        if (headerCount === 0) reqHeader = e.data?.header?.config || null;
        headerCount++;
      }
      if (e.type === 'assistant/message' && e.data?.message?.source?.kind === 'model') {
        const s = e.data.message.source;
        executed = { provider: s.provider, model: s.model };
        execCount++;
      }
      if (e.time) last = Math.max(last, e.time);
    }
    const failErr = ev2.find((e) => e.type === 'turn/end' && e.data?.reason?.kind === 'error');
    childRoutes.set(c.childId, {
      found: true, created, agentPreset, reqHeader, headerCount,
      executedRouteExec: executed, executedMsgs: execCount, last, totalLines: ev2.length,
      failError: failErr ? JSON.stringify(failErr.data.reason.error) : null,
    });
  }
  return { runs, children, childRoutes };
}

// Expected route from script literals at the run level (mirror of gate's extractModelSpecs)
function scriptRoutes(script) {
  const specs = [];
  const re = /(?:provider\s*:\s*['"]([^'"]+)['"]\s*,\s*)?model\s*:\s*['"]([^'"]+)['"]/g;
  let m;
  while ((m = re.exec(script)) !== null) specs.push({ provider: m[1] || null, model: m[2] });
  return specs;
}

// ---------- main ----------
const { runs, children, childRoutes } = wfChildren(parentPath, bucket, workersDir);
const rows = [];
for (const c of children) {
  const info = childRoutes.get(c.childId);
  const run = c.runId ? runs.get(c.runId) : null;
  const specs = scriptRoutes(c.script);
  let expected = null;
  let expectedKind = 'none';
  if (specs.length > 0) {
    expected = specs[0].provider ? `${specs[0].provider}/${specs[0].model}` : `(this.provider)/${specs[0].model}`;
    expectedKind = 'script-literal';
  } else if (run && run.meta?.phases?.length) {
    const ph = run.meta.phases[0];
    if (ph?.model) { expected = ph.provider ? `${ph.provider}/${ph.model}` : `(this.provider)/${ph.model}`; expectedKind = 'meta.phase'; }
  } else if (PRESET_ROUTES.cc) {
    const p = PRESET_ROUTES.cc;
    expected = `${p.provider}/${p.model}`;
    expectedKind = 'preset-declared';
  } else {
    expected = '(inherited)';
    expectedKind = 'inherited';
  }
  const exec = info.found && info.reqHeader ? `${info.reqHeader.provider}/${info.reqHeader.model}` : null;
  let status;
  if (!info.found) status = 'NO_SESSION';
  else if (!exec) status = info.failError ? 'UNADMITTED_FAIL' : 'NO_HEADER';
  else if (expectedKind === 'inherited') status = 'INHERITED';
  else if (exec === expected || (expectedKind === 'script-literal' && specs[0].provider === null && exec.split('/')[1] === specs[0].model)) status = 'MATCH';
  else status = 'DRIFT';
  rows.push({
    worker_id: c.childId,
    task: c.label || c.runName || 'subagent',
    workflow: c.runName || null,
    run_id: c.runId || null,
    agentPreset: info.agentPreset || null,
    expected_kind: expectedKind,
    expected: expectedKind === 'inherited' ? null : expected,
    executed_provider: exec ? exec.split('/')[0] : null,
    executed_model: exec ? exec.split('/')[1] : null,
    executed_source: info.found && info.reqHeader ? 'request/header' : null,
    header_count: info.headerCount ?? null,
    executed_messages: info.executedMsgs ?? null,
    execution_timestamp: info.created ? new Date(info.created).toISOString() : null,
    result_artifact_hint: run ? (run.name || null) : 'subagent-result',
    executed_fail_error: info.failError || null,
    routing_status: status,
  });
}
const text = rows.map((r) => JSON.stringify(r)).join('\n');
fs.writeFileSync(outPath, text);
console.log(`provenance rows: ${rows.length} -> ${outPath}`);
console.log('status counts: ' + JSON.stringify(Object.fromEntries([...new Set(rows.map(r => r.routing_status))].map(s => [s, rows.filter(r => r.routing_status === s).length]))));
for (const r of rows) {
  console.log(`${r.routing_status.padEnd(12)} ${String(r.worker_id).slice(0, 8)}  ${(r.task || '').slice(0, 42).padEnd(42)}  expected=${r.expected || '(none)'}  executed=${r.executed_provider || '?'}/${r.executed_model || '?'}`);
}
