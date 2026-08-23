// User-level Cordis plugin: subagent-usage-observer
//
// Registers two model-visible tools that read subagent session logs through
// the sessionQuery service and report token consumption, tool counts, and
// stalled detection.
//
// - subagent_usage:      query one subagent session -> tokens/models/toolCalls/mutations/turns/steps
// - subagent_stalled_check: conservative MUTATED/BLOCKED/STALLED/IMPLEMENTING decision
//
// Data source: sessionQuery.readSession (service-level, no zstd needed).
// Read-only: never modifies session state.
//
// Why a user-level plugin: the dynamic-Cordis alternative (harness.defineTool)
// is session-scoped and disappears when the session ends. A user-level plugin
// persists across sessions and survives process restarts, making the tools
// available to every session in the profile.
//
// Deploy: place this file in ~/.dsh/profiles/<profile>/plugins/ and register
// it in the profile's cordis.patch.yml (see cordis.patch.yml in this package).

export const name = 'subagent-usage-observer';
export const inject = ['sessionQuery', 'tools'];

export function apply(ctx) {
  /** Summarize one subagent session from its event log. */
  async function summarize(sessionId) {
    const snap = await ctx.sessionQuery.readSession(sessionId);
    if (!snap) return null;
    const seedLength = Number.isSafeInteger(snap.session && snap.session.seedLength)
      ? snap.session.seedLength
      : 0;
    const events = (snap.events || []).slice(seedLength);
    let inT = 0, outT = 0, cacheReadT = 0, cacheWriteT = 0, reasoningT = 0;
    let toolCalls = 0, mutations = 0, turns = 0, steps = 0;
    let potentialMutationCalls = 0, blockedReports = 0;
    const models = new Set();
    const toolNamesByCallId = new Map();
    const potentialMutationCallIds = new Set();
    const hasMutationIntent = (toolName, rawArguments) => {
      const source = String(rawArguments || '');
      if (/^(subagent|subagent_fork|workflow|ralph)$/.test(toolName)) return true;
      if (toolName.startsWith('mcp__')) {
        return /"task_kind"\s*:\s*"implementation"|"mode"\s*:\s*"(?:accept-edits|workspace-write|danger-full-access)"|"allowed_files"\s*:/i.test(source);
      }
      if (!/^(pwsh|bash|run_code)$/.test(toolName)) return false;
      return /(?:Set-Content|Add-Content|Out-File|Remove-Item|Move-Item|Copy-Item|New-Item|\b(?:rm|mv|cp|mkdir|touch)\b|\bsed\s+-i\b|\b(?:git\s+apply|patch)\b|(?:^|[^>])>{1,2}(?!=))/i.test(source);
    };
    const preset = snap.session && snap.session.agentPreset || '';
    for (const ev of events) {
      const t = ev.type, d = ev.data || {};
      if (t === 'assistant/message') {
        if (d.usage) {
          inT += d.usage.inputTokens || 0;
          outT += d.usage.outputTokens || 0;
          cacheReadT += d.usage.cacheReadTokens || 0;
          cacheWriteT += d.usage.cacheWriteTokens || 0;
          reasoningT += d.usage.reasoningTokens || 0;
        }
        const text = (d.message && d.message.content || [])
          .filter((block) => block.type === 'text')
          .map((block) => block.text || '')
          .join('\n');
        if (/missing_fact/i.test(text) && /why_required/i.test(text) &&
            /already_checked/i.test(text) && /requested_context/i.test(text)) {
          blockedReports++;
        }
      } else if (t === 'request/header') {
        const cfg = d.header && d.header.config;
        if (cfg) models.add(cfg.provider + '/' + cfg.model);
      } else if (t === 'request/context') {
        if (d.model) models.add(d.provider + '/' + d.model);
      } else if (t === 'tool/call') {
        toolCalls++;
        toolNamesByCallId.set(d.callId, d.name);
        if (hasMutationIntent(d.name, d.arguments)) {
          potentialMutationCallIds.add(d.callId);
        }
      } else if (t === 'tool/result') {
        const resultBlock = (d.message && d.message.content || [])
          .find((block) => block.type === 'tool-result');
        const toolName = toolNamesByCallId.get(resultBlock && resultBlock.toolCallId);
        const succeeded = d.error === undefined && resultBlock && resultBlock.isError !== true;
        if ((toolName === 'edit' || toolName === 'write') && succeeded) {
          mutations++;
        }
        if (potentialMutationCallIds.has(resultBlock && resultBlock.toolCallId) && !succeeded) {
          potentialMutationCallIds.delete(resultBlock.toolCallId);
        }
      } else if (t === 'turn/start') turns++;
      else if (t === 'step/start') steps++;
    }
    potentialMutationCalls = potentialMutationCallIds.size;
    return {
      sessionId, preset, seedLength, models: Array.from(models),
      tokens: {
        uncachedInput: inT,
        output: outT,
        cacheRead: cacheReadT,
        cacheWrite: cacheWriteT,
        reasoning: reasoningT,
        billedInput: inT + cacheReadT + cacheWriteT,
      },
      toolCalls, mutations, potentialMutationCalls, blockedReports, turns, steps,
    };
  }

  const output = {
    schema: { type: 'object', additionalProperties: true },
    render: (_args, value) => [{ type: 'text', text: JSON.stringify(value, null, 2) }],
  };

  const usageTool = {
    name: 'subagent_usage',
    description: '查询一个子代理会话的 token 消耗、工具调用数、mutation 次数、模型与预设。' +
      '输入子代理 session id（如 c8e2fc19-fdff-47d2-a05f-2e8200878890），输出统计摘要。数据源为会话日志，只读。',
    parameters: {
      type: 'object',
      properties: {
        sessionId: { type: 'string', description: '子代理会话 ID' },
      },
      required: ['sessionId'],
    },
    output,
    execute: async (args, _exec) => {
      if (!args || typeof args.sessionId !== 'string' || args.sessionId.length === 0) {
        return { error: 'invalid sessionId' };
      }
      try {
        const s = await summarize(args.sessionId);
        if (!s) return { error: 'session not found', sessionId: args.sessionId };
        return s;
      } catch (error) {
        return { error: 'session read failed', sessionId: args.sessionId, detail: String(error) };
      }
    },
  };

  const stallTool = {
    name: 'subagent_stalled_check',
    description: '基于预算约束判定子代理是否空转（STALLED）。' +
      '输入 sessionId 与可选预算（mutationBudget/toolBudget/turnBudget），' +
      '输出 state（MUTATED/BLOCKED/STALLED/IMPLEMENTING）与判定证据。',
    parameters: {
      type: 'object',
      properties: {
        sessionId: { type: 'string', description: '子代理会话 ID' },
        mutationBudget: { type: 'number', description: 'mutation 最低次数，默认 1' },
        toolBudget: { type: 'number', description: '首次写入前工具调用上限，默认 6' },
        turnBudget: { type: 'number', description: '轮次上限，默认 3' },
      },
      required: ['sessionId'],
    },
    output,
    execute: async (args, _exec) => {
      if (!args || typeof args.sessionId !== 'string' || args.sessionId.length === 0) {
        return { error: 'invalid sessionId' };
      }
      let s;
      try {
        s = await summarize(args.sessionId);
      } catch (error) {
        return { error: 'session read failed', sessionId: args.sessionId, detail: String(error) };
      }
      if (!s) return { error: 'session not found', sessionId: args.sessionId };
      const positiveBudget = (value, fallback) => {
        const number = Number(value);
        return Number.isSafeInteger(number) && number > 0 ? number : fallback;
      };
      const mutBudget = positiveBudget(args.mutationBudget, 1);
      const toolBudget = positiveBudget(args.toolBudget, 6);
      const turnBudget = positiveBudget(args.turnBudget, 3);
      const evidence = [];
      if (s.mutations >= mutBudget) {
        evidence.push('confirmed mutation >= budget: ' + s.mutations + '/' + mutBudget);
        return { state: 'MUTATED', evidence: evidence.join('; '), ...s };
      }
      if (s.blockedReports > 0) {
        evidence.push('structured BLOCKED report detected: ' + s.blockedReports);
        return { state: 'BLOCKED', evidence: evidence.join('; '), ...s };
      }
      if (s.toolCalls >= toolBudget) evidence.push('toolCalls >= budget: ' + s.toolCalls + '/' + toolBudget);
      if (s.turns >= turnBudget) evidence.push('turns >= budget: ' + s.turns + '/' + turnBudget);
      if (s.potentialMutationCalls > 0) {
        evidence.push('successful or in-flight command/delegation call has explicit mutation intent: ' + s.potentialMutationCalls);
        return { state: 'IMPLEMENTING', evidence: evidence.join('; '), ...s };
      }
      let state = 'IMPLEMENTING';
      if (s.toolCalls >= toolBudget || s.turns >= turnBudget) {
        state = 'STALLED';
        evidence.push('action or fallback turn budget exhausted without confirmed mutation or BLOCKED report');
      } else {
        evidence.push('still within budget, awaiting confirmed mutation or BLOCKED report');
      }
      return { state, evidence: evidence.join('; '), ...s };
    },
  };

  ctx.tools.register(usageTool);
  ctx.tools.register(stallTool);
}