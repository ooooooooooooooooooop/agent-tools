// User-level Cordis plugin: raise every provider's model-request retry policy
// to the Claude Code baseline (maxRetries 10, 500 ms start, 8 s per-attempt
// cap, 25% jitter) unless the provider already configures at least that much.
//
// Why a plugin instead of a node_modules patch: `dsh-llm` lives under
// `node_modules/@deepseek-ai/` and every `npm update` / reinstall of
// `@deepseek-ai/dsh` overwrites it, silently dropping the fix. This plugin
// lives in the profile directory (`~/.dsh/profiles/web/plugins/`) and is
// loaded through `cordis.patch.yml`, so it survives package upgrades.
//
// Mechanism: the agent loop prepares every model request through
// `LlmRuntime.prepareCall()`, and the `dsh-llm-retry` plugin executes the
// prepared call's `retryPolicy` on the `agent/request-error` waterfall. We
// wrap `prepareCall` so each prepared call hands the retry executor the
// Claude Code baseline instead of a weaker policy (the shipped default is
// 5 retries / 10 s cap / 10% jitter in rc.2). Providers already at or above
// the baseline, and `always`-mode policies, pass through untouched.

/** Claude Code retry baseline — `min(0.5 * 2^n, 8 s)` backoff, 25% jitter. */
const CLAUDE_CODE_MAX_RETRIES = 10;
const CLAUDE_CODE_INITIAL_DELAY_MS = 500;
const CLAUDE_CODE_MAX_DELAY_MS = 8000;
const CLAUDE_CODE_JITTER_RATIO = 0.25;

export const name = "llm-retry-claude-code";
export const inject = ["llm"];

/** Raise one normal-mode policy to the Claude Code baseline; otherwise pass through. */
function raiseToClaudeCodeBaseline(policy) {
	if (policy === void 0 || policy.mode !== "normal") return policy;
	const atBaseline =
		policy.maxRetries >= CLAUDE_CODE_MAX_RETRIES &&
		policy.initialDelayMs >= CLAUDE_CODE_INITIAL_DELAY_MS &&
		policy.maxDelayMs >= CLAUDE_CODE_MAX_DELAY_MS &&
		policy.jitterRatio >= CLAUDE_CODE_JITTER_RATIO;
	if (atBaseline) return policy;
	return {
		...policy,
		maxRetries: Math.max(policy.maxRetries ?? 0, CLAUDE_CODE_MAX_RETRIES),
		initialDelayMs: Math.max(policy.initialDelayMs ?? 0, CLAUDE_CODE_INITIAL_DELAY_MS),
		maxDelayMs: Math.max(policy.maxDelayMs ?? 0, CLAUDE_CODE_MAX_DELAY_MS),
		jitterRatio: Math.max(policy.jitterRatio ?? 0, CLAUDE_CODE_JITTER_RATIO)
	};
}

export function apply(ctx) {
	const llm = ctx.llm;
	const originalPrepareCall = llm.prepareCall;
	async function wrappedPrepareCall(config, signal) {
		const prepared = await originalPrepareCall.call(llm, config, signal);
		const retryPolicy = raiseToClaudeCodeBaseline(prepared.retryPolicy);
		if (retryPolicy === prepared.retryPolicy) return prepared;
		return Object.freeze({
			...prepared,
			retryPolicy
		});
	}
	llm.prepareCall = wrappedPrepareCall;
	ctx.effect(() => () => {
		// `ctx.llm` 是 Cordis traceable proxy：读取方法属性返回 shadow proxy，
		// 函数身份比较（llm.prepareCall === wrappedPrepareCall）恒为 false，
		// 不能用作"是否仍是我们安装的包装"的判据。effect 清理在插件停止或
		// HMR 更新时执行（先卸载旧实例再安装新实例），直接还原 apply 时捕获
		// 的原始方法引用即可。
		llm.prepareCall = originalPrepareCall;
	});
}
