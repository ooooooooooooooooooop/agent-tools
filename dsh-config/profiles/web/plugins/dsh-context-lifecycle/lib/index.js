import { Service } from "@deepseek-ai/cordis";
import z from "@deepseek-ai/schemastery";
import { LlmError, createUserMessage } from "@deepseek-ai/dsh-llm";
import { createHash, randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { join } from "node:path";

export const READ_ONLY_CONTEXT_EXHAUSTED = "READ_ONLY_CONTEXT_EXHAUSTED";
export const READ_ONLY_ARCHIVED = "READ_ONLY_ARCHIVED";
export const RESTART_REQUIRED = "RESTART_REQUIRED";
export const CONTEXT_PREFLIGHT_BLOCKED = "CONTEXT_PREFLIGHT_BLOCKED";
const DEFAULT_ARCHIVE_THRESHOLD = 800000;
const DEFAULT_MAX_TEXT = 320;
const API_PROMPT_GUARD = Symbol("dsh-context-lifecycle.api-prompt-guard");

const textOf = (value) => {
	if (typeof value === "string") return value;
	if (!value || typeof value !== "object") return "";
	if (Array.isArray(value)) return value.map(textOf).join(" ");
	return Object.values(value).map(textOf).join(" ");
};
const compactText = (value, max = DEFAULT_MAX_TEXT) => textOf(value).replace(/\s+/g, " ").trim().slice(0, max);
const sha256 = (value) => createHash("sha256").update(value, "utf8").digest("hex");
const sessionDigest = (session) => sha256(JSON.stringify({ header: session.header, events: session.events }));
const safeName = (sessionId) => sha256(String(sessionId)).slice(0, 32);
const clone = (value) => structuredClone(value);

function extractFiles(text) {
	const matches = text.match(/(?:[A-Za-z]:[\\/]|\.\.?[\\/]|\/)[^\s`"'<>]+/g) ?? [];
	return [...new Set(matches.map((item) => item.replace(/[),.;:]+$/, "")))].slice(0, 100);
}

function latestTodo(events) {
	return events.findLast((event) => event.type === "todo/write")?.data.todos ?? [];
}

function latestGoal(events) {
	const direct = events.findLast((event) => event.type === "user/message" && event.data.source?.kind === "user");
	return compactText(direct?.data?.content ?? "", 1000) || "Continue the prior session's unfinished work.";
}

function latestChange(events) {
	const event = events.findLast((candidate) => /change|task|goal/i.test(candidate.type) || /change/i.test(JSON.stringify(candidate.data)));
	return event === void 0 ? null : { type: event.type, seq: event.seq, summary: compactText(event.data, 500) };
}

function collectTestsAndBlockers(events) {
	const tests = [];
	const blockers = [];
	for (const event of events) {
		const text = compactText(event.data, 500);
		if (/(?:test|typecheck|build|deploy|smoke)/i.test(text)) tests.push(text);
		if (/(?:blocked|blocker|context_preflight|error|failed|failure)/i.test(text)) blockers.push(text);
	}
	return { tests: [...new Set(tests)].slice(-30), blockers: [...new Set(blockers)].slice(-30) };
}

function collectArtifacts(events) {
	const artifacts = [];
	for (const event of events) {
		const text = JSON.stringify(event.data);
		for (const hash of text.matchAll(/sha256=([a-f0-9]{64})/g)) artifacts.push({ hash: hash[1], eventSeq: event.seq });
	}
	return artifacts.filter((item, index, all) => all.findIndex((other) => other.hash === item.hash) === index).slice(0, 100);
}

function currentCommit(cwd) {
	if (!cwd) return null;
	try {
		return execFileSync("git", ["-C", cwd, "rev-parse", "HEAD"], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim() || null;
	} catch {
		return null;
	}
}

function isRestartCommand(exec) {
	const name = String(exec?.name ?? "").toLowerCase();
	const args = JSON.stringify(exec?.arguments ?? "").toLowerCase();
	if (/(restart|reboot|shutdown|self[-_ ]?kill)/i.test(name)) return true;
	if (/(stop-process|taskkill|kill\s+-9|pkill|process\.kill|start-sleep|sleep\s+\d+[\s\S]*stop-process)/i.test(args)) return true;
	return false;
}

export class ContextLifecycle extends Service {
	static inject = [];
	static Config = z.object({
		archiveThresholdTokens: z.number().step(1).min(1).default(DEFAULT_ARCHIVE_THRESHOLD)
	});
	states = new Map();
	writeChains = new Map();
	admissions = new Map();
	compactions = new Map();
	circuits = new Map();
	guardedAgents = new WeakMap();
	guardedApis = new WeakMap();
	constructor(ctx, config = {}) {
		super(ctx, "contextLifecycle");
		this.config = {
			archiveThresholdTokens: Number.isSafeInteger(config.archiveThresholdTokens) ? config.archiveThresholdTokens : DEFAULT_ARCHIVE_THRESHOLD,
			sidecarDir: config.sidecarDir || process.env.DSH_CONTEXT_LIFECYCLE_DIR || join(process.cwd(), ".dsh-context-lifecycle")
		};
		ctx.on("session/created", (session) => { void this.observe(session); });
		ctx.on("session/event", (session) => { void this.observe(session); });
		ctx.on("agent/created", ({ agent }) => { this.installAgentInputGuard(agent); });
		ctx.on("agent/disposed", ({ agent }) => { this.uninstallAgentInputGuard(agent); });
		ctx.on("agent/request", async ({ agent }, next) => {
			const state = await this.ensureState(agent.session);
			if (state?.status === READ_ONLY_CONTEXT_EXHAUSTED) {
				throw new LlmError(`session ${agent.session.id} is ${READ_ONLY_CONTEXT_EXHAUSTED}; continue from a handoff session`, CONTEXT_PREFLIGHT_BLOCKED);
			}
			return next();
		});
		ctx.on("tools/pre-execute", async (exec, next) => {
			if (!isRestartCommand(exec)) return next();
			const session = exec.agent?.session;
			if (session !== void 0) await this.requestExternalRestart(session, { command: exec.name });
			return { kind: "deny", reason: `${RESTART_REQUIRED}: persist state and let the external supervisor restart this host` };
		});
		// The HTTP/API boundary must reject a cold archived session before agent
		// lookup, durable prompt append, prepareCall, compaction, or dispatch.
		// Agent-level guards remain installed as a second line for non-HTTP callers.
		ctx.inject(["apiProxy"], (apiCtx) => {
			if (this.installApiPromptGuard(apiCtx.apiProxy)) {
				ctx.effect(() => () => this.uninstallApiPromptGuard(apiCtx.apiProxy), "contextLifecycle.apiPromptGuard");
			}
		});
		const sessions = ctx.get("sessions");
		for (const session of sessions?.list?.() ?? []) void this.observe(session);
	}

	installApiPromptGuard(apiProxy) {
		const prompt = apiProxy?.sessions?.prompt;
		if (typeof prompt !== "function" || prompt[API_PROMPT_GUARD] !== void 0) return false;
		const service = this;
		const guarded = async function guardedPrompt(request, ...args) {
			const sessionId = request?.payload?.sessionId;
			if (typeof sessionId === "string") {
				const error = service.promptAdmissionError(sessionId);
				if (error !== void 0) return { rpcId: request.rpcId, result: { ok: false, error } };
			}
			return prompt.apply(this, [request, ...args]);
		};
		guarded[API_PROMPT_GUARD] = true;
		apiProxy.sessions.prompt = guarded;
		this.guardedApis.set(apiProxy, { original: prompt, guarded });
		return true;
	}

	uninstallApiPromptGuard(apiProxy) {
		const entry = this.guardedApis.get(apiProxy);
		if (entry === void 0) return;
		if (apiProxy?.sessions?.prompt === entry.guarded) apiProxy.sessions.prompt = entry.original;
		this.guardedApis.delete(apiProxy);
	}

	stateFile(sessionId) { return join(this.config.sidecarDir, `${safeName(sessionId)}.json`); }

	loadStateSync(sessionId) {
		const cached = this.states.get(sessionId);
		if (cached !== void 0) return cached;
		try {
			const parsed = JSON.parse(readFileSync(this.stateFile(sessionId), "utf8"));
			if (parsed.sessionId === sessionId) {
				this.states.set(sessionId, parsed);
				return parsed;
			}
		} catch (error) {
			if (error?.code !== "ENOENT") this.ctx.logger.warn(`context-lifecycle: synchronous sidecar read failed: ${String(error)}`);
		}
		return void 0;
	}

	installAgentInputGuard(agent) {
		if (!agent || typeof agent.send !== "function" || this.guardedAgents.has(agent)) return;
		const originalSend = agent.send;
		const service = this;
		agent.send = function guardedSend(message, target, wakeup) {
			service.assertAdmissible(agent.session);
			return originalSend.call(this, message, target, wakeup);
		};
		this.guardedAgents.set(agent, { originalSend });
	}

	uninstallAgentInputGuard(agent) {
		const entry = this.guardedAgents.get(agent);
		if (entry === void 0) return;
		if (agent.send !== entry.originalSend) agent.send = entry.originalSend;
		this.guardedAgents.delete(agent);
	}

	async ensureState(session) {
		if (this.states.has(session.id)) return this.states.get(session.id);
		try {
			const parsed = JSON.parse(await readFile(this.stateFile(session.id), "utf8"));
			if (parsed.sessionId === session.id) this.states.set(session.id, parsed);
		} catch (error) {
			if (error?.code !== "ENOENT") this.ctx.logger.warn(`context-lifecycle: sidecar read failed: ${String(error)}`);
		}
		return this.states.get(session.id);
	}

	persist(sessionId, state) {
		this.states.set(sessionId, clone(state));
		const previous = this.writeChains.get(sessionId) ?? Promise.resolve();
		const next = previous.catch(() => {}).then(async () => {
			await mkdir(this.config.sidecarDir, { recursive: true });
			const target = this.stateFile(sessionId);
			const temporary = `${target}.${process.pid}.${randomUUID()}.tmp`;
			await writeFile(temporary, `${JSON.stringify(state, null, 2)}\n`, "utf8");
			await rename(temporary, target);
		});
		this.writeChains.set(sessionId, next);
		return next;
	}

	async observe(session) {
		const state = await this.ensureState(session);
		if (state !== void 0) return state;
		const meter = this.ctx.get("tokenMeter");
		if (meter === void 0) return;
		let measurement;
		try { measurement = meter.measure(session); } catch { return; }
		if (measurement.totalTokens < this.config.archiveThresholdTokens) return;
		return this.markReadOnly(session, `durable context pressure ${measurement.totalTokens} >= ${this.config.archiveThresholdTokens}`, measurement);
	}

	async markReadOnly(session, reason, measurement) {
		const existing = await this.ensureState(session);
		if (existing?.status === READ_ONLY_CONTEXT_EXHAUSTED) return existing;
		const state = {
			sessionId: session.id,
			status: READ_ONLY_CONTEXT_EXHAUSTED,
			operationalLabel: READ_ONLY_ARCHIVED,
			reason,
			markedAt: new Date().toISOString(),
			eventCount: session.events.length,
			lastSeq: session.events.at(-1)?.seq ?? -1,
			sessionSha256: sessionDigest(session),
			...measurement === void 0 ? {} : { measuredTokens: measurement.totalTokens }
		};
		await this.persist(session.id, state);
		return state;
	}

	/** Persist an archive decision for a cold durable session before it is reopened. */
	async archiveSnapshot(sessionId, details = {}) {
		const existing = this.states.get(sessionId);
		if (existing?.status === READ_ONLY_CONTEXT_EXHAUSTED) return existing;
		const state = {
			sessionId,
			status: READ_ONLY_CONTEXT_EXHAUSTED,
			operationalLabel: READ_ONLY_ARCHIVED,
			reason: details.reason ?? "durable context snapshot exceeded the configured lifecycle threshold",
			markedAt: new Date().toISOString(),
			...details.eventCount === void 0 ? {} : { eventCount: details.eventCount },
			...details.lastSeq === void 0 ? {} : { lastSeq: details.lastSeq },
			...details.sessionSha256 === void 0 ? {} : { sessionSha256: details.sessionSha256 },
			...details.measuredTokens === void 0 ? {} : { measuredTokens: details.measuredTokens },
			...details.evidence === void 0 ? {} : { evidence: clone(details.evidence) }
		};
		await this.persist(sessionId, state);
		return state;
	}

	status(session) {
		return clone(this.states.get(session.id) ?? { sessionId: session.id, status: "ACTIVE" });
	}

	assertAdmissible(session) {
		const error = this.promptAdmissionError(session.id);
		if (error !== void 0) throw new LlmError(error.message, error.details.admissionCode);
		return true;
	}

	promptAdmissionError(sessionId) {
		const state = this.loadStateSync(sessionId);
		if (state?.status !== READ_ONLY_CONTEXT_EXHAUSTED) return void 0;
		return {
			code: READ_ONLY_CONTEXT_EXHAUSTED,
			message: `session ${sessionId} is ${READ_ONLY_CONTEXT_EXHAUSTED}; continue from a handoff session`,
			details: { sessionId, status: state.status, operationalLabel: state.operationalLabel, admissionCode: CONTEXT_PREFLIGHT_BLOCKED }
		};
	}

	recordAdmission(session, snapshot) {
		const normalized = clone(snapshot);
		// `trustedUsage` is a provenance claim, not a numeric fallback.  If an
		// upstream caller supplies an estimate in that slot, move it to the
		// explicitly estimated field before it can reach the durable projection.
		if (normalized.sampleValidity !== "trusted" && normalized.trustedUsage !== void 0) {
			normalized.usageEstimate ??= typeof normalized.trustedUsage === "object" ? normalized.trustedUsage.tokens : normalized.trustedUsage;
			delete normalized.trustedUsage;
		}
		const value = clone({ ...normalized, sessionId: session.id, recordedAt: new Date().toISOString() });
		this.admissions.set(session.id, value);
		this.appendObservabilityContext(session, {
			...normalized.provider === void 0 ? {} : { provider: normalized.provider },
			...normalized.model === void 0 ? {} : { model: normalized.model },
			...normalized.contextWindow === void 0 ? {} : { contextWindow: normalized.contextWindow },
			projectedInput: normalized.projectedInput,
			reservedOutput: normalized.reservedOutput,
			combinedContext: normalized.combinedContext,
			configuredLimit: normalized.configuredLimit,
			effectiveLimit: normalized.effectiveLimit,
			providerAttestedLimit: normalized.providerAttestedLimit,
			...normalized.sampleValidity === "trusted" && normalized.trustedUsage !== void 0
				? { trustedUsage: typeof normalized.trustedUsage === "object" ? normalized.trustedUsage.tokens : normalized.trustedUsage }
				: {},
			sampleValidity: normalized.sampleValidity,
			sampleSource: normalized.sampleSource,
			sampleStatus: normalized.sampleStatus,
			...normalized.sampleValidity === "estimated" && normalized.usageEstimate !== void 0
				? { usageEstimate: normalized.usageEstimate }
				: {},
			estimateMethod: normalized.estimateMethod,
			estimateConfidence: normalized.estimateConfidence
		});
		return value;
	}

	recordCompaction(session, state) {
		const value = clone({ ...state, sessionId: session.id, recordedAt: new Date().toISOString() });
		this.compactions.set(session.id, value);
		this.appendObservabilityContext(session, { compactionState: state.state });
		return value;
	}

	recordCircuit(session, state) {
		const value = clone({ ...state, sessionId: session.id, recordedAt: new Date().toISOString() });
		this.circuits.set(session.id, value);
		this.appendObservabilityContext(session, { circuitBreakerState: state.state });
		return value;
	}

	/** Publish lifecycle fields through the existing UI-visible request/context projection. */
	appendObservabilityContext(session, fields) {
		if (session === void 0 || typeof session.append !== "function") return;
		const route = session.requestContext?.() ?? {};
		const provider = fields.provider ?? route.provider;
		const model = fields.model ?? route.model;
		if (typeof provider !== "string" || typeof model !== "string") return;
		const next = Object.fromEntries(Object.entries({ provider, model, ...fields }).filter(([, value]) => value !== void 0));
		const same = Object.keys(next).every((key) => route[key] === next[key]);
		if (!same) session.append("request/context", next);
	}

	observability(session) {
		return clone({
			...this.admissions.get(session.id) ?? {},
			...this.status(session),
			compactionState: this.compactionState(session),
			circuitBreakerState: this.circuitState(session)
		});
	}

	compactionState(session) {
		if (this.compactions.has(session.id)) return clone(this.compactions.get(session.id));
		const starts = session.events.filter((event) => event.type === "compaction/start").length;
		const ends = session.events.filter((event) => event.type === "compaction/end").length;
		return starts > ends ? "RUNNING" : starts > 0 ? "IDLE" : "NONE";
	}

	circuitState(session) {
		if (this.circuits.has(session.id)) return clone(this.circuits.get(session.id));
		return session.events.findLast((event) => event.type === "compaction/circuit")?.data?.state ?? "UNKNOWN";
	}

	handoff(session) {
		const events = session.events;
		const todos = latestTodo(events);
		const { tests, blockers } = collectTestsAndBlockers(events);
		const summary = {
			format: "DSH_HANDOFF_V1",
			sourceSessionId: session.id,
			sourceSessionSha256: sessionDigest(session),
			sourceEventCount: events.length,
			goal: latestGoal(events),
			activeChange: latestChange(events),
			incompleteTasks: todos.filter((todo) => todo.status !== "completed").map((todo) => ({ content: todo.content, status: todo.status })),
			keyDecisions: events.filter((event) => event.type === "goal/change" || event.type === "plan/mode").slice(-20).map((event) => ({ type: event.type, seq: event.seq, summary: compactText(event.data, 500) })),
			files: extractFiles(events.map((event) => compactText(event.data, 1000)).join("\n")),
			commit: currentCommit(session.header.cwd),
			artifacts: collectArtifacts(events),
			tests,
			blockers,
			context: this.observability(session)
		};
		return clone(summary);
	}

	preview(session) {
		return { kind: "preview", exportable: true, ...this.handoff(session) };
	}

	async export(session) {
		const handoff = this.handoff(session);
		await mkdir(this.config.sidecarDir, { recursive: true });
		const body = `${JSON.stringify(handoff, null, 2)}\n`;
		const artifactPath = join(this.config.sidecarDir, `handoff-${safeName(session.id)}-${Date.now()}.json`);
		await writeFile(artifactPath, body, "utf8");
		return { ...handoff, kind: "export", artifactPath, artifactSha256: sha256(body), bytes: Buffer.byteLength(body) };
	}

	async createNewSession(session, options = {}) {
		const handoff = this.handoff(session);
		const sessionId = options.sessionId ?? `handoff-${randomUUID()}`;
		const route = session.requestHeader()?.config ?? {};
		const handle = await this.ctx.agents.create({
			sessionId,
			agentOptions: { provider: options.provider ?? route.provider, model: options.model ?? route.model },
			meta: { cwd: session.header.cwd }
		});
		const payload = `Handoff from ${session.id}. Original session remains read-only and byte-identical.\n${JSON.stringify(handoff)}`;
		handle.agent.inject(createUserMessage({ content: [{ type: "text", text: payload }], source: { kind: "plugin", plugin: "dsh-context-lifecycle" } }));
		return { sessionId, sourceSessionId: session.id, sourceSessionSha256: handoff.sourceSessionSha256, agent: handle.agent };
	}

	async requestExternalRestart(session, details = {}) {
		const handoff = await this.export(session);
		const record = {
			kind: RESTART_REQUIRED,
			oldPid: process.pid,
			port: Number(process.env.DSH_PORT ?? 3080),
			requestedAt: new Date().toISOString(),
			handoffArtifact: handoff.artifactPath,
			command: details.command ?? null,
			status: "WAITING_FOR_EXTERNAL_SUPERVISOR"
		};
		await writeFile(join(this.config.sidecarDir, `restart-request-${process.pid}.json`), `${JSON.stringify(record, null, 2)}\n`, "utf8");
		return record;
	}

	async recordExternalRestart({ oldPid, newPid = process.pid, port = Number(process.env.DSH_PORT ?? 3080), health, restartedAt = new Date().toISOString() }) {
		await mkdir(this.config.sidecarDir, { recursive: true });
		const record = { kind: "EXTERNAL_RESTART", oldPid, newPid, port, restartedAt, health };
		const path = join(this.config.sidecarDir, `restart-${newPid}.json`);
		await writeFile(path, `${JSON.stringify(record, null, 2)}\n`, "utf8");
		return { path, ...record };
	}
}

export default ContextLifecycle;
export { extractFiles, isRestartCommand, sessionDigest, compactText };
