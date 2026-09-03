// User-level Cordis plugin: workflow-model-preflight-gate (v2)
//
// Enforces model admission on the `workflow` tool BEFORE any child agent is
// spawned, and (v2) FAILS CLOSED on workflow scripts that carry NO explicit
// provider/model literal while the active preset DECLARES a child route.

import { readFileSync, appendFileSync, mkdirSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { homedir } from 'node:os';

export const name = 'workflow-model-preflight-gate';
export const inject = ['tools'];

const DEFAULT_SETTINGS = join(homedir(), '.dsh', 'settings.yaml');

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

const DEFAULT_PRESET_ROUTES = {
  cc: { provider: 'cpa', model: 'gpt-5.6-luna-max' },
};

function loadAdmittedCatalog(settingsPath) {
  const text = readFileSync(settingsPath, 'utf8');
  const lines = text.split(/\r?\n/);
  const catalog = {};
  let inProviders = false, currentProvider = null, inModels = false;
  for (const line of lines) {
    if (/^\S/.test(line)) { inProviders = false; currentProvider = null; inModels = false; }
    if (/^\s{2}providers:\s*$/.test(line)) { inProviders = true; continue; }
    if (!inProviders) continue;
    const pm = line.match(/^\s{4}([A-Za-z0-9._-]+):\s*$/);
    if (pm) { currentProvider = pm[1]; inModels = false; catalog[currentProvider] ||= []; continue; }
    if (!currentProvider) continue;
    if (/^\s{6}models:\s*$/.test(line)) { inModels = true; continue; }
    if (inModels) {
      if (/^\s{4}[A-Za-z0-9._-]+:\s*$/.test(line) || /^\s{0,4}\S/.test(line)) {
        inModels = false;
        continue;
      }
      const im = line.match(/^\s*(?:-\s+)?id:\s*['"]?(\S+?)['"]?\s*$/);
      if (im) {
        catalog[currentProvider].push(im[1]);
      }
    }
  }
  return catalog;
}

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
  const re2 = /model\s*:\s*['"]([^'"]+)['"]([^}]{0,120}?)provider\s*:\s*['"]([^'"]+)['"]/g;
  while ((m = re2.exec(script)) !== null) push(m[1], m[3], 'script');
  for (const ph of args?.meta?.phases || []) {
    if (ph && typeof ph.model === 'string') push(ph.model, typeof ph.provider === 'string' ? ph.provider : null, 'meta.phases');
  }
  return specs;
}

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

      if (specs.length === 0) {
        const preset = activePreset(execution);
        let declared = presetRoutes?.[preset];
        const prefFile = join(homedir(), '.dsh', 'subtask-model-profile.json');
        if (existsSync(prefFile)) {
          try {
            const prefData = JSON.parse(readFileSync(prefFile, 'utf8'));
            if (prefData.provider && prefData.model) {
              declared = { provider: prefData.provider, model: prefData.model };
            }
          } catch {}
        }
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
      ctx.logger.warn(`workflow-model-preflight-gate: internal error, allowing: ${String(error)}`);
      return undefined;
    }
  });
}
