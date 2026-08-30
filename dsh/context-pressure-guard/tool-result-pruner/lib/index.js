import { Service } from "@deepseek-ai/cordis";
import z from "@deepseek-ai/schemastery";
import { deepFreeze, freezeMessage } from "@deepseek-ai/dsh-llm";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
//#region lib/types/config.js
/** Configuration resolution for deterministic tool-result pruning. */
/** Fixed marker substituted for every removed middle span. */
const PRUNE_MARKER = "\n\n[... tool result middle pruned ...]\n\n";
/** Low-friction defaults for coding-agent tool output. */
const DEFAULTS = deepFreeze({
	thresholdChars: 8192,
	headChars: 4096,
	tailChars: 1024
});
const CONFIG_KEYS = new Set([
	"thresholdChars",
	"headChars",
	"tailChars"
]);
/**
* Count Unicode code points without splitting surrogate pairs.
* @param text - text to measure.
* @returns the Unicode code-point count.
*/
function codePointLength(text) {
	return Array.from(text).length;
}
/**
* Resolve and validate pruning budgets.
* @param config - raw plugin configuration.
* @returns a detached deeply immutable configuration.
*/
function resolveConfig(config = {}) {
	for (const key of Object.keys(config)) if (!CONFIG_KEYS.has(key)) throw new Error(`ToolResultPruneConfig: unknown key "${key}" (allowed: thresholdChars, headChars, tailChars)`);
	const resolved = {
		thresholdChars: config.thresholdChars ?? DEFAULTS.thresholdChars,
		headChars: config.headChars ?? DEFAULTS.headChars,
		tailChars: config.tailChars ?? DEFAULTS.tailChars
	};
	assertPositiveInteger("thresholdChars", resolved.thresholdChars);
	assertNonNegativeInteger("headChars", resolved.headChars);
	assertNonNegativeInteger("tailChars", resolved.tailChars);
	const emittedChars = resolved.headChars + codePointLength(PRUNE_MARKER) + resolved.tailChars;
	if (emittedChars > resolved.thresholdChars) throw new Error(`ToolResultPruneConfig: headChars + marker + tailChars (${emittedChars}) must be at most thresholdChars (${resolved.thresholdChars})`);
	return deepFreeze(structuredClone(resolved));
}
function assertPositiveInteger(name, value) {
	if (!Number.isInteger(value) || value <= 0) throw new Error(`ToolResultPruneConfig: ${name} (${value}) must be a positive integer`);
}
function assertNonNegativeInteger(name, value) {
	if (!Number.isInteger(value) || value < 0) throw new Error(`ToolResultPruneConfig: ${name} (${value}) must be a non-negative integer`);
}
/** Find the durable model call paired with one tool result. */
function callFor(session, callId, resultSeq) {
	for (let index = resultSeq - 1; index >= 0; index -= 1) {
		const event = session.events[index];
		if (event?.type === "tool/call" && event.data.callId === callId) return event;
	}
}
/** Keep the reference summary short enough to be useful on the next request. */
function shortSummary(content) {
	return content.filter((block) => block.type === "text").map((block) => block.text).join(" ").replace(/\s+/g, " ").trim().slice(0, 240);
}
/** Parse the opaque local locator emitted by the deterministic reference. */
function referenceIn(content) {
	const text = content.filter((block) => block.type === "text").map((block) => block.text).join("\n");
	const match = text.match(/\[tool-result-reference[\s\S]*?artifact_path=(.*?)]/);
	if (!match) return;
	const body = match[0];
	const path = body.match(/artifact_path=([^\s\]]+)/)?.[1];
	const hash = body.match(/sha256=([a-f0-9]{64})/)?.[1];
	return hash === void 0 || path === void 0 || path.length === 0 ? void 0 : { locator: path, sha256: hash };
}
/** Only completed, stale results are eligible for model-surface replacement. */
function isPrunableResult(event, currentStep) {
	if (event?.type !== "tool/result") return false;
	const data = event.data ?? {};
	const result = data.message?.content?.[0];
	if (data.error !== void 0 || data.completed === false || data.status === "failed" || data.status === "incomplete" || result?.isError === true) return false;
	const turn = data.turn;
	const step = data.step;
	if (currentStep !== void 0 && turn === currentStep.turn && step === currentStep.step) return false;
	return true;
}
const PERSISTED_TOOL_NAMES = new Set(["write", "edit", "apply_patch"]);
const TOOL_CALL_REFERENCE_PREFIX = "[tool-call-reference";
function parseArguments(argumentsText) {
	try { return JSON.parse(argumentsText); } catch { return void 0; }
}
function persistedToolName(name) {
	const normalized = String(name ?? "").toLowerCase().replace(/[^a-z_]+/g, "");
	return PERSISTED_TOOL_NAMES.has(normalized) ? normalized : void 0;
}
function argumentFiles(value, key = "", paths = []) {
	if (typeof value === "string") {
		if (/(?:path|file|target|filename|uri)/i.test(key) || /(?:[A-Za-z]:[\\/]|\.\.?[\\/]|\/)[^\s`"'<>]+/.test(value)) paths.push(value);
		return paths;
	}
	if (Array.isArray(value)) for (const item of value) argumentFiles(item, key, paths);
	else if (value !== null && typeof value === "object") for (const [childKey, childValue] of Object.entries(value)) argumentFiles(childValue, childKey, paths);
	return paths;
}
function toolCallArgumentsReference({ callId, toolName, argumentsText, eventSeq }) {
	const parsed = parseArguments(argumentsText);
	const files = [...new Set(argumentFiles(parsed ?? argumentsText))].slice(0, 8);
	const keys = parsed !== null && typeof parsed === "object" && !Array.isArray(parsed) ? Object.keys(parsed).slice(0, 12).join(",") : "raw";
	const digest = createHash("sha256").update(argumentsText, "utf8").digest("hex");
	const file = files[0] ?? "none";
	const summary = `keys=${keys}; chars=${argumentsText.length}; files=${files.length}`;
	return `${TOOL_CALL_REFERENCE_PREFIX} call_id=${JSON.stringify(callId)} tool_name=${JSON.stringify(toolName)} status="completed" sha256=${digest} event_seq=${eventSeq} file=${JSON.stringify(file)} short_summary=${JSON.stringify(summary)}] full arguments retained in durable session event`;
}
function isToolCallReference(argumentsText) {
	return typeof argumentsText === "string" && argumentsText.startsWith(TOOL_CALL_REFERENCE_PREFIX);
}
function completedToolResult(session, callId) {
	return session.events.find((event) => event?.type === "tool/result" && (event.data?.message?.source?.callId === callId || event.data?.callId === callId));
}
//#endregion
//#region lib/types/index.js
/**
* Replay-safe, model-free tool-result pruning service.
*
* @module @deepseek-ai/dsh-compaction-tool-result-pruner
*/
/** Deterministic head/middle/tail pruning for current tool-result surface nodes. */
var ToolResultPruner = class extends Service {
	static inject = ["tokenMeter"];
	static Config = z.object({
		thresholdChars: z.number().step(1).min(1).default(DEFAULTS.thresholdChars),
		headChars: z.number().step(1).min(0).default(DEFAULTS.headChars),
		tailChars: z.number().step(1).min(0).default(DEFAULTS.tailChars)
	});
	/** Resolved and immutable character budgets. */
	config;
	constructor(ctx, config = {}) {
		super(ctx, "toolResultPruner");
		this.config = resolveConfig(config);
	}
	/**
	* Measure text content in Unicode code points; non-text blocks cost zero.
	* @param blocks - tool-result content to measure.
	* @returns total Unicode code points across text blocks.
	*/
	measureContent(blocks) {
		let chars = 0;
		for (const block of blocks) if (block.type === "text") chars += codePointLength(block.text);
		return chars;
	}
	/**
	* Replace an over-budget text middle while retaining rich-block order.
	* Text slicing is by Unicode code point, not UTF-16 code unit, so a retained
	* boundary cannot split a surrogate pair. Grapheme clusters may still split.
	* @param blocks - original tool-result content.
	* @returns pruned content, or `null` when the text is within budget.
	*/
	pruneContent(blocks) {
		const totalChars = this.measureContent(blocks);
		if (totalChars <= this.config.thresholdChars) return null;
		const removedStart = this.config.headChars;
		const removedEnd = totalChars - this.config.tailChars;
		const pruned = [];
		let consumed = 0;
		let markerInserted = false;
		for (const block of blocks) {
			if (block.type !== "text") {
				pruned.push(block);
				continue;
			}
			const points = Array.from(block.text);
			const blockStart = consumed;
			const blockEnd = blockStart + points.length;
			const headEnd = Math.min(points.length, Math.max(0, removedStart - blockStart));
			const tailStart = Math.min(points.length, Math.max(0, removedEnd - blockStart));
			const marker = blockStart < removedEnd && blockEnd > removedStart && !markerInserted ? PRUNE_MARKER : "";
			if (marker.length > 0) markerInserted = true;
			const text = points.slice(0, headEnd).join("") + marker + points.slice(tailStart).join("");
			if (text.length > 0) pruned.push({
				...block,
				text
			});
			consumed = blockEnd;
		}
		/* v8 ignore next -- totalChars > threshold and valid budgets guarantee a removed text span. */
		if (!markerInserted) throw new Error("tool-result prune: failed to locate the removed text span");
		const charsAfter = this.measureContent(pruned);
		/* v8 ignore next -- config validation fixes the emitted head + marker + tail budget. */
		if (charsAfter > this.config.thresholdChars || charsAfter >= totalChars) throw new Error("tool-result prune: replacement must be smaller and within threshold");
		return pruned;
	}
	/**
	* Prune every over-budget tool result from one stable current-surface snapshot.
	* Each replacement preserves the complete event data except for `content`,
	* cites the shadowed node so replay can recover the replacement input, and is
	* immediately preceded by a `compaction/prune` shadow-price event pricing the
	* shadowed node through the injected token meter, so pure consumers can
	* subtract it without per-node state.
	* @param session - session whose current surface is rewritten.
	* @returns landed replacements and aggregate Unicode-code-point savings.
	* @throws when the session rejects a replacement; replacements committed
	* earlier in the pass remain durable.
	 */
	async restore(session, callId) {
		for (const seq of [...session.surface.nodes]) {
			const event = session.events[seq];
			if (event?.type !== "tool/result" || event.data.message.source.callId !== callId) continue;
			const reference = referenceIn(event.data.message.content[0].content);
			if (reference === void 0) throw new Error(`tool-result restore: no durable reference for call ${callId}`);
			const originalText = await this.readArtifact(reference.locator);
			const digest = createHash("sha256").update(originalText, "utf8").digest("hex");
			if (digest !== reference.sha256) throw new Error(`tool-result restore: artifact hash mismatch for call ${callId}`);
			const message = freezeMessage({
				...event.data.message,
				content: [{ ...event.data.message.content[0], content: [{ type: "text", text: originalText }] }]
			});
			const replacement = session.append("tool/result", { ...event.data, message }, {
				surfaceOp: { op: "replace", start: seq, end: seq },
				sourceEventSeqs: [seq]
			});
			return { callId, originalSeq: seq, replacementSeq: replacement.seq, restoredChars: originalText.length };
		}
		throw new Error(`tool-result restore: call ${callId} not found`);
	}
	/** Restore the full durable arguments for an old persisted write/edit call. */
	async restoreToolCallArguments(session, callId) {
		let visibleSeq;
		let visibleEvent;
		let visibleMessage;
		for (const seq of session.surface.nodes) {
			const event = session.events[seq];
			if (event?.type !== "assistant/message") continue;
			const message = event.data?.message;
			if (message?.content?.some((block) => block.type === "tool-call" && block.id === callId && isToolCallReference(block.arguments))) {
				visibleSeq = seq;
				visibleEvent = event;
				visibleMessage = message;
				break;
			}
		}
		if (visibleEvent === void 0) throw new Error(`tool-call restore: reference for call ${callId} not found`);
		const originalEvent = session.events.find((event) => event?.type === "assistant/message" && event.data?.message?.content?.some((block) => block.type === "tool-call" && block.id === callId && !isToolCallReference(block.arguments)));
		const originalBlock = originalEvent?.data?.message?.content?.find((block) => block.type === "tool-call" && block.id === callId);
		if (originalBlock === void 0) throw new Error(`tool-call restore: durable arguments for call ${callId} not found`);
		const message = freezeMessage({
			...visibleMessage,
			content: visibleMessage.content.map((block) => block.type === "tool-call" && block.id === callId ? { ...block, arguments: originalBlock.arguments } : block)
		});
		const replacement = session.append("assistant/message", { ...visibleEvent.data, message }, {
			surfaceOp: { op: "replace", start: visibleSeq, end: visibleSeq },
			sourceEventSeqs: [visibleSeq]
		});
		return { callId, originalSeq: visibleSeq, replacementSeq: replacement.seq, restoredChars: String(originalBlock.arguments).length };
	}
	async readArtifact(locator) {
		const spillStore = this.ctx.get("spillStore");
		if (typeof spillStore?.readText === "function") return spillStore.readText(locator);
		return readFile(locator, "utf8");
	}
	async pruneSession(session) {
		const candidates = [];
		const currentStep = [...session.events].reverse().find((event) => event.type === "step/start");
		const currentStepKey = currentStep === void 0 ? void 0 : { turn: currentStep.data.turn, step: currentStep.data.step };
		for (const seq of [...session.surface.nodes]) {
			const event = session.events[seq];
			/* v8 ignore next -- surface seqs are validated contiguous log references. */
			if (isPrunableResult(event, currentStepKey)) candidates.push({
				seq,
				event
			});
		}
		const pruned = [];
		let charsRemoved = 0;
		for (const { seq, event } of candidates) {
			const result = event.data.message.content[0];
			let content = this.pruneContent(result.content);
			if (content === null) continue;
			const charsBefore = this.measureContent(result.content);
			const spillStore = this.ctx.get("spillStore");
			if (spillStore !== void 0 && result.content.every((block) => block.type === "text")) {
				const originalText = result.content.map((block) => block.text).join("");
				try {
					const ref = await spillStore.saveText({
						owner: { sessionId: session.header.id },
						source: { toolName: "tool-result-pruner", callId: event.data.message.source.callId, label: "historical-result" },
						suggestedName: "historical-tool-result.txt",
						content: originalText
					});
					const digest = createHash("sha256").update(originalText, "utf8").digest("hex");
					const summary = shortSummary(content);
					const call = callFor(session, event.data.message.source.callId, seq);
					const status = event.data.error?.code ?? (result.isError === true ? "failed" : "completed");
					content = [{
						type: "text",
						text: `${summary}\n\n[tool-result-reference call_id=${JSON.stringify(event.data.message.source.callId)} tool_name=${JSON.stringify(call?.data.name ?? "unknown")} status=${JSON.stringify(status)} sha256=${digest} event_seq=${seq} artifact_path=${ref.locator} short_summary=${JSON.stringify(summary)}] ${ref.retrievalHint}`
					}];
				} catch (error) {
					this.ctx.logger.warn(`tool-result prune: artifact save failed; retaining deterministic preview: ${String(error)}`);
				}
			}
			const charsAfter = this.measureContent(content);
			const message = freezeMessage({
				...event.data.message,
				content: [{
					...result,
					content
				}]
			});
			session.append("compaction/prune", {
				shadowedRange: {
					start: seq,
					end: seq
				},
				shadowedSeqs: [seq],
				shadowedTokenCount: this.ctx.tokenMeter.estimateMessage(event.data.message)
			});
			const replacement = session.append("tool/result", {
				...event.data,
				message
			}, {
				surfaceOp: {
					op: "replace",
					start: seq,
					end: seq
				},
				sourceEventSeqs: [seq]
			});
			pruned.push({
				originalSeq: seq,
				replacementSeq: replacement.seq,
				callId: event.data.message.source.callId,
				charsBefore,
				charsAfter
			});
			charsRemoved += charsBefore - charsAfter;
		}
		const toolCalls = [];
		for (const seq of [...session.surface.nodes]) {
			const event = session.events[seq];
			if (event?.type !== "assistant/message") continue;
			if (currentStepKey !== void 0 && event.data.turn === currentStepKey.turn && event.data.step === currentStepKey.step) continue;
			let changed = false;
			let callCharsRemoved = 0;
			const content = event.data.message.content.map((block) => {
				const toolName = block.type === "tool-call" ? persistedToolName(block.name) : void 0;
				if (toolName === void 0 || typeof block.arguments !== "string" || isToolCallReference(block.arguments)) return block;
				const result = completedToolResult(session, block.id);
				if (!isPrunableResult(result, currentStepKey)) return block;
				const reference = toolCallArgumentsReference({ callId: block.id, toolName, argumentsText: block.arguments, eventSeq: seq });
				changed = true;
				callCharsRemoved += Math.max(0, block.arguments.length - reference.length);
				return { ...block, arguments: reference };
			});
			if (!changed) continue;
			const message = freezeMessage({ ...event.data.message, content });
			session.append("compaction/prune", {
				shadowedRange: { start: seq, end: seq },
				shadowedSeqs: [seq],
				shadowedTokenCount: this.ctx.tokenMeter.estimateMessage(event.data.message)
			});
			const replacement = session.append("assistant/message", { ...event.data, message }, {
				surfaceOp: { op: "replace", start: seq, end: seq },
				sourceEventSeqs: [seq]
			});
			const callIds = content.filter((block) => block.type === "tool-call" && isToolCallReference(block.arguments)).map((block) => block.id);
			toolCalls.push({ originalSeq: seq, replacementSeq: replacement.seq, callIds, charsRemoved: callCharsRemoved });
			charsRemoved += callCharsRemoved;
		}
		return {
			pruned,
			toolCalls,
			charsRemoved
		};
	}
};
//#endregion
export { DEFAULTS, PRUNE_MARKER, ToolResultPruner, ToolResultPruner as default, codePointLength, resolveConfig };
