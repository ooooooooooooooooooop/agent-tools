// User-level Cordis plugin: workflow-model-preflight-gate (v2)
//
// Enforces model admission on the `workflow` tool BEFORE any child agent is
// spawned, and (v2) FAILS CLOSED on workflow scripts that carry NO explicit
// provider/model literal while the active preset DECLARES a child route.
//
// Proven incident (session-869904c0, 2026-08-31):
//   - cc preset declares child route ONLY via tool-subagent config
//     (agentOptions: cpa/gpt-5.6-luna-max); the workflow engine has NO
//     equivalent injection (dsh-workflow-worker-thread startChild() only
//     forwards script literals).
//   - One-shot workflow workers without literals resolved their route from
//     the parent session's re-seeded options (= settings agent-default-model
//     at last create/resume), silently executing cpa/gemini-3.7-flash-high
//     (07:59-08:34) and kimi-coding/k3-256k (10:39) instead of the preset's
//     cpa/gpt-5.6-luna-max 鈥?MODEL_CONDITION_DRIFT, CONFIGURED != EXECUTED.
//   - agent(..., {model:'gpt-5.6-luna'}) was forwarded verbatim; unadmitted
//     alias failed per-child with UNKNOWN_MODEL (pi-ai) after dispatch.
//
// v1 mechanism (unchanged): ctx.tools.guard() 鈥?monotonic guard stage before
// dispatch; a returned string denies execution with the reason fed back to
// the calling model (self-healing retry loop). Live-verified in this deploy.
//
// v2 additions:
//   5. script has NO model literal AND the active preset declares a child
//      route (config.presetRoutes) -> deny REQUIRED_EXPLICIT_MODEL with the
//      preset route spelled out: the engine cannot inject preset agentOptions
//      into one-shot workers, so the caller MUST declare provider/model in
//      agent() opts. No silent fallback to inheritance/settings default.
//   6. every decision is appended to the audit file, including 'allow' rows,
//      and post-run provenance is appended separately by the CLI verifier
//      (scripts/dsh-model-provenance.mjs) from real request headers.
//
// Policy (fail-closed), v2:
//   1. model admitted for its provider in ~/.dsh/settings.yaml  -> pass
//   2. not admitted, explicit fallback rule exists and target admitted
//      -> deny with guided resolution (caller rewrites the literal)
//   3. not admitted and no rule                                -> deny,
//      UNADMITTED_OR_UNKNOWN_MODEL (fail closed)
//   4. no model literals, but preset declares a child route that the
//      workflow path cannot receive                          -> deny,
//      REQUIRED_EXPLICIT_MODEL (fail closed; the drift channel)
//   5. no model literals and no preset-declared route (inheritance is the
//      declared semantic) or gate-internal error             -> pass
//      (gate never invents denials for things it cannot see)

