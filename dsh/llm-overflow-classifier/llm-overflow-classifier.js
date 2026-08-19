// User-level Cordis plugin: classify "Input token exceed the limit" overflow
// wording as CONTEXT_WINDOW_EXCEEDED so compaction/retry handling triggers.
//
// Why a plugin instead of a node_modules patch: `dsh-llm` lives under
// `node_modules/@deepseek-ai/` and every `npm update` / reinstall of
// `@deepseek-ai/dsh` overwrites it, silently dropping the fix. This plugin
// lives in the profile directory (`~/.dsh/profiles/web/plugins/`) and is
// loaded through `cordis.patch.yml`, so it survives package upgrades.
//
// Mechanism: `LlmRuntime.stream()` dispatches the `llm/stream` waterfall;
// listeners wrap the returned chunk stream. We inspect each `finish` chunk
// whose reason is an error and, when the failure message matches the
// DeepSeek/OpenAI "Input token exceed the limit" wording, reclassify its
// code to CONTEXT_WINDOW_EXCEEDED_CODE. Downstream consumers (agent-loop's
// `agent/request-error` waterfall, compaction-basic's code check) then see
// the corrected classification.

import { CONTEXT_WINDOW_EXCEEDED_CODE } from "@deepseek-ai/dsh-llm";

/** DeepSeek/OpenAI-compatible "Input token exceed the limit" overflow wording. */
const INPUT_TOKEN_EXCEED_LIMIT = new RegExp(
	String.raw`\b(?:input|prompt|request)\s+token(?:s)?\s+` +
	String.raw`(?:exceed(?:s|ed)?|is\s+(?:over|beyond)|overflows?)\s+` +
	String.raw`(?:the\s+)?(?:maximum\s+)?limit\b`,
	"i"
);

export const name = "llm-overflow-classifier";
export const inject = ["llm"];

export function apply(ctx) {
	ctx.on("llm/stream", (options, next) => {
		return (async function* () {
			for await (const chunk of next()) {
				if (
					chunk?.type === "finish" &&
					chunk.reason?.kind === "error" &&
					typeof chunk.reason.failure?.message === "string" &&
					chunk.reason.failure.code !== CONTEXT_WINDOW_EXCEEDED_CODE &&
					INPUT_TOKEN_EXCEED_LIMIT.test(chunk.reason.failure.message)
				) {
					yield {
						...chunk,
						reason: {
							...chunk.reason,
							failure: {
								...chunk.reason.failure,
								code: CONTEXT_WINDOW_EXCEEDED_CODE
							}
						}
					};
					continue;
				}
				yield chunk;
			}
		})();
	});
}
