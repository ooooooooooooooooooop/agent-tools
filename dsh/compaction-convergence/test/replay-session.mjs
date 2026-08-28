// Offline, read-only replay of the original DSH session through the patched
// selector + FIX B state machine. Reads a plain JSONL dump (one event per line).
// Emits BEFORE/AFTER metrics; never mutates the original session log.
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';

const CHECK = process.env.DSH_CHECKOUT;
const DUMP = process.env.DSH_REPLAY_JSONL;
if (!CHECK || !DUMP) throw new Error('DSH_CHECKOUT and DSH_REPLAY_JSONL must be set');
const NM = path.join(CHECK, 'node_modules', '@deepseek-ai');
const compactionMod = await import(pathToFileURL(path.join(NM, 'dsh-compaction', 'lib', 'index.js')).href);
const fork = await import(pathToFileURL(path.join(NM, 'dsh-compaction-basic-convergence', 'lib', 'index.js')).href);
const { toolPairingBalancedBefore } = compactionMod;
const { selectCompactableRange: selectAfter } = fork;

// ---- minimal session object accepted by toolPairing/selectCompactableRange ----
class ReplaySession {
  constructor() {
    this.events = [];
    this.surface = { nodes: [], replaceGeneration: 0 };
    this.priced = new Map(); // seq -> tokens (token-meter estimator)
  }
  // token-meter heuristic mirror (estimate.ts): chars/4 + 4 per block, +4 frame
  priceContents(blocks) {
    let t = 0;
    for (const b of blocks ?? []) {
      if (b.type === 'text' || b.type === 'reasoning') t += Math.ceil(b.text.length / 4) + 4;
      else if (b.type === 'tool-call') t += Math.ceil(b.name.length / 4) + Math.ceil(b.arguments.length / 4) + 4;
      else if (b.type === 'tool-result') t += this.priceContents(b.content) + 4;
      else t += 4 + Math.ceil(JSON.stringify(b).length / 4);
    }
    return t;
  }
  messageOf(type, data) {
    if (type === 'user/message') return data;
    if (type === 'assistant/message') {
      const m = data?.message ?? {};
      return m.content && m.content.length ? m : null;
    }
    if (type === 'tool/result') return data?.message ?? null;
    return null;
  }
  fold(ev) {
    const seq = ev.seq;
    this.events[seq] = ev;
    const op = ev.surfaceOp;
    if (op === undefined) return;
    const msg = this.messageOf(ev.type, ev.data);
    const price = msg === null ? 0 : this.priceContents(msg.content) + 4;
    if (op === 'append') {
      this.surface.nodes.push(seq);
      this.priced.set(seq, price);
    } else if (op && op.op === 'replace') {
      const a = this.surface.nodes.indexOf(op.start);
      const b = this.surface.nodes.indexOf(op.end);
      if (a === -1 || b === -1) throw new Error(`replay: replace range ${op.start}-${op.end} absent`);
      const removed = this.surface.nodes.slice(a, b + 1);
      removed.forEach((s) => this.priced.delete(s));
      this.surface.nodes.splice(a, b - a + 1, seq);
      this.surface.replaceGeneration += 1;
      this.priced.set(seq, price);
    } else throw new Error(`replay: unexpected surfaceOp ${JSON.stringify(op)}`);
  }
  measurement(retainTokens) {
    const nodes = this.surface.nodes.map((seq) => ({ seq, tokens: this.priced.get(seq) ?? 0 }));
    const surfaceTokens = nodes.reduce((a, n) => a + n.tokens, 0);
    return { nodes, surfaceTokens, totalTokens: surfaceTokens, baseline: { kind: 'estimated', tokens: 0 }, surfaceDeltaTokens: 0 };
  }
}
function isCheckpointSeq(session, seq) {
  const src = session.events[seq]?.data?.source;
  return src != null && src.kind === 'plugin' && src.plugin === 'compact';
}
// original 0.1.1-rc.2 behavior: always start at surface node 0
function selectBefore(session, retainTokens) {
  const m = session.measurement();
  const nodes = m.nodes;
  const surfaceNodes = session.surface.nodes;
  let keepFromIdx = nodes.length;
  let acc = 0;
  for (let i = nodes.length - 1; i >= 0; i -= 1) {
    acc += nodes[i].tokens;
    keepFromIdx = i;
    if (acc >= retainTokens) break;
  }
  if (keepFromIdx === 0) return null;
  while (keepFromIdx > 0) {
    if (toolPairingBalancedBefore(session, surfaceNodes[keepFromIdx])) break;
    keepFromIdx -= 1;
  }
  if (keepFromIdx === 0) return null;
  return { start: surfaceNodes[0], end: surfaceNodes[keepFromIdx - 1] };
}