import { readFileSync, appendFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { homedir } from 'node:os';

export const name = 'workflow-model-preflight-gate';
export const inject = ['tools'];

const DEFAULT_SETTINGS = join(homedir(), '.dsh', 'settings.yaml');

// Explicit fallback policy (mirror of skills/subagent-execution-governance/
// scripts/workflow_preflight_router.py EXPLICIT_FALLBACK_RULES; keep in sync).
// Silent/implicit replacement is forbidden: every mapping is declared here.
const DEFAULT_FALLBACKS = {
  'gpt-5.6-luna': {
    fallback_model: 'gpt-5.6-luna-max',
    fallback_provider: 'cpa',
    reason_code: 'GATEWAY_ADMITTED_ALIAS_UPGRADE',
    mapping_type: 'EXPLICIT_COMPATIBILITY_MAPPING',
    quality_tier_impact: 'UNKNOWN',
  },
  'gpt-5.6-sol': {
    fallback_model: 'gpt-5.6-sol-xhigh',
    fallback_provider: 'cpa',
    reason_code: 'GATEWAY_ADMITTED_ALIAS_UPGRADE',
    mapping_type: 'EXPLICIT_COMPATIBILITY_MAPPING',
    quality_tier_impact: 'UNKNOWN',
  },
};

// Preset-declared child routes. SSOT: the active preset's tool-subagent
// agentOptions (see cc/agent.cordis.yml). Kept here as a mirror because the
// guard stage cannot read other plugins' tool configs portably; keep in sync
// with every preset that declares a child route.
const DEFAULT_PRESET_ROUTES = {
  cc: { provider: 'cpa', model: 'gpt-5.6-luna-max' },
};

// Extract { provider: [modelIds] } from the llm-pi-ai providers section of
// settings.yaml by indentation scanning (no yaml dependency in profile env).
function loadAdmittedCatalog(settingsPath) {
  const text = readFileSync(settingsPath, 'utf8');
  const lines = text.split(/\r?\n/);
  const catalog = {};
  let inProviders = false, currentProvider = null, inModels = false;
  for (const line of lines) {
    if (/^\S/.test(line)) { inProviders = false; currentProvider = null; inModels = false; }
    if (/^ {2}providers:\s*$/.test(line)) { inProviders = true; continue; }
    if (!inProviders) continue;
    const pm = line.match(/^ {4}([A-Za-z0-9._-]+):\s*$/);
    if (pm) { currentProvider = pm[1]; inModels = false; catalog[currentProvider] ||= []; continue; }
    if (!currentProvider) continue;
    if (/^ {6}models:\s*$/.test(line)) { inModels = true; continue; }
    if (inModels) {
      const im = line.match(/^ {8}- id:\s*(\S+)\s*$/);
      if (im) { catalog[currentProvider].push(im[1]); continue; }
      if (/^ {6}\S/.test(line)) inModels = false; // left the models list
    }
  }
  return catalog;
}

// Collect { model, provider? } literals from script text + meta.phases.
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
  // model-before-provider order inside the same object literal
  const re2 = /model\s*:\s*['"]([^'"]+)['"]([^}]{0,120}?)provider\s*:\s*['"]([^'"]+)['"]/g;
  while ((m = re2.exec(script)) !== null) push(m[1], m[3], 'script');
  for (const ph of args?.meta?.phases || []) {
    if (ph && typeof ph.model === 'string') push(ph.model, typeof ph.provider === 'string' ? ph.provider : null, 'meta.phases');
  }
  return specs;
}

// Resolve the ACTIVE preset name for the calling agent, best-effort:
// composition service first, then execution payload, then session header.
function activePreset(execution) {
  try {
    const agent = execution?.agent;
    const composed = agent?.ctx?.get?.('agentPresets')?.composedPreset?.(agent.ctx);
    if (typeof composed === 'string' && composed.length > 0) return composed;
  } catch { /* ignore */ }
  if (execution?.agentPreset) return execution.agentPreset;
  try { return execution?.agent?.session?.header?.agentPreset; } catch { return null; }
  return null;
}

