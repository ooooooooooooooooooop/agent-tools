// Runtime smoke for the deployed dsh-compaction-convergence overlay.
// Runs in a fresh Node process (independent of the DSH GUI process) and loads the
// deployed package from the running checkout: proves the module that a restarted
// GUI would resolve carries the patched code, the expected version/hash marker,
// and the checkpoint-aware/convergent behavior at the selector level.
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';

const CHECK = process.env.DSH_CHECKOUT;
if (!CHECK) throw new Error('DSH_CHECKOUT must be set');
const checkout = path.join(CHECK, 'node_modules', '@deepseek-ai');
const profile = path.join(process.env.USERPROFILE, '.dsh', 'profiles', 'web');
const forkDir = path.join(profile, 'plugins', 'dsh-compaction-convergence');

const pkgPath = path.join(forkDir, 'package.json');
const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
const markerPath = path.join(forkDir, 'lib', '.dsh-convergence.json');
const marker = fs.existsSync(markerPath) ? JSON.parse(fs.readFileSync(markerPath, 'utf8')) : null;
const libHash = await import('node:crypto').then((c) => c.createHash('sha256').update(fs.readFileSync(path.join(forkDir, 'lib', 'index.js'))).digest('hex'));

const compactionMod = await import(pathToFileURL(path.join(checkout, 'dsh-compaction', 'lib', 'index.js')).href);
const deployed = await import(pathToFileURL(path.join(forkDir, 'lib', 'index.js')).href);
const { toolPairingBalancedBefore } = compactionMod;
const { selectCompactableRange, BasicCompactionEngine, isSummaryNotSmallerError } = deployed;

// light session stub accepted by selectCompactableRange
const events = [];
const surface = { nodes: [], replaceGeneration: 0 };
const priced = new Map();
function push(seq, type, text, source) {
  const ev = { seq, type, data: { content: [{ type: 'text', text }], ...(source ? { source } : {}) } };
  events[seq] = ev;
  surface.nodes.push(seq);
  priced.set(seq, Math.max(8, Math.ceil(text.length / 4) + 4));
}
const session = { events, surface };
const measurement = () => ({
  nodes: surface.nodes.map((seq) => ({ seq, tokens: priced.get(seq) ?? 0 })),
  surfaceTokens: surface.nodes.reduce((a, s) => a + priced.get(s), 0),
  totalTokens: surface.nodes.reduce((a, s) => a + priced.get(s), 0),
  baseline: { kind: 'estimated', tokens: 0 },
  surfaceDeltaTokens: 0,
});

const results = {};
results.version = pkg.version;
results.markerPresent = marker !== null;
results.markerVersion = marker?.deployed_version ?? null;
results.libHashMatchesMarker = !!marker && marker.fork_lib_index_sha256 === libHash;
results.checkpointAwareExport = typeof selectCompactableRange === 'function' && typeof isSummaryNotSmallerError === 'function';
results.engineClass = typeof BasicCompactionEngine === 'function';

// behavior: checkpoint must not be selected with ordinary history present
for (let i = 0; i < 30; i += 1) push(30 + i, 'user/message', `user ${i}`.repeat(120));
push(99, 'user/message', 'cp'.repeat(300), { kind: 'plugin', plugin: 'compact', compactionId: 'smoke' });
const withCp = surface.nodes.slice();
// rebuild ordering: checkpoint first, then normal nodes
surface.nodes.length = 0;
surface.nodes.push(99, ...withCp.slice(0, 30));
const r1 = selectCompactableRange(session, measurement(), 500);
results.checkpointNotSelected = r1 !== null && r1.start !== 99;

// behavior: all-checkpoint candidate region returns null
const tiny = { events, surface: { nodes: [99], replaceGeneration: 0 } };
const r2 = selectCompactableRange(tiny, { nodes: [{ seq: 99, tokens: 200 }], surfaceTokens: 200, totalTokens: 200, baseline: { kind: 'estimated', tokens: 0 }, surfaceDeltaTokens: 0 }, 0);
results.allCheckpointReturnsNull = r2 === null;

const allPass = Object.values(results).every((v) => v === true || v === pkg.version);
console.log(JSON.stringify({ deployed: results, allPass }, null, 2));
process.exit(allPass ? 0 : 1);
