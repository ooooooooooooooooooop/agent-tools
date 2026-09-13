// dsh-world-model：世界模型机制层插件（Personal AI cognition runtime）
//
// 机制（非提示词）：
//   - world_model 工具：predict/model/observe/evaluate/update/probe/meta/value/persist → 真实落盘
//   - tools/result → RAW_EVIDENCE 机械捕获（不升级 Observation）
//   - tools.guard → CORE/FULL 模式：consequential mutation 无绑定预测不放行；
//     irreversible 探测必须有 irreversible:true 预测
//   - session/event + agent/pre-step → STATE_RESTORE 机械注入 + kernel 提醒
//   - L1 ledger/runs 随时 append；L2 canonical 只写 proposals/（不自动改写）
//
// 用法：把本文件放入 <profile>/plugins/，cordis.patch.yml 加入条目（见 cordis.patch.yml）。
// 模式：config.mode 或 env DSH_WM_MODE = off|core|full（默认 off=零门禁，仅 ledger）。

import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { homedir } from 'node:os';
import { randomUUID } from 'node:crypto';

export const name = 'dsh-world-model';
export const inject = ['tools'];

const SCHEMA_VERSION = '1.0';
const CONSEQUENT_TOOLS = new Set(['edit', 'write', 'str-replace-editor', 'notebook_edit', 'exec', 'mcp_call_tool']);
const IRREVERSIBLE_RE = /rm\s+-rf|del\s+\/[sq]|rmdir|Remove-Item[^\n]*-Recurse|drop\s+table|drop\s+database|truncate|git\s+push[^\n]*(--force|-f\b)|git\s+reset[^\n]*--hard/i;

function today() { return new Date().toISOString().slice(0, 10); }
function safeJson(v) { try { return JSON.stringify(v); } catch { return '"<unserializable>"'; } }

