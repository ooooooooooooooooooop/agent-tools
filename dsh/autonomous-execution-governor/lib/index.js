// User-level Cordis plugin: autonomous-execution-governor (v2 unified)
//
// DSH 侧的 AUTONOMOUS_EXECUTION_GOVERNANCE 运行时 adapter。
// 消费 AIC 生成的预算投影（~/.dsh/governance/execution-profiles.generated.json）。
//
// 机制：
//   - ctx.tools.guard() 单调守卫层：拦截无界自主执行、超限预算、死循环熔断（Hard gate，Fail closed）。
//   - tools/post-execute 观测层：注入子代理准备/执行平衡与看门狗模式建议（Advisory reminder，Fail open）。

import { readFileSync, appendFileSync, mkdirSync, writeFileSync, unlinkSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { homedir } from 'node:os';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';

export const name = 'autonomous-execution-governor';
export const inject = ['tools'];

const DSH_HOME = join(homedir(), '.dsh');
const DEFAULT_PROFILES = join(DSH_HOME, 'governance', 'execution-profiles.generated.json');
const DEFAULT_STATE_DIR = join(DSH_HOME, 'governance', 'state');
const DEFAULT_AUDIT = join(DSH_HOME, 'governance', 'governor-audit.jsonl');

const MUTATING_TOOLS = new Set(['edit', 'write', 'str-replace-editor', 'todo_write']);
const EXEC_KEYWORDS = /^(?:\[EXEC\]|implement|build|run|repair|fix|generate|execute|apply|produce|create)/i;
const WARN_PREP = 6;
const WARN_INTERRUPTS = 3;

export function sha1hex(s) {
  return createHash('sha1').update(String(s)).digest('hex');
}

export function actionKey(toolName, args) {
  return `${toolName}:${sha1hex(JSON.stringify(args ?? {})).slice(0, 16)}`;
}

export function emptyState({ taskId, projectId, profile, harness, sessionId }) {
  return {
    schema: 1, taskId, projectId, profile, harness, sessionId,
    startTs: new Date().toISOString(),
    actions: 0, providerCalls: 0, ring: [], ringWindow: 6,
    consecutiveNoProgress: 0, lastProgressAt: null, circuitBroken: false,
    lastCheckpointAtActions: 0, repeatedKeys: {},
    consecutivePrep: 0, interrupts: 0,
  };
}

export function evaluateGuards(state, evt, profile) {
  const violations = [];
  const next = { ...state, ring: [...state.ring], repeatedKeys: { ...state.repeatedKeys } };

  // 1) circuit breaker（硬）——先于一切
  if (next.circuitBroken) {
    return { violations: ['circuit_broken'], stop: { kind: 'session', hit: 'loop_breaker' }, nextState: next };
  }

  // 2) runtime budget
  const nowMs = Date.now();
  const startMs = Date.parse(next.startTs) || nowMs;
  const runtimeMin = (nowMs - startMs) / 60000;
  const runtimeCap = profile.runtime_min;
  if (Number.isFinite(runtimeCap) && runtimeCap > 0 && runtimeMin >= runtimeCap) {
    return { violations: ['runtime_exceeded'], stop: { kind: 'task', hit: 'runtime_min' }, nextState: next };
  }

  // 3) counters
  next.actions += 1;
  next.providerCalls += 1;

  // 4) turn budget
  const turnCap = profile.agent_turns;
  if (Number.isFinite(turnCap) && turnCap > 0 && next.actions >= turnCap) {
    violations.push(`agent_turns_budget`);
    return { violations, stop: { kind: 'session', hit: 'agent_turns' }, nextState: next };
  }

  // 5) provider call budget
  const callCap = profile.provider_calls;
  if (Number.isFinite(callCap) && callCap > 0 && next.providerCalls >= callCap) {
    violations.push('provider_calls_budget');
    return { violations, stop: { kind: 'task', hit: 'provider_calls' }, nextState: next };
  }

  // 6) loop breaker：repeated identical tool call
  const key = evt.key;
  next.repeatedKeys[key] = (next.repeatedKeys[key] || 0) + 1;
  next.ring.push(key);
  while (next.ring.length > (profile.loop_breaker?.soft_window ?? 6)) next.ring.shift();
  const sameInWindow = next.ring.filter((k) => k === key).length;
  if (sameInWindow >= (profile.loop_breaker?.hard_window ?? profile.loop_breaker?.soft_window ?? 6)) {
    violations.push('repeated_identical_tool_call');
    next.circuitBroken = true;
    return { violations, stop: { kind: 'session', hit: 'loop_breaker' }, nextState: next };
  }

  // 7) no-progress（PROGRESS_DELTA）
  if (evt.isMutating) {
    next.consecutiveNoProgress = 0;
    next.lastProgressAt = new Date().toISOString();
  } else {
    next.consecutiveNoProgress += 1;
    if (next.consecutiveNoProgress >= (profile.loop_breaker?.hard_window ?? 6)) {
      violations.push('no_progress');
      next.circuitBroken = true;
      return { violations, stop: { kind: 'session', hit: 'loop_breaker' }, nextState: next };
    }
  }

  return { violations, stop: null, nextState: next };
}

function loadProfiles(path) {
  if (!existsSync(path)) return null;
  try { return JSON.parse(readFileSync(path, 'utf8')); } catch { return null; }
}

function loadStateFile(path) {
  if (!existsSync(path)) return null;
  try { return JSON.parse(readFileSync(path, 'utf8')); } catch { return null; }
}

function writeJson(path, obj) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(obj, null, 2) + '\n', 'utf8');
}

