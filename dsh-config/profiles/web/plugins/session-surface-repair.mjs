// User-level Cordis plugin: session-surface-repair
//
// A previously interrupted turn can be durably closed while its assistant
// tool-call message remains on the current surface without matching results.
// The built-in crash repair only handles an open final turn, so this small
// repair closes only calls whose own turn/end proves that they are stale.
//
// The result text deliberately records an unknown outcome. It never claims a
// side effect succeeded and it never executes or retries the old tool call.

export const name = 'session-surface-repair';
export const inject = [];

const UNKNOWN_TEXT =
  'The tool call was interrupted after it was recorded, but no result was durably recorded. ' +
  'Its outcome is unknown. Decide whether to retry from the tool semantics: retry only if the ' +
  'operation is read-only or idempotent; if it may have side effects, first verify external state ' +
  'or ask the user. Do not retry blindly.';
const NOT_STARTED_TEXT =
  'The tool call was interrupted before the Harness recorded it as started. Retry it if it is still needed.';

function pendingSurfaceCalls(session) {
  const events = session?.events;
  const nodes = session?.surface?.nodes;
  if (!Array.isArray(events) || !Array.isArray(nodes)) return [];

  const bySeq = new Map(events.map((event) => [event.seq, event]));
  const pending = new Map();
  for (const seq of nodes) {
    const event = bySeq.get(seq);
    if (event?.type === 'assistant/message') {
      for (const block of event.data?.message?.content ?? []) {
        if (block?.type !== 'tool-call' || typeof block.id !== 'string' || block.id.length === 0) continue;
        pending.set(block.id, {
          callId: block.id,
          turn: event.data?.turn,
          step: event.data?.step,
          messageSeq: event.seq,
        });
      }
    } else if (event?.type === 'tool/result') {
      const callId = event.data?.message?.source?.callId;
      if (typeof callId === 'string') pending.delete(callId);
    }
  }
  return [...pending.values()];
}

function recordedToolCalls(events) {
  const calls = new Map();
  for (const event of events) {
    if (event.type !== 'tool/call') continue;
    const callId = event.data?.callId;
    if (typeof callId === 'string' && !calls.has(callId)) calls.set(callId, event);
  }
  return calls;
}

function hasEndedTurnAfter(events, turn, seq) {
  return Number.isSafeInteger(turn) && events.some(
    (event) => event.type === 'turn/end' && event.data?.turn === turn && event.seq > seq,
  );
}

function appendUnknownResult(session, pending, callEvent) {
  const started = callEvent !== undefined;
  const turn = callEvent?.data?.turn ?? pending.turn;
  const step = callEvent?.data?.step ?? pending.step;
  const callId = pending.callId;
  const nextSeq = session.seq;
  const message = {
    role: 'user',
    id: `interrupted-tool-result-${callId}-${nextSeq}`,
    source: { kind: 'tool', callId },
    content: [{
      type: 'tool-result',
      toolCallId: callId,
      isError: true,
      content: [{ type: 'text', text: started ? UNKNOWN_TEXT : NOT_STARTED_TEXT }],
    }],
  };
  const data = {
    turn,
    step,
    message,
    error: started
      ? { name: 'ToolOutcomeUnknownError', code: 'TOOL_OUTCOME_UNKNOWN' }
      : { name: 'ToolNotStartedError', code: 'TOOL_NOT_STARTED' },
  };
  const intent = started
    ? { surfaceOp: 'append', sourceEventSeqs: [callEvent.seq] }
    : { surfaceOp: 'append' };
  session.append('tool/result', data, intent);
}

function repairClosedSurfaceCalls(session) {
  const events = session?.events;
  if (!Array.isArray(events)) return { pending: 0, repaired: 0 };

  const pending = pendingSurfaceCalls(session);
  if (pending.length === 0) return { pending: 0, repaired: 0 };
  const calls = recordedToolCalls(events);
  let repaired = 0;
  for (const item of pending) {
    if (!hasEndedTurnAfter(events, item.turn, item.messageSeq)) continue;
    appendUnknownResult(session, item, calls.get(item.callId));
    repaired += 1;
  }
  return { pending: pending.length, repaired };
}

export function apply(ctx) {
  // This notification is synchronous. Appending here completes before the
  // AgentLoop starts its first step, so pressure compaction sees the repaired
  // surface rather than the stale one.
  ctx.on('agent/session-start', ({ agent }) => {
    const result = repairClosedSurfaceCalls(agent?.session);
    if (result.repaired > 0) {
      ctx.logger?.info?.(
        `[session-surface-repair] closed ${result.repaired} stale tool result(s) before first step`,
      );
    }
    if (result.pending > result.repaired) {
      ctx.logger?.warn?.(
        `[session-surface-repair] ${result.pending - result.repaired} tool call(s) remain unresolved; pre-step will refuse the request`,
      );
    }
  });

  // The startup notification is non-vetoing. This second guard makes a repair
  // failure fail closed instead of allowing an unbalanced transcript into the
  // provider request.
  ctx.on('agent/pre-step', async ({ agent }, next) => {
    const result = repairClosedSurfaceCalls(agent?.session);
    const remaining = pendingSurfaceCalls(agent?.session).length;
    if (remaining > 0) {
      throw new Error(
        `[session-surface-repair] refusing model request with ${remaining} unresolved surface tool call(s) ` +
        `(repaired ${result.repaired} stale call(s))`,
      );
    }
    return next();
  });
}
