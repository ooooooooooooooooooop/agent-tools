// User-level Cordis plugin: inject per-model steering guidance into the system
// prompt, selected automatically by the CURRENT provider/model route of the
// live agent. Each entry is sourced from the vendor's own official docs /
// prompts (source URL in the table), so no local benchmark budget is needed —
// the vendors already spent it.
//
// Why a plugin instead of a persona config: `dsh-persona` is a static
// template (`{{model}}` only interpolates the model name, no per-model
// branching). This plugin registers one system-prompt section and rewrites
// its text on the `system-prompt/assemble` waterfall AFTER downstream
// listeners ran, reading `variables.provider/model` — which
// `installModelSelection` has already overwritten with the selection in
// effect for THIS request. That matters because a conversation can switch
// models mid-session: `agent.options` is only the creation-time snapshot,
// while the assembled variables carry the model the request will actually
// use. Unmatched models render an empty string, which `renderPrompt` drops,
// costing zero tokens.
//
// Mechanism (verified against dsh-system-prompt + dsh-agent sources):
// - `SystemPrompt.assemble()` runs the `system-prompt/assemble` waterfall;
//   `dsh-agent.installModelSelection()` is a scoped listener on that waterfall
//   that overwrites `variables.provider/model` from `selection.current`.
// - This plugin's own listener awaits `next()` first, so it observes the
//   post-selection values, then patches its section's text in place.
// - Matching order per model id: exact `provider:model` first, then bare
//   `model` across providers (so the table stays portable when another device
//   names its routes differently).
// - KV cache: a matched model always renders the same text, so the prefix
//   stays stable; switching models changes this section only (expected).
//
// Steering philosophy (from research, 2026-08-24):
// - Models differ in real behavioral tendencies, not just protocols:
//   over-thinking, over-engineering, over-eagerness, drift.
// - Vendor prompts are the free, vendor-benchmarked starting point; treat
//   every entry as a versioned, revertible hypothesis, not a permanent rule.
// - Mechanisms (stop conditions, empty-patch rules) beat natural language;
//   this plugin only carries the language layer — enforcement stays in
//   harness mechanics.

/** Steering block for one model id or `provider:model` key. */
const PERSONAS = {
	// ── DeepSeek V4 (user routes: deepseek via b.ai proxy) ────────────────
	// Official: simple agent tasks ~= Pro parity on Flash, cheaper & faster;
	// effort default high, low only for latency, max for complex tasks;
	// checkpoint/stop conditions recommended to curb expansion.
	"deepseek-v4-flash": {
		source: "https://api-docs.deepseek.com/news/news260424/ (V4 release)",
		steering: [
			"普通或简单任务直接执行：不要先进行冗长的自我思考或规划。",
			"每个阶段设置明确的停止条件与完成标准；达标即停，不允许为了显得完整而继续扩展。",
			"不要为了展示能力而重构或扩大改动范围；只做任务要求的修改。"
		]
	},
	"deepseek-v4-pro": {
		source: "https://api-docs.deepseek.com/news/news260424/ (V4 release)",
		steering: [
			"复杂长链路任务优先，简单任务不在此模型上扩大范围。",
			"检查输出是否被 max_tokens 截断（finish_reason=length）；若截断，先补全关键结论再继续。",
			"每个阶段设置检查点与停止条件；完成即停，不继续扩展。"
		]
	},
	// ── Kimi K3 (user route: kimi-coding, k3-256k at Kimi Code endpoint) ──
	// Official: K3 admits over-eagerness — may make unintended decisions on
	// small/vague requests; keep effort fixed within a session (switching
	// breaks prefix cache).
	"k3-256k": {
		source: "https://www.kimi.ai/blog/kimi-k3 + https://platform.kimi.com/docs/guide/use-reasoning-effort",
		steering: [
			"意图模糊时先提问澄清，不要替用户做未言明的决定。",
			"超出任务文件范围的事不做、无必要不改、不得自行增加需求。",
			"会话内保持同一推理强度档位，不要中途切换思考级别。"
		]
	},
	// ── Claude (user route: cpa proxy, OpenAI-compatible) ─────────────────
	// Official (Anthropic best practices): skip plan when scope is clear;
	// touch only relevant files, fix root cause, no drive-by fixes; require
	// evidence (tests/lint) instead of claims.
	"claude-sonnet-4-6": {
		source: "https://code.claude.com/docs/en/best-practices",
		steering: [
			"任务范围清楚、差异能用一句话描述时跳过计划，直接执行。",
			"只改相关文件、修根因；不顺手修无关问题。",
			"完成时运行测试/构建/lint 并展示证据，不要仅声称完成。",
			"输出可以简短，但调查与验证不能省略。"
		]
	},
	// `claude-opus-4-6-thinking` = claude-opus-4-6 + thinking (adaptive).
	// Official: Opus responds strongly to system prompts — forceful wording
	// like "always use tools" causes over-calling; Opus 4.6 tends to over-explore,
	// over-use subagents, and over-engineer; prefer conditional tool guidance.
	"claude-opus-4-6-thinking": {
		source: "https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices + effort docs",
		steering: [
			"使用条件性工具指引（“信息不足时使用工具”），不要“有任何疑问都必须使用工具”。",
			"明确要求最小实现：不做超出任务要求的重构或抽象。",
			"限制前置探索：确认需求后立即行动，避免过度使用子代理或过度调查。",
			"思考与工具调用交替进行，不要长时间只思考不动手。"
		]
	},
	// ── Gemini (user route: cpa proxy; gemini-3.7-flash-high = 3.7 flash +
	// thinking_level high) ─────────────────────────────────────────────────
	// Official/Gemini CLI: reproduce first; explicit empty-patch when nothing
	// needs changing (SRI FixedCode: +20% on such tasks); Plan/read-only phase
	// before editing.
	"gemini-3.7-flash-high": {
		source: "https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/system-prompt.md + SRI FixedCode",
		steering: [
			"先复现问题；若无需修改，明确提交空补丁，不要先改代码。",
			"无必要变更时必须保持空补丁，不为了显得有用而改动。",
			"动手前先进入只读/计划阶段，确认目标后再修改。"
		]
	},
	// ── GPT/Codex (user route: cpa proxy) ─────────────────────────────────
	// Official (Codex base instructions): default to acting over explaining;
	// no plans for simple tasks; work end-to-end; verify changed region first;
	// don't guess, don't fix unrelated issues, don't re-read patched files.
	"gpt-5.6-sol-xhigh": {
		source: "https://github.com/openai/codex/blob/main/codex-rs/protocol/src/prompts/base_instructions/default.md",
		steering: [
			"默认直接执行代码修改或运行工具，而不是只解释方案。",
			"简单任务不使用计划；计划不填充显而易见的步骤。",
			"持续工作直到任务端到端完成；先验证改动区域，再扩大测试。",
			"不猜测、不修无关问题、不重复读取已成功处理的文件。"
		]
	},
	"gpt-5.6-luna-max": {
		source: "https://github.com/openai/codex/blob/main/codex-rs/protocol/src/prompts/base_instructions/default.md",
		steering: [
			"默认直接执行；工具调用前只发简短前言。",
			"成本敏感：max 档只在质量收益明确时使用，避免为小问题做深度分析。",
			"简单任务直接完成，不扩展范围、不追加无关优化。"
		]
	},
	// Codex auto-review model: reviewer role, not a permission granter.
	"codex-auto-review": {
		source: "https://developers.openai.com/codex/sandboxing/auto-review",
		steering: [
			"你是审查者角色：只审查与报告问题，不执行修改。",
			"输出结构化审查结论：问题、证据、风险与建议，不越权批准。"
		]
	},
	// gpt-image-2: image-generation model — registered with empty steering so it
	// matches (and suppresses the generic fallback) while injecting nothing.
	"gpt-image-2": {
		source: "https://developers.openai.com/api/docs/models/gpt-image-2",
		steering: []
	}
};