const WM_PARAMETERS = {
  type: 'object',
  properties: {
    op: { type: 'string', enum: ['activate', 'model', 'predict', 'observe', 'evaluate', 'update', 'probe', 'meta', 'value', 'persist', 'status'] },
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
    observation_refs: { type: 'array', items: { type: 'string' } },
    model_id: { type: 'string' },
    revision_type: { type: 'string', enum: ['param', 'structure', 'h4'] },
    change: { type: 'string' },
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
  const envMode = String(config.mode || process.env.DSH_WM_MODE || 'off').toLowerCase();
  for (const d of [join(wmDir, 'ledger'), join(wmDir, 'runs'), join(canonicalDir, 'proposals')]) {
    try { mkdirSync(d, { recursive: true }); } catch { /* ignore */ }
  }

  const sessions = new Map();
  function sessionFor(exec) {
    const sid = String(exec?.agent?.session?.id || exec?.session?.id || 'unknown');
    if (!sessions.has(sid)) sessions.set(sid, { id: sid, mode: envMode, predictions: new Map(), evaluated: new Set(), activated: envMode !== 'off' });
    return sessions.get(sid);
  }

  function emit(sessionId, eventType, data = {}) {
    const ev = {
      event_id: randomUUID(), schema_version: SCHEMA_VERSION, session_id: sessionId,
      event_type: eventType, timestamp: new Date().toISOString(), actor: 'agent', ...data
    };
    try {
      appendFileSync(join(wmDir, 'ledger', `${today()}.jsonl`), safeJson(ev) + '\n');
      appendFileSync(join(wmDir, 'runs', `${sessionId}.jsonl`), safeJson(ev) + '\n');
    } catch { /* ledger write must never crash the loop */ }
    return ev;
  }

  function writeProposal(kind, payload, sessionId) {
    const p = join(canonicalDir, 'proposals', `${today()}-${kind}-${randomUUID().slice(0, 8)}.json`);
    try { writeFileSync(p, safeJson({ schema_version: SCHEMA_VERSION, kind, session_id: sessionId, timestamp: new Date().toISOString(), payload, status: 'proposed' })); } catch { /* ignore */ }
    return p;
  }

  function readCanonicalSummary() {
    const cur = join(canonicalDir, 'current.yaml');
    if (!existsSync(cur)) return '(no canonical world-model yet — first activation)';
    try {
      const text = readFileSync(cur, 'utf8');
      return text.length > 1500 ? text.slice(0, 1500) + '\n…(truncated)' : text;
    } catch { return '(canonical unreadable)'; }
  }

  function updateCurrentJson(patch) {
    const cur = join(wmDir, 'current.json');
    let state = { schema_version: SCHEMA_VERSION, updated_at: null, open_predictions: [], models: {}, open_loops: [] };
    try { if (existsSync(cur)) state = { ...state, ...JSON.parse(readFileSync(cur, 'utf8')) }; } catch { /* ignore */ }
    Object.assign(state, patch, { updated_at: new Date().toISOString() });
    try { writeFileSync(cur, safeJson(state)); } catch { /* ignore */ }
    return state;
  }

  // ---- mechanical RAW_EVIDENCE capture: every tool result, unfiltered ----
  try {
    ctx.on('tools/result', (exec, result) => {
      try {
        const s = sessionFor(exec);
        const text = typeof result === 'string' ? result : safeJson(result);
        emit(s.id, 'RAW_EVIDENCE', {
          subject: exec?.name || 'unknown-tool', happened: `tool ${exec?.name || '?'} returned`,
          payload: String(text).slice(0, 500), evidence_refs: [], source: 'tools/result'
        });
      } catch { /* never crash */ }
    });
  } catch { /* event bus optional */ }

  // ---- STATE_RESTORE on first session event ----
  try {
    let restored = false;
    ctx.on('session/event', (session, event) => {
      try {
        if (restored) return;
        const sid = String(session?.id || 'unknown');
        const s = sessionFor({ agent: { session: { id: sid } } });
        if (event?.type === 'turn/start' || event?.type === 'step/start' || event?.type === 'user/message') {
          restored = true;
          emit(s.id, 'STATE_RESTORE', {
            happened: 'world-model state restored into session',
            payload: readCanonicalSummary(), source: 'plugin'
          });
        }
      } catch { /* never crash */ }
    });
  } catch { /* event bus optional */ }

  // ---- world_model tool ----
  // strict-off：实验对照模式——工具完全不注册，模型无法自愿调用（区别于 off-natural 的生产语义）
  const reg = ctx?.tools?.register;
  if (typeof reg === 'function' && envMode !== 'strict-off') {
    reg.call(ctx.tools, {
      name: 'world_model',
      description: '世界模型状态机：activate/model/predict/observe/evaluate/update/probe/meta/value/persist/status。所有调用真实落盘到 ~/.dsh/world-model/ledger——这是审计对象不是 prose。consequential 改动前必须先 predict（绑定 intended_action）。',
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
            emit(s.id, 'WM_ACTIVATE', { ...base, happened: `world-model activated mode=${s.mode}`, payload: { mode: s.mode, reason: input.reason } });
            return { ok: true, mode: s.mode, note: 'WM active. Before consequential mutations: world_model(op:"predict") with intended_action binding.' };
          }
          case 'model': {
            const ids = (input.models || []).map(m => m.id || m.model_id || m.proposition || 'unnamed');
            emit(s.id, 'MODEL_CREATED', { ...base, happened: `${ids.length} model(s) registered`, payload: { models: input.models } });
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
            // 生命周期收敛：显式 supersedes + 同 intended_action/同 subject 的悬挂旧预测自动 supersede
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
            emit(s.id, 'OBSERVATION_RECORDED', { ...base, prediction_id: input.prediction_id, happened: 'observation linked', payload: { observation: input.observation, source: input.source } });
            return { ok: true };
          }
          case 'evaluate': {
            const pred = s.predictions.get(input.prediction_id);
            if (input.prediction_id) s.evaluated.add(input.prediction_id);
            emit(s.id, 'PREDICTION_EVALUATED', { ...base, prediction_id: input.prediction_id, happened: `verdict=${input.verdict}`, payload: { verdict: input.verdict, observation_refs: input.observation_refs, residual: input.reason } });
            return { ok: true, prior: pred ? 'bound' : 'unbound', verdict: input.verdict };
          }
          case 'update': {
            emit(s.id, 'MODEL_UPDATED', { ...base, model_id: input.model_id, prediction_id: input.prediction_id, happened: `revision=${input.revision_type}`, payload: { revision_type: input.revision_type, change: input.change, reason: input.reason, supersedes: input.supersedes } });
            const p = writeProposal('model-update', { model_id: input.model_id, revision_type: input.revision_type, change: input.change, reason: input.reason }, s.id);
            return { ok: true, canonical_proposal: p };
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
            if (input.value_update) proposal = writeProposal('value-update', { ...input.value_update, status: 'proposed' }, s.id);
            return proposal ? { ok: true, value_proposal: proposal } : { ok: true };
          }
          case 'persist': {
            emit(s.id, 'STATE_PERSISTED', { ...base, happened: 'state persisted', payload: { summary: input.summary, open_loops: input.open_loops } });
            const cur = join(wmDir, 'current.json');
            let prior = {};
            try { if (existsSync(cur)) prior = JSON.parse(readFileSync(cur, 'utf8')); } catch { /* ignore */ }
            // 合并而非覆盖：跨会话遗留 open 保留，本会话未评估的并入，已评估的移除
            const merged = new Set(Array.isArray(prior.open_predictions) ? prior.open_predictions : []);
            for (const [pid] of s.predictions) { if (!s.evaluated.has(pid)) merged.add(pid); }
            for (const pid of s.evaluated) merged.delete(pid);
            const patch = { open_predictions: [...merged], last_summary: input.summary || prior.last_summary };
            if (input.open_loops !== undefined) patch.open_loops = input.open_loops;
            const st = updateCurrentJson(patch);
            let proposal;
            if (input.canonical_proposal) proposal = writeProposal('canonical', input.canonical_proposal, s.id);
            return proposal ? { ok: true, state: st, canonical_proposal: proposal } : { ok: true, state: st };
          }
          case 'status': {
            const cur = join(wmDir, 'current.json');
            const st = existsSync(cur) ? JSON.parse(readFileSync(cur, 'utf8')) : {};
            return { ok: true, mode: s.mode, session_predictions: [...s.predictions.keys()], current: st };
          }
          default:
            return { ok: false, code: 'UNKNOWN_OP', message: `unknown op ${op}` };
        }
      }
    });
  }

  // ---- hard gate: consequential mutation requires bound prediction ----
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