export function apply(ctx, config = {}) {
  const profilesPath = config.profilesPath || DEFAULT_PROFILES;
  const stateDir = config.stateDir || DEFAULT_STATE_DIR;
  const auditPath = config.auditPath || DEFAULT_AUDIT;
  const checkpointScript = config.checkpointScript
    || process.env.AE_GOV_CHECKPOINT_SCRIPT || null;
  const admissionScript = config.admissionScript
    || process.env.AE_GOV_ADMISSION_SCRIPT || null;
  const enforcedTaskId = config.taskId || process.env.AE_GOV_TASK_ID || null;
  const enforcedProfile = config.profile || process.env.AE_GOV_PROFILE || null;
  const enforcedObjective = config.objective || process.env.AE_GOV_OBJECTIVE || '';
  const enforcedProject = config.projectId || process.env.AE_GOV_PROJECT || 'unknown';
  const autoAdmit = config.autoAdmit !== false;
  const harness = 'dsh';
  let admittedProfile = null;
  let admitAttempted = false;

  const audit = (record) => {
    try {
      mkdirSync(dirname(auditPath), { recursive: true });
      appendFileSync(auditPath, JSON.stringify({ time: new Date().toISOString(), ...record }) + '\n', 'utf8');
    } catch (error) {
      ctx.logger?.warn?.(`autonomous-execution-governor: audit write failed: ${String(error)}`);
    }
  };

  const admitProfile = (taskId, projectId) => {
    if (!autoAdmit || !admissionScript) return null;
    try {
      const res = spawnSync(process.env.AE_GOV_PYTHON || 'python',
        [admissionScript, 'run', '--task', taskId, '--project', projectId,
         '--objective', enforcedObjective || '(unknown — safe default)', '--harness', harness],
        { encoding: 'utf8', timeout: 20000 });
      if (res.status !== 0) {
        audit({ event: 'admission_error', rc: res.status, out: String(res.stdout || res.stderr || '').slice(-300) });
        return null;
      }
      const out = JSON.parse(res.stdout);
      audit({ event: 'admission', profile: out.profile, confidence: out.confidence,
              bulk_workload: out.bulk_workload, reasons: (out.reasons || []).slice(0, 3) });
      return out.profile || null;
    } catch (error) {
      audit({ event: 'admission_error', error: String(error) });
      return null;
    }
  };

  const checkpointNow = (state, stop) => {
    if (!checkpointScript || !state?.taskId) return;
    try {
      const payload = {
        task_id: state.taskId, project_id: state.projectId,
        execution_profile: state.profile, harness,
        stop_reason: stop?.hit === 'loop_breaker' ? 'loop_breaker' : 'budget_limit',
        next: `governor ${stop?.hit}: budget/loop limit reached; report to task owner and resume from checkpoint`,
        actions: state.actions > 0 ? [`governor-stop-${stop?.hit}`] : [],
        usage_json: JSON.stringify({
          calls_by_model: { [harness]: state.providerCalls },
          input_tokens: 0, cached_input_tokens: 0, output_tokens: 0,
        }),
      };
      const tmp = join(stateDir, `.${state.taskId}.ckpt-input.json`);
      writeJson(tmp, payload);
      mkdirSync(dirname(tmp), { recursive: true });
      const res = spawnSync(process.env.AE_GOV_PYTHON || 'python',
        [checkpointScript, 'save', '--task', state.taskId, '--from-json', tmp],
        { encoding: 'utf8', timeout: 15000 });
      try { unlinkSync(tmp); } catch { }
      audit({ event: 'checkpoint', stop, rc: res.status, out: String(res.stdout || '').slice(-200) });
    } catch (error) {
      audit({ event: 'checkpoint_error', error: String(error) });
    }
  };

  let profilesCache = null;
  const reloadProfiles = () => { profilesCache = loadProfiles(profilesPath); };

  const ensureCheckpoint = (state) => {
    if (!checkpointScript || !state?.taskId) return;
    const ckKey = state.taskId;
    const probe = join(stateDir, `.${ckKey}.known`);
    if (existsSync(probe)) return;
    try {
      const res = spawnSync(process.env.AE_GOV_PYTHON || 'python',
        [checkpointScript, 'new', '--task', state.taskId, '--project', state.projectId,
         '--objective', `autonomous task (governed by ${state.profile})`,
         '--harness', 'dsh', '--profile', state.profile],
        { encoding: 'utf8', timeout: 15000 });
      writeJson(probe, { ok: res.status === 0 });
      audit({ event: 'checkpoint_new', rc: res.status, out: String(res.stdout || res.stderr || '').slice(-200) });
    } catch (error) {
      audit({ event: 'checkpoint_new_error', error: String(error) });
    }
  };

  ctx.tools.guard((execution) => {
    try {
      if (profilesCache === null) reloadProfiles();
      const name = execution?.name;
      if (!name) return undefined;

      if (!enforcedTaskId || !profilesCache) {
        return undefined;
      }
      let profileName = enforcedProfile;
      if (!profileName) {
        if (!admittedProfile && !admitAttempted) {
          admitAttempted = true;
          admittedProfile = admitProfile(enforcedTaskId, enforcedProject) || 'AUTONOMOUS_STANDARD';
        }
        profileName = admittedProfile;
      }
      const profile = profilesCache.profiles?.[profileName]
        || profilesCache.profiles?.['AUTONOMOUS_STANDARD']
        || null;
      if (!profile) {
        audit({ event: 'canonical_broken_no_profile', task: enforcedTaskId });
        return undefined;
      }

      const stateFile = join(stateDir, `task-${enforcedTaskId}.json`);
      let state = loadStateFile(stateFile)
        || emptyState({ taskId: enforcedTaskId, projectId: enforcedProject,
                        profile: profileName, harness, sessionId: String(execution?.agent?.session?.id || '') });
      if (state.profile !== profileName) state.profile = profileName;
      const evt = { key: actionKey(name, execution?.arguments ?? execution?.args),
                    isMutating: MUTATING_TOOLS.has(name) };
      const { violations, stop, nextState } = evaluateGuards(state, evt, profile);
      writeJson(stateFile, nextState);
      audit({ event: 'guard', tool: name, actions: nextState.actions,
              providerCalls: nextState.providerCalls, stop, violations });

      if (stop) {
        checkpointNow(nextState, stop);
        audit({ event: 'fail_closed', tool: name, stop });
        return `[autonomous-execution-governor] FAIL CLOSED (${stop.kind}:${stop.hit}): ` +
          `execution budget/loop limit reached under profile "${enforcedProfile}" ` +
          `(turns=${nextState.actions}, calls=${nextState.providerCalls}). ` +
          `Durable checkpoint saved; stop and report to the task owner. Resume reads the checkpoint — ` +
          `do not continue in this conversation.`;
      }

      const cadence = profile.checkpoint_cadence_turns || 16;
      if (checkpointScript && nextState.actions - nextState.lastCheckpointAtActions >= cadence) {
        ensureCheckpoint(nextState);
        nextState.lastCheckpointAtActions = nextState.actions;
        writeJson(stateFile, nextState);
        audit({ event: 'checkpoint_cadence', actions: nextState.actions, cadence });
      }
      return undefined;
    } catch (error) {
      ctx.logger?.warn?.(`autonomous-execution-governor: internal error, allowing: ${String(error)}`);
      audit({ event: 'internal_error_allowed', error: String(error) });
      return undefined;
    }
  });
}
