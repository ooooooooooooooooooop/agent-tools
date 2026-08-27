// User-level Cordis plugin: session-supervisor
//
// 会话监督一体化（合并 progress-summarizer + idle-watchdog）
//
// 1. 回合摘要（pre-step）：回合结束后从事件流提取工具/失败/待办/思考
//    （reasoning 文本，deepseek 系可见），生成 3 行摘要注入下一轮，
//    并落盘 <会话目录>/progress.json（跨会话可读，供监督者查看）。
// 2. 空转告警（post-execute）：连续 ≥idleThreshold 次轮询/等待类工具
//    调用无实质产出时注入可见警告（不打断执行）。
//
// 重复工具检测由 DSH 内置 repeat-tool-reminder 负责，本插件不做。
// 零 LLM 调用，纯 JS 微秒级运行时。

export const name = 'session-supervisor';
export const inject = [];

const DEFAULT_IDLE_THRESHOLD = 4;
const DEFAULT_MIN_TURNS = 3;      // 至少几回合才生成摘要（跳过短会话）
const DEFAULT_MAX_SUMMARY = 300;
const DEFAULT_TURN_TIMEOUT_MINUTES = 10; // 回合内距上次实质产出超过该分钟数 → 超时警告

const IDLE_TOOLS = new Set([
  'mcp__agent-switchboard__request_result',
  'mcp__agent-switchboard__request_status',
  'mcp__agent-switchboard__get_goal',
  'mcp__agent-switchboard__list_agents',
  'mcp__agent-switchboard__job_list',
  'mcp__agent-switchboard__get_cli_requests',
  'mcp__agent-switchboard__get_codex_requests',
  'mcp__agent-switchboard__get_topic_status',
  'mcp__agent-switchboard__get_work_memory',
  'request_result', 'request_status', 'get_goal', 'list_agents',
  'job_list', 'job_output', 'get_cli_requests', 'get_codex_requests',
]);

const RESET_TOOLS = new Set([
  'write', 'edit', 'todo_write', 'create_goal', 'update_goal',
  'mcp__agent-switchboard__queue_cli_request',
  'mcp__agent-switchboard__queue_codex_request',
  'mcp__agent-switchboard__queue_claude_request',
  'mcp__agent-switchboard__start_managed_claude_supervisor',
  'mcp__agent-switchboard__send_to_managed_claude_session',
  'subagent', 'subagent_fork', 'workflow', 'ralph',
]);

