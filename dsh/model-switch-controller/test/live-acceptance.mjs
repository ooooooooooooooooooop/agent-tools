// live-acceptance.mjs — deployed-artifact live acceptance for model-switch-controller.
//
// Runs in a fresh Node process against the DEPLOYED runtime distribution:
//   * real cordis Context (plugin event wiring is the production path)
//   * real dsh-session Session objects (durable surface, request headers)
//   * deployed dsh-compaction-convergence engine (real selector + replacement)
//   * deployed dsh-context-lifecycle (real DSH_HANDOFF_V1 handoff + markReadOnly)
//   * deployed dsh-model-switch-controller (the artifact the host loaded)
//   * real CPA relay summarization for compaction (live gateway call)
//   * capacity truth read from the live ~/.dsh/settings.yaml registry
//
// Usage: node dsh/model-switch-controller/test/live-acceptance.mjs
// Exit 0 with ACCEPTANCE=PASS, non-zero otherwise. Never fakes outcomes.
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, existsSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const HOME = process.env.USERPROFILE;
const PROFILE = join(HOME, ".dsh", "profiles", "web");
const NM = join(PROFILE, "base-dsh-0.1.1-rc.2", "node_modules");
const importDep = (p) => import(pathToFileURL(join(NM, p)).href);

// --- deployed modules (the artifacts the restarted host resolved) ----------
const { Context } = await importDep(join("@deepseek-ai", "cordis", "lib", "index.js"));
const dshSession = await importDep(join("@deepseek-ai", "dsh-session", "lib", "index.js"));
const dshLlm = await importDep(join("@deepseek-ai", "dsh-llm", "lib", "index.js"));
const deployedCompaction = await import(pathToFileURL(join(PROFILE, "plugins", "dsh-compaction-convergence", "lib", "index.js")).href);
const deployedLifecycle = await import(pathToFileURL(join(PROFILE, "plugins", "dsh-context-lifecycle", "lib", "index.js")).href);
const deployedSwitch = await import(pathToFileURL(join(PROFILE, "plugins", "dsh-model-switch-controller", "lib", "index.js")).href);

const { Session } = dshSession;
const { LlmError, createUserMessage } = dshLlm;
const { BasicCompactionEngine, selectCompactableRange, isSummaryNotSmallerError } = deployedCompaction;
const ContextLifecycle = deployedLifecycle.default;

const checks = [];
const check = (name, ok, detail = "") => {
  checks.push({ name, ok: !!ok, detail });
  console.log(`${ok ? "ok" : "NOT OK"} - ${name}${detail ? ` — ${detail}` : ""}`);
  if (!ok) process.exitCode = 1;
};