const RETAIN = 41943; // floor(262144 * 0.16)
const CUTOFF = Number(process.env.DSH_REPLAY_CUTOFF || 287005);

const lines = fs.readFileSync(DUMP, 'utf8').split('\n');
const events = [];
for (const line of lines) {
  if (!line.trim()) continue;
  try { const ev = JSON.parse(line); if (Number.isInteger(ev.seq) && ev.seq <= CUTOFF) events.push(ev); }
  catch { /* skip malformed */ }
}
events.sort((a, b) => a.seq - b.seq);

const session = new ReplaySession();
const compactionIds = new Map(); // id -> {startEv, endEv}
let stats = {
  starts: 0,
  beforeCheckpointAsStart: 0,
  beforeRangeNonNull: 0,
  afterRangeNonNull: 0,
  afterNull: 0,
  afterCheckpointAsStart: 0,
  convergeSkip: 0,
  summaryNotSmaller: 0,
  lastFailed: null,
};
for (const ev of events) {
  const type = ev.type;
  const data = ev.data ?? {};
  if (type === 'compaction/start') compactionIds.set(data.compactionId, { start: ev, end: null });
  else if (type === 'compaction/end') {
    const row = compactionIds.get(data.compactionId);
    if (row) row.end = ev;
  }
  // fold after recording markers so compaction events themselves are present
  if (type === 'compaction/summary' || type === 'compaction/prune') {
    // these are meter-facing events, not surface; skip fold (they never carry surfaceOp)
  }
  session.fold(ev);
  if (type !== 'compaction/start') continue;
  stats.starts += 1;
  const m = session.measurement();
  const fingerprint = `${m.surfaceTokens}:${session.surface.nodes.length}`;
  const before = selectBefore(session, RETAIN);
  const after = selectAfter(session, m, RETAIN);
  if (before !== null && isCheckpointSeq(session, session.surface.nodes[0])) stats.beforeCheckpointAsStart += 1;
  if (before !== null) stats.beforeRangeNonNull += 1;
  if (after !== null) {
    stats.afterRangeNonNull += 1;
    if (isCheckpointSeq(session, after.start)) stats.afterCheckpointAsStart += 1;
    const failed = stats.lastFailed;
    if (failed && failed.start === after.start && failed.end === after.end && failed.fingerprint === fingerprint) {
      stats.convergeSkip += 1;
      continue;
    }
  } else stats.afterNull += 1;
  // simulate outcome from the durable log: no end error => success
  const row = compactionIds.get(data.compactionId);
  const endErr = row?.end?.data?.error ?? '';
  if (endErr.startsWith('summary is not smaller')) {
    stats.summaryNotSmaller += 1;
    if (after !== null) stats.lastFailed = { start: after.start, end: after.end, fingerprint };
  } else if (after !== null && row?.end && !endErr) {
    stats.lastFailed = null; // success clears the remembered failure
  }
}
console.log(JSON.stringify({
  cutoff: CUTOFF,
  materializedEvents: events.length,
  starts: stats.starts,
  BEFORE: {
    rangeNonNull: stats.beforeRangeNonNull,
    checkpointAsStart: stats.beforeCheckpointAsStart,
  },
  AFTER: {
    rangeNonNull: stats.afterRangeNonNull,
    null: stats.afterNull,
    checkpointAsStart: stats.afterCheckpointAsStart,
    convergeSkip: stats.convergeSkip,
    summaryNotSmallerSeen: stats.summaryNotSmaller,
  },
  expectation: {
    checkpointRecompactEliminated: stats.afterCheckpointAsStart === 0,
    sameRegionUnchangedRetryEliminated: stats.convergeSkip === 0 || stats.convergeSkip > 0 ? 'skipped where unchanged' : 'n/a',
  },
}, null, 2));