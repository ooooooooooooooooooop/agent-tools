// dsh-world-model：Personal AI 世界模型机制层插件（身体侧适配器）
// theory: V0.3.1 | schema: 1.1
//
// 身份：插件是 Personal AI 身体上的器官（反射弧/海马体），不是世界模型本身。
//   灵魂 = canonicalDir 下的 W/V/U/L 状态（harness 无关，可随身体迁移）。
//   身体 = DSH；本插件只认 canonicalDir/stateDir/bodyId 三个接口。
//
// 机制（非提示词）：
//   - world_model 工具：activate/model/predict/observe/evaluate/update/probe/
//     meta/value/input/persist/status/declassify → 真实落盘
//   - tools/result → RAW_EVIDENCE（L0）机械捕获，带 source/channel/tool provenance
//   - tools.guard → CORE/FULL：consequential mutation 无绑定预测不放行
//   - session 首事件 → 结构化 briefing（canonical 编译产物）注入 + STATE_RESTORE
//   - INPUT_SEMANTICS 路由：EPISTEMIC_CLAIM→W / NORMATIVE_DIRECTIVE→查 U 权威 /
//     AUTHORIZATION→许可记录 / DURABLE_VALUE→value proposal / PREFERENCE→局部
//   - body lease：同一 entity_id 只允许一个 canonical writer（runtime-state.json）
//   - fork 检测：canonical lineage_head 与本 body 记录不一致 → FORK_DETECTED
//   - L1 ledger/runs append-only；canonical 只写 proposals/（治理路径）
//
// 用法：plugins/ + cordis.patch.yml 条目。模式 config.mode/env DSH_WM_MODE。