// --- capacity truth from the live settings registry ------------------------
function parseSettings() {
  const text = readFileSync(join(HOME, ".dsh", "settings.yaml"), "utf8");
  const declared = {};
  const evidence = {};
  let currentProvider = null, currentModel = null, inModels = false;
  for (const line of text.split(/\r?\n/)) {
    const pm = line.match(/^\s{4}([A-Za-z0-9._-]+):\s*$/);
    if (pm) { currentProvider = pm[1]; inModels = false; currentModel = null; continue; }
    if (/^\s{6}models:\s*$/.test(line)) { inModels = true; continue; }
    const mm = line.match(/^\s{6}-\s+id:\s*['"]?(\S+?)['"]?\s*$/);
    if (mm && inModels && currentProvider) {
      currentModel = mm[1];
      declared[`${currentProvider}/${currentModel}`] = null;
      continue;
    }
    const cw = line.match(/^\s+contextWindow:\s*(\d+)\s*$/);
    if (cw && currentModel) { declared[`${currentProvider}/${currentModel}`] = Number(cw[1]); continue; }
    const ev = line.match(/^\s+grade:\s*['"]?(\w+)['"]?\s*$/);
    if (ev && currentModel) { evidence[`${currentProvider}/${currentModel}`] = ev[1]; }
  }
  return { declared, evidence };
}
const SETTINGS = parseSettings();
const GEMINI = "gemini-3.7-flash-high";
const CPA = "cpa";
const geminiDeclared = SETTINGS.declared[`${CPA}/${GEMINI}`];
check("capacity truth: gemini declared window present in live registry", geminiDeclared === 1048576,
  `declared=${geminiDeclared}`);
check("capacity truth: gemini has NO authoritative evidence (UNKNOWN)", SETTINGS.evidence[`${CPA}/${GEMINI}`] !== "authoritative",
  `grade=${SETTINGS.evidence[`${CPA}/${GEMINI}`] ?? "absent"}`);

// --- real CPA relay summarization (live gateway call) ----------------------
function cpaKey() {
  if (process.env.CPA_API_KEY) return process.env.CPA_API_KEY;
  try {
    const creds = readFileSync(join(HOME, ".dsh", ".credentials.yaml"), "utf8");
    return (creds.match(/CPA_API_KEY\s*[:=]\s*['"]?(\w+)['"]?/) ?? [])[1] ?? "";
  } catch { return ""; }
}
function messageText(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return JSON.stringify(content);
  return content.map((b) => b.text ?? b.thinking ?? "").join("\n");
}
async function* realCpaStream(options) {
  const key = cpaKey();
  if (!key) throw new LlmError("CPA_API_KEY unavailable for live summarization", "ADAPTER_AUTH");
  const messages = options.messages.map((m) => ({ role: m.role === "assistant" ? "assistant" : "user", content: messageText(m.content) }));
  if (options.system) messages.unshift({ role: "system", content: messageText(options.system) });
  const resp = await fetch("http://127.0.0.1:8317/v1/chat/completions", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}` },
    body: JSON.stringify({ model: options.model, messages, max_tokens: options.maxTokens ?? 2048, temperature: 0 }),
    signal: options.signal instanceof AbortSignal ? options.signal : undefined,
  });
  if (!resp.ok) throw new LlmError(`cpa relay ${resp.status} for ${options.model}`, "ADAPTER_HTTP");
  const payload = await resp.json();
  const text = payload?.choices?.[0]?.message?.content ?? "";
  if (!text.trim()) throw new LlmError("cpa relay returned empty summary", "ADAPTER_EMPTY");
  yield { type: "block-start", index: 0, blockType: "text" };
  yield { type: "text-delta", index: 0, text };
  yield { type: "block-end", index: 0, block: { type: "text", text } };
  yield { type: "finish", reason: "stop" };
}

// llm service boundary: production resolver semantics from the live registry.
const llmService = {
  async resolveModelInfo(provider, model) {
    const declared = SETTINGS.declared[`${provider}/${model}`];
    if (declared === undefined || declared === null) {
      throw new LlmError(`no adapter for ${provider}/${model}`, "NO_ADAPTER");
    }
    return { provider, id: model, name: model, context: { contextWindow: declared } };
  },
  stream: (options) => realCpaStream(options),
};

// --- measurement: same pricing family the guard preview uses (ceil len/4) --
// --- measurement: exact fixed-density heuristic the real meter uses ----------
// (ceil(chars/4) + BLOCK_OVERHEAD per text block, +4 role framing) so the
// controller's preview and the guard's admission price identical content alike.
function estimateMessage(message) {
  const text = messageText(message?.content ?? "");
  return Math.ceil(text.length / 4) + 4 + 4;
}
function measure(session) {
  let total = 0;
  const nodes = [];
  for (const seq of session.surface.nodes) {
    const ev = session.events[seq];
    const tokens = estimateMessage(ev?.data ?? {});
    nodes.push({ seq, tokens });
    total += tokens;
  }
  return { nodes, totalTokens: total, surfaceTokens: total, baseline: { kind: "estimated", tokens: 0 }, surfaceDeltaTokens: 0 };
}
const tokenMeter = { measure, estimateMessage };

// --- session construction (real Session class) ------------------------------
function makeSession(id, cwd) {
  const session = Session.create(id, [], { version: 0, id, createdAt: Date.now(), cwd });
  session.append("request/header", { header: { config: { provider: CPA, model: "gpt-5.6-luna-max" } } });
  // The compaction engine only compacts inside an open turn (production
  // invariant); the drill must model that lifecycle honestly.
  session.append("turn/start", { turn: `turn-${id}` });
  return session;
}
function pushUser(session, text) {
  return session.append("user/message", { content: [{ type: "text", text }] }, { surfaceOp: "append" }).seq;
}
// Append-only event log immutability: every event present before the switch
// must still exist with byte-identical data afterwards (compaction adds new
// events and rewires surface pointers; it never rewrites history).
function snapshotEvents(session) {
  const snap = new Map();
  for (const seq of Object.keys(session.events)) snap.set(seq, JSON.stringify(session.events[seq]?.data ?? null));
  return snap;
}
function eventsUnchanged(session, snap) {
  for (const [seq, data] of snap) {
    if (JSON.stringify(session.events[seq]?.data ?? null) !== data) return false;
  }
  return true;
}
// lifecycle sidecar file name: sha256(sessionId).slice(0, 32).json
const lifecycleSidecar = (id) => join(LIFECYCLE_DIR, `${createHash("sha256").update(id).digest("hex").slice(0, 32)}.json`);

// --- boot the real plugin stack on a real cordis context --------------------
const DRILL = mkdtempSync(join(tmpdir(), "msw-live-"));
const LIFECYCLE_DIR = join(DRILL, "lifecycle");
const SWITCH_DIR = join(DRILL, "switch");
const createdAgents = [];
const injected = [];

const ctx = new Context();
ctx.provide("tokenMeter", tokenMeter);
ctx.provide("llm", llmService);
ctx.provide("sessions", { list: () => [] });
ctx.provide("agents", {
  create: async ({ sessionId, agentOptions, meta }) => {
    const target = makeSession(sessionId, meta?.cwd ?? DRILL);
    createdAgents.push({ sessionId, agentOptions, meta, session: target });
    return { agent: { inject: (msg) => injected.push({ sessionId, msg }) } };
  },
});
ctx.plugin(ContextLifecycle, { archiveThresholdTokens: 800000, sidecarDir: LIFECYCLE_DIR });
ctx.plugin(BasicCompactionEngine, {
  summarizationProvider: CPA,
  summarizationModel: "gemini-3.8-flash-high",
  maxTokens: 2048,
  maxOverflowRetries: 2,
  maxConsecutiveFailures: 3,
  auto: false,
});
ctx.plugin(deployedSwitch.apply, {
  sidecarDir: SWITCH_DIR,
  safetyMargin: 16384,
  requiredOutputBudget: 65536,
  inputMultiplier: 1.08,
  conservativeFallbackLimit: 262144,
  maxCompactionAttempts: 2,
});
// cordis activates plugin fibers asynchronously; let them settle before lookup.
await new Promise((r) => setImmediate(r));
await new Promise((r) => setImmediate(r));

const engine = ctx.get("compaction"); // vendor dsh-compaction pins this name
check("live engine retrievable by registered name", engine instanceof BasicCompactionEngine,
  "ctx.get(\"compaction\")");
const lifecycle = ctx.get("contextLifecycle");
check("live contextLifecycle service present", lifecycle instanceof ContextLifecycle);

const signal = { aborted: false };
const proposed = { provider: CPA, model: GEMINI };
const runWaterfall = (agent, target = proposed) =>
  ctx.waterfall("agent/request", { agent, signal }, () => Promise.resolve(target));

function eventsLog() {
  const p = join(SWITCH_DIR, "events.jsonl");
  if (!existsSync(p)) return [];
  return readFileSync(p, "utf8").split("\n").filter(Boolean).map(JSON.parse);
}
const lastEvent = (type) => eventsLog().filter((e) => e.type === type).at(-1);

// === Case A: real small session -> IN_PLACE =================================
{
  const session = makeSession("live-case-a", DRILL);
  for (let i = 0; i < 12; i += 1) pushUser(session, `case-a turn ${i} `.repeat(40));
  const agent = { session, options: { provider: CPA, model: GEMINI } };
  const measured = measure(session);
  const out = await runWaterfall(agent);
  check("Case A: switch admitted in place", out?.model === GEMINI, `measured=${measured.totalTokens}`);
  const cap = lastEvent("TARGET_CAPABILITY_RESOLVED");
  check("Case A: honest gemini semantics (declared 1M, effective 262144, CONSERVATIVE_FALLBACK)",
    cap?.declaredContext === 1048576 && cap?.effectiveLimit === 262144 && cap?.capacityProvenance === "CONSERVATIVE_FALLBACK",
    JSON.stringify({ declared: cap?.declaredContext, effective: cap?.effectiveLimit, provenance: cap?.capacityProvenance }));
  check("Case A: no handoff/compaction side effects", createdAgents.length === 0,
    `created=${createdAgents.length}`);
}

// === Case B: over limit, compactable head -> COMPACT_THEN_SWITCH =============
{
  const session = makeSession("live-case-b", DRILL);
  for (let i = 0; i < 24; i += 1) pushUser(session, `case-b history ${i} `.repeat(1750)); // ~178k head, just over the admitted limit
  pushUser(session, "case-b latest instruction, keep me in the tail.");
  const agent = { session, options: { provider: CPA, model: GEMINI } };
  const pre = measure(session).totalTokens;
  const first = await runWaterfall(agent).then(() => null, (e) => e);
  check("Case B: first pass triggers bounded compaction", first?.code === "MODEL_SWITCH_COMPACTED",
    first?.code ?? "returned");
  const compactResult = lastEvent("COMPACTION_RESULT");
  check("Case B: real engine compaction committed via live gateway",
    compactResult?.result === "ok" && typeof compactResult?.postCompactionInput === "number",
    `result=${compactResult?.result} post=${compactResult?.postCompactionInput}`);
  const post = measure(session).totalTokens;
  check("Case B: surface actually shrank", post < pre, `${pre} -> ${post}`);
  const second = await runWaterfall(agent).then((v) => v, (e) => e);
  check("Case B: retry admitted as COMPACT_THEN_SWITCH", second?.model === GEMINI,
    second?.model ? `model=${second.model}` : `threw: ${second?.code} :: ${second?.message ?? second}`);
  check("Case B: no handoff session created", createdAgents.length === 0, `created=${createdAgents.length}`);
}

// === Case C: 424k-scale with huge retained tail -> HANDOFF ==================
{
  const session = makeSession("live-case-c", DRILL);
  for (let i = 0; i < 10; i += 1) pushUser(session, `case-c head ${i} `.repeat(800));
  pushUser(session, "TAIL-BLOCK ".repeat(200_000)); // ~550k tokens in one retained tail message
  const agent = { session, options: { provider: CPA, model: GEMINI } };
  const preEvents = snapshotEvents(session);
  const pre = measure(session).totalTokens;
  check("Case C: fixture over conservative-admitted limit", Math.ceil(pre * 1.08) + 16384 + 65536 > 262144,
    `conservative=${Math.ceil(pre * 1.08)}`);

  const e1 = await runWaterfall(agent).then(() => null, (e) => e);
  check("Case C: pass 1 compacts (bounded)", e1?.code === "MODEL_SWITCH_COMPACTED", e1?.code);
  const e2 = await runWaterfall(agent).then(() => null, (e) => e);
  check("Case C: pass 2 compacts (bounded)", e2?.code === "MODEL_SWITCH_COMPACTED", e2?.code);
  const attempts = eventsLog().filter((e) => e.type === "COMPACTION_ATTEMPTED").length;
  check("Case C: compaction attempts bounded (<= maxCompactionAttempts=2)", attempts <= 2, `attempts=${attempts}`);
  const e3 = await runWaterfall(agent).then(() => null, (e) => e);
  check("Case C: endpoint is HANDOFF_COMPLETED, never CONTEXT_PREFLIGHT_BLOCKED",
    e3?.code === "MODEL_SWITCH_HANDOFF_COMPLETED", e3?.code);
  check("Case C: handoff UX copy (new session / source retained / no second worker)",
    /已自动创建新的/.test(e3?.message ?? "") && /原会话已保留/.test(e3?.message ?? "") && /后台长任务未重复启动/.test(e3?.message ?? ""));
  check("Case C: exactly one continuation session created", createdAgents.length === 1,
    `created=${createdAgents.length}`);
  const created = createdAgents[0];
  check("Case C: deterministic handoff session id", /^handoff-[0-9a-f]{16}$/.test(created?.sessionId ?? ""), created?.sessionId);
  check("Case C: target route is gemini (no silent fallback)",
    created?.agentOptions?.provider === CPA && created?.agentOptions?.model === GEMINI,
    JSON.stringify(created?.agentOptions));
  check("Case C: DSH_HANDOFF_V1 payload injected into target session (not the raw transcript)",
    injected.length === 1 && /Handoff from live-case-c/.test(injected[0].msg?.content?.[0]?.text ?? "") &&
    injected[0].msg.content[0].text.length < 20000,
    `injected=${injected.length} bytes=${injected[0]?.msg?.content?.[0]?.text?.length ?? 0}`);
  const readonlySidecar = lifecycleSidecar("live-case-c");
  check("Case C: source marked READ_ONLY via lifecycle sidecar", existsSync(readonlySidecar),
    readonlySidecar);
  if (existsSync(readonlySidecar)) {
    const state = JSON.parse(readFileSync(readonlySidecar, "utf8"));
    check("Case C: read-only reason recorded", state.status === "READ_ONLY_CONTEXT_EXHAUSTED" && !!state.reason,
      `${state.status}: ${state.reason}`);
  }
  check("Case C: source event log append-only (history never rewritten)", eventsUnchanged(session, preEvents),
    `events=${preEvents.size}`);
  // Retry against the retained source: the pre-existing lifecycle guard rejects
  // it read-only ("continue from a handoff session") — the handoff target is
  // the continuation point, and no second continuation session is created.
  const e4 = await runWaterfall(agent).then((v) => v, (e) => e);
  check("Case C: retried request on retained source rejected read-only; no second session",
    e4?.code === "CONTEXT_PREFLIGHT_BLOCKED" && /handoff session/.test(e4?.message ?? "")
    && createdAgents.length === 1,
    `created=${createdAgents.length} e4=${e4?.code}`);
}

// === Case F: unavailable target -> explicit TARGET_MODEL_UNAVAILABLE ========
{
  const session = makeSession("live-case-f", DRILL);
  pushUser(session, "small session");
  const agent = { session, options: { provider: "nope", model: "nope" } };
  const err = await runWaterfall(agent, { provider: "nope", model: "nope" }).then((v) => v, (e) => e);
  check("Case F: unknown route blocked with TARGET_MODEL_UNAVAILABLE",
    err?.code === "TARGET_MODEL_UNAVAILABLE", err?.code ?? "returned");
  check("Case F: no session created, no compaction", createdAgents.length <= 1);
}

// === evidence sequence ======================================================
{
  const types = eventsLog().map((e) => e.type);
  for (const t of ["MODEL_SWITCH_REQUESTED", "TARGET_CAPABILITY_RESOLVED", "CONTEXT_PREFLIGHT_RESULT",
    "COMPACTION_ATTEMPTED", "COMPACTION_RESULT", "HANDOFF_REQUIRED", "HANDOFF_CREATED",
    "TARGET_SESSION_CREATED", "TARGET_MODEL_VERIFIED", "SOURCE_SESSION_RETAINED"]) {
    check(`evidence: ${t} recorded`, types.includes(t));
  }
  const preflights = eventsLog().filter((e) => e.type === "CONTEXT_PREFLIGHT_RESULT");
  check("evidence: no CONTEXT_PREFLIGHT_BLOCKED endpoint anywhere",
    !eventsLog().some((e) => e.mode === "CONTEXT_PREFLIGHT_BLOCKED" || e.code === "CONTEXT_PREFLIGHT_BLOCKED"));
  const admitted = preflights.filter((e) => e.mode !== "BLOCKED_WITH_REASON");
  check("evidence: every admitted preflight decision carries the admission fields",
    admitted.length > 0 && admitted.every((e) => e.conservativeInput !== undefined && e.safetyMargin === 16384
      && e.requiredOutputBudget === 65536 && e.targetEffectiveLimit === 262144
      && !!e.targetCapacityProvenance));
  const blocked = preflights.filter((e) => e.mode === "BLOCKED_WITH_REASON");
  check("evidence: blocked preflights carry the block reason, never guessed capacity",
    blocked.every((e) => !!e.reason && e.targetEffectiveLimit === null && e.targetCapacityProvenance === null));
}

// === summary ================================================================
const pass = checks.filter((c) => c.ok).length;
console.log(`\nLIVE_ACCEPTANCE ${process.exitCode ? "FAIL" : "PASS"} — ${pass}/${checks.length} checks`);
console.log(`durable jobs: not exercised by drill (DURABLE_JOB_REF=N/A; no second worker started: ${createdAgents.length <= 1})`);