/** Generic fallback when no exact model entry exists (portable default). */
const FALLBACK_STEERING = [
	"默认直接执行而非只解释；简单任务不用计划。",
	"只做任务要求的修改，不顺手修无关问题、不扩大范围。",
	"完成时提供验证证据（测试/lint/输出），不依赖模型自身断言。"
];

/** Section name and order: after persona (0), before tool guidance (100–199). */
const SECTION_NAME = "model:persona";
const SECTION_ORDER = 50;

/** Exact `provider:model` keys take precedence over bare model ids. */
function lookup(provider, model) {
	if (provider && model) {
		const exact = PERSONAS[`${provider}:${model}`];
		if (exact) return exact;
	}
	if (model && Object.hasOwn(PERSONAS, model)) return PERSONAS[model];
	return void 0;
}

function renderPersona(provider, model) {
	const persona = lookup(provider, model) ?? { steering: FALLBACK_STEERING };
	return persona.steering.map((line) => `- ${line}`).join("\n");
}

export const name = "model-persona";
export const inject = ["systemPrompt"];

/**
 * Apply: register a placeholder section, then rewrite its text on the
 * `system-prompt/assemble` waterfall AFTER downstream listeners (notably
 * `installModelSelection`, which overrides `variables.provider/model` with the
 * CURRENT selection) have run. This keeps the steering in sync with
 * mid-conversation model switches — `agent.options` alone is only the
 * creation-time snapshot, while the assembled `variables` carry the model the
 * request will actually use.
 */
export function apply(ctx) {
	const systemPrompt = ctx.systemPrompt;
	ctx.effect(() =>
		systemPrompt.section({
			name: SECTION_NAME,
			order: SECTION_ORDER,
			text: "" // placeholder; rewritten on the assemble waterfall
		})
	);
	ctx.on("system-prompt/assemble", async (assembly, _context, next) => {
		const result = await next();
		const model = result.variables?.model;
		if (!model) return result;
		const text = renderPersona(result.variables.provider, model);
		return {
			...result,
			sections: result.sections.map((section) =>
				section.name === SECTION_NAME ? { ...section, text } : section
			)
		};
	});
}
