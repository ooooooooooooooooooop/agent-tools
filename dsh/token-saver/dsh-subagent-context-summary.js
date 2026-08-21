// DSH 用户级插件：子代理（fork）上下文摘要化
//
// 目标（省 token）：父会话上下文超过阈值时，fork 子代理不再全量复制父
// 会话历史（seed），而是：
//   1. seed 只保留最近 tailTurns 轮完整对话（operational tail，业界共识）；
//   2. 从父会话最近一次 compaction/summary 取模型已生成的摘要（零额外
//      LLM 调用），作为一条「父会话上下文摘要」user 消息注入 seed 开头，
//      子代理的第一轮输入即读到摘要 + 最近对话。
//
// 参考：Claude Code auto-compact / LangChain SummarizationMiddleware /
// MemGPT 的「摘要 + 近期原文」模式；完整原文仍留在父会话可检索。
//
// 仅包装 inheritsParentContext 的 provider（fork）；spawn（全新子代理）
// 不受影响。阈值内的小会话保持原样，行为不变。
//
// 依赖：DSH >= rc.7（dsh-subagent-in-process-driver 导出 startInProcessRun、
//       dsh-session 的 compaction/summary 事件格式）。

import { startInProcessRun } from '@deepseek-ai/dsh-subagent-in-process-driver';

const name = 'dsh-subagent-context-summary';
const inject = ['subagents'];

const DEFAULT_THRESHOLD_CHARS = 30000; // ≈7.5k token 的父会话才开始摘要化
const DEFAULT_TAIL_TURNS = 1;          // seed 保留最近几轮完整对话

function apply(ctx, config = {}) {
  const thresholdChars = config.thresholdChars ?? DEFAULT_THRESHOLD_CHARS;
  const tailTurns = config.tailTurns ?? DEFAULT_TAIL_TURNS;

  /** 父会话已完成轮次的事件数组（与 fork provider 原始逻辑一致）。 */
  function completedTurnPrefix(parent) {
    const events = parent.session.events;
    const lastEnd = events.findLast((e) => e.type === 'turn/end');
    if (lastEnd === undefined) return [];
    return events.slice(0, lastEnd.seq + 1);
  }

  function seedChars(events) {
    let n = 0;
    for (const e of events) n += JSON.stringify(e).length;
    return n;
  }

  /** 父会话最近一次 compaction/summary 的文本，无则 null（免费摘要）。 */
  function lastSummaryText(parent) {
    const events = parent.session.events;
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i];
      if (e.type === 'compaction/summary') {
        const s = e.data?.summary;
        if (Array.isArray(s)) {
          const text = s
            .filter((b) => b && typeof b === 'object' && b.type === 'text')
            .map((b) => b.text)
            .join('\n')
            .trim();
          if (text.length > 0) return text;
        }
      }
    }
    return null;
  }

  /** 摘要注入为 seed 第一条 user 消息事件（合法 session 事件 envelope）。 */
  function summaryMessageEvent(summary) {
    const time = Date.now();
    return {
      type: 'user/message',
      seq: 0,
      time,
      data: {
        content: [{ type: 'text', text: '【父会话上下文摘要】以下是父会话更早部分的模型生成摘要。完整历史仍在父会话中，需要细节时请父会话按需提供：\n\n' + summary }],
        source: { kind: 'plugin', plugin: 'dsh-subagent-context-summary' },
        role: 'user',
        id: 'ctx-summary-' + time + '-' + Math.random().toString(36).slice(2, 8)
      },
      surfaceOp: 'append'
    };
  }

  /** 只保留最近 tailTurns 轮完整对话，seq 重排为从 offset 开始连续。 */
  function tailSeed(parent, offset) {
    const events = parent.session.events;
    const lastEnd = events.findLast((e) => e.type === 'turn/end');
    if (lastEnd === undefined) return [];
    const starts = [];
    for (let i = lastEnd.seq; i >= 0 && starts.length < tailTurns; i--) {
      if (events[i].type === 'turn/start') starts.push(i);
    }
    const from = starts.length > 0 ? starts[starts.length - 1] : 0;
    const slice = events.slice(from, lastEnd.seq + 1);
    return slice.map((e, i) => ({ ...e, seq: i + offset }));
  }

  /**
   * 构造子代理 seed：
   * - 父会话未超阈值 → 原样全量（行为不变，返回 null 表示不干预）；
   * - 超阈值且父会话有 compaction summary → 摘要消息 + 最近 tailTurns 轮；
   * - 超阈值但无 summary（极端情况）→ 仍保留最近 tailTurns 轮并加一条
   *   说明消息，避免子代理误以为看到了完整历史。
   */
  function buildSeed(parent) {
    const full = completedTurnPrefix(parent);
    if (seedChars(full) <= thresholdChars) return null;
    const summary = lastSummaryText(parent);
    const tail = tailSeed(parent, 1);
    const head = summary !== null
      ? summaryMessageEvent(summary)
      : summaryMessageEvent('（父会话上下文超过阈值，较早部分未随子代理传递；需要历史细节时请父会话提供。）');
    return [head, ...tail];
  }

  function wrapProvider(provider) {
    if (provider.__contextSummaryWrapped) return;
    provider.__contextSummaryWrapped = true;
    if (!provider.inheritsParentContext) return;

    const origStart = provider.start.bind(provider);
    const origPrepare = provider.prepareContinuable?.bind(provider);

    provider.start = async (request) => {
      const seed = buildSeed(request.parent);
      if (seed === null) return origStart(request);
      return startInProcessRun(request, { seed });
    };

    if (origPrepare !== undefined) {
      provider.prepareContinuable = async (request) => {
        const seed = buildSeed(request.parent);
        if (seed === null) return origPrepare(request);
        return Promise.resolve({ seed });
      };
    }
  }

  ctx.on('subagent/provider-added', (provider) => {
    wrapProvider(provider);
  });

  // patch 层在 bundle 之后加载，fork provider 可能已注册 → 立即包装存量。
  for (const providerName of ctx.subagents.list()) {
    const provider = ctx.subagents.getProvider(providerName);
    if (provider !== undefined) wrapProvider(provider);
  }
}

export { apply, inject, name };