export function apply(ctx, config = {}) {
  const settingsPath = config.settingsPath || DEFAULT_SETTINGS;
  const auditPath = config.auditPath || join(homedir(), '.dsh', 'workflow-preflight-audit.jsonl');
  const fallbacks = config.fallbacks || DEFAULT_FALLBACKS;
  const presetRoutes = config.presetRoutes || DEFAULT_PRESET_ROUTES;

  const audit = (record) => {
    try {
      mkdirSync(dirname(auditPath), { recursive: true });
      appendFileSync(auditPath, JSON.stringify({ time: new Date().toISOString(), ...record }) + '\n', 'utf8');
    } catch (error) {
      ctx.logger.warn(`workflow-model-preflight-gate: audit write failed: ${String(error)}`);
    }
  };

  ctx.tools.guard((execution) => {
    if (execution?.name !== 'workflow') return undefined;
    try {
      const args = execution?.arguments ?? execution?.args ?? {};
      const specs = extractModelSpecs(args);

      // v2: the silent-drift channel 鈥?a preset declares a child route but the
      // workflow path cannot receive it, so an optionless script would fall to
      // inheritance/settings default. Fail closed with explicit guidance.
      if (specs.length === 0) {
        const preset = activePreset(execution);
        const declared = presetRoutes?.[preset];
        const parentProvider = execution?.agent?.options?.provider || null;
        if (declared) {
          const reason = `[workflow-model-preflight-gate] FAIL CLOSED (REQUIRED_EXPLICIT_MODEL): the active preset "${preset}" declares child route provider="${declared.provider}" model="${declared.model}" (its tool-subagent agentOptions), but the workflow engine does not inject preset agentOptions into one-shot workers (dsh-workflow-worker-thread only forwards script literals). An optionless agent() would silently fall back to the parent/settings default instead of the declared route (MODEL_CONDITION_DRIFT). Rewrite the workflow script so EVERY agent() call carries provider="${declared.provider}", model="${declared.model}" and re-issue; no child agent was started.`;
          audit({ decision: 'block', kind: 'required_explicit_model', preset, requested_model: null, provider: declared.provider, resolved_model: declared.model, parent_provider: parentProvider, reason_code: 'REQUIRED_EXPLICIT_MODEL', policy_source: 'workflow-model-preflight-gate.presetRoutes mirror of preset tool-subagent agentOptions', source: 'preset' });
          return reason;
        }
        audit({ decision: 'allow', kind: 'inheritance', preset: preset || null, specs: [], note: 'no preset-declared route; inheritance semantics preserved' });
        return undefined;
      }

      const catalog = loadAdmittedCatalog(settingsPath);
      const parentProvider = execution?.agent?.options?.provider || null;

      for (const spec of specs) {
        const providersToCheck = spec.provider ? [spec.provider] : [parentProvider, ...Object.keys(catalog)].filter(Boolean);
        const admittedIn = providersToCheck.find((p) => (catalog[p] || []).includes(spec.model));
        if (admittedIn) continue;

        const rule = fallbacks[spec.model];
        if (rule && (catalog[rule.fallback_provider] || []).includes(rule.fallback_model)) {
          const reason = `[workflow-model-preflight-gate] model "${spec.model}" is not admitted in DSH settings for provider "${spec.provider || parentProvider || '(inherited)'}". `
            + `Explicit policy ${rule.reason_code}: use provider="${rule.fallback_provider}" model="${rule.fallback_model}" instead. `
            + `Rewrite the workflow script with the resolved model and re-issue the call; no child agent was started.`;
          audit({ decision: 'block', kind: 'fallback_guided', requested_model: spec.model, provider: spec.provider || parentProvider, resolved_model: rule.fallback_model, resolved_provider: rule.fallback_provider, reason_code: rule.reason_code, mapping_type: rule.mapping_type || 'EXPLICIT_COMPATIBILITY_MAPPING', quality_tier_impact: rule.quality_tier_impact || 'UNKNOWN', policy_source: 'workflow-model-preflight-gate.fallbacks (mirror of workflow_preflight_router.EXPLICIT_FALLBACK_RULES)', source: spec.source });
          return reason;
        }

        const reason = `[workflow-model-preflight-gate] UNADMITTED_OR_UNKNOWN_MODEL: model "${spec.model}" is not declared in DSH settings for provider "${spec.provider || parentProvider || '(inherited)'}" and no explicit fallback policy permits its conversion. `
          + `Fail closed: no child agent was started. Choose an admitted model or declare an explicit fallback rule.`;
        audit({ decision: 'block', kind: 'fail_closed', requested_model: spec.model, provider: spec.provider || parentProvider, reason_code: 'UNADMITTED_OR_UNKNOWN_MODEL', policy_source: 'none', source: spec.source });
        return reason;
      }

      audit({ decision: 'allow', specs: specs.map((s) => `${s.provider || parentProvider || '(inherited)'}/${s.model}`) });
      return undefined;
    } catch (error) {
      // Gate-internal failure must never break unrelated workflows.
      ctx.logger.warn(`workflow-model-preflight-gate: internal error, allowing: ${String(error)}`);
      return undefined;
    }
  });
}
