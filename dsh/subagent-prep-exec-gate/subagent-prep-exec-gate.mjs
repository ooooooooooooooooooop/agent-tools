// User-level Cordis plugin: subagent-prep-exec-gate
//
// Counts subagent dispatches by PREP/EXEC class (from description prefix) and
// injects a visible warning when a session reaches 6+ consecutive PREP
// subagents with zero EXEC — "preparation overwhelms execution" guard for the
// subagent channel (2026-08-25: 19 subagents in one day, all investigation,
// 14 codex dispatches, 0 implementation; user escalated twice).
//
// Architecture (mirrors @deepseek-ai/dsh-repeat-tool-reminder):
//   tools/post-execute  — count subagent dispatches, attach warning via
//                         additionalContexts (denied calls also flow through
//                         here, so the guard sees every attempt)
//   agent/pre-step      — reset the per-session window when a new user message
//                         arrives (a fresh instruction starts a fresh window)
//
// Classification: description starting with "[EXEC]" or matching
// implement/build/run/repair/fix/generate/execute/apply/produce/create is EXEC;
// anything else (including "[PREP]" and bare descriptions) counts as PREP.
// One EXEC dispatch resets the consecutive-PREP counter.
//
// Deploy: place this file in ~/.dsh/profiles/<profile>/plugins/ and register
// it in the profile's cordis.patch.yml (see the cordis.patch.yml in this package).

export const name = 'subagent-prep-exec-gate';

const WARN_PREP = 6;
const EXEC_KEYWORDS = /^(?:\[EXEC\]|implement|build|run|repair|fix|generate|execute|apply|produce|create)/i;

const chains = new Map();

function classify(description) {
  if (!description) return 'PREP';
  return EXEC_KEYWORDS.test(String(description).trim()) ? 'EXEC' : 'PREP';
}

function counterFor(agent) {
  let c = chains.get(agent);
  if (!c) {
    c = { prep: 0, exec: 0 };
    chains.set(agent, c);
  }
  return c;
}

function prependContext(block, additional) {
  return block ? [...additional, ...block] : additional;
}

export function apply(ctx, config = {}) {
  const warnPrep = Number(config.warnPrep) || WARN_PREP;

  ctx.on('tools/post-execute', async (exec, _result, next) => {
    const downstream = await next();
    const toolName = exec.name;
    if (toolName !== 'subagent') return downstream;

    const c = counterFor(exec.agent);
    const cls = classify(exec.args?.description);
    if (cls === 'EXEC') {
      c.exec += 1;
      c.prep = 0; // one EXEC resets the consecutive-PREP window
    } else {
      c.prep += 1;
    }

    if (c.prep >= warnPrep && c.exec === 0) {
      const reminder = {
        type: 'text',
        text: `[subagent-prep-exec-gate] 警告：本轮已连续派发 ${c.prep} 个调查类 subagent（[PREP]）、`
            + `尚无执行类 subagent（[EXEC]）。准备压倒执行——请先派发一个 [EXEC] 类 subagent`
            + `（description 以 [EXEC] 或 implement/build/run/repair 等开头），否则调查类派发将被视为空转。`,
        source: {
          kind: 'governance',
          form: 'notice',
          summary: `subagent-prep-exec-gate: ${c.prep} PREP / ${c.exec} EXEC`,
        },
      };
      if (downstream.kind === 'block') {
        return {
          kind: 'block',
          feedback: downstream.feedback,
          additionalContexts: prependContext(reminder, downstream.additionalContexts),
        };
      }
      return {
        ...downstream,
        additionalContexts: prependContext(reminder, downstream.additionalContexts),
      };
    }
    return downstream;
  });

  // A fresh user instruction starts a fresh window.
  ctx.on('agent/pre-step', ({ agent, messages }, next) => {
    if (messages.some((message) => message.source?.kind === 'user')) {
      chains.delete(agent);
    }
    return next();
  });
}