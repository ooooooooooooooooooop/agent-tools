// User-level Cordis plugin: compaction-summarization-fallback
//
// 压缩总结双路由（2026-08-27）：主用 cpa/gemini-3.7-flash-high（便宜、短输出），
// 主流失败时兜底原会话模型，避免 flash 不可用 → 压缩失败 → 本轮运行失败。
//
// 背景：cordis.patch.yml 已把 compaction-basic 的总结固定到 flash（修复
// "summary is not smaller than the shadowed content" 死循环——推理模型产出
// 长篇 checkpoint 导致压缩越压越大）。但 compaction-basic 的 summarizeWithLlm
// 只有 configured ?? latest ?? agentTarget 单链，configured 失败即抛错，无兜底。
// 本插件在 llm/stream waterfall 上补这一层：
//   purpose='compaction' 且命中主模型时，主流若抛错或 finish.reason.kind='error'
//   且尚未产出任何内容块，则用同参数换回原会话模型（requestHeader().config）重发。
//
// 安全边界：
// - 只拦 purpose='compaction' 且 provider/model 精确匹配主配置的请求，其余直通；
// - 已产出内容块后出错不兜底（避免摘要内容混杂），原样抛错；
// - finish.kind='aborted'（用户取消）不兜底，正常透传；
// - 兜底目标与主模型相同（如 flash 会话自身压缩）时跳过兜底，抛原始错误；
// - 兜底仅一级：兜底流自身失败则透传其错误，交给 compaction 既有恢复路径。
// - 冷却熔断（LiteLLM 模式）：主模型失败后 cooldownMs 内直通兜底、不再试错，
//   冷却结束自动半开重试主模型。进程内状态，随 DSH 重启清零。

export const name = 'compaction-summarization-fallback';
export const inject = [];

const PURPOSE = 'compaction';
const DEFAULT_COOLDOWN_MS = 300000; // 5 分钟

function toError(failure) {
  if (failure instanceof Error) return failure;
  const err = new Error(failure?.message ?? 'compaction primary summarization stream failed');
  if (failure?.code) err.code = failure.code;
  return err;
}

export function apply(ctx, config = {}) {
  const primaryProvider = config.primaryProvider ?? 'cpa';
  const primaryModel = config.primaryModel ?? 'gemini-3.7-flash-high';
  const cooldownMs = Number.isFinite(config.cooldownMs) ? config.cooldownMs : DEFAULT_COOLDOWN_MS;
  let primaryDownUntil = 0; // 熔断截止时间戳；0 = 未熔断

  ctx.on('llm/stream', (options, next) => {
    if (options?.purpose !== PURPOSE) return next();
    if (options.provider !== primaryProvider || options.model !== primaryModel) return next();
    // 冷却期内跳过试错，直接兜底；无可用兜底目标时仍试主模型（优于直接失败）
    if (Date.now() < primaryDownUntil) {
      const fb = resolveFallback(options);
      if (fb) {
        ctx.logger?.warn?.(`[compaction-fallback] primary ${primaryModel} in cooldown; direct fallback to ${fb.provider}/${fb.model}`);
        return streamFallback(options, fb);
      }
    }
    return wrapWithFallback(options, next);
  });

  function resolveFallback(options) {
    // 兜底目标 = 原会话模型（即 compaction-basic 原生 fallback 的 latest）
    const agent = options.sessionId ? ctx.agents.get(options.sessionId) : undefined;
    const latest = agent?.session?.requestHeader?.()?.config;
    const fbProvider = latest?.provider;
    const fbModel = latest?.model;
    if (!fbProvider || !fbModel || (fbProvider === options.provider && fbModel === options.model)) return undefined;
    return { provider: fbProvider, model: fbModel };
  }

  async function* streamFallback(options, fb) {
    const fbOptions = { ...options, provider: fb.provider, model: fb.model };
    // 重进 llm/stream waterfall：provider/model 不再匹配主配置 → 本监听器直通到适配器
    yield* await ctx.llm.stream(fbOptions);
  }

  async function* wrapWithFallback(options, next) {
    let failure;
    let produced = 0;
    try {
      for await (const chunk of next()) {
        if (chunk?.type === 'finish' && chunk.reason?.kind === 'error') {
          failure = chunk.reason.failure ?? chunk.reason;
          break; // 不把错误终态转发给下游，改走兜底
        }
        if (chunk?.type === 'block-start' || chunk?.type === 'text-delta' || chunk?.type === 'reasoning-delta') produced++;
        yield chunk;
      }
    } catch (err) {
      failure = err;
    }
    if (!failure) return;
    if (produced > 0) throw toError(failure); // 已有部分内容流出，无法干净重发

    primaryDownUntil = Date.now() + cooldownMs; // 熔断主模型
    const fb = resolveFallback(options);
    if (!fb) throw toError(failure);
    ctx.logger?.warn?.(
      `[compaction-fallback] primary ${options.provider}/${options.model} failed: ${toError(failure).message}; ` +
      `fallback to session model ${fb.provider}/${fb.model}; cooldown ${Math.round(cooldownMs / 1000)}s`
    );
    yield* streamFallback(options, fb);
  }
}