export function apply(ctx, config = {}) {
  const idleThreshold = Number(config.idleThreshold) || DEFAULT_IDLE_THRESHOLD;
  const minTurns = Number(config.minTurns) || DEFAULT_MIN_TURNS;
  const maxSummary = Number(config.maxSummaryChars) || DEFAULT_MAX_SUMMARY;
  const turnTimeoutMinutes = Number(config.turnTimeoutMinutes) || DEFAULT_TURN_TIMEOUT_MINUTES;

  const processedSeqs = new WeakMap(); // 回合摘要游标
  const idleCounters = new WeakMap();  // 空转计数

  // 摘要持久化遵循 DSH 会话协议：pre-step 注入的 user message（source.kind=
  // session-supervisor）会作为合法事件进入会话事件流持久化（已验证 13 条），
  // 会话恢复/查询事件流均可读到——不需要任何旁路文件。

  // ---------- 1. 回合摘要 ----------
  ctx.on('agent/pre-step', async ({ agent, messages }, next) => {
    try {
      const decision = await next();
      if (decision.kind !== 'enter') return decision;

      const events = agent.session?.events;
      if (!Array.isArray(events) || events.length < 4) return decision;

      let lastEnd = null;
      for (let i = events.length - 1; i >= 0; i--) {
        if (events[i].type === 'turn/end') { lastEnd = events[i]; break; }
      }
      if (!lastEnd) return decision;

      const endSeq = lastEnd.seq;
      const turn = lastEnd.data?.turn;
      if (turn < minTurns) return decision;

      const mem = processedSeqs.get(agent.session) || 0;
      if (endSeq <= mem) return decision;
      if (decision.messages.some(m => m.source?.kind === 'session-supervisor')) return decision;

      let startIdx = 0;
      for (let i = events.length - 1; i >= 0; i--) {
        if (events[i].type === 'turn/start' && events[i].data?.turn === turn) { startIdx = i; break; }
      }
      const turnEvents = events.slice(startIdx, events.length);

      const toolNames = [];
      const seen = new Set();
      let errorCount = 0, assistantCount = 0, todoDone = 0, todoTotal = 0;
      let reasoningText = '';
      for (const e of turnEvents) {
        if (e.type === 'tool/call') {
          const n = e.data?.name;
          if (n && !seen.has(n)) { seen.add(n); toolNames.push(n); }
        } else if (e.type === 'tool/result') {
          if (e.data?.error) errorCount++;
        } else if (e.type === 'assistant/message') {
          assistantCount++;
          for (const b of (e.data?.message?.content || [])) {
            if (b && typeof b === 'object' && b.type === 'reasoning') {
              const t = String(b.text || b.thinking || '');
              if (t) reasoningText = t;
            }
          }
        } else if (e.type === 'todo/write') {
          const todos = e.data?.todos || [];
          todoTotal = todos.length;
          todoDone = todos.filter(t => t.status === 'completed').length;
        }
      }

      const lines = [
        `[回合 #${turn} 结束] 工具: ${toolNames.join('、') || '（无工具调用）'}`,
        `结果: 模型输出 ${assistantCount} 条` + (errorCount ? `，${errorCount} 个工具调用出错` : '') + (todoTotal ? `，待办 ${todoDone}/${todoTotal}` : ''),
      ];
      if (reasoningText) {
        const t = reasoningText.slice(0, 200).replace(/\s+/g, ' ');
        lines.push(`思考: ${t}${reasoningText.length > 200 ? '…' : ''}`);
      } else {
        const lastMsg = [...turnEvents].reverse().find(e => e.type === 'assistant/message');
        if (lastMsg) {
          const text = (lastMsg.data?.message?.content || [])
            .filter(b => b.type === 'text').map(b => b.text).join(' ').trim();
          const t = text.slice(0, 100).replace(/\s+/g, ' ');
          lines.push(t ? `要点: ${t}${text.length > 100 ? '…' : ''}` : '');
        }
      }

      let summary = lines.filter(Boolean).join('\n');
      if (summary.length > maxSummary) summary = summary.slice(0, maxSummary) + '…';

      processedSeqs.set(agent.session, endSeq);

      return {
        kind: 'enter',
        messages: [
          {
            role: 'user',
            id: `session-supervisor-${agent.session?.header?.id || agent.id || 'session'}-${turn}-${endSeq}`,
            source: { kind: 'session-supervisor', name: 'session-supervisor' },
            content: [{ type: 'text', text: summary }],
          },
          ...decision.messages,
        ],
      };
    } catch (err) {
      console.warn('[session-supervisor] summary skip:', err && err.message ? err.message : err);
      return next();
    }
  });

  // ---------- 2. 空转告警 + 回合内超时看门狗 ----------
  ctx.on('tools/post-execute', async (exec, _result, next) => {
    const downstream = await next();
    try {
      const toolName = exec.name;
      let c = idleCounters.get(exec.agent);
      if (!c) {
        c = { n: 0, warned: false, lastProduceAt: Date.now(), timeoutWarned: false };
        idleCounters.set(exec.agent, c);
      }

      const now = Date.now();

      if (RESET_TOOLS.has(toolName)) {
        // 实质产出 → 重置空转计数与超时窗口
        c.n = 0; c.warned = false; c.timeoutWarned = false; c.lastProduceAt = now;
        return downstream;
      }

      if (IDLE_TOOLS.has(toolName)) {
        c.n++;
      } else {
        c.n = Math.max(0, c.n - 1);
      }

      // 空转告警（连续空转调用）
      if (c.n >= idleThreshold && !c.warned) {
        c.warned = true;
        return withReminder(downstream, {
          role: 'user',
          id: `session-supervisor-idle-${exec.agent?.session?.header?.id || exec.agent?.id || 'session'}-${toolName}-${c.n}`,
          content: [{
            type: 'text',
            text: `[session-supervisor] 空转检测：连续 ${c.n} 次工具调用为轮询/等待类，无实质产出。代理未停止，建议检查是否卡住或换方案。`,
          }],
          source: { kind: 'governance', form: 'notice', summary: 'session-supervisor: idle pattern detected' },
        });
      }

      // 回合内超时看门狗（模拟 supervisor 的 stall_timeout）：
      // 距上次实质产出超过 turnTimeoutMinutes 分钟 → 注入超时警告（不中止）
      const idleMs = now - c.lastProduceAt;
      if (idleMs > turnTimeoutMinutes * 60 * 1000 && !c.timeoutWarned) {
        c.timeoutWarned = true;
        const mins = Math.round(idleMs / 60000);
        return withReminder(downstream, {
          role: 'user',
          id: `session-supervisor-timeout-${exec.agent?.session?.header?.id || exec.agent?.id || 'session'}-${toolName}`,
          content: [{
            type: 'text',
            text: `[session-supervisor] 回合超时看门狗：距上次实质产出已 ${mins} 分钟（超 ${turnTimeoutMinutes} 分钟阈值），当前只有轮询/等待类活动。代理未中止，如需兜底请手动停止或重定向。`,
          }],
          source: { kind: 'governance', form: 'notice', summary: 'session-supervisor: turn timeout watchdog fired' },
        });
      }

      return downstream;
    } catch (err) {
      console.warn('[session-supervisor] idle skip:', err && err.message ? err.message : err);
      return downstream;
    }
  });
}

function withReminder(downstream, reminder) {
  if (downstream.kind === 'block') {
    const base = Array.isArray(downstream.additionalContexts) ? downstream.additionalContexts : [];
    return { kind: 'block', feedback: downstream.feedback, additionalContexts: [reminder, ...base] };
  }
  return downstream;
}