import { appendFileSync, existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { homedir, hostname } from 'node:os';
import { randomUUID } from 'node:crypto';

export const name = 'dsh-world-model';
export const inject = ['tools', 'systemPrompt'];

const SCHEMA_VERSION = '1.1';
const THEORY_VERSION = '0.3.1';
const BCC_VERSION = 'BCC-1';
const BRIEF_HARD_CAP = 8 * 1024;
const CONSEQUENT_TOOLS = new Set(['edit', 'write', 'str-replace-editor', 'notebook_edit', 'exec', 'mcp_call_tool']);
const IRREVERSIBLE_RE = /rm\s+-rf|del\s+\/[sq]|rmdir|Remove-Item[^\n]*-Recurse|drop\s+table|drop\s+database|truncate|git\s+push[^\n]*(--force|-f\b)|git\s+reset[^\n]*--hard/i;
const SEMANTIC_TYPES = new Set(['EPISTEMIC_CLAIM', 'NORMATIVE_DIRECTIVE', 'AUTHORIZATION', 'DURABLE_VALUE_STATEMENT', 'PREFERENCE']);

function today() { return new Date().toISOString().slice(0, 10); }
function safeJson(v) { try { return JSON.stringify(v); } catch { return '"<unserializable>"'; } }
function readJson(p) { try { return existsSync(p) ? JSON.parse(readFileSync(p, 'utf8')) : null; } catch { return null; } }
function bytes(s) { return Buffer.byteLength(String(s), 'utf8'); }

const WM_PARAMETERS = {
  type: 'object',
  properties: {
    op: { type: 'string', enum: ['activate', 'model', 'predict', 'observe', 'evaluate', 'update', 'probe', 'meta', 'value', 'input', 'persist', 'status', 'declassify'] },
    reason: { type: 'string' },
    mode: { type: 'string', enum: ['core', 'full'] },
    models: { type: 'array', items: { type: 'object', additionalProperties: true } },
    subject: { type: 'string' },
    intended_action: { type: 'string' },
    expected_observation: { type: 'string' },
    falsifier: { type: 'string' },
    time_horizon: { type: 'string' },
    confidence_bucket: { type: 'string', enum: ['high', 'medium', 'low', 'unknown'] },
    irreversible: { type: 'boolean' },
    prediction_id: { type: 'string' },
    observation: { type: 'string' },
    verdict: { type: 'string', enum: ['confirmed', 'refuted', 'partial', 'unknown'] },
    evaluation_source: { type: 'string', enum: ['mechanical', 'later_reality', 'independent_model', 'human', 'self'] },
    observation_refs: { type: 'array', items: { type: 'string' } },
    model_id: { type: 'string' },
    revision_type: { type: 'string', enum: ['param', 'structure', 'h4'] },
    change: { type: 'string' },
    update_class: { type: 'string', enum: ['world_model', 'value_model', 'governance_u1', 'governance_u0', 'lineage', 'declassification', 'body_binding', 'gate_policy', 'schema'] },
    target: { type: 'string', enum: ['confirmation', 'discrimination', 'falsification', 'exploration'] },
    probe_mode: { type: 'string', enum: ['understand', 'act'] },
    level: { type: 'string', enum: ['param', 'model', 'representation'] },
    expected_gain: { type: 'string', enum: ['high', 'medium', 'low', 'unknown'] },
    cost: { type: 'string', enum: ['cheap', 'medium', 'expensive', 'unknown'] },
    rejected_alternatives: { type: 'array', items: { type: 'string' } },
    decision: { type: 'string', enum: ['continue', 'query', 'act', 'stop'] },
    bottleneck: { type: 'string', enum: ['information', 'computation', 'model', 'value', 'none', 'unknown'] },
    goal: { type: 'string' },
    decision_criteria: { type: 'string' },
    proxy_risk: { type: 'string' },
    value_update: { type: 'object', additionalProperties: true },
    // INPUT_SEMANTICS (V0.3.1 §6)
    semantic_type: { type: 'string', enum: ['EPISTEMIC_CLAIM', 'NORMATIVE_DIRECTIVE', 'AUTHORIZATION', 'DURABLE_VALUE_STATEMENT', 'PREFERENCE'] },
    content: { type: 'string' },
    source_id: { type: 'string' },
    scope: { type: 'string' },
    action_ref: { type: 'string' },
    // classification (§8)
    access_level: { type: 'string', enum: ['PRIVATE', 'INTERNAL', 'SANITIZED', 'PUBLIC'] },
    epistemic_layer: { type: 'string', enum: ['L0', 'L1', 'L2', 'L3'] },
    declassify_target: { type: 'string' },
    destination: { type: 'string' },
    redaction_manifest: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
    open_loops: { type: 'array', items: { type: 'string' } },
    canonical_proposal: { type: 'object', additionalProperties: true },
    evidence_refs: { type: 'array', items: { type: 'string' } },
    supersedes: { type: 'array', items: { type: 'string' } },
    uncertainty_type: { type: 'string', enum: ['empirical', 'logical'] }
  },
  required: ['op'],
  additionalProperties: true
};

export function apply(ctx, config = {}) {
  const wmDir = config.stateDir || join(homedir(), '.dsh', 'world-model');
  const canonicalDir = config.canonicalDir || join(homedir(), 'personal-ai-state', 'world-model');
  const bodyId = config.bodyId || `dsh-${hostname()}`;
  const envMode = String(config.mode || process.env.DSH_WM_MODE || 'off').toLowerCase();
  for (const d of [join(wmDir, 'ledger'), join(wmDir, 'runs'), join(canonicalDir, 'proposals'), join(canonicalDir, 'history')]) {
    try { mkdirSync(d, { recursive: true }); } catch { /* ignore */ }
  }
  const bodyStatePath = join(wmDir, 'body-state.json');
  let bodyState = readJson(bodyStatePath) || { body_id: bodyId, last_lineage_head: null, epoch_seen: null };
  function saveBodyState() { try { writeFileSync(bodyStatePath, safeJson(bodyState)); } catch { /* ignore */ } }

  const sessions = new Map();
  let globalSeq = 0;
  function sessionFor(exec) {
    const sid = String(exec?.agent?.session?.id || exec?.session?.id || 'unknown');
    if (!sessions.has(sid)) sessions.set(sid, {
      id: sid, mode: envMode, predictions: new Map(), evaluated: new Set(),
      activated: envMode !== 'off', seq: 0, lastEventId: null
    });
    return sessions.get(sid);
  }

  function runtimeState() { return readJson(join(canonicalDir, 'runtime-state.json')) || {}; }

  function emit(sessionId, eventType, data = {}) {
    const s = sessions.get(sessionId);
    const seq = ++globalSeq;
    const ev = {
      event_id: randomUUID(), schema_version: SCHEMA_VERSION, theory_version: THEORY_VERSION,
      session_id: sessionId, seq, prev_event: s ? s.lastEventId : null,
      event_type: eventType, timestamp: new Date().toISOString(), actor: 'agent',
      body_id: bodyId, bcc: BCC_VERSION,
      layer: eventType === 'RAW_EVIDENCE' ? 'L0' : 'L1',
      access: data.access || 'PRIVATE',
      ...data
    };
    if (s) s.lastEventId = ev.event_id;
    try {
      appendFileSync(join(wmDir, 'ledger', `${today()}.jsonl`), safeJson(ev) + '\n');
      appendFileSync(join(wmDir, 'runs', `${sessionId}.jsonl`), safeJson(ev) + '\n');
    } catch { /* ledger write must never crash the loop */ }
    return ev;
  }

  // ---- body lease: single canonical writer per entity (V0.3.1 §11) ----
  function leaseCheck(s) {
    const rs = runtimeState();
    const active = rs.active_body || {};
    if (!active.body_id) return { ok: true, note: 'no active body bound' };
    if (active.body_id === bodyId) {
      // fork detection: canonical head moved under us by another writer
      const head = rs.lineage_head;
      if (bodyState.last_lineage_head && head && head !== bodyState.last_lineage_head) {
        emit(s.id, 'FORK_DETECTED', { happened: 'canonical lineage_head changed under active lease', payload: { expected: bodyState.last_lineage_head, seen: head }, source: 'plugin' });
      }
      bodyState.last_lineage_head = head || bodyState.last_lineage_head;
      bodyState.epoch_seen = rs.continuity_epoch || bodyState.epoch_seen;
      saveBodyState();
      return { ok: true };
    }
    emit(s.id, 'LEASE_DENIED', { happened: `canonical write denied: lease held by ${active.body_id}`, payload: { holder: active.body_id, requester: bodyId }, source: 'plugin' });
    return { ok: false, holder: active.body_id };
  }

  function writeProposal(kind, payload, sessionId, s, access) {
    const lease = s ? leaseCheck(s) : { ok: true };
    if (!lease.ok) return { denied: true, holder: lease.holder };
    const p = join(canonicalDir, 'proposals', `${today()}-${kind}-${randomUUID().slice(0, 8)}.json`);
    try {
      writeFileSync(p, safeJson({
        schema_version: SCHEMA_VERSION, kind, session_id: sessionId,
        timestamp: new Date().toISOString(), body_id: bodyId,
        classification: { level: access || 'PRIVATE', basis: ['taint_or_default'] },
        payload, status: 'proposed'
      }));
    } catch { /* ignore */ }
    return { path: p };
  }

  function readCanonicalSummary() {
    const cur = join(canonicalDir, 'current.yaml');
    if (!existsSync(cur)) return '(no canonical world-model yet — first activation)';
    try {
      const text = readFileSync(cur, 'utf8');
      return text.length > 1500 ? text.slice(0, 1500) + '\n…(truncated)' : text;
    } catch { return '(canonical unreadable)'; }
  }

  function canonicalStale() {
    try {
      const bp = join(canonicalDir, 'briefing.md');
      const rp = join(canonicalDir, 'runtime-state.json');
      if (!existsSync(bp) || !existsSync(rp)) return 'compiled artifacts missing';
      const bm = statSync(bp).mtimeMs, rm = statSync(rp).mtimeMs;
      let newest = 0;
      for (const f of readdirSync(canonicalDir)) {
        if (!f.endsWith('.yaml')) continue;
        const m = statSync(join(canonicalDir, f)).mtimeMs;
        if (m > newest) newest = m;
      }
      return (bm < newest || rm < newest) ? 'canonical newer than compiled artifacts — re-run canonical_compile.py' : null;
    } catch { return null; }
  }

  function updateCurrentJson(patch) {
    const cur = join(wmDir, 'current.json');
    let state = { schema_version: SCHEMA_VERSION, updated_at: null, open_predictions: [], models: {}, open_loops: [] };
    try { if (existsSync(cur)) state = { ...state, ...JSON.parse(readFileSync(cur, 'utf8')) }; } catch { /* ignore */ }
    Object.assign(state, patch, { updated_at: new Date().toISOString() });
    try { writeFileSync(cur, safeJson(state)); } catch { /* ignore */ }
    return state;
  }

  // ---- mechanical RAW_EVIDENCE capture (L0): every tool result, unfiltered ----
  try {
    ctx.on('tools/result', (exec, result) => {
      try {
        const s = sessionFor(exec);
        const text = typeof result === 'string' ? result : safeJson(result);
        emit(s.id, 'RAW_EVIDENCE', {
          subject: exec?.name || 'unknown-tool', happened: `tool ${exec?.name || '?'} returned`,
          payload: String(text).slice(0, 500), evidence_refs: [],
          source: 'tools/result', channel_id: 'tool_result',
          sensor_id: exec?.name || 'unknown',
          tool: { canonical_tool_id: exec?.name || 'unknown', body_tool_id: exec?.name || 'unknown' }
        });
      } catch { /* never crash */ }
    });
  } catch { /* event bus optional */ }

  // ---- session briefing：canonical 自动编译产物（非手写），永远注入 ----
  // 世界模型永远在场；OFF/CORE/FULL 只是形式化深度。简报=给 prior 也给
  // 推翻 prior 的钥匙（每条 current best 带 confidence/scope/counter/falsifier）。
  function buildBriefing() {
    const p = join(canonicalDir, 'briefing.md');
    if (!existsSync(p)) return null;
    try {
      let text = readFileSync(p, 'utf8').trim();
      if (!text) return null;
      if (bytes(text) > BRIEF_HARD_CAP) text = text.slice(0, BRIEF_HARD_CAP - 60) + '\n…(hard-capped at 8KiB)';
      return text;
    } catch { return null; }
  }

  // 简报=system-prompt section：永远在场、不竞争 user 消息、每次 prompt
  // assemble 重读 briefing.md（canonical 重编译后自动生效、跨 compaction 存活）。
  let briefingMounted = false;
  try {
    if (ctx.systemPrompt?.section) {
      ctx.systemPrompt.section({
        name: 'world-model:briefing',
        order: 10,
        text: () => buildBriefing() || ''
      });
      briefingMounted = true;
    }
  } catch { /* systemPrompt service optional */ }

  try {
    const briefedSessions = new Set();
    ctx.on('session/event', (session, event) => {
      try {
        const sid = String(session?.id || 'unknown');
        if (briefedSessions.has(sid)) return;
        const s = sessionFor({ agent: { session: { id: sid } } });
        if (event?.type === 'turn/start' || event?.type === 'step/start' || event?.type === 'user/message') {
          briefedSessions.add(sid);
          const stale = canonicalStale();
          emit(s.id, 'STATE_RESTORE', {
            happened: 'world-model state restored into session',
            payload: readCanonicalSummary(), stale, source: 'plugin'
          });
          if (stale) emit(s.id, 'BRIEFING_STALE', { happened: stale, source: 'plugin' });
          if (briefingMounted) {
            emit(s.id, 'BRIEFING_INJECTED', { happened: 'briefing mounted as system-prompt section', payload: { bytes: bytes(buildBriefing() || '') }, source: 'plugin' });
          } else {
            // fallback：无 systemPrompt 服务时退化为 user-message inject
            // （必须在下一 tick——同步 inject 会重入 append publisher）。
            const briefing = buildBriefing();
            if (briefing) {
              setImmediate(() => {
                try {
                  const agentsSvc = ctx.get?.('agents') || ctx.agents;
                  const agent = agentsSvc?.get?.(sid);
                  const msg = { id: randomUUID(), role: 'user', content: [{ type: 'text', text: briefing }], source: { kind: 'plugin', plugin: name } };
                  if (agent?.inject) { agent.inject(msg); emit(s.id, 'BRIEFING_INJECTED', { happened: 'session briefing injected', payload: { bytes: bytes(briefing) }, source: 'plugin' }); }
                  else if (agent?.followup) { agent.followup(msg); emit(s.id, 'BRIEFING_INJECTED', { happened: 'session briefing injected via followup', payload: { bytes: bytes(briefing) }, source: 'plugin' }); }
                  else emit(s.id, 'BRIEFING_FAILED', { happened: 'no agent handle for briefing injection', source: 'plugin' });
                } catch (e) { emit(s.id, 'BRIEFING_FAILED', { happened: `briefing injection threw: ${e?.message || e}`, source: 'plugin' }); }
              });
            }
          }
        }
      } catch { /* never crash */ }
    });
  } catch { /* event bus optional */ }

  // ---- world_model tool ----
  const reg = ctx?.tools?.register;
  if (typeof reg === 'function' && envMode !== 'strict-off') {
    reg.call(ctx.tools, {
      name: 'world_model',
      description: '世界模型状态机（theory V0.3.1 / schema 1.1）：activate/model/predict/observe/evaluate/update/probe/meta/value/input/persist/status/declassify。所有调用真实落盘 ledger——审计对象不是 prose。consequential 改动前必须先 predict（绑定 intended_action）。input 路由：用户/外部输入先经 semantic_type 分类——EPISTEMIC_CLAIM 进 W；NORMATIVE_DIRECTIVE 查 governance 权威 scope；DURABLE_VALUE_STATEMENT 走 value proposal；PREFERENCE 默认不持久化。',
      parameters: WM_PARAMETERS,
      output: { schema: { type: 'object', additionalProperties: true }, render: (_a, v) => [{ type: 'text', text: safeJson(v) }] },
      execute: (input = {}, exec) => {
        const s = sessionFor(exec);
        const op = String(input.op || '');
        const base = { subject: input.subject, evidence_refs: input.evidence_refs || [] };
        switch (op) {
          case 'activate': {
            if (input.mode) s.mode = input.mode;
            s.activated = true;
            const lease = leaseCheck(s);
            emit(s.id, 'WM_ACTIVATE', { ...base, happened: `world-model activated mode=${s.mode}`, payload: { mode: s.mode, reason: input.reason, lease } });
            return { ok: true, mode: s.mode, lease, note: 'WM active (schema 1.1 / V0.3.1). Before consequential mutations: world_model(op:"predict") with intended_action binding.' };
          }
          case 'model': {
            const ids = (input.models || []).map(m => m.id || m.model_id || m.proposition || 'unnamed');
            // access taint: model entries inherit strictest level of their evidence; default PRIVATE
            for (const m of (input.models || [])) {
              if (!m.access) m.access = { level: input.access_level || 'PRIVATE', basis: ['taint_or_default'] };
            }
            emit(s.id, 'MODEL_CREATED', { ...base, happened: `${ids.length} model(s) registered`, payload: { models: input.models }, epistemic_layer: input.epistemic_layer || 'L2' });
            const cur = join(wmDir, 'current.json');
            let st = {};
            try { if (existsSync(cur)) st = JSON.parse(readFileSync(cur, 'utf8')); } catch { /* ignore */ }
            const models = { ...(st.models || {}) };
            for (const m of (input.models || [])) models[m.id || m.model_id || 'unnamed'] = m;
            updateCurrentJson({ models });
            return { ok: true, model_ids: ids };
          }
          case 'predict': {
            const pid = `P-${randomUUID().slice(0, 8)}`;
            const closeSuperseded = (old) => {
              if (!s.predictions.has(old) || s.evaluated.has(old)) return;
              s.evaluated.add(old);
              emit(s.id, 'PREDICTION_EVALUATED', { ...base, prediction_id: old, happened: 'verdict=superseded', payload: { verdict: 'unknown', superseded_by: pid, residual: 'superseded by newer prediction' } });
            };
            for (const old of (Array.isArray(input.supersedes) ? input.supersedes : [])) closeSuperseded(String(old));
            const ia = String(input.intended_action || '');
            const subj = String(input.subject || '');
            for (const [old, p] of s.predictions) {
              if (s.evaluated.has(old)) continue;
              const same = (ia && String(p.intended_action || '') === ia) || (!ia && subj && String(p.subject || '') === subj);
              if (same) closeSuperseded(old);
            }
            const rec = {
              prediction_id: pid, subject: input.subject, intended_action: input.intended_action,
              expected_observation: input.expected_observation, falsifier: input.falsifier,
              time_horizon: input.time_horizon, confidence_bucket: input.confidence_bucket,
              irreversible: input.irreversible === true, model_id: input.model_id,
              uncertainty_type: input.uncertainty_type
            };
            s.predictions.set(pid, rec);
            emit(s.id, 'PREDICTION_CREATED', { ...base, prediction_id: pid, model_id: input.model_id, happened: `prediction ${pid}`, payload: rec });
            return { ok: true, prediction_id: pid };
          }
          case 'observe': {
            emit(s.id, 'OBSERVATION_RECORDED', { ...base, prediction_id: input.prediction_id, happened: 'observation linked', payload: { observation: input.observation, source: input.source }, source_id: input.source_id, channel_id: input.channel_id, sensor_id: input.sensor_id });
            return { ok: true };
          }
          case 'evaluate': {
            const pred = s.predictions.get(input.prediction_id);
            if (input.prediction_id) s.evaluated.add(input.prediction_id);
            emit(s.id, 'PREDICTION_EVALUATED', { ...base, prediction_id: input.prediction_id, happened: `verdict=${input.verdict}`, payload: { verdict: input.verdict, observation_refs: input.observation_refs, residual: input.reason, evaluation_source: input.evaluation_source || 'self' } });
            return { ok: true, prior: pred ? 'bound' : 'unbound', verdict: input.verdict };
          }
          case 'update': {
            emit(s.id, 'MODEL_UPDATED', { ...base, model_id: input.model_id, prediction_id: input.prediction_id, happened: `revision=${input.revision_type}`, payload: { revision_type: input.revision_type, change: input.change, reason: input.reason, supersedes: input.supersedes, update_class: input.update_class || 'world_model' } });
            const r = writeProposal('model-update', { model_id: input.model_id, revision_type: input.revision_type, change: input.change, reason: input.reason, update_class: input.update_class || 'world_model' }, s.id, s);
            if (r.denied) return { ok: false, code: 'LEASE_DENIED', holder: r.holder };
            return { ok: true, canonical_proposal: r.path };
          }
          case 'probe': {
            emit(s.id, 'PROBE_PLANNED', { ...base, happened: `probe target=${input.target}`, payload: { target: input.target, probe_mode: input.probe_mode, level: input.level, expected_gain: input.expected_gain, cost: input.cost, rejected_alternatives: input.rejected_alternatives } });
            return { ok: true };
          }
          case 'meta': {
            emit(s.id, 'META_DECISION', { ...base, happened: `decision=${input.decision}`, payload: { decision: input.decision, bottleneck: input.bottleneck, rationale: input.reason } });
            return { ok: true };
          }
          case 'value': {
            emit(s.id, 'VALUE_DECISION', { ...base, happened: 'value/decision recorded', payload: { goal: input.goal, decision_criteria: input.decision_criteria, proxy_risk: input.proxy_risk } });
            let proposal;
            if (input.value_update) {
              const r = writeProposal('value-update', { ...input.value_update, status: 'proposed', update_class: 'value_model' }, s.id, s);
              if (r.denied) return { ok: false, code: 'LEASE_DENIED', holder: r.holder };
              proposal = r.path;
            }
            return proposal ? { ok: true, value_proposal: proposal } : { ok: true };
          }
          case 'input': {
            // INPUT_SEMANTICS routing (V0.3.1 §6)：输入先分类，不直接全当 evidence
            const st = String(input.semantic_type || '');
            if (!SEMANTIC_TYPES.has(st)) return { ok: false, code: 'BAD_SEMANTIC_TYPE', allowed: [...SEMANTIC_TYPES] };
            const src = String(input.source_id || 'user');
            const scope = String(input.scope || 'task_goal');
            const content = String(input.content || '');
            if (st === 'EPISTEMIC_CLAIM') {
              emit(s.id, 'INPUT_ROUTED', { ...base, happened: `EPISTEMIC_CLAIM from ${src}`, payload: { semantic_type: st, source_id: src, content: content.slice(0, 500), routed_to: 'world_model_evidence', disagreement_allowed: true }, source_id: src });
              return { ok: true, routed: 'world_model_evidence', note: 'epistemic claim recorded as evidence; principled disagreement permitted if direct evidence is stronger — record via evaluate/update' };
            }
            if (st === 'NORMATIVE_DIRECTIVE') {
              const rs = runtimeState();
              const auth = (((rs.normative_authorities || {})[src] || {}).scopes || {})[scope];
              if (auth === 'authoritative' || auth === 'authoritative-via-value-proposal' || (src === 'user' && !Object.keys(rs.normative_authorities || {}).length)) {
                emit(s.id, 'CONSTRAINT_ACCEPTED', { ...base, happened: `normative directive accepted scope=${scope}`, payload: { source_id: src, scope, authority: auth || 'default-user-root', content: content.slice(0, 500) }, source_id: src });
                return { ok: true, routed: 'constraint', scope, authority: auth || 'default-user-root' };
              }
              emit(s.id, 'NORMATIVE_DENIED', { ...base, happened: `normative directive denied: ${src} lacks authority on ${scope}`, payload: { source_id: src, scope, authority: auth || 'none', content: content.slice(0, 300) }, source_id: src });
              return { ok: false, code: 'NORMATIVE_DENIED', scope, authority: auth || 'none' };
            }
            if (st === 'AUTHORIZATION') {
              emit(s.id, 'AUTHORIZATION_RECORDED', { ...base, happened: `authorization ${src} → ${input.action_ref || scope}`, payload: { source_id: src, scope, action_ref: input.action_ref, content: content.slice(0, 300) }, source_id: src });
              return { ok: true, routed: 'session_authorization', note: 'action-scoped permission; NOT a durable value' };
            }
            if (st === 'DURABLE_VALUE_STATEMENT') {
              const r = writeProposal('value-update', { durable_value: content, source_id: src, authorization_ref: input.action_ref || `input:${s.id}`, update_class: 'value_model', status: 'proposed' }, s.id, s);
              if (r.denied) return { ok: false, code: 'LEASE_DENIED', holder: r.holder };
              emit(s.id, 'INPUT_ROUTED', { ...base, happened: `DURABLE_VALUE_STATEMENT → value proposal`, payload: { source_id: src, proposal: r.path }, source_id: src });
              return { ok: true, routed: 'value_update_proposal', proposal: r.path };
            }
            emit(s.id, 'PREFERENCE_RECORDED', { ...base, happened: `preference (local/non-durable)`, payload: { source_id: src, scope, content: content.slice(0, 300) }, source_id: src });
            return { ok: true, routed: 'local_context', note: 'PREFERENCE affects current context only; not persisted as durable value' };
          }
          case 'declassify': {
            // Declassification proposal (§8)：只产出提案，不改原件等级
            if (!input.declassify_target) return { ok: false, code: 'NEED_TARGET' };
            const r = writeProposal('declassification', {
              target: input.declassify_target, destination: input.destination,
              redaction_manifest: input.redaction_manifest || [], update_class: 'declassification',
              flow: 'PRIVATE artifact → sanitizer/redactor → leakage check → authority review → derivative; original keeps its level', status: 'proposed'
            }, s.id, s);
            if (r.denied) return { ok: false, code: 'LEASE_DENIED', holder: r.holder };
            emit(s.id, 'DECLASSIFICATION_PROPOSED', { ...base, happened: `declassify ${input.declassify_target}`, payload: { proposal: r.path, destination: input.destination } });
            return { ok: true, proposal: r.path };
          }
          case 'persist': {
            emit(s.id, 'STATE_PERSISTED', { ...base, happened: 'state persisted', payload: { summary: input.summary, open_loops: input.open_loops } });
            const cur = join(wmDir, 'current.json');
            let prior = {};
            try { if (existsSync(cur)) prior = JSON.parse(readFileSync(cur, 'utf8')); } catch { /* ignore */ }
            const merged = new Set(Array.isArray(prior.open_predictions) ? prior.open_predictions : []);
            for (const [pid] of s.predictions) { if (!s.evaluated.has(pid)) merged.add(pid); }
            for (const pid of s.evaluated) merged.delete(pid);
            const patch = { open_predictions: [...merged], last_summary: input.summary || prior.last_summary };
            if (input.open_loops !== undefined) patch.open_loops = input.open_loops;
            const st = updateCurrentJson(patch);
            let proposal;
            if (input.canonical_proposal) {
              const r = writeProposal('canonical', input.canonical_proposal, s.id, s);
              if (r.denied) return { ok: false, code: 'LEASE_DENIED', holder: r.holder, state: st };
              proposal = r.path;
            }
            return proposal ? { ok: true, state: st, canonical_proposal: proposal } : { ok: true, state: st };
          }
          case 'status': {
            const cur = join(wmDir, 'current.json');
            const st = existsSync(cur) ? JSON.parse(readFileSync(cur, 'utf8')) : {};
            const rs = runtimeState();
            return { ok: true, mode: s.mode, session_predictions: [...s.predictions.keys()], current: st, identity: rs.identity || null, lineage_head: rs.lineage_head || null, epoch: rs.continuity_epoch || null, body_id: bodyId, bcc: BCC_VERSION };
          }
          default:
            return { ok: false, code: 'UNKNOWN_OP', message: `unknown op ${op}` };
        }
      }
    });
  }

  // ---- hard gate: consequential mutation requires bound prediction (CORE/FULL) ----
  try {
    ctx.tools.guard((execution) => {
      try {
        const toolName = execution?.name;
        if (!toolName || !CONSEQUENT_TOOLS.has(toolName)) return undefined;
        const s = sessionFor(execution);
        if (s.mode !== 'core' && s.mode !== 'full') return undefined;
        const argsText = safeJson(execution?.arguments ?? execution?.args ?? {});
        const irreversible = IRREVERSIBLE_RE.test(argsText);
        for (const [pid, p] of s.predictions) {
          if (s.evaluated.has(pid)) continue;   // superseded/evaluated prediction cannot authorize
          const ia = String(p.intended_action || '');
          if (!ia) continue;
          const bound = ia.includes(toolName) || /mutation|edit|write|exec|modify|change/i.test(ia);
          if (!bound) continue;
          if (irreversible && p.irreversible !== true) continue;
          return undefined; // bound prediction exists → allow
        }
        emit(s.id, 'GUARD_BLOCKED', { subject: toolName, happened: `blocked ${toolName} (no bound prediction)`, payload: { irreversible } });
        return `[dsh-world-model] BLOCKED: ${toolName} is a consequential action in ${s.mode} mode. First call world_model(op:"predict") with intended_action naming this tool/action${irreversible ? ' and irreversible:true (irreversible pattern detected)' : ''}.`;
      } catch { return undefined; }
    });
  } catch { /* guard optional */ }
}
