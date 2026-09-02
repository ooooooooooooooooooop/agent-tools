// User-level Cordis plugin: autonomous-execution-governor (v1)
//
// DSH 侧的 AUTONOMOUS_EXECUTION_GOVERNANCE 运行时 adapter。
// 消费 AIC 生成的预算投影（~/.dsh/governance/execution-profiles.generated.json，
// generated state，禁手改——由 `aic render/apply dsh` 管理）。
//
// 机制（与 workflow-model-preflight-gate 同层）：ctx.tools.guard() 单调守卫层，
// dispatch 之前拦截；返回字符串 = 拒绝并把原因反馈给调用模型（fail closed）。
//
// 强制范围（v1，诚实边界）：
//   - 硬：agent_turns（以 tool action 数为 turn 代理，偏保守）、provider_calls、
//     runtime_min、loop breaker（repeated identical call / no-progress）、bounded retry。
//   - 记账：cached_input_tokens 等 token/成本预算在 checkpoint 落盘时由
//     scripts/autonomy/checkpoint.py 按 usage 快照记账（session telemetry /
//     subagent-usage-observer 提供 cacheRead 计量——DSH 原生可读）。
//   - token/cost 硬门禁属 python 侧记账 + ledger 检测（usage_ledger.py runaway），
//     本插件在 headroom 告警层处理——见 README REMAINING_LIMITATIONS。
//
// 安全：无 taskId/profile 配置 = 仅观察（audit），不拒绝任何调用；内部错误 fail-open
// （永远不因插件自身故障破坏会话）。

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

// write-like tools = progress（PROGRESS_DELTA）；其余（read/retry/judge/probe）不算。
const MUTATING_TOOLS = new Set(['edit', 'write', 'str-replace-editor', 'todo_write']);

// ---------------------------------------------------------------------------
// pure logic（无 node 依赖运行时可测；tests/test-governor.mjs 逐一验证）
// ---------------------------------------------------------------------------

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
  };
}

// 返回 { violations: string[], stop: {kind, hit} | null, nextState }
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

  // 3) counters（turn/action 代理 + provider call 代理）
  next.actions += 1;
  next.providerCalls += 1;

  // 4) turn budget
  const turnCap = profile.agent_turns;
  if (Number.isFinite(turnCap) && turnCap > 0 && next.actions >= turnCap) {
    violations.push(`agent_turns_budget`);
    return { violations, stop: { kind: 'session', hit: 'agent_turns' }, nextState: next };
  }

  // 5) provider call budget（task 累计，跨 resume 由 checkpoint 续算）
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

  // 7) no-progress（PROGRESS_DELTA）：write-like = progress，否则累计
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

// ---------------------------------------------------------------------------
// guard integration
// ---------------------------------------------------------------------------

function loadProfiles(path) {
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, 'utf8'));
  } catch {
    return null;
  }
}

function loadStateFile(path) {
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, 'utf8'));
  } catch {
    return null;
  }
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
  const autoAdmit = config.autoAdmit !== false;   // 缺省自动 admission
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

  // Automatic Profile Admission：taskId 已声明但 profile 未声明时，首次 guard 前自动
  // 调用 Personal AI classifier（profile_admission.py）。objective 缺失 → UNKNOWN →
  // safe default AUTONOMOUS_STANDARD（UNKNOWN ≠ UNBOUNDED，admission 先于任务首个执行动作）。
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
      try { unlinkSync(tmp); } catch { /* ignore */ }
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

      // —— 无任务声明（普通会话）：零副作用观察（非 autonomous task，无 autonomous 义务）
      if (!enforcedTaskId || !profilesCache) {
        return undefined;
      }
      // —— Automatic Profile Admission：profile 未声明 → 自动分类（UNKNOWN → safe default）
      let profileName = enforcedProfile;
      if (!profileName) {
        if (!admittedProfile && !admitAttempted) {
          admitAttempted = true;
          admittedProfile = admitProfile(enforcedTaskId, enforcedProject) || 'AUTONOMOUS_STANDARD';
        }
        profileName = admittedProfile;
      }
      const profile = profilesCache.profiles?.[profileName]
        || profilesCache.profiles?.['AUTONOMOUS_STANDARD']   // runtime safe default 兜底
        || null;
      if (!profile) {
        audit({ event: 'canonical_broken_no_profile', task: enforcedTaskId });
        return undefined;   // canonical 破损：fail-open + 留痕（不应发生；aic diff 会暴露）
      }

      const stateFile = join(stateDir, `task-${enforcedTaskId}.json`);
      let state = loadStateFile(stateFile)
        || emptyState({ taskId: enforcedTaskId, projectId: enforcedProject,
                        profile: profileName, harness, sessionId: String(execution?.agent?.session?.id || '') });
      if (state.profile !== profileName) state.profile = profileName;   // admission 后绑定
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

      // checkpoint cadence（在 checkpointScript 可用时）
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