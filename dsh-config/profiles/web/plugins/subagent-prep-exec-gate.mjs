// User-level Cordis plugin: subagent-prep-exec-gate
//
// Two guards on the subagent channel:
//   A) PREP/EXEC balance: counts subagent dispatches by class (description
//      prefix) and injects a warning when a session reaches 6+ consecutive
//      PREP subagents with zero EXEC ("preparation overwhelms execution").
//   B) Interrupt-redispatch budget: counts interrupt_agent calls per session
//      and injects a warning from the 3rd interrupt ("micromanagement 2.0" —
//      stop-and-redispatch loops; see GitHub "run->persist->inspect->retry"
//      discipline and Temporal max-attempts semantics).
//
// Architecture (mirrors @deepseek-ai/dsh-repeat-tool-reminder):
//   tools/post-execute  — count subagent dispatches AND interrupt_agent calls,
//                         attach warnings via additionalContexts
//   agent/pre-step      — reset per-session windows when a new user message
//                         arrives (a fresh instruction starts fresh windows)
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
const WARN_INTERRUPTS = 3; // 3rd interrupt_agent in the same window -> warning
const EXEC_KEYWORDS = /^(?:\[EXEC\]|implement|build|run|repair|fix|generate|execute|apply|produce|create)/i;

const chains = new Map();

function classify(description) {
  if (!description) return 'PREP';
  return EXEC_KEYWORDS.test(String(description).trim()) ? 'EXEC' : 'PREP';
}

function counterFor(agent) {
  let c = chains.get(agent);
  if (!c) {
    c = { prep: 0, exec: 0, interrupts: 0, watchdogWarned: false };
    chains.set(agent, c);
  }
  return c;
}

function prependContext(block, additional) {
  const base = Array.isArray(additional) ? additional : [];
  return block ? [block, ...base] : base;
}

function withReminder(downstream, reminder) {
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

export function apply(ctx, config = {}) {
  const warnPrep = Number(config.warnPrep) || WARN_PREP;
  const warnInterrupts = Number(config.warnInterrupts) || WARN_INTERRUPTS;

  ctx.on('tools/post-execute', async (exec, _result, next) => {
    const downstream = await next();
    const toolName = exec.name;

    // Guard C: self-sleep watchdog detection (铁律九三原则).
    // A pwsh command that sleeps in a loop and claims to be a watchdog
    // ("WATCHDOG"/"deadline" + Start-Sleep) cannot wake a finished session;
    // inject the three-principle reminder once per user-message window.
    if (toolName === 'pwsh') {
      const cmd = String(exec.args?.command || '');
      if (/Start-Sleep/.test(cmd) && /WATCHDOG|watchdog|deadline/.test(cmd)) {
        const c = counterFor(exec.agent);
        if (!c.watchdogWarned) {
          c.watchdogWarned = true;
          const reminder = {
            content: [{
              type: 'text',
              text: `[subagent-prep-exec-gate] 铁律九警告：此命令是"自睡看门狗"（Start-Sleep 循环 + WATCHDOG/deadline）——`
                  + `自睡 job 不算看门狗（业界：定时器必须在持久化状态机/服务端，agent 内 sleep 不能唤醒已结束会话）。`
                  + `正确模式：① job 只负责检查并产出结果（Test-Path → READY/PENDING）；`
                  + `② 唤醒必须来自 goal round 自动轮次（下一轮 goal 回来时用 job_output(job_id) 消费结果）；`
                  + `③ 不要把唤醒依赖在 job 完成通知上（通知不启动已结束会话）。`,
            }],
            source: {
              kind: 'governance',
              form: 'notice',
              summary: 'subagent-prep-exec-gate: self-sleep watchdog detected',
            },
          };
          return withReminder(downstream, reminder);
        }
      }
      return downstream;
    }

    // Guard B: interrupt-and-redispatch budget.
    if (toolName === 'interrupt_agent') {
      const c = counterFor(exec.agent);
      c.interrupts += 1;
      if (c.interrupts >= warnInterrupts) {
        const reminder = {
          content: [{
            type: 'text',
            text: `[subagent-prep-exec-gate] 警告：本轮已对子代理执行 ${c.interrupts} 次 interrupt_agent。`
                + `停止中断-重派循环——先收集子代理已有结果（persist→inspect），只有出现新证据或参数变化`
                + `才允许重派，且只重派失败子步骤（Temporal 粒度语义），不要整批 stop→restart。`,
          }],
          source: {
            kind: 'governance',
            form: 'notice',
            summary: `subagent-prep-exec-gate: ${c.interrupts} interrupts`,
          },
        };
        return withReminder(downstream, reminder);
      }
      return downstream;
    }

    // Guard A: PREP/EXEC balance on subagent dispatches.
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
        content: [{
          type: 'text',
          text: `[subagent-prep-exec-gate] 警告：本轮已连续派发 ${c.prep} 个调查类 subagent（[PREP]）、`
              + `尚无执行类 subagent（[EXEC]）。准备压倒执行——请先派发一个 [EXEC] 类 subagent`
              + `（description 以 [EXEC] 或 implement/build/run/repair 等开头），否则调查类派发将被视为空转。`,
        }],
        source: {
          kind: 'governance',
          form: 'notice',
          summary: `subagent-prep-exec-gate: ${c.prep} PREP / ${c.exec} EXEC`,
        },
      };
      return withReminder(downstream, reminder);
    }
    return downstream;
  });

  // A fresh user instruction starts fresh windows.
  ctx.on('agent/pre-step', ({ agent, messages }, next) => {
    if (Array.isArray(messages) && messages.some((message) => message.source?.kind === 'user')) {
      chains.delete(agent);
    }
    return next();
  });
}